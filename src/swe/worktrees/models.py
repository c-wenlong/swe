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
STATES = (MERGED_CLEAN, MERGED_DIRTY, OPEN_PR, NO_PR, ORPHANED, UNKNOWN)

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

    def to_json(self) -> dict:
        return {
            "path": self.path,
            "repo": self.repo.root if self.repo else None,
            "repo_name": self.repo.name if self.repo else None,
            "branch": self.branch,
            "head": self.head,
            "detached": self.detached,
            "locked": self.locked,
            "location": self.location,
            "registered": self.registered,
            "state": self.state,
            "reason": self.reason,
            "pr": self.pr,
            "last_commit": self.last_commit,
            "changes": self.changes,
            "untracked": self.untracked,
            "hold": self.hold,
            "size_kb": self.size_kb,
            "rebuildable_kb": self.rebuildable_kb,
            "rebuildable": self.rebuildable,
        }
