"""Link, unlink, and move operations for skill roots and skill folders."""

from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path

from swe.paths import backup_tree, skills_dir_for
from swe.skills.discovery import discover_skills
from swe.skills.layout import (
    SHARED_LABEL,
    SkillLayoutError,
    check_link_records_writable,
    enumerate_skill_roots,
    record_selection,
    record_symlink,
    remove_link_record,
    remove_selection_record,
    resolve_root_ref,
    selection_intent,
    selection_record_at,
)


# Defined in layout.py so resolve_root_ref can raise it; re-exported here
# because this is where callers have always imported it from.
__all__ = [
    "SkillLayoutError", "link_skill_root", "unlink_skill_root", "move_skill",
    "set_skill_enabled", "reconcile_selection_root", "reconcile_all_selections",
]


def _loops(path: Path) -> bool:
    """Whether ``path`` is (or runs through) a symlink loop.

    ``resolve(strict=True)`` reports a loop as RuntimeError before Python
    3.13 and as OSError(ELOOP) from 3.13 on, where the default non-strict
    resolve() no longer raises at all. A path that is merely missing is
    not a loop.
    """
    try:
        path.resolve(strict=True)
    except RuntimeError:
        return True
    except OSError as exc:
        return exc.errno == errno.ELOOP
    return False


def _same_tree(a: Path, b: Path) -> bool:
    """Whether two roots resolve to one directory.

    A source root that cannot be resolved (a symlink loop) is not the same
    tree as anything, so linking replaces it instead of crashing; the
    target was already checked.
    """
    try:
        return a.resolve() == b.resolve()
    except (OSError, RuntimeError):
        return False


def _pure_link_dir(path: Path, target: Path) -> bool:
    """Whether every entry under ``path`` is a symlink resolving into ``target``.

    Such a directory is a selection of the target's skills with nothing
    private in it — relinking the root to the target loses no link the
    target can't reproduce, so it needs neither --force nor a backup.
    """
    try:
        resolved = target.resolve()
        entries = list(path.iterdir())
    except (OSError, RuntimeError):
        return False
    if not entries:
        return False
    for entry in entries:
        if not entry.is_symlink():
            return False
        try:
            entry.resolve().relative_to(resolved)
        except (ValueError, OSError, RuntimeError):
            return False
    return True


def link_skill_root(
    source_ref: str,
    target_ref: str | None = None,
    *,
    home: Path | None = None,
    cwd: Path | None = None,
    force: bool = False,
) -> tuple[str, Path, Path]:
    """Point source root at target via symlink. Default target: shared."""
    home = home or Path.home()
    # The record write comes after the symlink lands; a records file that
    # cannot be read must fail here, before the filesystem moves at all.
    check_link_records_writable()
    target_ref = target_ref or SHARED_LABEL
    src_label, src_path = resolve_root_ref(source_ref, home=home, cwd=cwd)
    _tgt_label, tgt_path = resolve_root_ref(target_ref, home=home, cwd=cwd)

    # Checked before anything moves: replacing a working link with one
    # into a loop would leave the source root unreadable.
    if _loops(tgt_path):
        raise SkillLayoutError(f"{tgt_path} is a symlink loop; fix it before linking to it")
    if _same_tree(src_path, tgt_path):
        raise SkillLayoutError(f"{src_label} already resolves to {tgt_path}")

    # lexists, not exists: a dangling symlink at the source is invisible to
    # exists() but still occupies the path, so falling through to
    # symlink_to() died with FileExistsError instead of replacing it.
    if os.path.lexists(src_path):
        if src_path.is_symlink():
            src_path.unlink()
        elif src_path.is_dir():
            if _pure_link_dir(src_path, tgt_path):
                # A selection made only of links into the target: nothing
                # in it is irreplaceable, so relinking needs no --force.
                pass
            elif any(src_path.iterdir()):
                if not force:
                    raise SkillLayoutError(
                        f"{src_path} is a non-empty directory. "
                        f"Move skills out first, or pass --force to back it "
                        f"up and replace it."
                    )
                # --force used to rmtree with no copy anywhere, while the
                # same decision in `swe init` backed up first. A harness
                # root can be the only home of a skill, so deleting it
                # unrecoverably was a real way to lose work.
                backup_tree(src_path, home)
            shutil.rmtree(src_path)
        else:
            src_path.unlink()

    src_path.parent.mkdir(parents=True, exist_ok=True)
    src_path.symlink_to(tgt_path, target_is_directory=True)
    record_symlink(src_label, src_path, tgt_path)
    return src_label, src_path, tgt_path


def unlink_skill_root(
    source_ref: str,
    *,
    home: Path | None = None,
    cwd: Path | None = None,
    mkdir: bool = False,
) -> tuple[str, Path]:
    """Remove a skill-root symlink; optionally replace with an empty directory."""
    home = home or Path.home()
    # Same pre-flight as link_skill_root: record removal happens after
    # the unlink, so an unreadable records file fails before it.
    check_link_records_writable()
    src_label, src_path = resolve_root_ref(source_ref, home=home, cwd=cwd)

    # lexists, not exists: a dangling symlink still occupies the path, and
    # exists() made one report "does not exist" so it could never be
    # removed.
    if not os.path.lexists(src_path):
        raise SkillLayoutError(f"{src_path} does not exist")
    if not src_path.is_symlink():
        # A real directory of per-skill links detaches by losing them:
        # the harness stops seeing shared skills, and its own folders
        # stay. The selection record goes too — keeping "except: nothing"
        # would say every shared skill is wanted while none is linked.
        if src_path.is_dir():
            from swe.init.layout import skill_root_links

            links = skill_root_links(src_path, skills_dir_for(home))
            if links or selection_record_at(src_path) is not None:
                for link in links.values():
                    link.unlink()
                remove_selection_record(src_path)
                remove_link_record(src_label)
                return src_label, src_path
        raise SkillLayoutError(
            f"{src_path} is a real directory, not a symlink. "
            f"Move skills elsewhere before delinking."
        )

    src_path.unlink()
    remove_link_record(src_label)
    if mkdir:
        src_path.mkdir(parents=True, exist_ok=True)
    return src_label, src_path


def _find_skill_matches(name: str, scope: str, home: Path, cwd: Path) -> list[dict]:
    """Skills in ``scope`` matching ``name``, preferring an exact name.

    Substring matching alone got this wrong both ways. Asking for
    "five-whys" when "five-whys-extended" also exists reported an
    ambiguity even though the name given was exact, and asking for "whys"
    quietly moved whichever single skill happened to contain it. An exact
    name now wins outright, and the looser match only applies when nothing
    matches exactly.

    ``scope`` is a root ref, not only a label: resolve_root_ref turns a
    literal path (``--from=~/.foo/skills``) or a discovered-root label into
    the label discovery reports and the root it sits at. Containment under
    the resolved root is the precise match — labels are not unique
    (``~/.foo/skills`` and ``~/.config/foo/skills`` are both ``foo``) and a
    scope that is itself a symlink reports its skills under the link
    target's label. Label matching stays as the fallback for a scope whose
    root resolves to nothing discovery walked.
    """
    needle = name.lower()
    try:
        scope_label, scope_root = resolve_root_ref(scope, home=home, cwd=cwd)
    except SkillLayoutError:
        scope_label, scope_root = scope, None
    scope_resolved = None
    if scope_root is not None:
        try:
            scope_resolved = scope_root.resolve()
        except (OSError, RuntimeError):
            scope_resolved = None
    all_skills = discover_skills(home=home, cwd=cwd)
    rooted: list[dict] = []
    if scope_resolved is not None:
        rooted = [
            s for s in all_skills
            if Path(s["path"]).is_relative_to(scope_resolved)
        ]
    skills = rooted or [
        s for s in all_skills
        if s["scope"].lower() in (scope.lower(), scope_label.lower())
    ]
    exact = [s for s in skills if s["name"].lower() == needle]
    if exact:
        return exact
    return [
        s for s in skills
        if needle in s["name"].lower() or needle in s["path"].lower()
    ]


def find_skill_directory(
    name: str,
    scope: str,
    *,
    home: Path | None = None,
    cwd: Path | None = None,
) -> Path:
    home = home or Path.home()
    cwd = cwd or Path.cwd()
    matches = _find_skill_matches(name, scope, home, cwd)
    if not matches:
        raise SkillLayoutError(f"No skill matching {name!r} in scope {scope!r}")
    if len(matches) > 1:
        paths = [m["path"] for m in matches[:5]]
        raise SkillLayoutError(
            f"Multiple skills match {name!r} in {scope!r}. Be more specific.\n  " + "\n  ".join(paths)
        )
    return Path(matches[0]["path"]).parent


def move_skill(
    name: str,
    from_scope: str,
    to_scope: str,
    *,
    home: Path | None = None,
    cwd: Path | None = None,
    force: bool = False,
) -> tuple[Path, Path]:
    """Move a skill folder from one scope root to another."""
    home = home or Path.home()
    cwd = cwd or Path.cwd()
    _from_label, from_root = resolve_root_ref(from_scope, home=home, cwd=cwd)
    _to_label, to_root = resolve_root_ref(to_scope, home=home, cwd=cwd)

    try:
        from_resolved = from_root.resolve()
        to_resolved = to_root.resolve()
    except (OSError, RuntimeError) as exc:
        # RuntimeError: a symlink loop, before Python 3.13.
        raise SkillLayoutError(str(exc)) from exc

    # Unconditional: destination would be the source itself, so a forced
    # overwrite below would back up and delete the very folder being moved.
    if from_resolved == to_resolved:
        raise SkillLayoutError(
            f"Scopes {from_scope!r} and {to_scope!r} resolve to the same directory ({from_resolved}). "
            f"Unlink one harness first to give it a private skills folder, then move."
        )

    # The broken-link check must run first: a dangling symlink fails
    # exists() too, so the old order always reported it as a missing root.
    if to_root.is_symlink() and not to_root.exists():
        raise SkillLayoutError(f"Destination symlink is broken: {to_root}")
    if not to_root.exists():
        raise SkillLayoutError(f"Destination root does not exist: {to_root}")

    skill_dir = find_skill_directory(name, from_scope, home=home, cwd=cwd)
    try:
        skill_dir.relative_to(from_resolved)
    except ValueError as exc:
        raise SkillLayoutError(f"Skill {skill_dir} is not under {from_root}") from exc

    dest_root = to_resolved if to_root.is_symlink() else to_root
    dest = dest_root / skill_dir.name
    if dest.exists() or dest.is_symlink():
        if not force:
            raise SkillLayoutError(
                f"Destination already exists: {dest}. "
                f"Pass --force to back it up and replace it."
            )
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        else:
            # Same backup convention as link_skill_root --force: a skill at
            # the destination can be its only copy, so never rmtree raw.
            if any(dest.iterdir()):
                backup_tree(dest, home)
            shutil.rmtree(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(skill_dir), str(dest))
    # A private skill promoted to shared should keep loading where it was
    # written: an only selection never picks a new skill up on its own, so
    # the root that created it claims the name in its record; an except
    # root gets it either way, and takes the name off its disabled list in
    # case the private copy sat behind an exclusion.
    try:
        landed_in_shared = dest_root.resolve() == skills_dir_for(home).resolve()
    except (OSError, RuntimeError):
        landed_in_shared = False
    if landed_in_shared:
        record = selection_record_at(from_root)
        if record is not None:
            mode, intent = selection_intent(record)
            if mode == "except":
                intent.discard(dest.name)
            else:
                intent.add(dest.name)
            record_selection(_from_label, from_root, mode, intent)
    # A move changes what the shared tree holds: links in selection roots
    # that pointed at the moved skill dangle, and a skill that landed in
    # shared belongs in every except selection. One reconcile pass makes
    # the links match each root's record again.
    reconcile_all_selections(home)
    return skill_dir, dest


# -- per-skill selections -----------------------------------------------------
#
# A harness need not take the whole shared tree: its skills folder can be a
# real directory of per-skill links — a "selection". Two rules cover where
# it came from. "except" is a whole-folder link with exceptions: linked
# everything but the folders on its `disabled` list, so a skill added to
# shared later shows up without anyone asking. "only" is a private folder
# that picked skills up: linked just the `enabled` list, so nothing new
# arrives until it is enabled by name. The folder on disk is what the
# harness sees; the record is the intent reconciler (`reconcile_*`) works
# back to after a hand edit.


def set_skill_enabled(
    name: str,
    harness_ref: str,
    *,
    enabled: bool,
    home: Path | None = None,
    cwd: Path | None = None,
) -> dict:
    """Turn one shared skill on or off in one harness's skills folder.

    Only skills that sit directly under the shared tree can be toggled: a
    link is the unit, and a folder the harness loads as its own (a private
    skill, a plugin's skill, a builtin) is switched another way — `swe
    skills move` for a private copy, the plugin's toggle for a plugin's.
    """
    home = home or Path.home()
    cwd = cwd or Path.cwd()
    check_link_records_writable()
    from swe.init.layout import _chain_lands_on, shared_skill_dirs, skill_root_links

    label, root = resolve_root_ref(harness_ref, home=home, cwd=cwd)
    shared = skills_dir_for(home)
    action = "enable" if enabled else "disable"

    try:
        shared_resolved = shared.resolve()
        root_resolved = root.resolve()
    except (OSError, RuntimeError) as exc:
        raise SkillLayoutError(str(exc)) from exc
    # The shared tree itself is never a toggle target. A root LINKED to it
    # is a client and converts below; matching only on the resolved path
    # would wrongly catch that link.
    if root == shared or (root_resolved == shared_resolved and not root.is_symlink()):
        raise SkillLayoutError(
            f"{label}'s skills folder IS the shared tree; nothing in it can be toggled"
        )

    folder = find_skill_directory(name, SHARED_LABEL, home=home, cwd=cwd)
    try:
        folder_resolved = folder.resolve()
    except (OSError, RuntimeError) as exc:
        raise SkillLayoutError(str(exc)) from exc
    if folder_resolved.parent != shared_resolved:
        raise SkillLayoutError(
            f"{name} is nested in {folder.parent.name}, not a top-level shared "
            "skill; toggles work on folders directly under the shared root"
        )
    fname = folder.name

    def reply(mode: str, changed: bool, converted: bool) -> dict:
        return {
            "action": action,
            "harness": label,
            "skill": fname,
            "mode": mode,
            "enabled": enabled,
            "changed": changed,
            "converted": converted,
        }

    if root.is_symlink():
        # A whole-folder link means every skill is on. Toggling one on is
        # a no-op; toggling one off materialises the root into a real
        # directory of links — an except selection.
        if not _chain_lands_on(root, shared):
            raise SkillLayoutError(
                f"{root} points at {os.readlink(root)}; link it to shared first"
            )
        if enabled:
            return reply("all", changed=False, converted=False)
        shared_dirs = shared_skill_dirs(shared)
        root.unlink()
        root.mkdir()
        for dname, dpath in shared_dirs.items():
            if dname != fname:
                (root / dname).symlink_to(dpath, target_is_directory=True)
        record_selection(label, root, "except", [fname])
        return reply("except", changed=True, converted=True)

    if not root.exists():
        # No folder yet: "enable" makes an only selection holding just
        # this skill — how a harness gets a single shared skill and
        # nothing else. "disable" has nothing to switch.
        if not root.parent.exists():
            raise SkillLayoutError(
                f"{label} is not installed: {root.parent} does not exist"
            )
        if not enabled:
            raise SkillLayoutError(f"{root} does not exist; {fname} is not linked anywhere")
        root.mkdir(parents=True)
        (root / fname).symlink_to(folder, target_is_directory=True)
        record_selection(label, root, "only", [fname])
        return reply("only", changed=True, converted=True)

    if not root.is_dir():
        raise SkillLayoutError(f"{root} is a file, not a skills folder")

    # A real directory: linked skills are the top-level links into shared.
    # A root with a record keeps its mode; one without (a private folder,
    # or links made by hand) is managed as "only" — it gets nothing new
    # unless asked, matching how it behaved before swe knew it.
    links = skill_root_links(root, shared)
    record = selection_record_at(root)
    if record is not None:
        mode, intent = selection_intent(record)
    else:
        mode, intent = "only", set(links)
    slot = root / fname
    changed = False
    if enabled:
        if slot.is_symlink():
            res = slot.resolve()
            if res != folder_resolved:
                if res.is_relative_to(shared_resolved):
                    slot.unlink()
                    slot.symlink_to(folder, target_is_directory=True)
                    changed = True
                else:
                    raise SkillLayoutError(
                        f"{slot} links to {res} — a private link, not swe's; "
                        "move it by hand first"
                    )
        elif os.path.lexists(slot):
            raise SkillLayoutError(
                f"{slot} is its own folder: a private {fname} already "
                "shadows the shared one there; move or rename it first"
            )
        else:
            slot.symlink_to(folder, target_is_directory=True)
            changed = True
        if mode == "except":
            intent.discard(fname)
        else:
            intent.add(fname)
    else:
        if slot.is_symlink():
            if slot.resolve().is_relative_to(shared_resolved):
                slot.unlink()
                changed = True
            else:
                raise SkillLayoutError(
                    f"{slot} is a private link, not swe's; remove it by hand"
                )
        # A real folder named the same already shadows the shared skill:
        # it is off either way, and the intent remembers that even if the
        # private copy is later removed.
        if mode == "except":
            intent.add(fname)
        else:
            intent.discard(fname)
    # Write the record whenever the root holds a selection afterwards:
    # recorded before, links now, or this op created one.
    if record is not None or changed or skill_root_links(root, shared):
        record_selection(label, root, mode, intent)
    return reply(mode, changed, converted=changed and record is None)


def reconcile_selection_root(root: Path, shared: Path, record: dict) -> dict:
    """Apply one recorded selection to the links under ``root``.

    Adds the links the record wants and removes the swe links it does
    not — including dangling ones whose skill left the shared tree. A
    link a user made by hand to something inside shared that is not a
    top-level skill folder is theirs, not this record's: it is left alone.
    """
    from swe.init.layout import shared_skill_dirs, skill_root_links

    mode, intent = selection_intent(record)
    shared_dirs = shared_skill_dirs(shared)
    links = skill_root_links(root, shared)
    want = (
        set(shared_dirs) - intent if mode == "except"
        else intent & set(shared_dirs)
    )
    added, removed = [], []
    for n in sorted(want - set(links)):
        slot = root / n
        if os.path.lexists(slot):
            if not slot.is_symlink():
                continue  # a private folder shadows the link; hands off
            slot.unlink()
        slot.symlink_to(shared_dirs[n], target_is_directory=True)
        added.append(n)
    for n in sorted(set(links) - want):
        link = links[n]
        if n in shared_dirs or not link.exists():
            link.unlink()
            removed.append(n)
    return {"added": added, "removed": removed}


def reconcile_all_selections(home: Path | None = None) -> list[dict]:
    """``reconcile_selection_root`` for every recorded selection that
    still sits where it was made; one ``{label, path, added, removed}``
    row per root it changed."""
    home = home or Path.home()
    shared = skills_dir_for(home)
    from swe.skills.layout import _selection_records

    out = []
    for record in _selection_records():
        try:
            path = Path(str(record.get("path", "")))
        except ValueError:
            continue
        if not path.is_dir() or path.is_symlink():
            continue
        result = reconcile_selection_root(path, shared, record)
        if result["added"] or result["removed"]:
            out.append({"label": record.get("label"), "path": str(path), **result})
    return out
