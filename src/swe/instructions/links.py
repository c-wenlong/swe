"""Repoint swe-owned instruction symlinks after ``instructions build``.

A harness's link should land on its own compiled output when it has one
and on the shared AGENTS.md otherwise. ``swe init`` creates and repairs
these links; this pass only repoints links that already lead into swe's
own outputs, so a link the user aimed elsewhere and every real file stay
untouched. Kept out of compiler.py so it may use ``swe.init.layout``
without a cycle back into the compiler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import tempfile

from swe.configuration import CorruptConfigurationError
from swe.instructions import targets as _targets
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


def _key(path: Path) -> Path:
    """Parent-resolved path: the final component is never followed."""
    return path.parent.resolve() / path.name


@dataclass
class RelinkResult:
    """What the repoint pass did. ``complete`` false means the plan could
    not be read (linkignore, registry, accounts), so the caller must not
    delete stale outputs: a link it never saw may still point at them."""

    relinked: list[tuple[Path, Path]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    in_use: set[Path] = field(default_factory=set)
    complete: bool = True
    # Account links that were repointed, path -> new target, for the
    # account-links.json record the caller updates under the account lock.
    account_retargets: dict[str, str] = field(default_factory=dict)


def relink(
    home: Path | None,
    outputs: dict[str | None, Path],
) -> RelinkResult:
    """Repoint instruction links to their harness's output.

    ``outputs`` maps a harness name (``None`` for the default) to the
    output the build just wrote.
    """
    from swe.init import accounts as _accounts
    from swe.init import layout as _layout

    home = home or Path.home()
    default = outputs.get(None) or agents_file_for(home)
    result = RelinkResult()
    try:
        patterns = _layout.load_linkignore(home)
        registry = _layout.load_registry(home)
    except (_layout.LinkIgnoreError, CorruptConfigurationError) as exc:
        result.complete = False
        result.notes.append(f"instruction links not re-pointed: {exc}")
        return result

    # A link may be re-pointed only while its first hop already names one of
    # swe's outputs — AGENTS.md, or an AGENTS.<name>.md still carrying the
    # generated marker (a markerless file is user content, never taken
    # over). The key resolves the parent but never the final component, so
    # a ~/.swe that is itself a link still matches while a hop through an
    # intermediate link (a Home Manager entry resolving back to AGENTS.md)
    # does not.
    owned = {_key(p) for p in outputs.values()}
    owned.add(_key(agents_file_for(home)))
    owned.update(
        _key(p) for p in _targets.harness_outputs(home).values())

    # (link path, desired target, account link?, movable)
    # Ignored and skipped rows are never moved, but their links are still
    # known: whatever they point at is in use and must not be deleted.
    wanted: list[tuple[Path, Path, bool, bool]] = []
    instructions, _ = _layout.plan(home, patterns, registry)
    for status in instructions:
        movable = status.state not in ("ignored", "skipped")
        wanted.append(
            (status.path,
             outputs.get(_layout.registry_name(status.label), default),
             False, movable))

    try:
        flags = _accounts.sync_flags(home)
        if flags.get("instructions"):
            homes = _accounts.load_account_homes(home)
            for primitive, status in _accounts.plan_accounts(
                    home, patterns, registry, flags, homes):
                if primitive != "instructions" or status.source is None:
                    continue
                # Same states the main branch takes: only a settled or
                # mis-aimed link may be moved; conflicts and blocked rows
                # are the account's own furniture, left alone.
                wanted.append((status.path, status.source, True,
                               status.state in ("linked", "relink")))
    except _accounts.AccountSyncUnavailable as exc:
        result.complete = False
        result.notes.append(
            f"account instruction links not re-pointed: {exc}")

    for path, target, account, movable in wanted:
        if not path.is_symlink():
            continue  # real files, absent paths and non-links stay untouched
        try:
            hop = Path(os.readlink(path))
        except OSError:
            continue
        if not hop.is_absolute():
            hop = path.parent / hop
        try:
            if not movable:
                if _key(hop) in owned:
                    result.in_use.add(_key(hop))
                else:
                    landed = path.resolve()
                    if landed in owned:
                        result.in_use.add(_key(landed))
                continue
            resolved_target = target.resolve()
            if _key(hop) in owned:
                if path.resolve() == resolved_target:
                    result.in_use.add(_key(hop))
                    continue  # already on the right output
                try:
                    _repoint(path, target)
                except OSError:
                    result.in_use.add(_key(hop))
                    continue
                result.relinked.append((path, target))
                result.in_use.add(_key(target))
                if account:
                    result.account_retargets[str(path)] = str(target)
                continue
            # Not a first hop into swe's outputs. A chain that still lands
            # on one is managed by whatever owns the middle link (a Home
            # Manager generation is the known case): say where to aim it,
            # but leave the chain alone.
            landed = path.resolve()
            if landed in owned:
                result.in_use.add(_key(landed))
                if landed != resolved_target:
                    result.notes.append(
                        f"{path} reaches {landed} through another link "
                        f"(Home Manager?); point it at {target} there")
        except OSError:
            continue

    if result.account_retargets:
        try:
            _accounts.retarget_records(home, result.account_retargets)
        except _accounts.AccountSyncUnavailable as exc:
            result.complete = False
            result.notes.append(f"account link record not updated: {exc}")
    return result
