"""Find worktrees: `git worktree list --porcelain` on each main checkout,
plus the folders harnesses put worktrees in. Never a recursive scan: a
harness folder is read two levels deep at most.

Scope:

- default: the repo that holds the current directory;
- ``roots``: each path is a repo, or a folder whose direct children are
  repos or worktrees;
- ``everywhere``: the current repo, the ``worktree.roots`` config, the
  bare mirrors `swe gh` keeps, and every repo a harness worktree folder
  points back at (``~/.swe/worktrees``, ``~/.swe/accounts/*/*/home/worktrees``).

Next to each repo it also reads ``<repo>/.claude/worktrees``,
``<repo>/<name>-worktrees``, ``../<name>-worktrees`` and
``../worktrees/<name>`` for folders git no longer knows about.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from swe import paths
from swe.github import manage
from swe.worktrees import gitio
from swe.worktrees.models import ORPHANED, Repo, Worktree
from swe.worktrees.settings import Settings

_GITHUB = re.compile(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


def _real(path: str) -> str:
    return os.path.realpath(path)


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def github_slug(url: str | None) -> str | None:
    match = _GITHUB.search((url or "").strip())
    return f"{match.group(1)}/{match.group(2)}" if match else None


def _main_ref(common_dir: str, bare: bool) -> str | None:
    head = gitio.out(["--git-dir", common_dir, "symbolic-ref", "--quiet",
                      "--short", "refs/remotes/origin/HEAD"])
    candidates = [head] if head else []
    candidates += ["origin/main", "origin/master"]
    if bare:
        candidates += ["main", "master"]
    for ref in candidates:
        if ref and gitio.out(["--git-dir", common_dir, "rev-parse", "--verify",
                              "--quiet", ref + "^{commit}"]):
            return ref
    return None


_REPO_CACHE: dict[str, Repo | None] = {}


def find_repo(path: str) -> Repo | None:
    """The main checkout ``path`` belongs to (``path`` may be any folder
    inside a checkout or a linked worktree), or None."""
    common = gitio.out(["-C", path, "rev-parse", "--path-format=absolute",
                        "--git-common-dir"])
    if not common:
        return None
    return repo_for_common_dir(_real(common))


def repo_for_common_dir(common: str) -> Repo | None:
    if common in _REPO_CACHE:
        return _REPO_CACHE[common]
    repo = None
    listing = gitio.out(["--git-dir", common, "worktree", "list", "--porcelain"])
    if listing is not None:
        first = parse_porcelain(listing)
        if first:
            bare = bool(first[0].get("bare"))
            root = _real(first[0]["path"])
            url = gitio.out(["--git-dir", common, "config", "--get",
                             "remote.origin.url"])
            repo = Repo(root=root, common_dir=common, bare=bare,
                        slug=github_slug(url), main_ref=_main_ref(common, bare))
    _REPO_CACHE[common] = repo
    return repo


def clear_cache() -> None:
    _REPO_CACHE.clear()


def parse_porcelain(text: str) -> list[dict]:
    """`git worktree list --porcelain` records, main checkout first."""
    rows: list[dict] = []
    row: dict | None = None
    for line in text.splitlines():
        if line.startswith("worktree "):
            row = {"path": line[9:]}
            rows.append(row)
        elif row is None:
            continue
        elif line.startswith("HEAD "):
            row["head"] = line[5:]
        elif line.startswith("branch "):
            ref = line[7:]
            row["branch"] = ref[11:] if ref.startswith("refs/heads/") else ref
        elif line == "detached":
            row["detached"] = True
        elif line == "bare":
            row["bare"] = True
        elif line.startswith("locked"):
            row["locked"] = True
            row["lock_reason"] = line[7:].strip() or None
        elif line.startswith("prunable"):
            row["prunable"] = True
    return rows


def read_gitfile(folder: str) -> str | None:
    """The ``gitdir:`` a linked worktree's ``.git`` file points at."""
    try:
        text = Path(folder, ".git").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("gitdir:"):
            target = line[7:].strip()
            return target if os.path.isabs(target) else os.path.normpath(
                os.path.join(folder, target))
    return None


def harness_dirs(home: Path | None = None) -> list[str]:
    """Folders harnesses create worktrees in, outside any repo."""
    swe = paths.swe_dir_for(home)
    base = Path(home or Path.home())
    found = [paths.worktrees_dir_for(home), base / ".codex" / "worktrees",
             base / ".devin" / "worktrees"]
    accounts = swe / "accounts"
    try:
        for vendor in sorted(accounts.iterdir()):
            if vendor.is_dir():
                for account in sorted(vendor.iterdir()):
                    found.append(account / "home" / "worktrees")
    except OSError:
        pass
    return [str(p) for p in found if p.is_dir()]


def repo_side_dirs(repo: Repo) -> list[str]:
    if repo.bare:
        return []
    root = repo.root
    name = os.path.basename(root)
    parent = os.path.dirname(root)
    dirs = [os.path.join(root, ".claude", "worktrees"),
            os.path.join(root, f"{name}-worktrees"),
            os.path.join(parent, f"{name}-worktrees"),
            os.path.join(parent, "worktrees", name)]
    return [d for d in dirs if os.path.isdir(d)]


def checkouts_in(folder: str, depth: int = 2) -> list[str]:
    """Folders under ``folder`` (``depth`` levels at most) whose ``.git``
    is a file, the mark of a linked worktree. Stops at the first hit."""
    found: list[str] = []
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name)
    except OSError:
        return found
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        if os.path.isfile(os.path.join(entry.path, ".git")):
            found.append(_real(entry.path))
        elif depth > 1 and not os.path.isdir(os.path.join(entry.path, ".git")):
            found += checkouts_in(entry.path, depth - 1)
    return found


def _repos_in_root(root: str) -> list[Repo]:
    """A configured root: a repo, or a folder of repos (one level)."""
    repos: list[Repo] = []
    if not os.path.isdir(root):
        return repos
    if os.path.exists(os.path.join(root, ".git")) or root.endswith(".git"):
        repo = find_repo(root)
        return [repo] if repo else []
    try:
        children = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return repos
    for child in children:
        if child.is_dir(follow_symlinks=False) and (
                os.path.exists(os.path.join(child.path, ".git"))
                or child.name.endswith(".git")):
            repo = find_repo(child.path)
            if repo:
                repos.append(repo)
    return repos


def location_of(path: str, home: Path | None = None) -> str:
    swe = _real(str(paths.swe_dir_for(home)))
    if _inside(path, _real(str(paths.worktrees_dir_for(home)))):
        return "swe"
    accounts = os.path.join(swe, "accounts") + "/"
    if path.startswith(accounts):
        return path[len(accounts):].split("/", 1)[0]
    if _inside(path, os.path.join(swe, "cache")):
        return "installer"
    if "/.claude/worktrees/" in path:
        return "claude"
    if "/.codex/worktrees/" in path:
        return "codex"
    if "/.devin/" in path or "/.devin-" in path:
        return "devin"
    if "-worktrees/" in path or "/worktrees/" in path:
        return "sibling"
    return "git"


class Discovery:
    """What one `swe wt` run sees: repos and their worktrees."""

    def __init__(self, home: Path | None = None, cwd: str | None = None,
                 settings: Settings | None = None):
        self.home = home
        self.cwd = _real(cwd or os.getcwd())
        self.settings = settings or Settings.load(home)
        self.repos: dict[str, Repo] = {}
        self.worktrees: dict[str, Worktree] = {}
        self.extra_checkouts: list[str] = []

    # ---------------------------------------------------------- scope
    def add_repo(self, repo: Repo | None) -> None:
        if repo is not None:
            self.repos.setdefault(repo.common_dir, repo)

    def scope_current(self) -> bool:
        repo = find_repo(self.cwd)
        self.add_repo(repo)
        return repo is not None

    def scope_roots(self, roots: list[str]) -> None:
        for root in roots:
            root = _real(os.path.expanduser(root))
            repo = find_repo(root) if os.path.isdir(root) else None
            if repo is not None:
                self.add_repo(repo)
                continue
            for r in _repos_in_root(root):
                self.add_repo(r)
            self._adopt_checkouts(checkouts_in(root))

    def scope_everywhere(self) -> None:
        self.scope_current()
        self.scope_roots(self.settings.roots())
        mirrors = paths.github_mirrors_for(self.home)
        if mirrors.is_dir():
            for child in sorted(mirrors.iterdir()):
                if child.name.endswith(".git") and child.is_dir():
                    self.add_repo(repo_for_common_dir(_real(str(child))))
        for folder in harness_dirs(self.home):
            self._adopt_checkouts(checkouts_in(folder))

    def _adopt_checkouts(self, checkouts: list[str]) -> None:
        for path in checkouts:
            gitdir = read_gitfile(path)
            if gitdir and os.path.isdir(gitdir):
                self.add_repo(find_repo(path))
            self.extra_checkouts.append(path)

    # ---------------------------------------------------------- list
    def collect(self) -> list[Worktree]:
        for repo in list(self.repos.values()):
            listing = gitio.out(["--git-dir", repo.common_dir, "worktree",
                                 "list", "--porcelain"])
            rows = parse_porcelain(listing or "")
            if rows and not repo.bare:
                self._add_registered(repo, rows[0])  # the main checkout, state main
            for row in rows[1:]:
                self._add_registered(repo, row)
            for folder in repo_side_dirs(repo):
                self.extra_checkouts += checkouts_in(folder)
        for path in self.extra_checkouts:
            if path not in self.worktrees:
                self._add_unregistered(path)
        self._annotate()
        return sorted(self.worktrees.values(),
                      key=lambda w: ((w.repo.name if w.repo else "~"), w.path))

    def _add_registered(self, repo: Repo, row: dict) -> None:
        raw = row["path"]
        path = _real(raw) if os.path.exists(raw) else raw
        wt = Worktree(path=path, repo=repo, branch=row.get("branch"),
                      head=row.get("head"), detached=bool(row.get("detached")),
                      locked=bool(row.get("locked")),
                      lock_reason=row.get("lock_reason"),
                      location=location_of(path, self.home))
        if row.get("prunable") or not os.path.isdir(raw):
            wt.state = ORPHANED
            wt.registered = True
            wt.reason = ("folder is gone; git still lists it "
                         "(`git worktree prune` drops the entry)")
        self.worktrees[path] = wt

    def _add_unregistered(self, path: str) -> None:
        gitdir = read_gitfile(path)
        repo = None
        if gitdir and os.path.isdir(gitdir):
            # The gitdir exists but its repo did not list this folder:
            # most often a copy or a move. git cannot remove it either.
            repo = find_repo(path)
            reason = "its gitdir exists but git does not list this folder"
        else:
            guess = gitdir.split("/.git/worktrees/")[0] if gitdir and "/.git/worktrees/" in gitdir else None
            if guess and os.path.isdir(os.path.join(guess, ".git")):
                repo = find_repo(guess)
            reason = ".git points at a gitdir that no longer exists"
        if repo is not None and repo.common_dir not in self.repos:
            self.add_repo(repo)
        wt = Worktree(path=path, repo=repo, state=ORPHANED, reason=reason,
                      location=location_of(path, self.home))
        wt.registered = False
        self.worktrees[path] = wt

    def _annotate(self) -> None:
        protect = self.settings.protect()
        for wt in self.worktrees.values():
            if wt.repo is not None and wt.path == wt.repo.root:
                wt.hold = "main checkout"
            elif any(_inside(wt.path, p) for p in protect):
                wt.hold = "protected"
            elif _inside(self.cwd, wt.path):
                wt.hold = "you are inside it"
            elif wt.locked:
                wt.hold = "locked (git worktree lock)"


def pr_number_from_path(wt: Worktree, home: Path | None = None) -> int | None:
    """The PR a `swe gh pr worktree` checkout was made for, read back from
    the folder name ``manage.pr_worktree_path`` gives it."""
    if wt.repo is None or not wt.repo.slug or wt.location != "swe":
        return None
    match = re.search(r"-pr(\d+)$", wt.path)
    if not match:
        return None
    number = int(match.group(1))
    expected = _real(manage.pr_worktree_path(wt.repo.slug, number, home))
    return number if expected == wt.path else None

