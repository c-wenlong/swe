"""Repoint swe-owned instruction symlinks after ``instructions build``.

A harness's link should land on its own compiled output when it has one
and on the shared AGENTS.md otherwise. ``swe init`` creates and repairs
these links; this pass only repoints links that already lead into swe's
own outputs, so a link the user aimed elsewhere and every real file stay
untouched. Kept out of compiler.py so it may use ``swe.init.layout``
without a cycle back into the compiler.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

from swe.configuration import CorruptConfigurationError
from swe.paths import agents_file_for, swe_dir_for


def _repoint(path: Path, target: Path) -> None:
    """Atomically replace the symlink at ``path`` with one to ``target``."""
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(descriptor)
    os.unlink(temporary)  # os.symlink needs a name that does not exist yet
    temporary = Path(temporary)
    try:
        temporary.symlink_to(target)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def relink(
    home: Path | None,
    outputs: dict[str | None, Path],
) -> tuple[list[tuple[Path, Path]], list[str]]:
    """Repoint instruction links to their harness's output.

    ``outputs`` maps a harness name (``None`` for the default) to the
    output the build just wrote. Returns ``(relinked, notes)``: each
    relinked pair is (link path, new target), and a note explains any pass
    that was skipped rather than failed — an unreadable .linkignore or
    registry must not break a good build.
    """
    from swe.init import accounts as _accounts
    from swe.init import layout as _layout

    home = home or Path.home()
    default = outputs.get(None) or agents_file_for(home)
    try:
        patterns = _layout.load_linkignore(home)
        registry = _layout.load_registry(home)
    except (_layout.LinkIgnoreError, CorruptConfigurationError) as exc:
        return [], [f"instruction links not re-pointed: {exc}"]

    # A link may be re-pointed only while its first hop already names one of
    # swe's outputs — AGENTS.md, or any AGENTS.<name>.md ever built. The key
    # resolves the parent but never the final component, so a ~/.swe that is
    # itself a link still matches while a hop through an intermediate link
    # (a Home Manager entry resolving back to AGENTS.md) does not.
    def key(path: Path) -> Path:
        return path.parent.resolve() / path.name

    owned = {key(p) for p in outputs.values()}
    owned.add(key(agents_file_for(home)))
    try:
        owned.update(key(p) for p in swe_dir_for(home).glob("AGENTS.*.md"))
    except OSError:
        pass

    wanted: list[tuple[Path, Path]] = []
    instructions, _ = _layout.plan(home, patterns, registry)
    for status in instructions:
        if status.state in ("ignored", "skipped"):
            continue
        wanted.append(
            (status.path, outputs.get(_layout.registry_name(status.label), default)))

    notes: list[str] = []
    try:
        flags = _accounts.sync_flags(home)
        if flags.get("instructions"):
            homes = _accounts.load_account_homes(home)
            for primitive, status in _accounts.plan_accounts(
                    home, patterns, registry, flags, homes):
                if primitive == "instructions" and status.source is not None:
                    wanted.append((status.path, status.source))
    except _accounts.AccountSyncUnavailable as exc:
        notes.append(f"account instruction links not re-pointed: {exc}")

    relinked: list[tuple[Path, Path]] = []
    for path, target in wanted:
        if not path.is_symlink():
            continue  # real files, absent paths and non-links stay untouched
        try:
            hop = Path(os.readlink(path))
        except OSError:
            continue
        if not hop.is_absolute():
            hop = path.parent / hop
        try:
            resolved_target = target.resolve()
            if key(hop) in owned:
                if path.resolve() == resolved_target:
                    continue  # already on the right output
                _repoint(path, target)
                relinked.append((path, target))
                continue
            # Not a first hop into swe's outputs. A chain that still lands
            # on one is managed by whatever owns the middle link (a Home
            # Manager generation is the known case): say where to aim it,
            # but leave the chain alone.
            landed = path.resolve()
            if landed in owned and landed != resolved_target:
                notes.append(
                    f"{path} reaches {landed} through another link "
                    f"(Home Manager?); point it at {target} there")
        except OSError:
            continue
    return relinked, notes
