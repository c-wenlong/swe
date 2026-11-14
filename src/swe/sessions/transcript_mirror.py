"""A local copy of Claude Code and Codex transcripts, kept past the
harnesses' own retention (#1918).

Both harnesses delete transcripts after about 30 days. This module copies
each one into ``~/.swe/data/transcripts/`` while it still exists, so the
session stays listed, readable and searchable afterwards.

Layout keeps the source's structure::

    transcripts/claude/<project-slug>/<uuid>.jsonl
    transcripts/codex/<path under sessions/ or archived_sessions/>
    transcripts/accounts/<vendor>/<account id>/<same, per SWE account home>
    transcripts/manifest.json     what was copied from where
    transcripts/mirror.json       the on/off switch and the last pass

Rules that keep user data safe, in the order they matter:

- **A mirror copy is never deleted here.** Not when its source disappears
  (that is the feature), not when a copy fails, not to make room. The one
  thing a pass removes is its own half-written temp file. There is no
  purge path yet.
- **A copy is atomic.** Bytes go to a temp name beside the target, are
  synced, then renamed over it, so a crash leaves the old copy or the new
  one and never half a file.
- **A shrinking source cannot erase history.** When the source is now
  smaller than the copy it would replace, the old copy is kept first as a
  hard link named ``<file>.shrunk-<mtime_ns>`` (not ``.jsonl``, so nothing
  lists or indexes it).
- **A copy keeps its source's mtime**, because a session's timestamp is
  its transcript's mtime and a mirrored row must sort where it did.
- **Only the manifest says which source a copy came from.** Losing it
  loses no transcript: the next pass copies again, and the read side
  falls back to the files themselves.

Mirroring rides :func:`swe.sessions.search_writer.sources`, the one
enumeration the search indexer also uses, so a pass costs no extra
directory walk: it is handed that list. Nothing in this module reads a
transcript's bytes except the copy itself, so no value derived from here
goes into ``parse_cache``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from swe.paths import (
    account_homes_for,
    atomic_write_text,
    locked_beside,
    swe_dir_for,
    transcripts_dir_for,
)

# The harnesses that prune their own transcripts. The rest keep theirs.
MIRRORED = ("claude", "codex")

MANIFEST_FILE = "manifest.json"
STATE_FILE = "mirror.json"
MANIFEST_VERSION = 1
# The manifest is rewritten after this many copies, so a pass killed
# midway has recorded most of what it did (an unrecorded copy is only
# copied again, never lost).
FLUSH_EVERY = 100
SHRUNK_SUFFIX = ".shrunk-"


class MirrorBusy(Exception):
    """Another pass holds the mirror lock and ``wait`` was false."""


# -- layout --------------------------------------------------------------


@dataclass(frozen=True)
class Root:
    """One directory a harness keeps transcripts in, and where its
    copies go."""

    harness: str
    path: str  # resolved, as ``search_writer._walk`` resolves it
    prefix: str  # subdirectory of the mirror root
    account_home: str  # "" for the harness's own home


def roots(home: Path) -> list[Root]:
    """Every transcript root :func:`search_writer.sources` walks for the
    mirrored harnesses, with its mirror prefix."""
    out: list[Root] = []

    def add(harness: str, homedir: Path, prefix: str, account: str, subdirs) -> None:
        for sub in subdirs:
            out.append(Root(harness, os.path.realpath(homedir / sub), prefix, account))

    add("claude", home / ".claude", "claude", "", ("projects",))
    add("codex", home / ".codex", "codex", "", ("sessions", "archived_sessions"))
    for vendor, subdirs in (("claude", ("projects",)),
                            ("codex", ("sessions", "archived_sessions"))):
        for account_home in account_homes_for(vendor, home):
            add(vendor, account_home, f"accounts/{vendor}/{account_home.parent.name}",
                str(account_home), subdirs)
    return out


def _mirror_rel(path: str, harness: str, table: list[Root]) -> tuple[str, str] | None:
    """``(mirror-relative path, account_home)`` for a resolved source
    path, or None when the harness is not mirrored. A file that sits
    outside every root (a symlink pointing elsewhere) goes under
    ``_external/<hash of its path>/`` so two of them cannot collide."""
    if harness not in MIRRORED:
        return None
    for root in table:
        if root.harness == harness and path.startswith(root.path + os.sep):
            return f"{root.prefix}/{path[len(root.path) + 1:]}", root.account_home
    digest = hashlib.sha1(path.encode("utf-8", "surrogateescape")).hexdigest()[:12]
    return f"{harness}/_external/{digest}/{os.path.basename(path)}", ""


# -- settings ------------------------------------------------------------


def _state_path(home: Path | None) -> Path:
    return transcripts_dir_for(home) / STATE_FILE


def load_state(home: Path | None = None) -> dict:
    """``{"enabled": bool, "last_pass": {...}}``. Mirroring is on until
    someone says ``--off``: it is a copy on local disk, and a machine
    that never opts in should still keep what it would lose."""
    try:
        data = json.loads(_state_path(home).read_text())
    except (OSError, ValueError):
        return {"enabled": True}
    return data if isinstance(data, dict) else {"enabled": True}


def is_enabled(home: Path | None = None) -> bool:
    return load_state(home).get("enabled") is not False


def _update_state(home: Path | None, change) -> dict:
    path = _state_path(home)
    _ensure_dir(path.parent, transcripts_dir_for(home))
    with locked_beside(path):
        data = load_state(home)
        change(data)
        atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n", private=True)
        return data


def set_enabled(home: Path | None, enabled: bool) -> None:
    _update_state(home, lambda data: data.update(enabled=bool(enabled)))


# -- manifest ------------------------------------------------------------


def manifest_path(home: Path | None = None) -> Path:
    return transcripts_dir_for(home) / MANIFEST_FILE


_manifest_cache: dict[str, tuple[tuple, dict]] = {}


def _manifest_signature(path: Path) -> tuple | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def _read_manifest(path: Path) -> tuple[dict, bool]:
    """``(entries, corrupt)``. A missing file is empty and not corrupt."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}, False
    except (OSError, ValueError, RecursionError):
        return {}, True
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        return {}, True
    return {k: v for k, v in files.items() if isinstance(v, dict)}, False


def load_manifest(home: Path | None = None) -> dict[str, dict]:
    """``{mirror-relative path: entry}``; empty when there is none or it
    cannot be read. Never raises, and is cheap to call again: the parse is
    kept until the file's stat changes."""
    path = manifest_path(home)
    signature = _manifest_signature(path)
    if signature is None:
        return {}
    cached = _manifest_cache.get(str(path))
    if cached is not None and cached[0] == signature:
        return cached[1]
    entries, _ = _read_manifest(path)
    _manifest_cache[str(path)] = (signature, entries)
    return entries


def _write_manifest(home: Path | None, entries: dict) -> None:
    body = {"version": MANIFEST_VERSION, "files": entries}
    atomic_write_text(manifest_path(home), json.dumps(body, indent=1, sort_keys=True) + "\n",
                      private=True)


def _ensure_dir(path: Path, stop: Path) -> None:
    """``mkdir -p`` with 0700 on every directory from ``stop`` down:
    ``mkdir(parents=True, mode=...)`` applies the mode to the leaf only."""
    missing = []
    current = path
    while not current.exists() and current != stop and current != current.parent:
        missing.append(current)
        current = current.parent
    if not current.exists():
        current.mkdir(parents=True, mode=0o700, exist_ok=True)
    for directory in reversed(missing):
        with contextlib.suppress(FileExistsError):
            directory.mkdir(mode=0o700)


# -- one pass ------------------------------------------------------------


@dataclass
class MirrorResult:
    files: int = 0  # mirrored-harness transcripts the enumeration held
    copied: int = 0
    unchanged: int = 0
    orphaned: int = 0  # copies whose source is gone: kept, as intended
    failed: int = 0
    conflicts: int = 0  # a copy owned by another source: left alone
    copied_bytes: int = 0
    unchanged_bytes: int = 0
    orphaned_bytes: int = 0
    seconds: float = 0.0

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "files", "copied", "unchanged", "orphaned", "failed", "conflicts",
            "copied_bytes", "unchanged_bytes", "orphaned_bytes", "seconds")}


def _unchanged(entry: dict | None, path: str, st: os.stat_result, dest: Path) -> bool:
    if not entry or entry.get("source") != path:
        return False
    if (entry.get("size"), entry.get("mtime_ns"), entry.get("ino")) != (
            st.st_size, st.st_mtime_ns, st.st_ino):
        return False
    try:
        return os.stat(dest).st_size == entry.get("mirror_size")
    except OSError:
        return False  # the copy is gone: copy again


def _keep_shrunk(dest: Path, written: int) -> None:
    """Before ``dest`` is replaced by something smaller, keep it under a
    second name. A hard link, so no bytes move and no gap opens."""
    try:
        old = os.stat(dest)
    except FileNotFoundError:
        return
    if old.st_size <= written:
        return
    backup = dest.with_name(f"{dest.name}{SHRUNK_SUFFIX}{old.st_mtime_ns}")
    try:
        os.link(dest, backup)
    except FileExistsError:
        pass  # this exact version is already kept


def _copy(source: str, dest: Path, root: Path) -> tuple[os.stat_result, int]:
    """Copy ``source`` to ``dest`` atomically. Returns the source's stat
    taken from the open handle *before* any byte is read (so a file that
    grows while it is copied reads as changed next pass) and the bytes
    written."""
    _ensure_dir(dest.parent, root)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".tmp")
    try:
        with open(source, "rb") as src, os.fdopen(fd, "wb") as out:
            st = os.fstat(src.fileno())
            written = 0
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                written += len(chunk)
            out.flush()
            os.fsync(out.fileno())
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.chmod(tmp, 0o600)
        _keep_shrunk(dest, written)
        os.replace(tmp, dest)
        return st, written
    except BaseException:
        # Our own half-written file, never a transcript.
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def sync(home: Path, sources: Iterable, should_stop=None, *, wait: bool = True) -> MirrorResult:
    """Copy every mirrored-harness file in ``sources`` that is new or
    changed, and count the copies whose source is gone.

    ``sources`` is what :func:`search_writer.sources` returned (anything
    with ``path`` and ``harness``); the caller owns the enumeration.
    Nothing is deleted. Raises :class:`MirrorBusy` when ``wait`` is false
    and another pass is running."""
    started = time.monotonic()
    result = MirrorResult()
    root = transcripts_dir_for(home)
    table = roots(home)
    _ensure_dir(root, root.parent)
    with _pass_lock(home, wait):
        entries, corrupt = _read_manifest(manifest_path(home))
        if corrupt:
            # Never overwrite what cannot be read: set it aside, and the
            # copies below rebuild the record.
            with contextlib.suppress(OSError):
                os.replace(manifest_path(home),
                           manifest_path(home).with_name(f"{MANIFEST_FILE}.corrupt-{int(time.time())}"))
        seen: set[str] = set()
        dirty = False
        pending = 0
        try:
            for source in sources:
                if should_stop is not None and should_stop():
                    break
                located = _mirror_rel(source.path, source.harness, table)
                if located is None:
                    continue
                rel, account_home = located
                result.files += 1
                seen.add(rel)
                dest = root / rel
                try:
                    st = os.stat(source.path)
                except OSError:
                    continue  # gone between the walk and now: orphan next pass
                entry = entries.get(rel)
                if (entry is not None and entry.get("source") != source.path
                        and not _is_gone(entry)):
                    # Two live sources claim one mirror path. Whichever
                    # got there first keeps it: overwriting could lose
                    # the only copy of the other. (A recorded source that
                    # is gone is a moved harness home: the same session
                    # at a new place, so this one takes over.)
                    result.conflicts += 1
                    continue
                if _unchanged(entry, source.path, st, dest):
                    result.unchanged += 1
                    result.unchanged_bytes += entry.get("mirror_size", 0)
                    continue
                try:
                    taken, written = _copy(source.path, dest, root)
                except OSError:
                    result.failed += 1
                    continue
                entries[rel] = {
                    "harness": source.harness,
                    "source": source.path,
                    "account_home": account_home,
                    "ino": taken.st_ino,
                    "mtime_ns": taken.st_mtime_ns,
                    "size": taken.st_size,
                    "mirror_size": written,
                    "copied_at": round(time.time(), 3),
                }
                result.copied += 1
                result.copied_bytes += written
                dirty = True
                pending += 1
                if pending >= FLUSH_EVERY:
                    _write_manifest(home, entries)
                    pending = 0
            # Copies whose source is no longer anywhere: counted, kept.
            for rel, entry in entries.items():
                if rel in seen or os.path.lexists(entry.get("source", "")):
                    continue
                if (root / rel).is_file():
                    result.orphaned += 1
                    result.orphaned_bytes += entry.get("mirror_size", 0)
        finally:
            if dirty or corrupt:
                _write_manifest(home, entries)
    result.seconds = round(time.monotonic() - started, 3)
    stamp = {**result.as_dict(), "finished_at": time.time()}
    with contextlib.suppress(OSError):
        _update_state(home, lambda data: data.update(last_pass=stamp))
    return result


@contextlib.contextmanager
def _pass_lock(home: Path, wait: bool):
    """One pass at a time per machine: the hub's loop, ``swe session
    index`` and ``swe session mirror`` can all run it."""
    path = manifest_path(home)
    if wait:
        with locked_beside(path):
            yield
        return
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows: locked_beside is a no-op too
        yield
        return
    with open(str(path) + ".lock", "a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise MirrorBusy("another transcript mirror pass is running") from None
        yield


def run_pass(home: Path | None = None, should_stop=None, *, wait: bool = True) -> MirrorResult:
    """One pass on its own: enumerate, then :func:`sync`. For the callers
    that have no index pass to ride (the hub with search indexing off,
    ``swe session mirror``)."""
    from swe.sessions import search_writer  # function-local: it imports this module

    home = Path(home) if home is not None else Path.home()
    return sync(home, search_writer.sources(home), should_stop, wait=wait)


# -- read side -----------------------------------------------------------


def _mirror_path(home: Path | None, rel: str) -> str:
    return os.path.join(os.path.realpath(transcripts_dir_for(home)), rel)


def _is_gone(entry: dict) -> bool:
    return not os.path.lexists(entry.get("source") or "")


def _group(rel: str, entry: dict) -> str:
    return entry.get("account_home") or ""


def listing_roots(harness: str, home: Path | None = None) -> list[tuple[str, str, set[str]]]:
    """Where the session listing should also look for ``harness``:
    ``(base_dir, account_home, skip_basenames)`` per mirror root.

    ``skip_basenames`` are the copies whose source still exists, so only
    sessions the harness has lost are parsed and listed; a copy missing
    from the manifest is not skipped, and the caller drops any session
    whose id is already live. Empty when nothing is mirrored."""
    if harness not in MIRRORED:
        return []
    home = home if home is not None else Path(os.path.expanduser("~"))
    base = transcripts_dir_for(home)
    if not base.is_dir():
        return []
    groups: list[tuple[str, str]] = [("", str(base / harness))]
    accounts = base / "accounts" / harness
    with contextlib.suppress(OSError):
        for child in sorted(os.scandir(accounts), key=lambda e: e.name):
            if child.is_dir():
                groups.append((str(swe_dir_for(home) / "accounts" / harness / child.name / "home"),
                               child.path))
    skip: dict[str, set[str]] = {account: set() for account, _ in groups}
    for rel, entry in load_manifest(home).items():
        if entry.get("harness") == harness and not _is_gone(entry):
            skip.setdefault(_group(rel, entry), set()).add(os.path.basename(rel))
    return [(path, account, skip.get(account, set()))
            for account, path in groups if os.path.isdir(path)]


@dataclass(frozen=True)
class Orphan:
    """A mirror copy whose source is gone."""

    path: str  # the copy, resolved, as the search index keys it
    harness: str
    source: str  # where it came from, as the index keyed it


def orphans(home: Path | None = None) -> list[Orphan]:
    """Every copy whose source is gone and which is still on disk."""
    out = []
    for rel, entry in sorted(load_manifest(home).items()):
        if entry.get("harness") in MIRRORED and _is_gone(entry):
            path = _mirror_path(home, rel)
            if os.path.isfile(path):
                out.append(Orphan(path, entry["harness"], entry.get("source", "")))
    return out


def mirror_dir(harness: str, account_home: str = "", home: Path | None = None) -> Path | None:
    """The mirror directory holding ``harness`` transcripts for an
    account home (``""`` for the harness's own), when there is one."""
    if harness not in MIRRORED:
        return None
    home = home if home is not None else Path(os.path.expanduser("~"))
    base = transcripts_dir_for(home)
    path = (base / "accounts" / harness / Path(account_home).parent.name
            if account_home else base / harness)
    return path if path.is_dir() else None


def status(home: Path | None = None) -> dict:
    """What the mirror holds and how its last pass went. Read-only."""
    entries = load_manifest(home)
    state = load_state(home)
    gone = [e for e in entries.values() if _is_gone(e)]
    return {
        "enabled": is_enabled(home),
        "root": str(transcripts_dir_for(home)),
        "entries": len(entries),
        "bytes": sum(int(e.get("mirror_size") or 0) for e in entries.values()),
        "orphaned": len(gone),
        "orphaned_bytes": sum(int(e.get("mirror_size") or 0) for e in gone),
        "last_pass": state.get("last_pass"),
    }
