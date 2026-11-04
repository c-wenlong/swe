"""Presentation and dispatch for ``swe instructions``."""

from __future__ import annotations

import sys
from pathlib import Path

from swe.instructions import compiler, watcher
from swe.json_output import emit, take_json_flag

SUBCOMMANDS = ("build", "check", "init", "watch")


def cmd_instructions(args) -> int:
    args = list(args or [])
    if not args or args in (["help"], ["--help"], ["-h"]):
        from swe.help_text import cmd_help

        cmd_help(["instructions"])
        return 0
    sub, rest = args[0], args[1:]
    if sub not in SUBCOMMANDS:
        print(f"Unknown instructions command: {sub!r}", file=sys.stderr)
        return 1
    if rest in (["--help"], ["-h"]):
        from swe.help_text import cmd_help

        cmd_help(["instructions"])
        return 0
    rest, json_view = take_json_flag(rest)
    if rest:
        print(f"Unexpected args: {' '.join(rest)}", file=sys.stderr)
        return 1
    if json_view and sub == "watch":
        print("--json only works with build, check and init: "
              "swe instructions check --json", file=sys.stderr)
        return 1

    def display(path: str) -> str:
        try:
            return "~/" + str(Path(path).relative_to(Path.home()))
        except ValueError:
            return path

    def report(result) -> None:
        # A BuildResult from watch, or the bool ``built`` init reports.
        changed = result.changed if hasattr(result, "changed") else result
        action = "Built" if changed else "Unchanged:"
        print(f"{action} ~/.swe/AGENTS.md", flush=True)
        for note in getattr(result, "notes", ()):
            print(f"Note: {note}", file=sys.stderr, flush=True)

    def report_error(exc: Exception) -> None:
        print(f"Instructions: {exc}", file=sys.stderr, flush=True)

    try:
        if sub == "watch":
            print("Watching ~/.swe/AGENTS_MD (Ctrl-C to stop)", flush=True)
            watcher.watch(on_result=report, on_error=report_error)
            return 0
        if sub == "init":
            result = compiler.init_sources()
        elif sub == "check" and json_view:
            document = compiler.survey()
            emit(document)
            return 1 if document["error"] or document["stale"] else 0
        else:
            built = compiler.build_run(check=sub == "check")
            changed = built.changed
    except KeyboardInterrupt:
        if sub == "watch":
            print("Stopped watching instructions.")
            return 0
        return 130
    except (compiler.InstructionError, OSError) as exc:
        report_error(exc)
        return 1

    if sub == "init":
        if json_view:
            return emit(result)
        print(f"Created ~/.swe/AGENTS_MD/{result['chapter']} from {result['source']}")
        report(result["built"])
        return 0
    if sub == "build" and json_view:
        return emit({
            "changed": built.changed,
            "outputs": built.outputs,
            "relinked": [link for link, _ in built.relinked],
            "removed": built.removed,
            "notes": built.notes,
        })
    if sub == "check":
        if changed:
            print(f"Stale or missing: {', '.join(display(p) for p in built.stale)}."
                  " Run swe instructions build.")
            return 1
        print("Current: ~/.swe/AGENTS.md")
    else:
        for output in built.outputs:
            action = "Built" if output["changed"] else "Unchanged:"
            print(f"{action} {display(output['path'])}", flush=True)
        for link, target in built.relinked:
            print(f"Relinked {display(link)} -> {display(target)}", flush=True)
        for path in built.removed:
            print(f"Removed {display(path)}", flush=True)
        for note in built.notes:
            print(f"Note: {note}", file=sys.stderr, flush=True)
    return 0
