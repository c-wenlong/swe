"""Skills tree, link, unlink, and move CLI commands."""

from pathlib import Path

from swe.console import c, tilde
from swe.flags import expand_value_flags
from swe.json_output import emit, error_stream
from swe.skills.help_text import (
    print_skills_link_help,
    print_skills_move_help,
    print_skills_unlink_help,
)
from swe.skills.models import LinkReply, MoveReply, SyncReply, ToggleReply, UnlinkReply
from swe.skills.link_ops import (
    SkillLayoutError,
    link_skill_root,
    move_skill,
    reconcile_all_selections,
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
    args = expand_value_flags(args, {"--harness"})
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
        return emit(MoveReply(name, str(src), str(dest)).to_json())
    home = Path.home()
    print(c("green", f"  ✓ Moved {name}"))
    print(c("dim", f"    from {tilde(src, home)}"))
    print(c("dim", f"    to   {tilde(dest, home)}\n"))
    return 0
