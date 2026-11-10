"""Prune and slim. Nothing here prints; the controller renders the plan.

Rules (ported from ``scripts/reaper.sh``, issue 747, and widened to every
repo and harness folder by issue 1909):

- dry run unless ``apply``: a dry run writes nothing, not even a fetch;
- prune selects ``merged-clean`` only, plus ``merged-dirty`` when asked,
  and each merged-dirty worktree still needs its own yes;
- never the main checkout, a protected path, a locked worktree or the one
  you are standing in;
- every removal goes through ``git worktree remove --force`` run against
  the main checkout, never ``rm -rf``, then ``git worktree prune``;
- the state is read again right before each removal;
- branches stay unless ``delete_branches``, which deletes local branches
  only (a merged ancestor of main, or a tip that is exactly a merged PR's
  head) and never a remote one;
- slim deletes only git-ignored rebuildable folders, untouched for
  ``days``, inside worktrees that are not merged.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from swe import paths
from swe.worktrees import gitio, sizing
from swe.worktrees.classify import PrLookup, classify
from swe.worktrees.models import (MERGED_CLEAN, MERGED_DIRTY, NO_PR, OPEN_PR,
                                  ORPHANED, REBUILDABLE, UNKNOWN, Repo,
                                  Worktree)
from swe.worktrees.settings import Settings

PRUNABLE_STATES = (MERGED_CLEAN, MERGED_DIRTY)
SLIM_STATES = (OPEN_PR, NO_PR, UNKNOWN)


@dataclass
class Plan:
    action: str                     # prune | slim
    apply: bool
    acted: list[dict] = field(default_factory=list)     # removed / would remove
    skipped: list[dict] = field(default_factory=list)
    branches: list[dict] = field(default_factory=list)  # deleted / would delete
    registrations: list[str] = field(default_factory=list)  # stale entries dropped
    estimated_kb: int = 0
    freed_bytes: int | None = None  # measured on the volumes, apply only
    log: str | None = None
    error: str | None = None

    def to_json(self) -> dict:
        return {"action": self.action, "dry_run": not self.apply,
                "acted": self.acted, "skipped": self.skipped,
                "branches": self.branches,
                "registrations": self.registrations,
                "estimated_kb": self.estimated_kb,
                "freed_bytes": self.freed_bytes, "log": self.log,
                "error": self.error}


# ------------------------------------------------------------------ lock

class Busy(RuntimeError):
    pass


class RunLock:
    """A mkdir lock with the owner's pid inside, as reaper.sh took: atomic
    everywhere, and a lock whose pid is dead is broken safely."""

    def __init__(self, home: Path | None = None):
        self.dir = paths.cache_dir_for(home) / "worktree.lock"
        self.held = False

    def __enter__(self):
        self.dir.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                self.dir.mkdir()
                (self.dir / "pid").write_text(str(os.getpid()))
                self.held = True
                return self
            except FileExistsError:
                pid = self._owner()
                if pid and _alive(pid):
                    raise Busy(f"another swe wt run holds {self.dir} (pid {pid})")
                try:
                    old = time.time() - self.dir.stat().st_mtime > 6 * 3600
                except OSError:
                    old = True
                if pid is None and not old:
                    raise Busy(f"another swe wt run holds {self.dir}")
                shutil.rmtree(self.dir, ignore_errors=True)
        raise Busy(f"could not take {self.dir}")

    def _owner(self) -> int | None:
        try:
            return int((self.dir / "pid").read_text().strip())
        except (OSError, ValueError):
            return None

    def __exit__(self, *exc):
        if self.held and self._owner() == os.getpid():
            shutil.rmtree(self.dir, ignore_errors=True)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ------------------------------------------------------------------ prune

def _skip(wt: Worktree, why: str) -> dict:
    return {"path": wt.path, "repo": wt.repo.name if wt.repo else None,
            "branch": wt.branch, "state": wt.state, "reason": why}


def _row(wt: Worktree) -> dict:
    return {"path": wt.path, "repo": wt.repo.name if wt.repo else None,
            "branch": wt.branch, "state": wt.state, "reason": wt.reason,
            "size_kb": wt.size_kb, "pr": wt.pr}


def prune(worktrees: list[Worktree], *, settings: Settings, apply: bool = False,
          include_dirty: bool = False, branch: str | None = None,
          delete_branches: bool = False, auto: bool = False,
          confirm: Callable[[Worktree], bool] | None = None,
          home: Path | None = None) -> Plan:
    plan = Plan("prune", apply)
    allowed = PRUNABLE_STATES if include_dirty else (MERGED_CLEAN,)
    if branch:
        worktrees = [w for w in worktrees if w.branch == branch]
    chosen: list[Worktree] = []
    for wt in worktrees:
        if wt.state == ORPHANED:
            if wt.registered:
                continue  # `git worktree prune` below drops these
            plan.skipped.append(_skip(wt, f"orphaned: {wt.reason}; not a worktree git "
                                          "can remove, so the folder is left for you"))
        elif wt.hold:
            plan.skipped.append(_skip(wt, wt.hold))
        elif wt.state not in allowed:
            why = wt.reason or wt.state
            if wt.state == MERGED_DIRTY:
                why += " (pass --include=merged-dirty to choose)"
            plan.skipped.append(_skip(wt, why))
        elif auto and not settings.auto_prune(wt.repo):
            plan.skipped.append(_skip(wt, "auto-prune is off for this repo "
                                          f"(worktree.repos.{wt.repo.name if wt.repo else '?'}.auto_prune)"))
        else:
            chosen.append(wt)
    repos = {w.repo.common_dir: w.repo for w in worktrees if w.repo}
    stale = [w for w in worktrees if w.state == ORPHANED and w.registered]

    if not apply:
        for wt in chosen:
            plan.acted.append(_row(wt))
            plan.estimated_kb += wt.size_kb or 0
        plan.registrations = [w.path for w in stale]
        if delete_branches:
            plan.branches = _branches(chosen, repos, settings, apply=False, only=branch)
        return plan

    before = sizing.free_by_volume([w.path for w in chosen])
    fresh = PrLookup(cache=False)
    removed: list[Worktree] = []
    for wt in chosen:
        if wt.state == MERGED_DIRTY and not (confirm and confirm(wt)):
            plan.skipped.append(_skip(wt, "merged-dirty and not confirmed"))
            continue
        was = wt.state
        # Read the state again right before acting: a session may have
        # written to it, or a PR may have been reopened, since the scan.
        classify(wt, fresh, settings, home)
        if wt.state != was or wt.state not in allowed or wt.hold:
            plan.skipped.append(_skip(wt, f"state changed to {wt.state} since the scan"
                                          + (f" ({wt.hold})" if wt.hold else "")))
            continue
        rc, _, err = gitio.git(["-C", wt.repo.root, "worktree", "remove",
                                "--force", wt.path], timeout=600)
        if rc != 0:
            plan.skipped.append(_skip(wt, f"git worktree remove failed: {err}"))
            continue
        removed.append(wt)
        plan.acted.append(_row(wt))
        plan.estimated_kb += wt.size_kb or 0
    for repo in repos.values():
        rc, out, _ = gitio.git(["--git-dir", repo.common_dir, "worktree",
                                "prune", "-v"])
        if rc == 0:
            plan.registrations += [l for l in out.splitlines() if l.strip()]
    if delete_branches:
        plan.branches = _branches(removed, repos, settings, apply=True, only=branch)
    after = sizing.free_by_volume([w.path for w in chosen])
    if before:
        plan.freed_bytes = sum(after.get(dev, free) - free for dev, free in before.items())
    plan.log = write_log(plan, home)
    return plan


# ------------------------------------------------------------------ branches

def _checked_out(repo: Repo) -> set[str]:
    from swe.worktrees.discover import parse_porcelain
    listing = gitio.out(["--git-dir", repo.common_dir, "worktree", "list",
                         "--porcelain"]) or ""
    return {r["branch"] for r in parse_porcelain(listing) if r.get("branch")}


def _branch_safe(repo: Repo, name: str, lookup: PrLookup) -> str | None:
    """Why ``name`` may go, or None to keep it."""
    if repo.main_ref and gitio.is_ancestor(repo.common_dir, f"refs/heads/{name}",
                                           repo.main_ref):
        return f"merged into {repo.main_ref}"
    if not repo.slug:
        return None
    prs, _ = lookup.for_branch(repo.slug, name)
    if not prs or any(p.get("state") == "OPEN" for p in prs):
        return None
    tip = gitio.out(["--git-dir", repo.common_dir, "rev-parse", f"refs/heads/{name}"])
    for pr in prs:
        if pr.get("state") == "MERGED" and tip and pr.get("headRefOid") == tip:
            return f"tip is merged PR #{pr.get('number')}'s head"
    return None


def _branches(removed: list[Worktree], repos: dict[str, Repo], settings: Settings,
              *, apply: bool, only: str | None) -> list[dict]:
    """Local branches to drop: those of removed worktrees, plus (reaper's
    second pass) branches whose origin upstream is gone. Never the default
    branch, one still checked out, or anything on a remote."""
    lookup = PrLookup()
    done: list[dict] = []
    candidates: dict[str, tuple[Repo, set[str]]] = {}
    for wt in removed:
        if wt.repo and wt.branch and not wt.repo.bare:
            candidates.setdefault(wt.repo.common_dir, (wt.repo, set()))[1].add(wt.branch)
    for repo in repos.values():
        if repo.bare:
            continue
        refs = gitio.out(["--git-dir", repo.common_dir, "for-each-ref",
                          "--format=%(refname:short)%09%(upstream:short)",
                          "refs/heads"]) or ""
        remote = gitio.out(["--git-dir", repo.common_dir, "ls-remote", "--heads",
                            "origin"])
        if remote is None or not remote:
            continue  # offline: gone-upstream checks off, branches kept
        live = {line.split("refs/heads/", 1)[1] for line in remote.splitlines()
                if "refs/heads/" in line}
        for line in refs.splitlines():
            name, _, upstream = line.partition("\t")
            if upstream.startswith("origin/") and upstream[7:] not in live:
                candidates.setdefault(repo.common_dir, (repo, set()))[1].add(name)
    leaving = {w.branch for w in removed if w.branch}
    for repo, names in candidates.values():
        busy = _checked_out(repo)
        if not apply:
            busy -= leaving  # a dry run's chosen worktrees still hold theirs
        default = (repo.main_ref or "").split("/")[-1]
        for name in sorted(names):
            if only and name != only:
                continue
            if name in (default, "main", "master") or name in busy:
                continue
            why = _branch_safe(repo, name, lookup)
            if why is None:
                continue
            row = {"repo": repo.name, "branch": name, "reason": why}
            if apply:
                rc, _, err = gitio.git(["--git-dir", repo.common_dir, "branch",
                                        "-D", name])
                if rc != 0:
                    row["error"] = err
            done.append(row)
    return done


# ------------------------------------------------------------------ slim

def slim(worktrees: list[Worktree], *, apply: bool = False, days: int = 14,
         home: Path | None = None, now: float | None = None) -> Plan:
    plan = Plan("slim", apply)
    chosen: list[tuple[Worktree, dict]] = []
    for wt in worktrees:
        if wt.state not in SLIM_STATES:
            if wt.rebuildable:
                why = ("merged: prune it instead" if wt.state in PRUNABLE_STATES
                       else wt.reason or wt.state)
                plan.skipped.append(_skip(wt, why))
            continue
        if wt.hold:
            if wt.rebuildable:
                plan.skipped.append(_skip(wt, wt.hold))
            continue
        for entry in wt.rebuildable:
            if sizing.stale(entry["path"], days, now):
                chosen.append((wt, entry))
            else:
                plan.skipped.append({**_skip(wt, f"modified within {days}d"),
                                     "folder": entry["path"]})
    volumes = sizing.free_by_volume([wt.path for wt, _ in chosen]) if apply else {}
    for wt, entry in chosen:
        folder = entry["path"]
        row = {"path": wt.path, "folder": folder,
               "repo": wt.repo.name if wt.repo else None, "state": wt.state,
               "size_kb": entry.get("kb")}
        if apply:
            ok = (os.path.basename(folder) in REBUILDABLE
                  and folder.startswith(wt.path.rstrip("/") + "/")
                  and os.path.isdir(folder) and not os.path.islink(folder)
                  and folder in sizing.rebuildable_dirs(wt.path)
                  and sizing.stale(folder, days, now))
            if not ok:
                plan.skipped.append({**_skip(wt, "changed since the scan"),
                                     "folder": folder})
                continue
            shutil.rmtree(folder, ignore_errors=True)
            if os.path.exists(folder):
                plan.skipped.append({**_skip(wt, "could not delete it all"),
                                     "folder": folder})
                continue
        plan.acted.append(row)
        plan.estimated_kb += entry.get("kb") or 0
    if apply:
        after = sizing.free_by_volume([wt.path for wt, _ in chosen])
        if volumes:
            plan.freed_bytes = sum(after.get(d, f) - f for d, f in volumes.items())
        plan.log = write_log(plan, home)
    return plan


# ------------------------------------------------------------------ log

def write_log(plan: Plan, home: Path | None = None) -> str | None:
    """One JSON file per applied run under ``~/.swe/logs/worktree/``."""
    folder = paths.worktree_logs_dir_for(home)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = folder / f"{plan.action}-{stamp}-{os.getpid()}.json"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        body = {**plan.to_json(), "log": None,
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        target.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return None
    return str(target)
