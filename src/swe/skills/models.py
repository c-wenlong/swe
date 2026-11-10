"""``--json`` payload models for the skills domain."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LinkReply:
    """The ``swe skills link --json`` reply."""

    harness: str
    source: str
    target: str

    def to_json(self) -> dict:
        return {
            "action": "link",
            "harness": self.harness,
            "source": self.source,
            "target": self.target,
        }


@dataclass(frozen=True)
class UnlinkReply:
    """The ``swe skills unlink --json`` reply."""

    harness: str
    path: str
    mkdir: bool

    def to_json(self) -> dict:
        return {
            "action": "unlink",
            "harness": self.harness,
            "path": self.path,
            "mkdir": self.mkdir,
        }


@dataclass(frozen=True)
class MoveReply:
    """The ``swe skills move --json`` reply."""

    name: str
    from_path: str
    to_path: str

    def to_json(self) -> dict:
        return {
            "action": "move",
            "name": self.name,
            "from": self.from_path,
            "to": self.to_path,
        }


@dataclass(frozen=True)
class ToggleReply:
    """The ``swe skills enable|disable --json`` reply.

    ``mode`` is the root's selection after the call: ``all`` for a
    whole-folder link, ``except`` or ``only`` for a directory of
    per-skill links. ``converted`` is true when this call turned a
    whole-folder link or an unmanaged folder into a selection.
    """

    action: str
    harness: str
    skill: str
    mode: str
    enabled: bool
    changed: bool
    converted: bool

    def to_json(self) -> dict:
        return {
            "action": self.action,
            "harness": self.harness,
            "skill": self.skill,
            "mode": self.mode,
            "enabled": self.enabled,
            "changed": self.changed,
            "converted": self.converted,
        }


@dataclass(frozen=True)
class AdoptReply:
    """The ``swe skills adopt --json`` reply.

    ``adopted`` rows carry ``{name, from}`` (the registry name of the
    donor root); ``deduped`` ``{name, harness}`` rows were identical
    private copies folded into links; ``conflicts`` ``{name, harnesses,
    detail}`` rows were left where they sat; ``held_off`` ``{harness,
    skills}`` rows are the roots that got an off-mark so the new shared
    skills stay invisible to them.
    """

    adopted: list[dict]
    deduped: list[dict]
    conflicts: list[dict]
    failed: list[dict]
    held_off: list[dict]
    dry_run: bool = False

    def to_json(self) -> dict:
        return {
            "action": "adopt",
            "dry_run": self.dry_run,
            "adopted": self.adopted,
            "deduped": self.deduped,
            "conflicts": self.conflicts,
            "failed": self.failed,
            "held_off": self.held_off,
        }


@dataclass(frozen=True)
class RemoveReply:
    """The ``swe skills remove --json`` reply.

    ``links_dropped`` lists the harness roots a link into the deleted
    folder was dropped from; ``private_left`` lists roots still holding
    a real folder of the same name (the next adopt pass will pull those
    back into the shared tree); ``backup`` is where the folder was
    copied before deletion.
    """

    name: str
    removed: str
    links_dropped: list[str]
    private_left: list[str]
    backup: str

    def to_json(self) -> dict:
        return {
            "action": "remove",
            "name": self.name,
            "removed": self.removed,
            "links_dropped": self.links_dropped,
            "private_left": self.private_left,
            "backup": self.backup,
        }


@dataclass(frozen=True)
class SyncReply:
    """The ``swe skills sync --json`` reply: roots reconcile changed."""

    roots: list[dict]

    def to_json(self) -> dict:
        return {"action": "sync", "roots": self.roots}


@dataclass(frozen=True)
class UsageReply:
    """The ``swe skills usage --json`` reply.

    ``stats`` is `skills.usage.skill_usage`'s dict: ``{"tracked": False}``
    when no search index exists, else ``calls``, ``sessions``,
    ``last_used``, ``by_harness`` and ``unattributed``.
    """

    skill: str
    stats: dict

    def to_json(self) -> dict:
        return {"action": "usage", "skill": self.skill, **self.stats}


@dataclass(frozen=True)
class CatalogRow:
    """One ``swe skills catalog discover --json`` row."""

    label: str
    path: str
    skill_count: int
    source: str
    status: str

    @classmethod
    def of(cls, f) -> "CatalogRow":
        return cls(
            label=f.label,
            path=str(f.path),
            skill_count=f.skill_count,
            source=f.source,
            status=f.status,
        )

    def to_json(self) -> dict:
        return {
            "label": self.label,
            "path": self.path,
            "skill_count": self.skill_count,
            "source": self.source,
            "status": self.status,
        }
