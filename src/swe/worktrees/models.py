"""The worktree row every `swe wt` surface prints and `--json` emits."""

from __future__ import annotations

from dataclasses import dataclass, field

# Exactly one per worktree. Only the first two are ever removed, and
# merged-dirty only with --include=merged-dirty plus a per-worktree yes.
MERGED_CLEAN = "merged-clean"
MERGED_DIRTY = "merged-dirty"
OPEN_PR = "open-pr"
NO_PR = "no-pr"
ORPHANED = "orphaned"
UNKNOWN = "unknown"
PROTECTED = "protected"   # ~/.swe/cache/install/src and worktree.protect
MAIN = "main"             # the main checkout itself
STATES = (MERGED_CLEAN, MERGED_DIRTY, OPEN_PR, NO_PR, ORPHANED, UNKNOWN,
          PROTECTED, MAIN)

# `location` -> the contract's `harness`.
_HARNESS = {"claude": "claude", "codex": "codex", "devin": "devin", "swe": "swe-pr"}

# Folders a worktree can rebuild from its lockfiles or sources. Only ones
# git ignores are ever counted or deleted.
REBUILDABLE = (".venv", "node_modules", ".next", "build", "dist", "target",
               "DerivedData", ".build")


@dataclass
class Repo:
    """A main checkout (or the bare mirror `swe gh pr worktree` uses)."""

    root: str             # the main checkout, or the bare repo itself
    common_dir: str       # the shared .git dir
    bare: bool = False
    slug: str | None = None       # owner/name when origin is on GitHub
    main_ref: str | None = None   # origin/main or the like, for ancestry

    @property
    def name(self) -> str:
        if self.slug:
            return self.slug
        base = self.root.rstrip("/").rsplit("/", 1)[-1]
        return base[:-4] if base.endswith(".git") else base


@dataclass
class Worktree:
    path: str
    repo: Repo | None
    branch: str | None = None
    head: str | None = None
    detached: bool = False
    locked: bool = False
    lock_reason: str | None = None
    location: str = "git"         # claude | swe | installer | sibling | git | <vendor>
    registered: bool = True       # git lists it (an orphan may not be)
    state: str = UNKNOWN
    reason: str = ""
    pr: dict | None = None        # {number, state, url, head}
    last_commit: int | None = None
    changes: int = 0              # tracked files with changes
    untracked: int = 0            # untracked files outside the ignore list
    hold: str | None = None       # why prune keeps it whatever its state
    size_kb: int | None = None    # du estimate
    rebuildable: list[dict] = field(default_factory=list)  # [{path, kb, mtime}]

    @property
    def rebuildable_kb(self) -> int | None:
        if self.size_kb is None and not self.rebuildable:
            return None
        return sum(r.get("kb") or 0 for r in self.rebuildable)

    @property
    def harness(self) -> str:
        return _HARNESS.get(self.location, "other")

    @property
    def dirty(self) -> bool:
        return bool(self.changes or self.untracked)

    def to_json(self) -> dict:
        """The contract's worktree object ``W`` (docs/json-contracts.md),
        then additive extras."""
        def size(kb):
            return None if kb is None else kb * 1024
        when = None
        if self.last_commit:
            from datetime import datetime, timezone
            when = datetime.fromtimestamp(self.last_commit, timezone.utc).isoformat()
        pr = None
        if self.pr:
            pr = {"number": self.pr.get("number"), "url": self.pr.get("url"),
                  "state": self.pr.get("state")}
        return {
            "path": self.path,
            "repo": self.repo.slug if self.repo else None,
            "main_checkout": self.repo.root if self.repo else None,
            "branch": self.branch,
            "head": self.head,
            "state": self.state,
            "pr": pr,
            "last_commit_at": when,
            "size_bytes": size(self.size_kb),
            "rebuildable_bytes": size(self.rebuildable_kb),
            "dirty": self.dirty,
            "untracked": self.untracked,
            "locked": self.locked,
            "lock_reason": self.lock_reason,
            "harness": self.harness,
            # Extras.
            "detached": self.detached,
            "changes": self.changes,
            "reason": self.reason,
            "hold": self.hold,
            "location": self.location,
            "registered": self.registered,
            "rebuildable": [{"path": r["path"], "bytes": size(r.get("kb"))}
                            for r in self.rebuildable],
        }
