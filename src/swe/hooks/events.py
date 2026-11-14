"""The hook run log: reading it, merging it with transcripts, counting it.

``shim_runtime.record`` appends one JSON line per hook run to
``~/.swe/cache/hooks/events.jsonl`` (and rotates it to ``events.jsonl.1``).
``hooks/transcripts.py`` recovers the same kind of event from Claude Code
transcripts, for history from before the helper existed. This module reads
the log, merges the two sources without counting a run twice, filters, and
folds events into the per-hook numbers ``swe hooks stats`` prints.

An event is a plain dict::

    ts, hook, harness, event, session_id, cwd, repo, git_branch,
    decision (block|warn|allow|error), reason, duration_ms, exit_code,
    stop_hook_active (bool or None), source (helper|transcript)

Nothing here writes: the transcript events live in memory for one
command and are never added to the log.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

from swe import paths as _paths
from swe.hooks import shim_runtime

DECISIONS = ("block", "warn", "allow", "error")
TOP_REASONS = 5
# A transcript event and a helper event for the same run are one run when
# their timestamps are this close (seconds). See merge_events.
DEDUPE_WINDOW = 5.0

# Script extensions dropped from a hook's name, so ``prose-guard.py`` and
# the ``prose-guard`` the transcripts name are the same hook.
SCRIPT_EXTENSIONS = (".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs",
                      ".ts", ".rb", ".pl")

_SINCE_RE = re.compile(r"^(\d+)\s*([smhdw])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def hook_stem(name: str) -> str:
    """``prose-guard`` for ``prose-guard.py`` or ``/x/hooks/prose-guard.py``."""
    base = os.path.basename(str(name or "").strip())
    lowered = base.lower()
    for ext in SCRIPT_EXTENSIONS:
        if lowered.endswith(ext) and len(base) > len(ext):
            return base[:-len(ext)]
    return base


def parse_since(text: str, now: float) -> float | None:
    """The epoch second ``--since=<n><s|m|h|d|w>`` starts at; None for ``all``.

    Raises ValueError with the message the command prints.
    """
    value = (text or "").strip().lower()
    if value == "all":
        return None
    match = _SINCE_RE.match(value)
    if not match or int(match.group(1)) <= 0:
        raise ValueError(
            f"--since={text}: write a span like 7d, 24h, 90m or 2w, or all")
    return now - int(match.group(1)) * _UNIT_SECONDS[match.group(2)]


# --- reading the log --------------------------------------------------------


def _clean(raw) -> dict | None:
    """One log line as an event dict, or None when it is not one."""
    if not isinstance(raw, dict):
        return None
    ts, hook = raw.get("ts"), raw.get("hook")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return None
    if not isinstance(hook, str) or not hook:
        return None
    decision = raw.get("decision")
    if decision not in DECISIONS:
        return None

    def text(key):
        value = raw.get(key)
        return value if isinstance(value, str) else None

    duration = raw.get("duration_ms")
    exit_code = raw.get("exit_code")
    active = raw.get("stop_hook_active")
    return {
        "ts": float(ts),
        "hook": hook_stem(hook),
        "harness": text("harness") or "",
        "event": text("event") or "",
        "session_id": text("session_id"),
        "cwd": text("cwd"),
        "repo": text("repo"),
        "git_branch": text("git_branch"),
        "decision": decision,
        "reason": (text("reason") or "")[:shim_runtime.REASON_MAX],
        "duration_ms": (int(duration) if isinstance(duration, (int, float))
                        and not isinstance(duration, bool) else None),
        "exit_code": (exit_code if isinstance(exit_code, int)
                      and not isinstance(exit_code, bool) else None),
        "stop_hook_active": active if isinstance(active, bool) else None,
        "source": "helper",
    }


def read_log(home: Path, since: float | None = None) -> list[dict]:
    """Every event in the log and its rotated generation, oldest first.

    A missing file, an unreadable one and a half-written or foreign line
    each read as nothing: the log is best-effort by design.
    """
    live = _paths.hook_events_file_for(home)
    out: list[dict] = []
    for path in (live.with_name(live.name + ".1"), live):
        try:
            handle = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    event = _clean(json.loads(line))
                except ValueError:
                    continue
                if event and (since is None or event["ts"] >= since):
                    out.append(event)
    out.sort(key=lambda e: e["ts"])
    return out


# --- merging helper and transcript events -----------------------------------


def _base_event(name: str) -> str:
    """``PreToolUse`` for ``PreToolUse:Bash``."""
    return (name or "").split(":", 1)[0]


def merge_events(helper: list[dict], transcript: list[dict],
                 window: float = DEDUPE_WINDOW) -> tuple[list[dict], int]:
    """(events, duplicates dropped): each run counted once, helper first.

    The same run can sit in both sources. A transcript event is that run
    when a helper event has the same ``session_id``, the same hook and the
    same event name before any ``:`` and a timestamp within ``window``
    seconds. Matching is one to one and nearest first, so two real fires a
    second apart in one session still count twice. The helper's event
    wins, since it carries the exact reason and ``stop_hook_active``.
    """
    unmatched: dict[tuple, list[dict]] = {}
    for event in helper:
        key = (event["session_id"], event["hook"], _base_event(event["event"]))
        unmatched.setdefault(key, []).append(event)
    kept: list[dict] = list(helper)
    dropped = 0
    for event in sorted(transcript, key=lambda e: e["ts"]):
        key = (event["session_id"], event["hook"], _base_event(event["event"]))
        pool = unmatched.get(key, [])
        best = None
        for candidate in pool:
            gap = abs(candidate["ts"] - event["ts"])
            if gap <= window and (best is None or gap < best[0]):
                best = (gap, candidate)
        if best is not None:
            pool.remove(best[1])
            dropped += 1
        else:
            kept.append(event)
    kept.sort(key=lambda e: e["ts"])
    return kept, dropped


def mark_recovered(events: list[dict]) -> None:
    """Fill ``stop_hook_active`` on events that lack it, in place.

    A Stop hook that blocks makes the harness continue, and the hook's next
    run in that session is the one that sees ``stop_hook_active``: so a
    Stop event whose predecessor (same session and hook) was a block ran
    with the flag set, and any other Stop event ran without it. Events that
    already carry the flag, which is every helper event, keep it.
    """
    last_decision: dict[tuple, str] = {}
    for event in sorted(events, key=lambda e: e["ts"]):
        if _base_event(event["event"]) not in ("Stop", "SubagentStop"):
            continue
        key = (event["session_id"], event["hook"], event["event"])
        if event["stop_hook_active"] is None:
            event["stop_hook_active"] = last_decision.get(key) == "block"
        last_decision[key] = event["decision"]


# --- filters ----------------------------------------------------------------

_REAL: dict[str, str] = {}


def _real(path: str) -> str:
    cached = _REAL.get(path)
    if cached is None:
        try:
            cached = os.path.realpath(path)
        except (OSError, ValueError):
            cached = path
        _REAL[path] = cached
    return cached


def _within(path: str | None, root: str) -> bool:
    if not path:
        return False
    real = _real(path)
    return real == root or real.startswith(root.rstrip(os.sep) + os.sep)


def repo_filter(path: str):
    """A predicate for ``--repo=<path>``: the event ran in that repository.

    ``path`` may be the repo root or any folder inside it. An event
    matches when its recorded repo is that repo's root, or its cwd sits
    under ``path``.
    """
    root = _real(os.path.expanduser(path))
    top, _ = shim_runtime.repo_info(root)
    top = _real(top) if top else root

    def match(event: dict) -> bool:
        repo = event.get("repo")
        if repo and _real(repo) == top:
            return True
        return _within(event.get("cwd"), root)

    return match


def filter_events(events: list[dict], *, hook: str | None = None,
                  harness: str | None = None, repo: str | None = None,
                  decision: str | None = None,
                  since: float | None = None) -> list[dict]:
    """The events matching every given filter, order kept."""
    wanted = hook_stem(hook) if hook else None
    in_repo = repo_filter(repo) if repo else None
    out = []
    for event in events:
        if since is not None and event["ts"] < since:
            continue
        if wanted and event["hook"] != wanted:
            continue
        if harness and event["harness"] != harness:
            continue
        if decision and event["decision"] != decision:
            continue
        if in_repo and not in_repo(event):
            continue
        out.append(event)
    return out


# --- counting ---------------------------------------------------------------


def _rate(blocks: int, fires: int) -> float:
    return round(blocks / fires, 4) if fires else 0.0


def _fold(events: list[dict]) -> dict:
    counts = Counter(e["decision"] for e in events)
    fires = len(events)
    durations = [e["duration_ms"] for e in events
                 if e["duration_ms"] is not None]
    blocks = [e["ts"] for e in events if e["decision"] == "block"]
    reasons: Counter = Counter(
        (e["decision"], e["reason"]) for e in events
        if e["decision"] != "allow" and e["reason"])
    ranked = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "fires": fires,
        "blocks": counts["block"],
        "warns": counts["warn"],
        "errors": counts["error"],
        "allows": counts["allow"],
        # Stop runs that came right after a block of the same hook and
        # let the turn end: "blocked, then the rewrite was accepted".
        "recovered": sum(1 for e in events
                         if e["decision"] == "allow"
                         and e["stop_hook_active"] is True),
        "block_rate": _rate(counts["block"], fires),
        "last_fired": max((e["ts"] for e in events), default=None),
        "last_block": max(blocks, default=None),
        "avg_duration_ms": (round(sum(durations) / len(durations))
                            if durations else None),
        "top_reasons": [
            {"decision": decision, "reason": reason, "count": count}
            for (decision, reason), count in ranked[:TOP_REASONS]],
    }


def build_stats(events: list[dict]) -> dict:
    """``{"totals": {...}, "hooks": [...]}`` for ``events``.

    One row per (hook, harness), most blocks first. The rows are the shape
    ``swe hooks stats --json`` documents.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for event in events:
        groups.setdefault((event["hook"], event["harness"]), []).append(event)
    rows = []
    for (hook, harness), group in groups.items():
        row = {"hook": hook, "harness": harness}
        row.update(_fold(group))
        rows.append(row)
    rows.sort(key=lambda r: (-r["blocks"], -r["fires"], r["hook"],
                             r["harness"]))
    totals = _fold(events)
    totals.pop("top_reasons")
    totals["hooks"] = len(rows)
    return {"totals": totals, "hooks": rows}
