"""Adopt harness-only skills into the shared tree.

A skill is "harness-only" when its folder sits inside a harness's real
skills directory — a root init's plan reports ``keep`` or ``selection`` —
rather than under ~/.swe/skills. Adoption moves it into the shared tree
verbatim, leaves a per-skill symlink behind in the donor root so that
harness sees no change, records the donor in skill_links.json's
``adopted_from``, and marks the skill off in every other root's selection
state so nothing new appears anywhere.

Same-name collisions never merge or overwrite: one deterministic donor
wins (sorted by registry name, then path) and every other copy is
reported as a conflict, or as a dedupe when its bytes already match the
shared one.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from swe.configuration import CorruptConfigurationError
from swe.paths import backup_tree, skills_dir_for
from swe.skills.layout import (
    SkillLayoutError,
    check_link_records_writable,
    record_adopted,
    record_selection,
    selection_intent,
    selection_record_at,
)


@dataclass
class _Candidate:
    """One real skill folder found under a real harness root."""

    folder: Path          # where it sits, under ``root``
    md: Path              # its SKILL.md
    name: str             # folder name — the link unit under shared/
    skill: str            # frontmatter name, for `adopt <name>` matching
    root: Path            # the harness root it was found under
    label: str            # plan label for that root
    carried: bool = False  # sits inside another candidate; moves with it


@dataclass
class AdoptionResult:
    """What an adopt pass did (or a dry run would do)."""

    adopted: list[dict] = field(default_factory=list)    # {name, from}
    deduped: list[dict] = field(default_factory=list)    # {name, harness}
    conflicts: list[dict] = field(default_factory=list)  # {name, harnesses}
    failed: list[dict] = field(default_factory=list)     # {name, error}
    held_off: list[dict] = field(default_factory=list)   # {harness, skills}
    dry_run: bool = False

    @property
    def planned(self) -> int:
        return len(self.adopted)


def _real_dir_root(path: Path, shared_resolved: Path) -> Path | None:
    """The resolved root when it is a real directory swe may adopt out of.

    Symlinked roots hold no private folders (their content lives at the
    link target, which swe does not own), and the shared root itself is
    the destination, never a donor.
    """
    if path.is_symlink() or not path.is_dir():
        return None
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return None
    if resolved == shared_resolved:
        return None
    return resolved


def _candidates(path: Path, root_real: Path, label: str) -> list[_Candidate]:
    """Real skill folders under one donor root, in deterministic order.

    A folder reached only through a symlink resolves outside the root —
    it lives in a vendored tree swe does not own, so moving it out of
    there is not adoption's business. A capped scan (``complete=False``)
    saw only part of the tree and cannot support moving any of it.
    """
    from swe.init.layout import _harness_owned_dirs, _skill_mds
    from swe.skills.discovery import parse_skill_md

    mds, complete = _skill_mds(path, _harness_owned_dirs(path))
    if not complete:
        return []
    folders: dict[Path, Path] = {}
    for paths in mds.values():
        for md in paths:
            folders.setdefault(md.parent, md)
    out: list[_Candidate] = []
    for folder, md in sorted(folders.items()):
        try:
            real = folder.resolve()
        except (OSError, RuntimeError):
            continue
        # Strictly below the root: a misplaced SKILL.md sitting at the
        # root itself would otherwise make the whole skills directory a
        # candidate and the move would take every folder in it.
        if not real.is_relative_to(root_real) or real == root_real:
            continue
        # ``folder`` may be the symlink spelling of a dir whose real path
        # the walk skipped as a dupe (link order decides which came
        # first). Moving a link moves the link, so adopt the dir it
        # stands for — the link-back at the real path keeps every
        # spelling resolving.
        if folder.is_symlink():
            folder = real
            md = folder / "SKILL.md"
        skill, _desc = parse_skill_md(md)
        out.append(_Candidate(folder, md, folder.name, skill, path, label))
    # A folder nested inside another candidate's folder rides along when
    # the outer skill moves — it is not its own adoption row, and a
    # top-level link-back for it would detach it from its parent.
    for cand in out:
        cand.carried = any(
            other is not cand and other.folder in cand.folder.parents
            for other in out
        )
    return out


def _same_skill(private_md: Path, shared_md: Path) -> bool:
    from swe.init.layout import _same_skill as same
    return same(private_md, shared_md)


def _link_escapes(folder: Path) -> bool:
    """A relative symlink under ``folder`` whose target leaves the folder.

    Moving the folder changes where such a link lands: ``assets ->
    ../common`` resolved inside the harness root but would aim inside
    ~/.swe/skills after adoption. A skill carrying one stays private
    rather than move somewhere its links no longer mean the same thing.
    """
    for dirpath, dirnames, filenames in os.walk(folder):
        for entry in (Path(dirpath) / n for n in dirnames + filenames):
            if not entry.is_symlink():
                continue
            try:
                target = Path(os.readlink(entry))
            except OSError:
                continue
            if target.is_absolute():
                continue
            dest = Path(os.path.normpath(str(entry.parent / target)))
            if not dest.is_relative_to(folder):
                return True
    return False


def _except_disabled(cand: _Candidate, name: str) -> bool:
    """An ``except`` record that disabled ``name`` chose the private copy."""
    record = selection_record_at(cand.root)
    if record is None:
        return False
    mode, intent = selection_intent(record)
    return mode == "except" and name in intent


def _plan_actions(
    home: Path,
    skills_statuses,
    shared: Path,
    shared_resolved: Path,
    only: str | None,
    archived: frozenset | set = frozenset(),
) -> tuple[list[tuple], list]:
    """Pair (actions, donor statuses). Each action is a tuple whose head is
    its kind: ("adopt", cand), ("dedupe", cand), ("conflict", name, [labels])."""
    from swe.init.layout import registry_name

    donors = [
        s for s in skills_statuses
        if s.state in ("keep", "selection")
        and registry_name(s.label) not in archived   # archived means unmanaged
    ]
    donors.sort(key=lambda s: (registry_name(s.label), str(s.path)))
    seen_folders: set[Path] = set()
    groups: dict[str, list[_Candidate]] = {}
    for status in donors:
        root_real = _real_dir_root(status.path, shared_resolved)
        if root_real is None:
            continue
        for cand in _candidates(status.path, root_real, registry_name(status.label)):
            try:
                real = cand.folder.resolve()
            except (OSError, RuntimeError):
                continue
            if real in seen_folders:
                continue  # two spellings of one root (a linked parent dir)
            seen_folders.add(real)
            if not cand.carried:
                groups.setdefault(cand.name, []).append(cand)

    if only is not None:
        needle = only.lower()
        groups = {
            name: holders
            for name, holders in groups.items()
            if name.lower() == needle
            or any(c.skill.lower() == needle for c in holders)
        }
        if not groups:
            raise SkillLayoutError(
                f"No harness-only skill matching {only!r} in a real skills "
                "root (folders in ~/.swe/skills and plugin caches are not "
                "adoption candidates)"
            )

    actions: list[tuple] = []
    for name in sorted(groups):
        holders = groups[name]
        holders.sort(key=lambda c: (c.label, str(c.folder)))
        slot = shared / name
        slot_md = slot / "SKILL.md"
        # A folder whose relative links would escape after the move is
        # never adopted or deduped — deleting or relocating it breaks the
        # links — it stays private and is reported instead.
        pinned = [c for c in holders if _link_escapes(c.folder)]
        for cand in pinned:
            actions.append((
                "conflict", name, [cand.label],
                "contains a relative link that would break after the move",
            ))
        holders = [c for c in holders if c not in pinned]
        if not holders:
            continue
        if os.path.lexists(slot):
            # The shared tree already holds this name: never overwritten.
            # Identical private copies fold into links; different content
            # is a conflict the report must surface.
            if not slot.is_dir() or not slot_md.is_file():
                actions.append((
                    "conflict", name,
                    [c.label for c in holders],
                    f"{slot} already exists and is not a skill folder",
                ))
                continue
            for cand in holders:
                if _same_skill(cand.md, slot_md):
                    # Same skip rule as the real pass: an except record
                    # that disabled the name keeps its private copy, so a
                    # dry run must not promise a swap that never happens.
                    if not _except_disabled(cand, name):
                        actions.append(("dedupe", cand, slot))
                else:
                    actions.append((
                        "conflict", name, [cand.label],
                        f"differs from the shared {name}",
                    ))
            continue
        donor = holders[0]
        actions.append(("adopt", donor, slot))
        for cand in holders[1:]:
            if _same_skill(cand.md, donor.md):
                if not _except_disabled(cand, name):
                    actions.append(("dedupe", cand, slot))
            else:
                actions.append((
                    "conflict", name, [cand.label],
                    f"differs from the adopted copy ({donor.label})",
                ))
    return actions, donors


def _claim_intent(cand: _Candidate, name: str) -> None:
    """Record that ``cand.root`` keeps ``name``: an ``except`` root drops it
    from ``disabled``, an ``only`` root picks it up, an unrecorded root
    needs nothing — the link itself is the keep."""
    record = selection_record_at(cand.root)
    if record is None:
        return
    mode, intent = selection_intent(record)
    if mode == "except":
        intent.discard(name)
    else:
        intent.add(name)
    record_selection(cand.label, cand.root, mode, intent)


def _drop_link_aimed_at(link: Path, targets: set[Path]) -> bool:
    """Unlink ``link`` when it aims at any of ``targets`` (normpath, one hop).

    Both spellings of a shared folder count — the literal path and its
    resolved one — because a hand-made link can use either.
    """
    try:
        pointed = Path(os.readlink(link))
    except OSError:
        return False
    if not pointed.is_absolute():
        pointed = link.parent / pointed
    if Path(os.path.normpath(str(pointed))) not in targets:
        return False
    try:
        link.unlink()
        return True
    except OSError:
        return False


def _dedupe(cand: _Candidate, name: str, slot: Path, home: Path) -> bool:
    """Replace an identical private copy with a link into shared.

    An ``except`` record that disabled the name deliberately chose the
    private copy over the shared one: hands off. Otherwise the link-back
    preserves visibility exactly (same bytes), so the private folder is
    backed up and swapped.
    """
    record = selection_record_at(cand.root)
    if _except_disabled(cand, name):
        return False
    backup_tree(cand.folder, home)
    shutil.rmtree(cand.folder)
    cand.folder.symlink_to(slot, target_is_directory=True)
    if record is not None:
        mode, intent = selection_intent(record)
        if mode == "except":
            intent.discard(name)
        else:
            intent.add(name)
        record_selection(cand.label, cand.root, mode, intent)
    return True


def adopt_harness_skills(
    home: Path | None = None,
    *,
    only: str | None = None,
    skills_statuses=None,
    dry_run: bool = False,
) -> AdoptionResult:
    """Move harness-only skills into the shared tree.

    ``skills_statuses`` is init's plan for skills roots when the caller
    already computed it (init passes its own so adoption sees the same
    root set, including held-new roots it already marked ``skipped``);
    otherwise ``plan()`` runs here. ``only`` restricts the pass to one
    skill name for `swe skills adopt <name>`.
    """
    home = home or Path.home()
    check_link_records_writable()
    shared = skills_dir_for(home)
    try:
        shared_resolved = shared.resolve()
    except (OSError, RuntimeError):
        shared_resolved = shared
    if skills_statuses is None:
        from swe.init.layout import plan

        _, skills_statuses = plan(home)

    # Archived harnesses are unmanaged: plan keeps some of their states
    # (linked, selection) untouched, so neither the donor pick nor the
    # mark-off pass may trust a state alone to mean "safe to change".
    from swe.init.layout import archived_names, load_registry

    archived = archived_names(load_registry(home))
    actions, _donors = _plan_actions(
        home, skills_statuses, shared, shared_resolved, only, archived)
    result = AdoptionResult(dry_run=dry_run)
    if dry_run:
        for action in actions:
            kind = action[0]
            if kind == "adopt":
                result.adopted.append({"name": action[1].name, "from": action[1].label})
            elif kind == "dedupe":
                result.deduped.append({"name": action[1].name, "harness": action[1].label})
            else:
                result.conflicts.append({
                    "name": action[1], "harnesses": action[2], "detail": action[3]})
        return result

    adopted_names: set[str] = set()
    kept: dict[Path, set[str]] = {}
    if any(a[0] == "adopt" for a in actions):
        # A standalone `swe skills adopt` can run before any init did:
        # the shared tree is the destination, so it must exist.
        shared.mkdir(parents=True, exist_ok=True)
    for action in actions:
        kind = action[0]
        if kind == "conflict":
            result.conflicts.append({
                "name": action[1], "harnesses": action[2], "detail": action[3]})
            continue
        cand: _Candidate = action[1]
        name = cand.name
        slot = action[2]
        try:
            if kind == "adopt":
                if os.path.lexists(slot):
                    # Won between plan and apply: never overwrite.
                    result.conflicts.append({
                        "name": name, "harnesses": [cand.label],
                        "detail": f"{slot} appeared before the move could run"})
                    continue
                shutil.move(str(cand.folder), str(slot))
                try:
                    cand.folder.symlink_to(slot, target_is_directory=True)
                    _claim_intent(cand, name)
                    record_adopted(name, cand.label)
                except Exception:
                    # Any failure after the move — the link-back or a
                    # record save — must leave a clean state, not a moved
                    # skill the mark-off pass never heard of. Put the
                    # folder back and report the pass's own failure.
                    if cand.folder.is_symlink():
                        cand.folder.unlink()
                    elif os.path.lexists(cand.folder):
                        raise
                    if os.path.lexists(slot):
                        shutil.move(str(slot), str(cand.folder))
                    raise
                adopted_names.add(name)
                kept.setdefault(cand.root, set()).add(name)
                result.adopted.append({"name": name, "from": cand.label})
            else:  # dedupe
                if not os.path.lexists(slot):
                    # The move this link would have depended on never
                    # landed: deleting the copy now would leave nothing.
                    result.failed.append({
                        "name": name,
                        "error": f"{slot} is missing — the adoption it "
                                 "depended on did not land"})
                    continue
                if _dedupe(cand, name, slot, home):
                    kept.setdefault(cand.root, set()).add(name)
                    result.deduped.append({"name": name, "harness": cand.label})
        except (OSError, SkillLayoutError, CorruptConfigurationError) as exc:
            result.failed.append({"name": name, "error": str(exc)})

    if adopted_names:
        result.held_off = _mark_off(
            adopted_names, kept, skills_statuses, shared, shared_resolved,
            archived, result.failed)
    return result


def _mark_off(
    names: set[str],
    kept: dict[Path, set[str]],
    skills_statuses,
    shared: Path,
    shared_resolved: Path,
    archived: set,
    failed: list[dict],
) -> list[dict]:
    """Mark adopted names off in every root that did not donate them.

    A root linked to shared wholesale would otherwise see the new folder
    the moment it lands; converting it to an ``except`` selection keeps
    its view unchanged. A selection record takes the name into
    ``disabled``/off ``enabled``. A hand-made link for the name that
    already resolved into shared was dead weight before and would
    suddenly go live — it is removed. Missing roots, file roots and
    roots the user asked swe to leave alone (``ignored``/``skipped``,
    archived harnesses) are never touched.
    """
    from swe.init.layout import (
        _chain_lands_on,
        _links_into,
        registry_name,
        shared_skill_dirs,
        skill_root_links,
    )

    held: dict[str, set[str]] = {}
    shared_dirs = shared_skill_dirs(shared)
    # ``kept`` keys are donor paths as found; the same physical root under
    # a second spelling (a linked parent dir) must count as kept too, or
    # the mark-off would strip that spelling's link-back.
    kept_real: dict[Path, set[str]] = {}
    for path, held_names in kept.items():
        try:
            kept_real.setdefault(path.resolve(), set()).update(held_names)
        except (OSError, RuntimeError):
            pass
    for status in skills_statuses:
        if status.state in ("ignored", "skipped"):
            continue
        path = status.path
        label = registry_name(status.label)
        if label in archived:
            continue
        off = names - kept.get(path, set())
        if off:
            try:
                off -= kept_real.get(path.resolve(), set())
            except (OSError, RuntimeError):
                pass
        if not off or path == shared:
            continue
        marked: set[str] = set()
        if path.is_symlink():
            if not _chain_lands_on(path, shared):
                continue
            # A whole-root link sees every shared folder; convert it to
            # an except selection that links everything but the adopted
            # names — the same move set_skill_enabled makes for a manual
            # disable. Build the replacement beside the link and swap it
            # in one rename, so a mid-build failure leaves the working
            # link untouched instead of a half-linked directory.
            tmp = path.parent / f".{path.name}.adopt-{os.getpid()}"
            try:
                raw_target = os.readlink(path)   # keep for rollback
                shutil.rmtree(tmp, ignore_errors=True)
                tmp.mkdir()
                for dname, dpath in shared_dirs.items():
                    if dname not in off:
                        (tmp / dname).symlink_to(
                            dpath, target_is_directory=True)
                # rename() will not replace a symlink that resolves to a
                # directory, so the swap unlinks first; a failed rename
                # re-links the original spelling.
                path.unlink()
                try:
                    os.rename(tmp, path)
                except OSError:
                    path.symlink_to(raw_target, target_is_directory=True)
                    raise
            except OSError as exc:
                shutil.rmtree(tmp, ignore_errors=True)
                failed.append({
                    "name": ", ".join(sorted(off)),
                    "error": f"could not convert the {label} skills root: {exc}"})
                continue
            try:
                record_selection(label, path, "except", sorted(off))
            except (OSError, CorruptConfigurationError) as exc:
                # The on-disk shape already keeps the adopted names out;
                # the lost record means a later sync will not re-derive
                # it, so the failure is reported, not swallowed.
                failed.append({
                    "name": ", ".join(sorted(off)),
                    "error": f"converted the {label} skills root but its "
                             f"selection record failed to save: {exc}"})
            marked = set(off)
        elif path.is_dir():
            record = selection_record_at(path)
            if record is not None:
                mode, intent = selection_intent(record)
                new = intent | off if mode == "except" else intent - off
                if new != intent:
                    record_selection(label, path, mode, new)
                    marked |= new - intent if mode == "except" else intent - new
            # Top-level selection links plus anything deeper, like an
            # adopt link-back at root/cat/name — either can resolve into
            # shared under its literal or resolved spelling.
            links = skill_root_links(path, shared)
            deep = _links_into(path, shared)
            for n in sorted(off):
                targets = {
                    Path(os.path.normpath(str(base / n)))
                    for base in (shared, shared_resolved)
                }
                aimed = [links[n]] if n in links else []
                aimed += [p for p in deep if p.name == n]
                if any(_drop_link_aimed_at(p, targets) for p in aimed):
                    marked.add(n)
        if marked:
            held[label] = held.get(label, set()) | marked
    return [{"harness": h, "skills": sorted(ns)} for h, ns in sorted(held.items())]
