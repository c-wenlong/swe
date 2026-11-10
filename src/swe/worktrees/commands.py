"""`swe worktree` / `swe wt`: the controller over ``swe.worktrees.api``.

Every subcommand renders the dict its API function returns; ``--json``
prints that dict in the usual envelope, and a ``WorktreeError`` prints
``{"schema": 1, "error": {"code", "message"}}`` on stdout with exit 1
(2 for bad arguments), the shape the pinned contract gives the clients.
"""

from __future__ import annotations

import json
import os
import sys
import time

from swe.console import c, tilde
from swe.json_output import SCHEMA, emit
from swe.table import Table
from swe.worktrees import api, sizing
from swe.worktrees.api import WorktreeError
from swe.worktrees.models import (MAIN, MERGED_CLEAN, MERGED_DIRTY, NO_PR,
                                  OPEN_PR, ORPHANED, PROTECTED, UNKNOWN)

# Value flags per subcommand; both --name=value and --name value parse.
VALUE_FLAGS = {
    "list": {"--repo", "--root"},
    "show": set(),
    "add": {"--branch", "--base", "--pr", "--path"},
    "remove": set(),
    "lock": {"--reason"},
    "unlock": set(),
    "move": set(),
    "prune": {"--repo", "--root", "--include", "--branch"},
    "slim": {"--repo", "--root", "--days"},
}
BOOL_FLAGS = {
    "list": {"--all", "--no-size"},
    "show": set(),
    "add": {"--new"},
    "remove": {"--force", "--delete-branch"},
    "lock": set(),
    "unlock": set(),
    "move": set(),
    "prune": {"--all", "--yes", "--delete-branches", "--auto"},
    "slim": {"--all", "--yes"},
}
POSITIONALS = {"list": (0, 0), "show": (1, 1), "add": (1, 1), "remove": (1, 1),
               "lock": (1, 1), "unlock": (1, 1), "move": (2, 2),
               "prune": (0, 0), "slim": (0, 1)}
SUBCOMMANDS = {"list": "list", "ls": "list", "show": "show", "add": "add",
               "remove": "remove", "rm": "remove", "lock": "lock",
               "unlock": "unlock", "move": "move", "mv": "move",
               "prune": "prune", "slim": "slim"}

STATE_COLOR = {MERGED_CLEAN: "green", MERGED_DIRTY: "yellow", OPEN_PR: "cyan",
               NO_PR: "dim", ORPHANED: "red", UNKNOWN: "dim",
               PROTECTED: "dim", MAIN: "bold"}


def parse(sub: str, args: list[str]) -> tuple[dict, list[str], set[str]]:
    """``(values, positionals, booleans)``; raises ``WorktreeError("bad_args")``."""
    values: dict[str, list[str]] = {}
    positionals: list[str] = []
    flags: set[str] = set()
    i = 0
    while i < len(args):
        arg = args[i]
        name, sep, value = arg.partition("=")
        if arg in BOOL_FLAGS[sub]:
            flags.add(arg)
        elif name in VALUE_FLAGS[sub]:
            if not sep:
                if i + 1 >= len(args) or args[i + 1].startswith("--"):
                    raise WorktreeError("bad_args", f"{name} takes a value: {name}=<value>")
                i += 1
                value = args[i]
            if not value:
                raise WorktreeError("bad_args", f"{name} takes a value: {name}=<value>")
            values.setdefault(name, []).append(value)
        elif arg.startswith("-"):
            raise WorktreeError("bad_args", f"swe wt {sub} does not take {arg}")
        else:
            positionals.append(arg)
        i += 1
    lo, hi = POSITIONALS[sub]
    if not lo <= len(positionals) <= hi:
        want = str(lo) if lo == hi else f"{lo} to {hi}"
        raise WorktreeError("bad_args", f"swe wt {sub} takes {want} path argument(s), "
                                        f"got {len(positionals)}")
    return values, positionals, flags


def _one_value(values: dict, *names: str) -> str | None:
    found = [v for n in names for v in values.get(n, [])]
    return found[-1] if found else None


def _repos(values: dict) -> list[str] | None:
    roots = [r for n in ("--repo", "--root") for v in values.get(n, [])
             for r in v.split(",") if r]
    return roots or None


def _fail(exc: WorktreeError, json_view: bool) -> int:
    code = 2 if exc.code == "bad_args" else 1
    if json_view:
        sys.stdout.write(json.dumps({"schema": SCHEMA, "error": exc.to_json()},
                                    indent=2) + "\n")
    else:
        print(c("red", exc.message))
    return code


def cmd_worktree(args: list[str]) -> int:
    json_view = "--json" in args
    args = [a for a in args if a != "--json"]
    if args and args[0] in ("help", "--help", "-h"):
        from swe.help_text import cmd_help
        cmd_help(["worktree"])
        return 0
    sub = "list"
    if args and not args[0].startswith("-"):
        if args[0] not in SUBCOMMANDS:
            return _fail(WorktreeError("bad_args", f"Unknown swe wt command: {args[0]} "
                                       "(list | show | add | remove | lock | unlock | "
                                       "move | prune | slim)"), json_view)
        sub, args = SUBCOMMANDS[args[0]], args[1:]
    try:
        values, pos, flags = parse(sub, args)
        return HANDLERS[sub](values, pos, flags, json_view)
    except WorktreeError as exc:
        return _fail(exc, json_view)


def _status(msg: str, json_view: bool) -> None:
    if not json_view and sys.stderr.isatty():
        print(c("dim", msg), file=sys.stderr)


# ------------------------------------------------------------------ list / show

def _age(iso: str | None) -> str:
    if not iso:
        return "-"
    from datetime import datetime
    try:
        secs = max(0.0, time.time() - datetime.fromisoformat(iso).timestamp())
    except ValueError:
        return "-"
    for size, unit in ((86400 * 365, "y"), (86400 * 30, "mo"), (86400, "d"),
                       (3600, "h"), (60, "m")):
        if secs >= size:
            return f"{int(secs // size)}{unit} ago"
    return "now"


def _human_bytes(n: int | None) -> str:
    return "-" if n is None else sizing.human(n // 1024)


def _where(w: dict) -> str:
    root = (w.get("main_checkout") or "").rstrip("/")
    if root and w["path"].startswith(root + "/"):
        return w["path"][len(root) + 1:]
    return tilde(w["path"])


def _repo_label(w: dict) -> str:
    if w.get("repo"):
        return w["repo"]
    root = w.get("main_checkout")
    return os.path.basename(root.rstrip("/")) if root else "?"


def _cmd_list(values, pos, flags, json_view) -> int:
    _status("Scanning worktrees and asking GitHub for PR states...", json_view)
    doc = api.list_worktrees(all="--all" in flags, repo=_repos(values),
                             sizes="--no-size" not in flags)
    if json_view:
        return emit(doc)
    _print_table(doc["worktrees"])
    _print_totals(doc["totals"], "--no-size" not in flags)
    return 0


def _note(w: dict) -> str:
    if w.get("locked"):
        return "locked" + (f": {w['lock_reason']}" if w.get("lock_reason") else "")
    if w.get("hold"):
        return w["hold"]
    if w["state"] in (UNKNOWN, ORPHANED, MERGED_DIRTY):
        return w.get("reason") or ""
    return ""


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print(c("dim", "\n  No worktrees.\n"))
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
    for w in rows:
        state = w["state"]
        t.add_row({
            "repo": _repo_label(w),
            "wt": "(main checkout)" if state == MAIN else _where(w),
            "branch": w["branch"] or ("(detached)" if w.get("head") else "-"),
            "state": c(STATE_COLOR.get(state, "dim"), state),
            "pr": f"#{w['pr']['number']}" if w.get("pr") and w["pr"].get("number") else "-",
            "last": _age(w.get("last_commit_at")),
            "size": _human_bytes(w.get("size_bytes")),
            "rebuild": _human_bytes(w.get("rebuildable_bytes")) if w.get("rebuildable") else "-",
            "note": _note(w),
        })
    print()
    for line in t.render():
        print(line)


def _print_totals(sums: dict, measured: bool) -> None:
    states = ", ".join(f"{n} {s}" for s, n in sums["by_state"].items()
                       if n and s != MAIN)
    print(f"\n  {sums['count']} linked worktree(s): {states or 'none'}")
    if measured:
        print(f"  ~{_human_bytes(sums['size_bytes'])} on disk, "
              f"~{_human_bytes(sums['rebuildable_bytes'])} of it in rebuildable folders "
              + c("dim", "(du estimates: shared files count more than once)"))
    if sums["prunable"]:
        size = f", ~{_human_bytes(sums['prunable_bytes'])}" if measured else ""
        print(f"  {sums['prunable']} merged-clean worktree(s) can go{size}: "
              + c("cyan", "swe wt prune --yes"))
    print()


def _print_one(w: dict) -> None:
    pr = w.get("pr")
    rows = [
        ("repo", f"{_repo_label(w)}  {c('dim', tilde(w.get('main_checkout') or '-'))}"),
        ("branch", w["branch"] or f"(detached at {(w.get('head') or '')[:12]})"),
        ("state", c(STATE_COLOR.get(w["state"], "dim"), w["state"])
         + (f"  {c('dim', w['reason'])}" if w.get("reason") else "")),
        ("pr", f"#{pr['number']} {pr['state']}  {pr['url']}" if pr else "-"),
        ("last commit", _age(w.get("last_commit_at"))),
        ("changes", f"{w.get('changes', 0)} tracked, {w.get('untracked', 0)} untracked"),
        ("size", f"~{_human_bytes(w.get('size_bytes'))}, "
                 f"~{_human_bytes(w.get('rebuildable_bytes'))} rebuildable (du)"),
        ("harness", w.get("harness")),
    ]
    if w.get("locked"):
        rows.append(("locked", w.get("lock_reason") or "yes"))
    if w.get("hold"):
        rows.append(("held", w["hold"]))
    print(f"\n  {c('bold', tilde(w['path']))}")
    for label, value in rows:
        print(f"  {c('dim', label.ljust(12))}{value}")
    print()


def _cmd_show(values, pos, flags, json_view) -> int:
    doc = api.show(pos[0])
    if json_view:
        return emit(doc)
    _print_one(doc["worktree"])
    return 0


def _cmd_add(values, pos, flags, json_view) -> int:
    doc = api.add(pos[0], branch=_one_value(values, "--branch"), new="--new" in flags,
                  base=_one_value(values, "--base"), pr=_one_value(values, "--pr"),
                  path=_one_value(values, "--path"))
    if json_view:
        return emit(doc)
    print(c("green", f"\n  Created {tilde(doc['worktree']['path'])}"))
    _print_one(doc["worktree"])
    return 0


def _cmd_remove(values, pos, flags, json_view) -> int:
    doc = api.remove(pos[0], force="--force" in flags,
                     delete_branch="--delete-branch" in flags)
    if json_view:
        return emit(doc)
    freed = doc.get("freed_bytes")
    size = f", freed {_human_bytes(freed)}" if freed and freed > 0 else ""
    print(c("green", f"  Removed {tilde(doc['removed'])}{size}"))
    if doc.get("branch_deleted") is False:
        print(c("dim", "  Kept the branch: git would not delete it safely"))
    elif doc.get("branch_deleted"):
        print(c("dim", "  Deleted its local branch"))
    return 0


def _cmd_lock(values, pos, flags, json_view) -> int:
    doc = api.lock(pos[0], reason=_one_value(values, "--reason"))
    if json_view:
        return emit(doc)
    print(c("green", f"  Locked {tilde(doc['worktree']['path'])}"))
    return 0


def _cmd_unlock(values, pos, flags, json_view) -> int:
    doc = api.unlock(pos[0])
    if json_view:
        return emit(doc)
    print(c("green", f"  Unlocked {tilde(doc['worktree']['path'])}"))
    return 0


def _cmd_move(values, pos, flags, json_view) -> int:
    doc = api.move(pos[0], pos[1])
    if json_view:
        return emit(doc)
    print(c("green", f"  Moved to {tilde(doc['worktree']['path'])}"))
    return 0


# ------------------------------------------------------------------ prune / slim

def _confirm(wt) -> bool:
    if not sys.stdin.isatty():
        return False
    from swe.prompt import read_line
    try:
        answer = read_line(f"  Remove {tilde(wt.path)} ({wt.reason})? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def _shortener(roots: list[str]):
    roots = sorted({r.rstrip("/") for r in roots if r}, key=len, reverse=True)

    def short(path: str) -> str:
        for root in roots:
            if path.startswith(root + "/"):
                return path[len(root) + 1:]
        return tilde(path)
    return short


def _print_skipped(skipped: list[dict], short) -> None:
    if skipped:
        print(c("dim", f"\n  Kept {len(skipped)}:"))
        for row in skipped:
            print(c("dim", f"    {short(row['path'])} [{row['state']}]: {row['reason']}"))


def _cmd_prune(values, pos, flags, json_view) -> int:
    include = [v for raw in values.get("--include", []) for v in raw.split(",") if v]
    _status("Scanning worktrees and asking GitHub for PR states...", json_view)
    doc = api.prune(all="--all" in flags, yes="--yes" in flags, include=include,
                    branch=_one_value(values, "--branch"), repo=_repos(values),
                    delete_branches="--delete-branches" in flags,
                    auto="--auto" in flags, confirm=None if json_view else _confirm)
    if json_view:
        return emit(doc)
    short = _shortener(doc.get("repos") or [])
    apply = not doc["dry_run"]
    print()
    for w in doc["candidates"]:
        head = c("green", "removed") if apply else c("cyan", "would remove")
        print(f"  {head} {short(w['path'])} (~{_human_bytes(w.get('size_bytes'))}): "
              f"{w.get('reason')}")
    for row in doc["branches"]:
        head = "deleted branch" if apply else "would delete branch"
        err = c("red", f" (failed: {row['error']})") if row.get("error") else ""
        print(f"  {c('green' if apply else 'cyan', head)} {row['repo']} "
              f"{row['branch']}: {row['reason']}{err}")
    for line in doc["registrations"]:
        print(f"  {c('dim', 'pruned' if apply else 'would drop stale entry')} {short(line)}")
    _print_skipped(doc["skipped"], short)
    count, kept = len(doc["candidates"]), len(doc["skipped"])
    est = _human_bytes(doc["estimated_bytes"])
    print()
    if apply:
        freed = doc["freed_bytes"]
        freed = _human_bytes(freed) if freed and freed > 0 else "nothing measurable"
        print(f"  Removed {len(doc['removed'])} worktree(s), kept {kept}. Freed {freed} "
              f"on disk (free space before and after); du estimated ~{est}.")
        if doc.get("log"):
            print(c("dim", f"  Log: {tilde(doc['log'])}"))
    else:
        print(f"  Dry run: would remove {count} worktree(s), ~{est} by du "
              f"(an estimate), kept {kept}.")
        if count:
            print(c("dim", "  Nothing changed. Re-run with --yes to do it."))
    print()
    return 0


def _cmd_slim(values, pos, flags, json_view) -> int:
    days = _one_value(values, "--days")
    if days is not None and not days.isdigit():
        raise WorktreeError("bad_args", f"--days must be a whole number of days, got {days}")
    if pos and ("--all" in flags or _repos(values)):
        raise WorktreeError("bad_args", "pass a PATH or a scope (--all, --repo), not both")
    _status("Finding rebuildable folders...", json_view)
    doc = api.slim(path=pos[0] if pos else None, all="--all" in flags,
                   yes="--yes" in flags, repo=_repos(values),
                   days=int(days) if days is not None else None)
    if json_view:
        return emit(doc)
    apply = not doc["dry_run"]
    print()
    for d in doc["dirs"]:
        head = c("green", "deleted") if apply else c("cyan", "would delete")
        print(f"  {head} {tilde(d['path'])} (~{_human_bytes(d['bytes'])})")
    _print_skipped(doc["skipped"], tilde)
    est = _human_bytes(doc["estimated_bytes"])
    print()
    if apply:
        freed = doc["freed_bytes"]
        freed = _human_bytes(freed) if freed and freed > 0 else "nothing measurable"
        print(f"  Deleted {len(doc['dirs'])} folder(s). Freed {freed} on disk; "
              f"du estimated ~{est}.")
        if doc.get("log"):
            print(c("dim", f"  Log: {tilde(doc['log'])}"))
    else:
        print(f"  Dry run: would delete {len(doc['dirs'])} folder(s), ~{est} by du "
              f"(an estimate). Folders touched in the last {doc['days']}d are kept.")
        if doc["dirs"]:
            print(c("dim", "  Nothing changed. Re-run with --yes to do it."))
    print()
    return 0


HANDLERS = {"list": _cmd_list, "show": _cmd_show, "add": _cmd_add,
            "remove": _cmd_remove, "lock": _cmd_lock, "unlock": _cmd_unlock,
            "move": _cmd_move, "prune": _cmd_prune, "slim": _cmd_slim}
