"""Sizes. ``du`` figures are estimates: clonefiles and hard links (uv
builds ``.venv`` folders that way) are counted once per path, so the sum
can be far above what deleting frees. Freed space is measured with free
space on the volume (``statvfs``, what ``df`` reads) before and after.
"""

from __future__ import annotations

import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from swe.worktrees import gitio
from swe.worktrees.models import ORPHANED, REBUILDABLE, Worktree

MAX_DEPTH = 4
DU_TIMEOUT = 900


def du_kb(paths: list[str]) -> dict[str, int]:
    """``{path: kilobytes}`` from one ``du -sk``; paths du skips are absent."""
    if not paths:
        return {}
    try:
        done = subprocess.run(["du", "-sk", *paths], capture_output=True,
                              text=True, timeout=DU_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    sizes: dict[str, int] = {}
    for line in done.stdout.splitlines():
        kb, _, path = line.partition("\t")
        if kb.strip().isdigit():
            sizes[path] = int(kb)
    return sizes


def rebuildable_dirs(root: str, max_depth: int = MAX_DEPTH) -> list[str]:
    """Rebuildable folders under ``root`` that git ignores, outermost only
    (a ``node_modules`` inside a ``node_modules`` is not listed twice)."""
    found: list[str] = []
    base_depth = root.rstrip("/").count("/")
    for dirpath, dirnames, _ in os.walk(root):
        depth = dirpath.rstrip("/").count("/") - base_depth
        keep = []
        for name in dirnames:
            full = os.path.join(dirpath, name)
            if name == ".git" or os.path.islink(full):
                continue
            if name in REBUILDABLE:
                found.append(full)
            elif depth + 1 < max_depth:
                keep.append(name)
        dirnames[:] = keep
    if not found:
        return []
    rels = [os.path.relpath(p, root) for p in found]
    rc, stdout, _ = gitio.git(["-C", root, "check-ignore", "--stdin", "-z"],
                              stdin="\0".join(rels) + "\0")
    if rc not in (0, 1):
        return []
    ignored = {r for r in stdout.split("\0") if r}
    return [os.path.join(root, r) for r in rels if r in ignored]


def newest_mtime(folder: str, cutoff: float) -> bool:
    """True as soon as anything under ``folder`` (itself included) was
    modified after ``cutoff``; an active build writes all the time."""
    try:
        if os.lstat(folder).st_mtime > cutoff:
            return True
    except OSError:
        return False
    for dirpath, dirnames, filenames in os.walk(folder):
        for name in dirnames + filenames:
            try:
                if os.lstat(os.path.join(dirpath, name)).st_mtime > cutoff:
                    return True
            except OSError:
                continue
    return False


def measure(wt: Worktree, sizes: bool = True) -> Worktree:
    if wt.state == ORPHANED and not os.path.isdir(wt.path):
        return wt
    dirs = rebuildable_dirs(wt.path)
    # Separate du runs: GNU du prints nothing for a folder it already
    # counted under an earlier argument, so root-then-children loses them.
    kb = {**du_kb([wt.path]), **du_kb(dirs)} if sizes else {}
    wt.size_kb = kb.get(wt.path) if sizes else None
    wt.rebuildable = [{"path": d, "kb": kb.get(d)} for d in dirs]
    return wt


def measure_all(worktrees: list[Worktree], sizes: bool = True,
                workers: int = 6) -> list[Worktree]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda w: measure(w, sizes), worktrees))
    return worktrees


def free_bytes(path: str) -> tuple[int, int] | None:
    """``(device, bytes free to the user)`` for the volume holding ``path``."""
    probe = path
    while probe and not os.path.exists(probe):
        probe = os.path.dirname(probe)
    try:
        st = os.statvfs(probe or "/")
        return os.stat(probe or "/").st_dev, st.f_bavail * st.f_frsize
    except OSError:
        return None


def free_by_volume(paths: list[str]) -> dict[int, int]:
    volumes: dict[int, int] = {}
    for path in paths:
        got = free_bytes(path)
        if got:
            volumes[got[0]] = got[1]
    return volumes


def stale(folder: str, days: int, now: float | None = None) -> bool:
    cutoff = (time.time() if now is None else now) - days * 86400
    return not newest_mtime(folder, cutoff)


def human(kb: int | None) -> str:
    if kb is None:
        return "-"
    size = float(kb) * 1024
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
