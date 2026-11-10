"""The `swe wt` Python API: one function per command.

Each returns the ``data`` its ``--json`` prints (the pinned contract in
docs/json-contracts.md, "`swe wt` shapes") and raises ``WorktreeError``
with a stable ``code`` on failure. Nothing here prints or prompts, so the
web app's hub calls these in process; the CLI in ``commands.py`` renders
the same dicts.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Callable

from swe.github import gh
from swe.worktrees import actions, discover, gitio, sizing
from swe.worktrees.classify import PrLookup, classify, classify_all
from swe.worktrees.models import (MAIN, MERGED_CLEAN, MERGED_DIRTY, ORPHANED,
                                  PROTECTED, STATES, Worktree)
from swe.worktrees.settings import Settings

ERROR_CODES = ("bad_args", "bad_config", "not_a_repo", "not_found", "not_safe",
               "exists", "git_failed", "io_error", "gh_unavailable", "busy")


class WorktreeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def to_json(self) -> dict:
        return {"code": self.code, "message": self.message}


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def load_settings(home: Path | None = None) -> Settings:
    """The ``worktree`` config, read strictly: a config.json that exists
    but cannot be read would otherwise drop every protect entry."""
    try:
        return Settings.load(home, strict=True)
    except ValueError as exc:
        raise WorktreeError("bad_config", str(exc)) from None


def _real(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


# ------------------------------------------------------------------ scan

def scan(*, cwd: str | None = None, all: bool = False,
         repos: list[str] | None = None, home: Path | None = None,
         settings: Settings | None = None) -> tuple[Settings, dict, list[Worktree]]:
    """Discover and classify. ``scope`` is ``{"scope", "repos", "roots"}``."""
    settings = settings or load_settings(home)
    discover.clear_cache()
    found = discover.Discovery(home=home, cwd=cwd, settings=settings)
    if repos:
        found.scope_roots(repos)
        if not found.repos and not found.extra_checkouts:
            raise WorktreeError("not_a_repo", f"no git repo at {', '.join(repos)}")
        kind = "repo"
    elif all:
        found.scope_everywhere()
        kind = "all"
    else:
        if not found.scope_current():
            raise WorktreeError("not_a_repo", "Not inside a git repo: run it from "
                                "a repo, or pass --all or --repo=PATH.")
        kind = "repo"
    worktrees = found.collect()
    classify_all(worktrees, PrLookup(), settings, home)
    scope = {"scope": kind, "repos": sorted(r.root for r in found.repos.values()),
             "roots": settings.roots() if kind == "all" else []}
    return settings, scope, worktrees


def totals(worktrees: list[Worktree]) -> dict:
    by_state = {s: 0 for s in STATES}
    for wt in worktrees:
        by_state[wt.state] = by_state.get(wt.state, 0) + 1
    prunable = [w for w in worktrees if w.state == MERGED_CLEAN and not w.hold]
    linked = [w for w in worktrees if w.state != MAIN]
    return {
        "count": len(linked),
        "by_state": by_state,
        "size_bytes": sum((w.size_kb or 0) * 1024 for w in linked),
        "rebuildable_bytes": sum((w.rebuildable_kb or 0) * 1024 for w in linked),
        "prunable": len(prunable),
        "prunable_bytes": sum((w.size_kb or 0) * 1024 for w in prunable),
    }


def list_worktrees(cwd: str | None = None, all: bool = False,
                   home: Path | None = None, repo: str | list[str] | None = None,
                   sizes: bool = True) -> dict:
    repos = [repo] if isinstance(repo, str) else repo
    _, scope, worktrees = scan(cwd=cwd, all=all, repos=repos, home=home)
    if sizes:
        sizing.measure_all(worktrees)
    return {"scope": scope["scope"], "generated_at": _now_iso(),
            "worktrees": [w.to_json() for w in worktrees],
            "repos": scope["repos"], "totals": totals(worktrees)}


# ------------------------------------------------------------------ one worktree

def _one(path: str, home: Path | None = None, sizes: bool = True,
         settings: Settings | None = None) -> Worktree:
    """The classified worktree at ``path`` (any folder inside one works)."""
    target = _real(path)
    if not os.path.exists(target):
        raise WorktreeError("not_found", f"no such folder: {path}")
    repo = discover.find_repo(target)
    if repo is None:
        if os.path.isfile(os.path.join(target, ".git")):
            raise WorktreeError("not_safe", f"{path} is orphaned: its .git points at a "
                                "gitdir that no longer exists, and swe never deletes "
                                "a worktree folder itself")
        raise WorktreeError("not_a_repo", f"not inside a git worktree: {path}")
    top = gitio.out(["-C", target, "rev-parse", "--show-toplevel"])
    top = _real(top) if top else target
    settings = settings or load_settings(home)
    found = discover.Discovery(home=home, cwd=os.getcwd(), settings=settings)
    found.add_repo(repo)
    worktrees = {w.path: w for w in found.collect()}
    wt = worktrees.get(top) or worktrees.get(target)
    if wt is None:
        raise WorktreeError("not_found", f"{path} is not a worktree git lists")
    classify(wt, PrLookup(), settings, home)
    if sizes:
        sizing.measure(wt)
    return wt


def show(path: str, home: Path | None = None) -> dict:
    return {"worktree": _one(path, home).to_json()}


def _default_path(root: str, leaf: str) -> str:
    name = os.path.basename(root.rstrip("/"))
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", leaf).strip("-") or "worktree"
    return os.path.join(os.path.dirname(root.rstrip("/")), f"{name}-worktrees", safe)


def _git_or_raise(args: list[str], what: str) -> str:
    rc, out, err = gitio.git(args, timeout=600)
    if rc != 0:
        raise WorktreeError("git_failed", f"{what}: {err or out or 'git failed'}")
    return out


def add(repo_path: str, branch: str | None = None, new: bool = False,
        base: str | None = None, pr: int | str | None = None,
        path: str | None = None, home: Path | None = None) -> dict:
    """``git worktree add`` for an existing branch, a new one (``new``,
    from ``base`` or the default branch) or a PR's head (``pr``)."""
    if bool(branch) == bool(pr):
        raise WorktreeError("bad_args", "pass exactly one of --branch=B or --pr=N")
    if pr is not None and not gh.digits(str(pr)):
        raise WorktreeError("bad_args", f"--pr takes a PR number, not {pr}")
    if (new or base) and not branch:
        raise WorktreeError("bad_args", "--new and --base go with --branch")
    # Read settings before touching git: a broken config must fail the
    # call before it leaves a checkout behind.
    settings = load_settings(home)
    root = _real(repo_path)
    repo = discover.find_repo(root) if os.path.isdir(root) else None
    if repo is None:
        raise WorktreeError("not_a_repo", f"no git repo at {repo_path}")
    if repo.bare:
        raise WorktreeError("bad_args", "add worktrees to a bare mirror with swe gh pr worktree")
    git_dir = ["--git-dir", repo.common_dir]
    if pr is not None:
        number = int(pr)
        head_name = None
        if repo.slug:
            got = gh.run_json(["pr", "view", str(number), "--repo", repo.slug,
                               "--json", "headRefName"])
            if got.status == "ok" and isinstance(got.data, dict):
                head_name = got.data.get("headRefName")
            elif got.status == "unavailable":
                raise WorktreeError("gh_unavailable", got.error or "gh is not installed")
            elif got.status == "not-found":
                raise WorktreeError("not_found", f"no PR #{number} in {repo.slug}")
        target = _real(path) if path else _default_path(repo.root, f"pr-{number}")
        if os.path.exists(target):
            raise WorktreeError("exists", f"{target} already exists")
        # Always fetch the PR's own head: a local branch with the same name
        # may be stale or unrelated (a fork PR from its main, say).
        _git_or_raise(git_dir + ["fetch", "--no-write-fetch-head", "origin",
                                 f"+refs/pull/{number}/head:refs/swe/pr/{number}"],
                      f"fetching PR #{number}")
        sha = gitio.out(git_dir + ["rev-parse", f"refs/swe/pr/{number}"])
        default = (repo.main_ref or "").split("/")[-1]
        names = [n for n in (head_name, f"pr-{number}")
                 if n and n not in (default, "main", "master")]
        branch = None
        for name in names:
            tip = gitio.out(git_dir + ["rev-parse", "--verify", "--quiet",
                                       f"refs/heads/{name}"])
            if not tip or tip == sha:
                branch, create = name, not tip
                break
        if branch is None:
            raise WorktreeError("exists", f"local branches {', '.join(names)} already "
                                f"exist at other commits than PR #{number}'s head")
        # Remember which PR this is: a pr-N fallback name matches no PR head,
        # and classify looks the PR up by this number. A branch made here has
        # no link to lose, so it is linked first; an existing branch's link
        # changes only once its checkout exists.
        link = git_dir + ["config", f"branch.{branch}.swe-pr", str(number)]
        if create:
            _git_or_raise(git_dir + ["branch", branch, sha], f"creating {branch}")
            _git_or_raise(link, f"linking {branch} to PR #{number}")
        _git_or_raise(["-C", repo.root, "worktree", "add", target, branch],
                      "git worktree add")
        if not create:
            _git_or_raise(link, f"linking {branch} to PR #{number}")
        gitio.git(git_dir + ["update-ref", "-d", f"refs/swe/pr/{number}"])
    else:
        target = _real(path) if path else _default_path(repo.root, branch)
        if os.path.exists(target):
            raise WorktreeError("exists", f"{target} already exists")
        has = gitio.out(git_dir + ["rev-parse", "--verify", "--quiet",
                                   f"refs/heads/{branch}"])
        if new:
            if has:
                raise WorktreeError("exists", f"branch {branch} already exists")
            start = base or repo.main_ref or "HEAD"
            _git_or_raise(["-C", repo.root, "worktree", "add", "--no-track",
                           "-b", branch, target, start], "git worktree add")
        else:
            if not has:
                raise WorktreeError("not_found",
                                    f"no local branch {branch} (pass --new to create it)")
            _git_or_raise(["-C", repo.root, "worktree", "add", target, branch],
                          "git worktree add")
    return {"worktree": _one(target, home, settings=settings).to_json()}


def _refuse_special(wt: Worktree, verb: str) -> None:
    if wt.state == MAIN:
        raise WorktreeError("not_safe", f"refusing to {verb} the main checkout")
    if wt.state == PROTECTED:
        raise WorktreeError("not_safe", f"refusing to {verb} a protected worktree ({wt.path})")


def remove(path: str, force: bool = False, delete_branch: bool = False,
           home: Path | None = None) -> dict:
    """Remove one worktree with ``git worktree remove``. Anything but
    ``merged-clean`` needs ``force``; main and protected never go."""
    settings = load_settings(home)
    target = _real(path)
    if not os.path.exists(target):
        # A registered worktree whose folder is gone: drop the entry.
        for wt in scan_registered_missing(target, home, settings):
            _git_or_raise(["--git-dir", wt.repo.common_dir, "worktree", "prune"],
                          "git worktree prune")
            return {"removed": target, "freed_bytes": 0, "branch_deleted": None}
        raise WorktreeError("not_found", f"no such folder: {path}")
    wt = _one(target, home, sizes=False, settings=settings)
    _refuse_special(wt, "remove")
    if wt.state == ORPHANED and not wt.registered:
        raise WorktreeError("not_safe", f"{wt.path} is not a worktree git lists; "
                                        "swe never deletes a worktree folder itself")
    if not force:
        if wt.state != MERGED_CLEAN:
            raise WorktreeError("not_safe", f"{wt.path} is {wt.state} ({wt.reason}); "
                                            "pass --force to remove it anyway")
        if wt.locked:
            raise WorktreeError("not_safe", f"{wt.path} is locked; pass --force")
        if wt.hold:
            raise WorktreeError("not_safe", f"{wt.path}: {wt.hold}; pass --force")
    if _inside(os.getcwd(), wt.path):
        raise WorktreeError("not_safe", "refusing to remove the worktree you are in")
    before = sizing.free_bytes(wt.path)
    args = ["-C", wt.repo.root, "worktree", "remove", "--force"]
    if wt.locked:
        args.append("--force")  # git needs it twice for a locked worktree
    _git_or_raise(args + [wt.path], "git worktree remove")
    after = sizing.free_bytes(wt.repo.root)
    freed = (after[1] - before[1]) if before and after and before[0] == after[0] else None
    deleted = None
    if delete_branch:
        deleted = _delete_branch(wt)
    return {"removed": wt.path, "freed_bytes": freed, "branch_deleted": deleted}


def _inside(path: str, root: str) -> bool:
    path = os.path.realpath(path)
    return path == root or path.startswith(root.rstrip("/") + "/")


def scan_registered_missing(target: str, home, settings) -> list[Worktree]:
    parent = os.path.dirname(target)
    while parent and not os.path.isdir(parent):
        parent = os.path.dirname(parent)
    repo = discover.find_repo(parent) if parent else None
    if repo is None:
        return []
    found = discover.Discovery(home=home, cwd=os.getcwd(), settings=settings)
    found.add_repo(repo)
    return [w for w in found.collect()
            if w.path == target and w.state == ORPHANED and w.registered]


def _delete_branch(wt: Worktree) -> bool:
    """Local only. A squash-merged branch needs ``-D``; anything else
    goes through ``-d``, which refuses unmerged work."""
    if not wt.branch or wt.repo is None:
        return False
    default = (wt.repo.main_ref or "").split("/")[-1]
    if wt.branch in (default, "main", "master"):
        return False
    # -D only when nothing on the branch is beyond what merged: its tip is
    # the merged PR's head (the squash case) or already on main.
    tip = gitio.out(["--git-dir", wt.repo.common_dir, "rev-parse", f"refs/heads/{wt.branch}"])
    merged = bool(wt.pr and wt.pr.get("state") == "MERGED" and tip
                  and wt.pr.get("head") == tip)
    flag = "-D" if merged else "-d"
    rc, _, _ = gitio.git(["--git-dir", wt.repo.common_dir, "branch", flag, wt.branch])
    return rc == 0


def lock(path: str, reason: str | None = None, home: Path | None = None) -> dict:
    wt = _one(path, home, sizes=False)
    _refuse_special(wt, "lock")
    if not wt.locked:
        args = ["-C", wt.repo.root, "worktree", "lock"]
        if reason:
            args += ["--reason", reason]
        _git_or_raise(args + [wt.path], "git worktree lock")
    return show(wt.path, home)


def unlock(path: str, home: Path | None = None) -> dict:
    wt = _one(path, home, sizes=False)
    if wt.locked:
        _git_or_raise(["-C", wt.repo.root, "worktree", "unlock", wt.path],
                      "git worktree unlock")
    return show(wt.path, home)


def move(path: str, new_path: str, home: Path | None = None) -> dict:
    wt = _one(path, home, sizes=False)
    _refuse_special(wt, "move")
    target = _real(new_path)
    if os.path.exists(target):
        raise WorktreeError("exists", f"{new_path} already exists")
    if wt.locked:
        raise WorktreeError("not_safe", f"{wt.path} is locked; unlock it first")
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
    except OSError as exc:
        raise WorktreeError("io_error", f"cannot create {os.path.dirname(target)}: "
                            f"{exc.strerror or exc}") from None
    _git_or_raise(["-C", wt.repo.root, "worktree", "move", wt.path, target],
                  "git worktree move")
    return show(target, home)


# ------------------------------------------------------------------ bulk

def prune(cwd: str | None = None, all: bool = False, yes: bool = False,
          include: str | list[str] | None = None, branch: str | None = None,
          home: Path | None = None, repo: str | list[str] | None = None,
          delete_branches: bool = False, auto: bool = False,
          confirm: Callable[[Worktree], bool] | None = None) -> dict:
    includes = [include] if isinstance(include, str) else list(include or [])
    bad = [i for i in includes if i != MERGED_DIRTY]
    if bad:
        raise WorktreeError("bad_args", f"--include takes merged-dirty, not {', '.join(bad)}")
    repos = [repo] if isinstance(repo, str) else repo
    settings, scope, worktrees = scan(cwd=cwd, all=all, repos=repos, home=home)
    targets = [w for w in worktrees if w.state in (MERGED_CLEAN, MERGED_DIRTY)]
    if branch:
        targets = [w for w in targets if w.branch == branch]
    sizing.measure_all(targets)
    apply = yes or auto
    kwargs = dict(settings=settings, apply=apply, include_dirty=bool(includes),
                  branch=branch, delete_branches=delete_branches, auto=auto,
                  confirm=confirm, home=home)
    try:
        if apply:
            with actions.RunLock(home):
                plan = actions.prune(worktrees, **kwargs)
        else:
            plan = actions.prune(worktrees, **kwargs)
    except actions.Busy as exc:
        raise WorktreeError("busy", str(exc)) from None
    by_path = {w.path: w for w in worktrees}
    candidates = [by_path[r["path"]].to_json() for r in plan.acted if r["path"] in by_path]
    estimated = plan.estimated_kb * 1024
    return {
        "dry_run": not apply,
        "candidates": candidates,
        "removed": [r["path"] for r in plan.acted] if apply else [],
        "skipped": [{"path": r["path"], "reason": r["reason"], "state": r["state"]}
                    for r in plan.skipped],
        "freed_bytes": plan.freed_bytes if apply else estimated,
        "estimated_bytes": estimated,
        "branches": plan.branches,
        "registrations": plan.registrations,
        "log": plan.log,
        "scope": scope["scope"],
        "repos": scope["repos"],
    }


def slim(path: str | None = None, all: bool = False, yes: bool = False,
         cwd: str | None = None, home: Path | None = None,
         repo: str | list[str] | None = None, days: int | None = None) -> dict:
    if path:
        wt = _one(path, home)
        _refuse_special(wt, "slim")
        if wt.state == ORPHANED:
            raise WorktreeError("not_safe", f"{wt.path} is orphaned")
        worktrees = [wt]
        days = 0 if days is None else days
        explicit = True
    else:
        repos = [repo] if isinstance(repo, str) else repo
        _, _, worktrees = scan(cwd=cwd, all=all, repos=repos, home=home)
        sizing.measure_all([w for w in worktrees if w.state != ORPHANED])
        days = 14 if days is None else days
        explicit = False
    try:
        if yes:
            with actions.RunLock(home):
                plan = actions.slim(worktrees, apply=True, days=days, home=home,
                                    any_state=explicit)
        else:
            plan = actions.slim(worktrees, apply=False, days=days, any_state=explicit)
    except actions.Busy as exc:
        raise WorktreeError("busy", str(exc)) from None
    estimated = plan.estimated_kb * 1024
    return {
        "dry_run": not yes,
        "dirs": [{"path": r["folder"], "bytes": (r.get("size_kb") or 0) * 1024,
                  "worktree": r["path"]} for r in plan.acted],
        "freed_bytes": plan.freed_bytes if yes else estimated,
        "estimated_bytes": estimated,
        "days": days,
        "skipped": [{"path": r.get("folder") or r["path"], "reason": r["reason"],
                     "state": r["state"]} for r in plan.skipped],
        "log": plan.log,
    }
