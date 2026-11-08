"""Cursor's ACP session store: the ``store.db`` beside ``meta.json``.

Two roots under ``~/.cursor`` hold sessions in this format:
``acp-sessions/<id>/`` (agent runs made over ACP: the editor's agent, SDK
and headless runs) and ``chats/<workspace-hash>/<id>/`` (the workspace's own
record; its id usually also names a transcript under
``~/.cursor/projects/*/agent-transcripts/``, which swe prefers when both
exist).

Inside the db, ``meta`` maps key ``"0"`` to a hex-encoded JSON manifest:
``agentId``, ``name``, ``createdAt``, ``latestRootBlobId`` and
``blobEncryptionKey``. ``blobs`` is content-addressed (``id`` is the data's
hex sha256): most rows are transcript messages as AI-SDK-style JSON
(``{"role": ..., "content": [{type: text|reasoning|tool-call|tool-result}]}``);
the rest are binary manifests or context payloads.

The manifest named by ``latestRootBlobId`` is a protobuf: field 1 repeats
the newest segment's message ids (32 raw bytes each), and field 13 points
at the base manifest whose field 1 lists older messages and whose field 4
links to the next base segment, oldest to newest. The transcript's order is
the base chain's ids followed by the root's field-1 ids. The remaining
fields carry session config (5), context refs (8, 15, 18), the workspace
URI (9), the client (22), the timezone (27) and run ids (38).
"""

from __future__ import annotations

import binascii
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlparse


def _cursor_home() -> str:
    return os.path.expanduser("~/.cursor")


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    """(value, next index) of the varint at ``i``."""
    out = 0
    shift = 0
    while i < len(buf):
        byte = buf[i]
        i += 1
        out |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return out, i
        shift += 7
    return out, i


def _protobuf_fields(buf: bytes) -> dict[int, list[bytes]]:
    """field number -> length-delimited values; varint/32/64-bit fields skipped."""
    fields: dict[int, list[bytes]] = {}
    i = 0
    while i < len(buf):
        tag, i = _varint(buf, i)
        number, wire = tag >> 3, tag & 7
        if wire == 2:
            length, i = _varint(buf, i)
            fields.setdefault(number, []).append(buf[i:i + length])
            i += length
        elif wire == 0:
            _, i = _varint(buf, i)
        elif wire == 5:
            i += 4
        elif wire == 1:
            i += 8
        else:
            break
    return fields


def _scan(root: str, depth: int) -> Iterator[tuple[str, str]]:
    """(session dir name, session dir) holding a ``store.db``, ``depth`` levels down."""
    try:
        with os.scandir(root) as it:
            entries = [entry for entry in it if entry.is_dir()]
    except OSError:
        return
    for entry in entries:
        if depth == 1:
            if os.path.isfile(os.path.join(entry.path, "store.db")):
                yield entry.name, entry.path
        else:
            yield from _scan(entry.path, depth - 1)


def session_dirs(cursor_home: str | None = None) -> Iterator[tuple[str, str]]:
    """(session_id, session dir) under ``acp-sessions`` then ``chats``.

    ``chats`` sessions sit one level deeper (``chats/<workspace>/<id>``).
    The same id can appear in both; the caller dedupes.
    """
    home = cursor_home or _cursor_home()
    yield from _scan(os.path.join(home, "acp-sessions"), 1)
    yield from _scan(os.path.join(home, "chats"), 2)


def store_index(cursor_home: str | None = None) -> dict[str, str]:
    """session id -> ``store.db`` path, for one-pass probes over many sessions.

    ``session_dirs`` yields ``acp-sessions`` before ``chats``; an id in both
    keeps the ACP record, the same source ``find_store`` prefers.
    """
    out: dict[str, str] = {}
    for sid, directory in session_dirs(cursor_home):
        out.setdefault(sid, os.path.join(directory, "store.db"))
    return out


def find_store(session_id: str, cursor_home: str | None = None) -> str:
    """The session's ``store.db`` path, or "" when there is none."""
    if not session_id:
        return ""
    home = cursor_home or _cursor_home()
    direct = os.path.join(home, "acp-sessions", session_id, "store.db")
    if os.path.isfile(direct):
        return direct
    chats = os.path.join(home, "chats")
    try:
        with os.scandir(chats) as it:
            for entry in it:
                if not entry.is_dir():
                    continue
                path = os.path.join(entry.path, session_id, "store.db")
                if os.path.isfile(path):
                    return path
    except OSError:
        pass
    return ""


def read_meta_json(session_dir: str) -> dict:
    """The ``meta.json`` sidecar's object (``title``, ``cwd``, timestamps), or {}."""
    try:
        with open(os.path.join(session_dir, "meta.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _connect(path: str) -> sqlite3.Connection:
    return sqlite3.connect(Path(path).absolute().as_uri() + "?mode=ro", uri=True)


def read_meta(conn: sqlite3.Connection) -> dict:
    """The ``meta`` table's decoded manifest row, or {}."""
    try:
        rows = conn.execute("SELECT value FROM meta").fetchall()
    except sqlite3.Error:
        return {}
    for (value,) in rows:
        try:
            data = json.loads(binascii.unhexlify(value))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("latestRootBlobId"):
            return data
    return {}


def _root_fields(conn: sqlite3.Connection, meta: dict) -> dict[int, list[bytes]]:
    """The root manifest's protobuf fields, or {} when unreadable."""
    root_id = meta.get("latestRootBlobId")
    if not isinstance(root_id, str) or not root_id:
        return {}
    try:
        row = conn.execute("SELECT data FROM blobs WHERE id = ?", (root_id,)).fetchone()
    except sqlite3.Error:
        return {}
    if not row:
        return {}
    return _protobuf_fields(row[0])


def _blob(conn: sqlite3.Connection, blob_id: str) -> bytes | None:
    try:
        row = conn.execute("SELECT data FROM blobs WHERE id = ?", (blob_id,)).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def ordered_ids(conn: sqlite3.Connection, meta: dict | None = None) -> list[str]:
    """Message blob ids in transcript order: base chunks, then the live segment."""
    meta = meta if meta is not None else read_meta(conn)
    fields = _root_fields(conn, meta)
    ordered: list[str] = []
    seen: set[str] = set()
    for head in fields.get(13, []):
        chain_id = head.hex()
        while chain_id and chain_id not in seen:
            seen.add(chain_id)
            data = _blob(conn, chain_id)
            if data is None:
                break
            chunk = _protobuf_fields(data)
            ordered.extend(raw.hex() for raw in chunk.get(1, []))
            nxt = chunk.get(4)
            chain_id = nxt[0].hex() if nxt else ""
    ordered.extend(raw.hex() for raw in fields.get(1, []))
    return ordered


def session_fields(path: str) -> tuple[dict, str]:
    """(manifest dict, workspace path) in one read; ({}, "") on failure.

    The workspace path is the root manifest's ``file://`` URI decoded;
    ``meta.json``'s ``cwd`` is the better source when it exists.
    """
    try:
        conn = _connect(path)
    except sqlite3.Error:
        return {}, ""
    try:
        meta = read_meta(conn)
        uri = ""
        raw = _root_fields(conn, meta).get(9)
        if raw:
            uri = raw[0].decode("utf-8", "replace")
        path_out = ""
        if uri.startswith("file://"):
            path_out = unquote(urlparse(uri).path)
        return meta, path_out
    finally:
        conn.close()


def _decode_message(data: bytes | None) -> dict | None:
    if data is None:
        return None
    try:
        message = json.loads(data)
    except Exception:
        return None
    return message if isinstance(message, dict) else None


def read_messages(path: str) -> list[dict]:
    """Every ordered message blob decoded, in transcript order.

    Blobs that are gone or not JSON (manifests, encrypted payloads) are
    skipped; a fully unreadable store answers [].
    """
    conn = _connect(path)
    try:
        ids = ordered_ids(conn)
        if not ids:
            return []
        by_id: dict[str, bytes] = {}
        for start in range(0, len(ids), 500):
            batch = ids[start:start + 500]
            rows = conn.execute(
                "SELECT id, data FROM blobs WHERE id IN ({})".format(
                    ",".join("?" * len(batch))),
                batch,
            ).fetchall()
            by_id.update(rows)
        return [
            message
            for blob_id in ids
            if (message := _decode_message(by_id.get(blob_id))) is not None
        ]
    finally:
        conn.close()


def last_message(path: str) -> dict | None:
    """The newest ordered message that decodes, or None."""
    conn = _connect(path)
    try:
        for blob_id in reversed(ordered_ids(conn)):
            message = _decode_message(_blob(conn, blob_id))
            if message is not None:
                return message
        return None
    finally:
        conn.close()


def store_stamp(session_dir: str, meta: dict, store_meta: dict) -> float:
    """Last-activity epoch ms: the newest of meta's stamps and the store's files.

    The ``-wal`` file moves on every write, so a live session's stamp tracks
    it rather than the db's own mtime.
    """
    stamp = 0.0
    for source, keys in ((meta, ("updatedAtMs", "createdAtMs")),
                         (store_meta, ("updatedAt", "createdAt"))):
        for key in keys:
            value = source.get(key)
            # meta.json writes its stamps as strings; the db manifest's are numbers.
            if isinstance(value, str) and value.strip().isdigit():
                value = float(value)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                stamp = max(stamp, float(value))
    for name in ("store.db", "store.db-wal"):
        try:
            stamp = max(stamp, os.path.getmtime(os.path.join(session_dir, name)) * 1000)
        except OSError:
            pass
    return stamp
