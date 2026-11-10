"""Give each worktree exactly one state.

Merged comes from the PR, never from ``merge-base --is-ancestor``: a
squash merge lands the work on main under a new sha, so ancestry calls
every squash-merged branch unmerged. A detached HEAD has no branch to
ask about, so it counts as merged when its commit is reachable from
``origin/main`` (or, for a `swe gh pr worktree` checkout, when its PR
merged). When ``gh`` is missing, signed out or rate-limited the state is
``unknown``, and unknown is never removed.
"""

from __future__ import annotations

import fnmatch
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from swe.github import gh
from swe.worktrees import gitio
from swe.worktrees.discover import pr_number_from_path
from swe.worktrees.models import (MAIN, MERGED_CLEAN, MERGED_DIRTY, NO_PR,
                                  OPEN_PR, ORPHANED, PROTECTED, UNKNOWN,
                                  Worktree)
from swe.worktrees.settings import Settings

# A detached HEAD that is already on main is also what a freshly made
# worktree looks like, before the session in it has committed anything.
# One touched this recently is held back from prune.
RECENT_DETACHED_SECONDS = 24 * 3600

_PR_FIELDS = "number,state,url,headRefOid,mergedAt"


class PrLookup:
    """PR reads through ``gh.py``, memoised for one run. ``None`` means
    gh could not answer (missing, signed out, rate-limited, down)."""

    def __init__(self, cache: bool = True):
        self.cache = cache
        self._memo: dict[tuple, tuple[list | dict | None, str]] = {}
        self._lock = threading.Lock()

    def _get(self, key: tuple, args: list[str]):
        if self.cache:
            with self._lock:
                if key in self._memo:
                    return self._memo[key]
        result = gh.run_json(args)
        value = (result.data, "") if result.status == "ok" else (None, result.error or result.status)
        if self.cache:
            with self._lock:
                self._memo[key] = value
        return value

    def for_branch(self, slug: str, branch: str) -> tuple[list | None, str]:
        data, err = self._get(("branch", slug, branch), [
            "pr", "list", "--repo", slug, "--head", branch, "--state", "all",
            "--limit", "30", "--json", _PR_FIELDS])
        return (data if isinstance(data, list) else None), err

    def for_number(self, slug: str, number: int) -> tuple[dict | None, str]:
        data, err = self._get(("number", slug, number), [
            "pr", "view", str(number), "--repo", slug, "--json", _PR_FIELDS])
        return (data if isinstance(data, dict) else None), err


def _pr_row(pr: dict) -> dict:
    return {"number": pr.get("number"), "state": pr.get("state"),
            "url": pr.get("url"), "head": pr.get("headRefOid"),
            "merged_at": pr.get("mergedAt")}


def worktree_status(path: str, ignore: list[str]) -> tuple[int, int] | None:
    """``(tracked changes, untracked files)`` outside ``ignore``; None
    when git status fails. Files git ignores never show up here."""
    rc, stdout, _ = gitio.git(["-C", path, "status", "--porcelain=v1", "-z",
                               "--untracked-files=all"])
    if rc != 0:
        return None
    tracked = untracked = 0
    parts = stdout.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        code, name = entry[:2], entry[3:]
        if code[0] in "RC":
            i += 1  # the rename's source path follows
        if code == "??":
            if not any(fnmatch.fnmatch(name, pat)
                       or fnmatch.fnmatch(os.path.basename(name), pat)
                       for pat in ignore):
                untracked += 1
        else:
            tracked += 1
    return tracked, untracked


def last_activity(wt: Worktree) -> float:
    """Newest mtime among the worktree's HEAD, index and reflog."""
    gitdir = None
    try:
        text = Path(wt.path, ".git").read_text(encoding="utf-8", errors="replace")
        if text.startswith("gitdir:"):
            gitdir = text[7:].strip()
    except OSError:
        pass
    newest = 0.0
    for p in ([os.path.join(gitdir, n) for n in ("HEAD", "index", "logs/HEAD")]
              if gitdir else []) + [wt.path]:
        try:
            newest = max(newest, os.stat(p).st_mtime)
        except OSError:
            pass
    return newest


def classify(wt: Worktree, lookup: PrLookup, settings: Settings,
             home: Path | None = None, now: float | None = None) -> Worktree:
    """Fill ``state``, ``reason``, ``pr``, ``changes``, ``untracked`` and
    ``last_commit`` in place. Orphans keep the state discovery gave them;
    the main checkout is ``main`` and a protected path ``protected``."""
    if wt.state == ORPHANED:
        return wt
    _classify(wt, lookup, settings, home, now)
    if wt.hold == "main checkout":
        wt.state, wt.pr, wt.hold = MAIN, None, None
        wt.reason = "the main checkout"
    elif wt.hold == "protected":
        wt.state, wt.hold = PROTECTED, None
        wt.reason = "protected path" + (f" ({wt.reason})" if wt.reason else "")
    return wt


def _classify(wt: Worktree, lookup: PrLookup, settings: Settings,
              home: Path | None, now: float | None) -> Worktree:
    if wt.hold == "main checkout":
        status = worktree_status(wt.path, settings.ignore(wt.repo))
        if status:
            wt.changes, wt.untracked = status
        ts = gitio.out(["-C", wt.path, "log", "-1", "--format=%ct", "HEAD"])
        wt.last_commit = int(ts) if ts and ts.isdigit() else None
        return wt
    repo = wt.repo
    status = worktree_status(wt.path, settings.ignore(repo))
    if status is None:
        wt.state, wt.reason = UNKNOWN, "git status failed"
        return wt
    wt.changes, wt.untracked = status
    ts = gitio.out(["-C", wt.path, "log", "-1", "--format=%ct", "HEAD"])
    wt.last_commit = int(ts) if ts and ts.isdigit() else None
    if not wt.head:
        wt.head = gitio.out(["-C", wt.path, "rev-parse", "HEAD"])
    dirty = bool(wt.changes or wt.untracked)
    merged_state = MERGED_DIRTY if dirty else MERGED_CLEAN
    wt.pr = None

    if wt.branch:
        if repo is None or not repo.slug:
            wt.state, wt.reason = UNKNOWN, "origin is not a GitHub repo, so there is no PR to check"
            return wt
        default = (repo.main_ref or "").split("/")[-1] or "main"
        if wt.branch in (default, "main", "master"):
            wt.state, wt.reason = NO_PR, f"on the default branch {wt.branch}"
            return wt
        prs, err = lookup.for_branch(repo.slug, wt.branch)
        if prs is None:
            wt.state, wt.reason = UNKNOWN, f"gh could not list PRs: {err}"
            return wt
        open_ = [p for p in prs if p.get("state") == "OPEN"]
        merged = [p for p in prs if p.get("state") == "MERGED"]
        if open_:
            wt.pr = _pr_row(open_[0])
            wt.state, wt.reason = OPEN_PR, f"PR #{open_[0].get('number')} is open"
        elif merged:
            best = max(merged, key=lambda p: p.get("mergedAt") or "")
            wt.pr = _pr_row(best)
            wt.state = merged_state
            wt.reason = f"PR #{best.get('number')} merged"
        elif prs:
            wt.pr = _pr_row(prs[0])
            wt.state, wt.reason = NO_PR, f"PR #{prs[0].get('number')} closed without merging"
        else:
            wt.state, wt.reason = NO_PR, "no PR for this branch"
        if wt.state == MERGED_DIRTY:
            wt.reason += f", but {_dirt(wt)}"
        return wt

    # Detached HEAD.
    number = pr_number_from_path(wt, home)
    if number is not None and repo is not None and repo.slug:
        pr, err = lookup.for_number(repo.slug, number)
        if pr is None:
            wt.state, wt.reason = UNKNOWN, f"gh could not read PR #{number}: {err}"
            return wt
        wt.pr = _pr_row(pr)
        state = pr.get("state")
        if state == "OPEN":
            wt.state, wt.reason = OPEN_PR, f"PR #{number} is open"
        elif state == "MERGED":
            wt.state, wt.reason = merged_state, f"PR #{number} merged"
        else:
            wt.state, wt.reason = NO_PR, f"PR #{number} closed without merging"
    elif repo is not None and repo.main_ref and wt.head and gitio.is_ancestor(
            repo.common_dir, wt.head, repo.main_ref):
        wt.state = merged_state
        wt.reason = f"detached HEAD is on {repo.main_ref}"
        now = time.time() if now is None else now
        if wt.hold is None and now - last_activity(wt) < RECENT_DETACHED_SECONDS:
            wt.hold = "detached HEAD used in the last 24h (a session may be starting in it)"
    else:
        ref = repo.main_ref if repo and repo.main_ref else "main"
        wt.state, wt.reason = NO_PR, f"detached HEAD is not on {ref}"
    if wt.state == MERGED_DIRTY:
        wt.reason += f", but {_dirt(wt)}"
    return wt


def _dirt(wt: Worktree) -> str:
    bits = []
    if wt.changes:
        bits.append(f"{wt.changes} changed file(s)")
    if wt.untracked:
        bits.append(f"{wt.untracked} untracked file(s)")
    return " and ".join(bits)


def classify_all(worktrees: list[Worktree], lookup: PrLookup,
                 settings: Settings, home: Path | None = None,
                 workers: int = 6) -> list[Worktree]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda w: classify(w, lookup, settings, home), worktrees))
    return worktrees
