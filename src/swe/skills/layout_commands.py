"""Skills tree, link, unlink, move, and usage CLI commands."""

from pathlib import Path

from swe.console import c, tilde, truncate
from swe.flags import expand_value_flags
from swe.json_output import emit, error_stream
from swe.skills.help_text import (
    print_skills_adopt_help,
    print_skills_link_help,
    print_skills_move_help,
    print_skills_remove_help,
    print_skills_unlink_help,
    print_skills_usage_help,
)
from swe.skills.models import (
    AdoptReply,
    LinkReply,
    MoveReply,
    RemoveReply,
    SyncReply,
    ToggleReply,
    UnlinkReply,
)
from swe.skills.link_ops import (
    SkillLayoutError,
    link_skill_root,
    move_skill,
    reconcile_all_selections,
    remove_skill,
    set_skill_enabled,
    unlink_skill_root,
)


def _parse_flags(args: list[str], allowed: frozenset[str]) -> tuple[dict, list[str]]:
    args = expand_value_flags(args, {"--from", "--to"})
    opts = {"force": False, "mkdir": False, "from": None, "to": None, "json": False}
    rest = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--from", "--to"):
            if arg not in allowed:
                raise ValueError(f"{arg} is not valid here")
            opts[arg[2:]] = args[i + 1]
            i += 1
        elif arg in ("--force", "--mkdir", "--json"):
            if arg not in allowed:
                raise ValueError(f"{arg} is not valid here")
            opts[arg[2:]] = True
        elif arg in ("-h", "--help"):
            rest.append(arg)
        elif arg.startswith("-"):
            raise ValueError(f"Unknown flag: {arg}")
        else:
            rest.append(arg)
        i += 1
    return opts, rest


def cmd_skills_link(args):
    json_view = "--json" in args
    try:
        opts, rest = _parse_flags(args, {"--force", "--json"})
    except ValueError as exc:
        print(c("red", f"  {exc}"), file=error_stream(json_view))
        return 1
    if rest and rest[0] in ("-h", "--help"):
        print_skills_link_help()
        return 0
    if not rest:
        print(c("red", "  Usage: swe skills link <source> [target] [--force]"),
              file=error_stream(json_view))
        return 1
    source = rest[0]
    target = rest[1] if len(rest) > 1 else None
    if len(rest) > 2:
        print(c("red", f"  Unexpected args: {' '.join(rest[2:])}"), file=error_stream(json_view))
        return 1
    try:
        label, src, tgt = link_skill_root(source, target, force=opts["force"])
    except SkillLayoutError as exc:
        print(c("red", f"  {exc}"), file=error_stream(json_view))
        return 1
    if opts["json"]:
        return emit(LinkReply(label, str(src), str(tgt)).to_json())
    home = Path.home()
    print(c("green", f"  ✓ Linked {label}: {tilde(src, home)} → {tilde(tgt, home)}"))
    print(c("dim", "  Run `swe skills tree` to verify.\n"))
    return 0


def cmd_skills_unlink(args):
    json_view = "--json" in args
    try:
        opts, rest = _parse_flags(args, {"--mkdir", "--json"})
    except ValueError as exc:
        print(c("red", f"  {exc}"), file=error_stream(json_view))
        return 1
    if rest and rest[0] in ("-h", "--help"):
        print_skills_unlink_help()
        return 0
    if not rest or len(rest) > 1:
        print(c("red", "  Usage: swe skills unlink <harness|path> [--mkdir]"),
              file=error_stream(json_view))
        return 1
    try:
        label, path = unlink_skill_root(rest[0], mkdir=opts["mkdir"])
    except SkillLayoutError as exc:
        print(c("red", f"  {exc}"), file=error_stream(json_view))
        return 1
    if opts["json"]:
        return emit(UnlinkReply(label, str(path), opts["mkdir"]).to_json())
    home = Path.home()
    msg = f"  ✓ Unlinked {label}: {tilde(path, home)}"
    if opts["mkdir"]:
        msg += " (empty directory created)"
    print(c("green", msg))
    print(c("dim", "  Run `swe skills tree` to verify.\n"))
    return 0


def _cmd_skills_toggle(args, enabled: bool) -> int:
    verb = "enable" if enabled else "disable"
    json_view = "--json" in args
    try:
        args = expand_value_flags(args, {"--harness"})
    except ValueError as exc:
        print(c("red", f"  {exc}"), file=error_stream(json_view))
        return 1
    harness = None
    rest = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--harness":
            harness = args[i + 1]
            i += 1
        elif arg == "--json":
            pass
        elif arg in ("-h", "--help"):
            rest.append(arg)
        elif arg.startswith("-"):
            print(c("red", f"  Unknown flag: {arg}"), file=error_stream(json_view))
            return 1
        else:
            rest.append(arg)
        i += 1
    if rest and rest[0] in ("-h", "--help"):
        from swe.skills.help_text import print_skills_toggle_help
        print_skills_toggle_help()
        return 0
    if len(rest) != 1 or not harness:
        print(c("red", f"  Usage: swe skills {verb} <name> --harness=<h>"),
              file=error_stream(json_view))
        return 1
    try:
        result = set_skill_enabled(rest[0], harness, enabled=enabled)
    except SkillLayoutError as exc:
        print(c("red", f"  {exc}"), file=error_stream(json_view))
        return 1
    if json_view:
        return emit(ToggleReply(**result).to_json())
    if not result["changed"]:
        state = "on" if enabled else "off"
        print(c("dim", f"  {result['skill']} is already {state} for {result['harness']}"))
        return 0
    mode = result["mode"]
    note = {
        "all": "the folder reads all shared skills",
        "except": "the folder now links every shared skill but this one",
        "only": "the folder links only the skills it picked",
    }.get(mode, mode)
    print(c("green", f"  ✓ {result['skill']} {'enabled' if enabled else 'disabled'}"
                    f" for {result['harness']}: {note}"))
    return 0


def cmd_skills_enable(args):
    return _cmd_skills_toggle(args, enabled=True)


def cmd_skills_disable(args):
    return _cmd_skills_toggle(args, enabled=False)


def cmd_skills_sync(args):
    json_view = "--json" in args
    rest = [a for a in args if a != "--json"]
    if rest and rest[0] in ("-h", "--help"):
        from swe.skills.help_text import print_skills_sync_help
        print_skills_sync_help()
        return 0
    if rest:
        print(c("red", f"  Unexpected args: {' '.join(rest)}"), file=error_stream(json_view))
        return 1
    rows = reconcile_all_selections()
    if json_view:
        return emit(SyncReply(rows).to_json())
    if not rows:
        print(c("dim", "  Every selected root already matches its skills.\n"))
        return 0
    home = Path.home()
    for row in rows:
        bits = []
        if row["added"]:
            bits.append(f"linked {', '.join(row['added'])}")
        if row["removed"]:
            bits.append(f"dropped {', '.join(row['removed'])}")
        print(c("green", f"  ✓ {row['label']} ({tilde(row['path'], home)}): {'; '.join(bits)}"))
    print()
    return 0


def cmd_skills_usage(args):
    """`swe skills usage <name>` — call counts from the session index."""
    from swe.skills.usage import skill_usage

    json_view = "--json" in args
    rest = [a for a in args if a != "--json"]
    if rest and rest[0] in ("-h", "--help"):
        print_skills_usage_help()
        return 0
    if len(rest) != 1 or rest[0].startswith("-"):
        print(c("red", "  Usage: swe skills usage <name>"),
              file=error_stream(json_view))
        return 1
    from swe.skills.models import UsageReply

    reply = UsageReply(rest[0], skill_usage(rest[0])).to_json()
    if json_view:
        return emit(reply)
    if not reply["tracked"]:
        print(c("dim", "  Usage is not tracked: no search index yet."))
        print(c("dim", "  Turn it on with `swe session index --on`.\n"))
        return 0
    calls, sessions = reply["calls"], reply["sessions"]
    print(f"\n  {c('bold', rest[0])} — {calls} call{'s' if calls != 1 else ''}"
          f" across {sessions} session{'s' if sessions != 1 else ''}")
    if reply["last_used"]:
        print(c("dim", f"  last used {reply['last_used'][:10]}"))
    for harness, n in reply["by_harness"].items():
        print(c("cyan", f"  {harness.ljust(12)} {n} call{'s' if n != 1 else ''}"))
    if reply["unattributed"]:
        print(c("dim", f"  + {reply['unattributed']} `Skill` call{'s' if reply['unattributed'] != 1 else ''}"
                       " indexed without a skill name"))
    if not calls:
        print(c("dim", "  No calls indexed for this skill yet. The index scans"
                       " existing sessions too, but calls written before the"
                       " `skill` input key tally under \"without a skill name\"."))
    print()
    return 0


def cmd_skills_move(args):
    json_view = "--json" in args
    try:
        opts, rest = _parse_flags(args, {"--from", "--to", "--force", "--json"})
    except ValueError as exc:
        print(c("red", f"  {exc}"), file=error_stream(json_view))
        return 1
    if rest and rest[0] in ("-h", "--help"):
        print_skills_move_help()
        return 0
    if not rest or len(rest) > 1 or not opts["from"] or not opts["to"]:
        print(c("red", "  Usage: swe skills move <name> --from=<scope> --to=<scope>"),
              file=error_stream(json_view))
        return 1
    name = rest[0]
    try:
        src, dest = move_skill(
            name,
            opts["from"],
            opts["to"],
            force=opts["force"],
        )
    except SkillLayoutError as exc:
        print(c("red", f"  {exc}"), file=error_stream(json_view))
        return 1
    if opts["json"]:
        warn = _move_out_of_shared_warning(src, dest)
        if warn:
            print(c("yellow", warn), file=error_stream(True))
        return emit(MoveReply(name, str(src), str(dest)).to_json())
    home = Path.home()
    print(c("green", f"  ✓ Moved {name}"))
    print(c("dim", f"    from {tilde(src, home)}"))
    print(c("dim", f"    to   {tilde(dest, home)}\n"))
    warn = _move_out_of_shared_warning(src, dest)
    if warn:
        print(c("yellow", warn))
    return 0


def _move_out_of_shared_warning(src: Path, dest: Path) -> str | None:
    """A skill moved out of the shared tree into a harness's real skills
    root is a harness-only skill now: the next adopt pass takes it back.
    Say so on moves where that is what just happened."""
    from swe.paths import skills_dir_for

    home = Path.home()
    try:
        shared = skills_dir_for(home).resolve()
    except (OSError, RuntimeError):
        return None
    try:
        if not src.parent.resolve() == shared:
            return None
        dest_root = dest.parent.resolve()
    except (OSError, RuntimeError):
        return None
    if dest_root == shared:
        return None
    from swe.init.layout import discover_skill_roots

    for root in discover_skill_roots(home):
        try:
            if root.resolve() == dest_root:
                return (
                    f"  note: {dest.name} now lives only in that root; the next "
                    "swe init or `swe skills adopt` pulls it back into the "
                    "shared tree"
                )
        except (OSError, RuntimeError):
            continue
    return None


def _print_adopt_result(result) -> None:
    verb = "Would adopt" if result.dry_run else "Adopted"
    if result.adopted:
        names = ", ".join(
            f"{r['name']} (from {r['from']})" for r in result.adopted)
        print(c("green", f"  ✓ {verb} {len(result.adopted)} "
                        f"skill{'s' if len(result.adopted) != 1 else ''} "
                        f"into ~/.swe/skills: {names}"))
    if result.deduped:
        names = ", ".join(
            f"{r['name']} (in {r['harness']})" for r in result.deduped)
        print(c("green", f"  ✓ Folded identical private {'copies' if len(result.deduped) != 1 else 'copy'} "
                        f"into shared links: {names}"))
    for row in result.held_off:
        print(c("dim", f"    {row['harness']}: kept {', '.join(row['skills'])} off"))
    for row in result.conflicts:
        print(c("yellow", f"  ⚠ {row['name']}: conflict in "
                        f"{', '.join(row['harnesses'])} — {row['detail']}"))
    for row in result.failed:
        print(c("red", f"  ! {row['name']}: {row['error']}"))
    if not (result.adopted or result.deduped or result.conflicts or result.failed):
        print(c("dim", "  Nothing to adopt: every skills root is linked, "
                       "or its folders are already in the shared tree.\n"))
    elif result.conflicts or result.failed:
        print()


def cmd_skills_adopt(args):
    json_view = "--json" in args
    rest = [a for a in args if a not in ("--json", "--all", "--dry-run")]
    if rest and rest[0] in ("-h", "--help"):
        print_skills_adopt_help()
        return 0
    unknown = [a for a in rest if a.startswith("-")]
    if unknown or len(rest) > 1:
        print(c("red", "  Usage: swe skills adopt [<name>] [--all] [--dry-run]"),
              file=error_stream(json_view))
        return 1
    from swe.skills.adopt import adopt_harness_skills

    try:
        result = adopt_harness_skills(
            only=rest[0] if rest else None, dry_run="--dry-run" in args)
    except SkillLayoutError as exc:
        print(c("red", f"  {exc}"), file=error_stream(json_view))
        return 1
    if json_view:
        emit(AdoptReply(
            result.adopted, result.deduped, result.conflicts,
            result.failed, result.held_off, result.dry_run).to_json())
        return 1 if result.failed else 0
    _print_adopt_result(result)
    return 1 if result.failed else 0


def cmd_skills_remove(args):
    json_view = "--json" in args
    rest = [a for a in args if a != "--json"]
    if rest and rest[0] in ("-h", "--help"):
        print_skills_remove_help()
        return 0
    if len(rest) != 1 or rest[0].startswith("-"):
        print(c("red", "  Usage: swe skills remove <name>"),
              file=error_stream(json_view))
        return 1
    try:
        result = remove_skill(rest[0])
    except SkillLayoutError as exc:
        print(c("red", f"  {exc}"), file=error_stream(json_view))
        return 1
    if json_view:
        return emit(RemoveReply(**result).to_json())
    print(c("green", f"  ✓ Removed {result['name']} from ~/.swe/skills "
                    f"(backup at {result['backup']})"))
    if result["links_dropped"]:
        print(c("dim", f"    dropped links in: {', '.join(result['links_dropped'])}"))
    if result["private_left"]:
        print(c("yellow",
                f"    {result['name']} still sits as a private folder in "
                f"{', '.join(result['private_left'])} — the next adopt pass "
                "pulls it back into the shared tree"))
    return 0
