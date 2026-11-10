"""`swe worktree` / `swe wt`: list, prune and slim agent worktrees."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from swe.console import c, tilde
from swe.flags import expand_value_flags
from swe.json_output import emit, error_stream, take_json_flag
from swe.table import Table
from swe.worktrees import actions, discover, sizing
from swe.worktrees.classify import PrLookup, classify_all
from swe.worktrees.models import (MERGED_CLEAN, MERGED_DIRTY, NO_PR, OPEN_PR,
                                  ORPHANED, STATES, UNKNOWN, Worktree)
from swe.worktrees.settings import Settings

VALUE_FLAGS = {"--root", "--include", "--branch", "--days"}
BOOL_FLAGS = {
    "list": {"--all", "--no-size"},
    "prune": {"--all", "--yes", "--delete-branches", "--auto"},
    "slim": {"--all", "--yes"},
}
VALUE_FLAGS_BY_SUB = {
    "list": {"--root"},
    "prune": {"--root", "--include", "--branch"},
    "slim": {"--root", "--days"},
}
SUBCOMMANDS = {"list": "list", "ls": "list", "prune": "prune", "slim": "slim"}

STATE_COLOR = {MERGED_CLEAN: "green", MERGED_DIRTY: "yellow", OPEN_PR: "cyan",
               NO_PR: "dim", ORPHANED: "red", UNKNOWN: "dim"}


class UsageError(ValueError):
    pass


def _parse(sub: str, args: list[str]) -> dict:
    flags: set[str] = set()
    for arg in args:
        if arg in BOOL_FLAGS[sub]:
            flags.add(arg)
    args = expand_value_flags([a for a in args if a not in BOOL_FLAGS[sub]],
                              VALUE_FLAGS)
    opts: dict = {"flags": flags, "roots": []}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in VALUE_FLAGS:
            if arg not in VALUE_FLAGS_BY_SUB[sub]:
                raise UsageError(f"swe wt {sub} does not take {arg}")
            value = args[i + 1]
            if arg == "--root":
                opts["roots"] += [v for v in value.split(",") if v]
            elif arg == "--include":
                bad = [v for v in value.split(",") if v and v != MERGED_DIRTY]
                if bad:
                    raise UsageError(f"--include takes merged-dirty, not {', '.join(bad)}")
                opts["include_dirty"] = True
            elif arg == "--branch":
                if not value:
                    raise UsageError("--branch needs a branch name")
                opts["branch"] = value
            elif arg == "--days":
                if not value.isdigit():
                    raise UsageError(f"--days must be a whole number of days, got {value}")
                opts["days"] = int(value)
            i += 2
            continue
        raise UsageError(f"unknown argument for swe wt {sub}: {arg}")
    return opts


def cmd_worktree(args: list[str]) -> int:
    args, json_view = take_json_flag(args)
    if args and args[0] in ("help", "--help", "-h"):
        from swe.help_text import cmd_help
        cmd_help(["worktree"])
        return 0
    sub = "list"
    if args and not args[0].startswith("-"):
        if args[0] not in SUBCOMMANDS:
            print(c("red", f"Unknown swe wt command: {args[0]} (list | prune | slim)"),
                  file=error_stream(json_view))
            return 2
        sub, args = SUBCOMMANDS[args[0]], args[1:]
    try:
        opts = _parse(sub, args)
    except (UsageError, ValueError) as exc:
        print(c("red", str(exc)), file=error_stream(json_view))
        return 2
    return {"list": _list, "prune": _prune, "slim": _slim}[sub](opts, json_view)


# ------------------------------------------------------------------ scope

def _scan(opts: dict, json_view: bool, home: Path | None = None):
    settings = Settings.load(home)
    found = discover.Discovery(home=home, settings=settings)
    if opts["roots"]:
        found.scope_roots(opts["roots"])
        scope = {"all": False, "roots": [os.path.realpath(os.path.expanduser(r))
                                         for r in opts["roots"]]}
    elif "--all" in opts["flags"]:
        found.scope_everywhere()
        scope = {"all": True, "roots": settings.roots()}
    else:
        if not found.scope_current():
            print(c("red", "Not inside a git repo: run it from a repo, or pass "
                           "--all or --root=PATH."), file=error_stream(json_view))
            return None
        scope = {"all": False, "roots": [r.root for r in found.repos.values()]}
    worktrees = found.collect()
    classify_all(worktrees, PrLookup(), settings, home)
    scope["repos"] = sorted(r.root for r in found.repos.values())
    return settings, scope, worktrees


def _status(msg: str, json_view: bool) -> None:
    if not json_view and sys.stderr.isatty():
        print(c("dim", msg), file=sys.stderr)


# ------------------------------------------------------------------ list

def _age(ts: int | None) -> str:
    if not ts:
        return "-"
    secs = max(0, time.time() - ts)
    for size, unit in ((86400 * 365, "y"), (86400 * 30, "mo"), (86400, "d"),
                       (3600, "h"), (60, "m")):
        if secs >= size:
            return f"{int(secs // size)}{unit} ago"
    return "now"


def _where(wt: Worktree) -> str:
    if wt.repo and wt.path.startswith(wt.repo.root.rstrip("/") + "/"):
        return wt.path[len(wt.repo.root.rstrip("/")) + 1:]
    return tilde(wt.path)


def totals(worktrees: list[Worktree]) -> dict:
    by_state = {s: 0 for s in STATES}
    for wt in worktrees:
        by_state[wt.state] = by_state.get(wt.state, 0) + 1
    prunable = [w for w in worktrees if w.state == MERGED_CLEAN and not w.hold]
    return {
        "count": len(worktrees),
        "by_state": by_state,
        "size_kb": sum(w.size_kb or 0 for w in worktrees),
        "rebuildable_kb": sum(w.rebuildable_kb or 0 for w in worktrees),
        "prunable": len(prunable),
        "prunable_kb": sum(w.size_kb or 0 for w in prunable),
    }


def _list(opts: dict, json_view: bool) -> int:
    _status("Scanning worktrees and asking GitHub for PR states...", json_view)
    scanned = _scan(opts, json_view)
    if scanned is None:
        return 1
    _, scope, worktrees = scanned
    if "--no-size" not in opts["flags"]:
        _status("Measuring sizes (du)...", json_view)
        sizing.measure_all(worktrees)
    sums = totals(worktrees)
    if json_view:
        return emit({"scope": scope, "worktrees": [w.to_json() for w in worktrees],
                     "totals": sums})
    _print_table(worktrees)
    _print_totals(sums, "--no-size" not in opts["flags"])
    return 0


def _print_table(worktrees: list[Worktree]) -> None:
    if not worktrees:
        print(c("dim", "\n  No linked worktrees.\n"))
        return
    t = Table()
    t.add_column("repo", "REPO", width=16, kind="text", fit="shrink")
    t.add_column("wt", "WORKTREE", width=40, kind="path", fit="shrink")
    t.add_column("branch", "BRANCH", width=28, kind="text", fit="shrink")
    t.add_column("state", "STATE", width=12, kind="preformatted", fit="fixed")
    t.add_column("pr", "PR", width=7, kind="text", fit="fixed")
    t.add_column("last", "LAST COMMIT", width=11, kind="text", fit="fixed")
    t.add_column("size", "SIZE~", width=8, kind="text", fit="fixed")
    t.add_column("rebuild", "REBUILD~", width=8, kind="text", fit="fixed")
    t.add_column("note", "NOTE", width=30, kind="text", fit="shrink")
    for wt in worktrees:
        t.add_row({
            "repo": wt.repo.name if wt.repo else "?",
            "wt": _where(wt),
            "branch": wt.branch or ("(detached)" if wt.head else "-"),
            "state": c(STATE_COLOR.get(wt.state, "dim"), wt.state),
            "pr": f"#{wt.pr['number']}" if wt.pr and wt.pr.get("number") else "-",
            "last": _age(wt.last_commit),
            "size": sizing.human(wt.size_kb),
            "rebuild": sizing.human(wt.rebuildable_kb) if wt.rebuildable else "-",
            "note": wt.hold or (wt.reason if wt.state in (UNKNOWN, ORPHANED, MERGED_DIRTY) else ""),
        })
    print()
    for line in t.render():
        print(line)


def _print_totals(sums: dict, measured: bool) -> None:
    states = ", ".join(f"{n} {s}" for s, n in sums["by_state"].items() if n)
    print(f"\n  {sums['count']} worktree(s): {states or 'none'}")
    if measured:
        print(f"  ~{sizing.human(sums['size_kb'])} on disk, "
              f"~{sizing.human(sums['rebuildable_kb'])} of it in rebuildable folders "
              + c("dim", "(du estimates: shared files count more than once)"))
    if sums["prunable"]:
        size = f", ~{sizing.human(sums['prunable_kb'])}" if measured else ""
        print(f"  {sums['prunable']} merged-clean worktree(s) can go{size}: "
              + c("cyan", "swe wt prune --yes"))
    print()


# ------------------------------------------------------------------ prune

def _confirm(wt: Worktree) -> bool:
    if not sys.stdin.isatty():
        return False
    from swe.prompt import read_line
    try:
        answer = read_line(f"  Remove {tilde(wt.path)} ({wt.reason})? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def _prune(opts: dict, json_view: bool) -> int:
    flags = opts["flags"]
    auto = "--auto" in flags
    apply = "--yes" in flags or auto
    _status("Scanning worktrees and asking GitHub for PR states...", json_view)
    scanned = _scan(opts, json_view)
    if scanned is None:
        return 1
    settings, scope, worktrees = scanned
    targets = [w for w in worktrees if w.state in (MERGED_CLEAN, MERGED_DIRTY)]
    if opts.get("branch"):
        targets = [w for w in targets if w.branch == opts["branch"]]
    _status("Measuring sizes (du)...", json_view)
    sizing.measure_all(targets)
    try:
        if apply:
            with actions.RunLock():
                plan = actions.prune(
                    worktrees, settings=settings, apply=True,
                    include_dirty=bool(opts.get("include_dirty")),
                    branch=opts.get("branch"),
                    delete_branches="--delete-branches" in flags, auto=auto,
                    confirm=None if json_view else _confirm)
        else:
            plan = actions.prune(
                worktrees, settings=settings, apply=False,
                include_dirty=bool(opts.get("include_dirty")),
                branch=opts.get("branch"),
                delete_branches="--delete-branches" in flags, auto=auto)
    except actions.Busy as exc:
        print(c("red", str(exc)), file=error_stream(json_view))
        return 1
    if json_view:
        return emit({"scope": scope, **plan.to_json()})
    _print_plan(plan, "worktree", _shortener(worktrees))
    return 0


def _shortener(worktrees: list[Worktree]):
    roots = sorted({w.repo.root.rstrip("/") for w in worktrees if w.repo and not w.repo.bare},
                   key=len, reverse=True)

    def short(path: str) -> str:
        for root in roots:
            if path.startswith(root + "/"):
                return path[len(root) + 1:]
        return tilde(path)
    return short


def _print_plan(plan: actions.Plan, noun: str, short=tilde) -> None:
    verb = "removed" if plan.action == "prune" else "deleted"
    print()
    for row in plan.acted:
        target = row.get("folder") or row["path"]
        size = sizing.human(row.get("size_kb"))
        if plan.action == "prune":
            head = c("green", "removed") if plan.apply else c("cyan", "would remove")
        else:
            head = c("green", "deleted") if plan.apply else c("cyan", "would delete")
        detail = f": {row['reason']}" if row.get("reason") else ""
        print(f"  {head} {short(target)} (~{size}){detail}")
    for row in plan.branches:
        head = "deleted branch" if plan.apply else "would delete branch"
        err = c("red", f" (failed: {row['error']})") if row.get("error") else ""
        print(f"  {c('green' if plan.apply else 'cyan', head)} {row['repo']} {row['branch']}: {row['reason']}{err}")
    for line in plan.registrations:
        head = "pruned" if plan.apply else "would drop stale entry"
        print(f"  {c('dim', head)} {short(line)}")
    if plan.skipped:
        print(c("dim", f"\n  Kept {len(plan.skipped)}:"))
        for row in plan.skipped:
            target = row.get("folder") or row["path"]
            print(c("dim", f"    {short(target)} [{row['state']}]: {row['reason']}"))
    est = sizing.human(plan.estimated_kb)
    count = len(plan.acted)
    print()
    if plan.apply:
        freed = (sizing.human(plan.freed_bytes // 1024) if plan.freed_bytes is not None
                 and plan.freed_bytes > 0 else "nothing measurable")
        print(f"  {verb.capitalize()} {count} {noun}(s), kept {len(plan.skipped)}. "
              f"Freed {freed} on disk (free space before and after); "
              f"du estimated ~{est}.")
        if plan.log:
            print(c("dim", f"  Log: {tilde(plan.log)}"))
    else:
        print(f"  Dry run: would {'remove' if plan.action == 'prune' else 'delete'} "
              f"{count} {noun}(s), ~{est} by du (an estimate), kept {len(plan.skipped)}.")
        if count:
            print(c("dim", "  Nothing changed. Re-run with --yes to do it."))
    print()


# ------------------------------------------------------------------ slim

def _slim(opts: dict, json_view: bool) -> int:
    apply = "--yes" in opts["flags"]
    _status("Scanning worktrees and asking GitHub for PR states...", json_view)
    scanned = _scan(opts, json_view)
    if scanned is None:
        return 1
    _, scope, worktrees = scanned
    _status("Finding rebuildable folders...", json_view)
    sizing.measure_all([w for w in worktrees if w.state != ORPHANED])
    days = opts.get("days", 14)
    try:
        if apply:
            with actions.RunLock():
                plan = actions.slim(worktrees, apply=True, days=days)
        else:
            plan = actions.slim(worktrees, apply=False, days=days)
    except actions.Busy as exc:
        print(c("red", str(exc)), file=error_stream(json_view))
        return 1
    if json_view:
        return emit({"scope": scope, "days": days, **plan.to_json()})
    _print_plan(plan, "folder", _shortener(worktrees))
    return 0
