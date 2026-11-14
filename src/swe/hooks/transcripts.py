"""Hook runs recovered from Claude Code transcripts.

Claude Code writes every hook run into the session transcript, so history
from before the event log existed, and every Claude run since, can be
counted without a helper. What a transcript holds:

* ``attachment`` records of type ``hook_success`` (exit 0),
  ``hook_non_blocking_error`` (any other non-2 exit) and ``hook_cancelled``,
  each with ``hookName`` (``PreToolUse:Bash``), ``command``, ``exitCode``,
  ``durationMs``, ``stderr`` and ``toolUseID``;
* ``system`` records of subtype ``stop_hook_summary``, one per Stop that ran
  any hook, listing ``hookInfos`` (``command``, ``durationMs``) and
  ``hookErrors``;
* a PreToolUse block (exit 2) as a tool result reading
  ``PreToolUse:<Tool> hook error: [<command>]: <stderr>``;
* a Stop block as a meta user message
  ``Stop hook feedback:\\n[<command>]: <stderr>``.

Every record carries ``sessionId``, ``cwd``, ``gitBranch`` and
``timestamp``. This module turns them into the event dicts of
``hooks/events.py``, with ``source: "transcript"``. It reads only: nothing
is written, and only the first line of a hook's message is kept.

The transcript list comes from ``sessions.search_writer.sources``, the same
enumeration the search index uses, so there is no second directory walk.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from datetime import datetime
from pathlib import Path

from swe.hooks import shim_runtime
from swe.hooks.events import SCRIPT_EXTENSIONS, hook_stem

# A cheap byte test before json.loads: every record this module reads
# mentions "hook". Most transcript lines do not.
_NEEDLE = b"hook"

# A feedback block or a summary that follows a block is the same run when
# it lands this close (seconds).
_STOP_WINDOW = 15.0

# ``PreToolUse:Bash hook error: [cmd]: stderr`` and the wordings Claude Code
# has used for it. The command sits in brackets; stderr is the rest.
_TOOL_BLOCK = re.compile(
    r"^(?P<event>[A-Za-z]+):(?P<tool>\S+) hook "
    r"(?:blocking )?error(?: from command)?:\s*"
    r"\[(?P<command>.*?)\]:\s*(?P<message>.*)\Z", re.DOTALL)
_FEEDBACK = re.compile(r"^(?P<event>[A-Za-z]+) hook feedback:\s*\n?(?P<body>.*)\Z",
                       re.DOTALL)
_FEEDBACK_ENTRY = re.compile(r"^\[(?P<command>.*?)\]:\s*", re.MULTILINE)


def command_hook(command: str) -> str:
    """The hook's name for a declared command line.

    ``python3 ~/.claude/hooks/_generated/prose-guard.py`` is
    ``prose-guard``: the first token that looks like a script, else the
    first token's basename.
    """
    try:
        tokens = shlex.split(command or "")
    except ValueError:
        tokens = (command or "").split()
    for token in tokens:
        if token.lower().endswith(SCRIPT_EXTENSIONS):
            return hook_stem(token)
    return hook_stem(tokens[0]) if tokens else ""


def _epoch(stamp) -> float | None:
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return None


def _int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _event(record: dict, ts: float, hook: str, name: str, decision: str,
           reason: str, duration, exit_code, tool_use_id) -> dict:
    return {
        "ts": ts,
        "hook": hook,
        "harness": "claude",
        "event": name,
        "session_id": record.get("sessionId")
        if isinstance(record.get("sessionId"), str) else None,
        "cwd": record.get("cwd") if isinstance(record.get("cwd"), str) else None,
        "repo": None,
        "git_branch": record.get("gitBranch")
        if isinstance(record.get("gitBranch"), str) and record.get("gitBranch")
        else None,
        "decision": decision,
        "reason": shim_runtime.reason_line(reason),
        "duration_ms": _int(duration),
        "exit_code": _int(exit_code),
        "stop_hook_active": None,
        "source": "transcript",
        # Used by the in-file merge below and dropped before returning.
        "_tool_use_id": tool_use_id if isinstance(tool_use_id, str) else None,
        "_kind": "",
    }


def _text_of(content) -> str:
    """A message's or tool result's text, whatever shape it came in."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
            if isinstance(item, str)
            or (isinstance(item, dict) and item.get("type") == "text"))
    return ""


def _attachment(record: dict, ts: float) -> list[dict]:
    att = record.get("attachment")
    if not isinstance(att, dict):
        return []
    kind = att.get("type")
    if kind not in ("hook_success", "hook_non_blocking_error",
                    "hook_cancelled", "hook_blocking_error"):
        return []
    command = att.get("command")
    if not isinstance(command, str) and isinstance(att.get("blockingError"),
                                                   dict):
        command = att["blockingError"].get("command")
    hook = command_hook(command or "")
    name = att.get("hookName") or att.get("hookEvent")
    if not hook or not isinstance(name, str):
        return []
    stderr = att.get("stderr")
    if not isinstance(stderr, str) or not stderr.strip():
        stderr = ""
    if kind == "hook_success":
        decision, reason = ("warn", stderr) if stderr else ("allow", "")
    elif kind == "hook_cancelled":
        decision, reason = "error", "cancelled"
    elif kind == "hook_blocking_error":
        blocking = att.get("blockingError")
        message = blocking.get("blockingError") if isinstance(
            blocking, dict) else ""
        decision, reason = "block", stderr or (message or "")
    else:
        stdout = att.get("stdout") if isinstance(att.get("stdout"), str) else ""
        decision = "error"
        reason = stderr or stdout or f"exit {att.get('exitCode')}"
    event = _event(record, ts, hook, name, decision, reason,
                   att.get("durationMs"), att.get("exitCode"),
                   att.get("toolUseID"))
    event["_kind"] = "attachment"
    return [event]


def _stop_summary(record: dict, ts: float) -> list[dict]:
    infos = record.get("hookInfos")
    if not isinstance(infos, list):
        return []
    errors = [e for e in (record.get("hookErrors") or [])
              if isinstance(e, str)]
    out = []
    for info in infos:
        if not isinstance(info, dict) or not isinstance(info.get("command"),
                                                        str):
            continue
        hook = command_hook(info["command"])
        if not hook:
            continue
        decision, reason = "allow", ""
        for error in errors:
            if info["command"] in error or hook in error:
                decision = "error"
                reason = re.sub(r"^\[.*?\]:\s*", "", error.strip())
                break
        event = _event(record, ts, hook, "Stop", decision, reason,
                       info.get("durationMs"), None, record.get("toolUseID"))
        event["_kind"] = "summary"
        out.append(event)
    return out


def _feedback(record: dict, ts: float, text: str) -> list[dict]:
    match = _FEEDBACK.match(text.strip())
    if not match:
        return []
    marks = list(_FEEDBACK_ENTRY.finditer(match.group("body")))
    out = []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else None
        message = match.group("body")[mark.end():end]
        hook = command_hook(mark.group("command"))
        if hook:
            event = _event(record, ts, hook, match.group("event"), "block",
                           message, None, 2, None)
            event["_kind"] = "feedback"
            out.append(event)
    return out


def _tool_results(record: dict, ts: float) -> list[dict]:
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    out = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "tool_result":
            continue
        match = _TOOL_BLOCK.match(_text_of(item.get("content")).strip())
        if not match:
            continue
        hook = command_hook(match.group("command"))
        if hook:
            event = _event(record, ts, hook,
                           f"{match.group('event')}:{match.group('tool')}",
                           "block", match.group("message"), None, 2,
                           item.get("tool_use_id"))
            event["_kind"] = "block"
            out.append(event)
    return out


def parse_lines(lines) -> list[dict]:
    """Hook events in one transcript's lines (bytes or str), file order.

    Runs that two records describe become one: a block's tool result
    absorbs the attachment with the same tool use id, and a Stop feedback
    message absorbs the nearest stop summary for the same hook.
    """
    found: list[dict] = []
    for line in lines:
        raw = line if isinstance(line, bytes) else line.encode("utf-8")
        if _NEEDLE not in raw:
            continue
        try:
            record = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        ts = _epoch(record.get("timestamp"))
        if ts is None:
            continue
        kind = record.get("type")
        if kind == "attachment":
            found += _attachment(record, ts)
        elif kind == "system" and record.get("subtype") == "stop_hook_summary":
            found += _stop_summary(record, ts)
        elif kind == "user":
            found += _tool_results(record, ts)
            message = record.get("message")
            content = message.get("content") if isinstance(
                message, dict) else None
            text = _text_of(content)
            if record.get("isMeta") and "hook feedback" in text:
                found += _feedback(record, ts, text)
    return _collapse(found)


def _collapse(events: list[dict]) -> list[dict]:
    absorbed: set[int] = set()
    blocks = [i for i, e in enumerate(events) if e["_kind"] == "block"]
    by_use: dict[tuple, int] = {}
    for i, event in enumerate(events):
        if event["_kind"] == "attachment" and event["_tool_use_id"]:
            by_use[(event["_tool_use_id"], event["hook"])] = i
    for i in blocks:
        event = events[i]
        j = by_use.get((event["_tool_use_id"], event["hook"]))
        if j is not None and j != i:
            if event["duration_ms"] is None:
                event["duration_ms"] = events[j]["duration_ms"]
            absorbed.add(j)
    summaries = [i for i, e in enumerate(events) if e["_kind"] == "summary"]
    for i, event in enumerate(events):
        if event["_kind"] != "feedback":
            continue
        best = None
        for j in summaries:
            other = events[j]
            if j in absorbed or other["hook"] != event["hook"] \
                    or other["event"] != event["event"] \
                    or other["session_id"] != event["session_id"]:
                continue
            gap = abs(other["ts"] - event["ts"])
            if gap <= _STOP_WINDOW and (best is None or gap < best[0]):
                best = (gap, j)
        if best is not None:
            event["duration_ms"] = events[best[1]]["duration_ms"]
            absorbed.add(best[1])
    kept = []
    for i, event in enumerate(events):
        if i in absorbed:
            continue
        event.pop("_tool_use_id", None)
        event.pop("_kind", None)
        kept.append(event)
    return kept


def parse_file(path: str, since: float | None = None) -> list[dict]:
    """Hook events in one transcript file; unreadable files give none."""
    try:
        with open(path, "rb") as handle:
            events = parse_lines(handle)
    except OSError:
        return []
    return [e for e in events if since is None or e["ts"] >= since]


def claude_transcripts(home: Path, since: float | None = None) -> list[str]:
    """Claude transcript files that can hold events newer than ``since``.

    One enumeration of the roots (``search_writer.sources``), then a stat
    each: a transcript last written before ``since`` holds nothing newer.
    """
    from swe.sessions import search_writer

    out = []
    for source in search_writer.sources(home):
        if source.harness != "claude":
            continue
        if since is not None:
            try:
                if os.stat(source.path).st_mtime < since:
                    continue
            except OSError:
                continue
        out.append(source.path)
    return out


def backfill(home: Path, since: float | None = None) -> list[dict]:
    """Every hook event the Claude transcripts hold since ``since``.

    ``repo`` is filled from each event's cwd (one lookup per folder).
    """
    events: list[dict] = []
    for path in claude_transcripts(home, since):
        events += parse_file(path, since)
    repos: dict[str, str | None] = {}
    for event in events:
        cwd = event["cwd"]
        if cwd:
            if cwd not in repos:
                repos[cwd] = shim_runtime.repo_info(cwd)[0]
            event["repo"] = repos[cwd]
    return events
