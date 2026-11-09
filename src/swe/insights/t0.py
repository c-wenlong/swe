"""The T0 metrics pass: drain the index's unmeasured sessions into
``session_metrics`` counters.

Runs after an index pass (docs/insights.md): every ``sessions`` row in
the search index should have one ``session_metrics`` row of counters
computed by :mod:`swe.insights.metrics`. A session is in the backlog
when its ``(agent, session_id)`` pair has no metrics row, or when the
``source_rev`` on that row no longer matches the index — ``source_rev``
fingerprints the message rows the counters came from (their ids, roles
and timestamps), so an appended transcript *and* a rewritten file both
show up stale even when the row count never moves. ``upsert_counters``
rewrites only the counter half of a row, so a score the T2 pass already
wrote survives a recount.

One caveat of the index: an ``(agent, session_id)`` pair can own several
``sessions`` rows — Devin's container copy next to its transcript export,
an ssh directory's ``agent-*.jsonl`` files beside the session's own, a
worktree duplicate. Metrics measure one copy: the file named after the
session id when present, else the fullest one.

The index is opened read-only and is never created here: no index, no
pass. The store *is* created — it is this feature's own file.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path

from ..sessions import search_index
from ..sessions.engines.common import open_sqlite_ro
from . import metrics, store

# Per ``sessions`` row, not per session id: one id can own several copies,
# and the pass picks between them.
_SESSIONS_SQL = (
    "SELECT s.id, s.agent, s.session_id, s.file_path, COUNT(m.id) "
    "FROM sessions s LEFT JOIN messages m ON m.session_row_id = s.id "
    "GROUP BY s.id")

# The staleness probe, deliberately cheap: the messages table alone, no
# FTS text. The index writer only ever inserts message rows — a file
# rewrite mints a new ``sessions`` row and new message ids, never an
# in-place edit — so equal (id, role, ts) tuples mean equal content.
_REV_SQL = (
    "SELECT m.id, m.role, m.ts FROM messages m "
    "WHERE m.session_row_id = ? ORDER BY m.id")

# ``messages_fts.rowid`` is ``messages.id``; ``m.id`` order is the
# conversation order the ack heuristic reads.
_ROWS_SQL = (
    "SELECT m.role, m.ts, COALESCE(f.user_text, ''),"
    " COALESCE(f.assistant_text, ''), COALESCE(f.tool_text, '') "
    "FROM messages m "
    "JOIN messages_fts f ON f.rowid = m.id "
    "WHERE m.session_row_id = ? ORDER BY m.id")


def _open_index(home: Path | None) -> sqlite3.Connection | None:
    """A read-only handle on the index, or None when it is missing or a
    schema version this build cannot read (left alone for its writer)."""
    if not search_index.available(home):
        return None
    return open_sqlite_ro(str(search_index.index_path(home)))


def _pick_copy(copies: list[tuple]) -> tuple:
    """The one ``sessions`` row a session id is measured from.

    A file named ``<session_id>.jsonl`` (or ``<session_id>``) is the
    session's own transcript — ssh directories and worktrees hold
    ``agent-*.jsonl`` side files next to it. Otherwise the copy with the
    most rows wins, its ``sessions`` row id breaking the tie (the newest
    replace wins, matching the index's own rewrite order).
    """
    def key(copy):
        row_id, _, session_id, file_path, count = copy
        stem = Path(file_path.split("#", 1)[0]).stem
        return (stem == session_id, count, row_id)
    return max(copies, key=key)


def _source_rev(session_row_id: int, rows: list[tuple]) -> int:
    """A fingerprint of what the counters were measured from: the owning
    ``sessions`` row plus each message row's id, role and timestamp,
    folded into one int. Stored on the metrics row so the next pass can
    tell "same rows" from "rewritten" without re-reading text."""
    digest = hashlib.blake2b(digest_size=8)
    digest.update(f"s{session_row_id};".encode())
    for row_id, role, ts in rows:
        digest.update(f"{row_id}|{role}|{ts};".encode())
    # masked to a signed sqlite INTEGER
    return int.from_bytes(digest.digest(), "big") & ((1 << 63) - 1)


def run_pass(home: Path | None = None, *,
             limit: int | None = None) -> dict:
    """Measure the backlog and upsert counters. Returns pass stats.

    ``limit`` caps the sessions measured in one call so a huge first
    drain can be spread across passes. Counts: ``indexed`` sessions the
    index holds, ``current`` already up to date, ``measured`` written
    this pass, ``backlog`` still waiting (over ``limit``).
    """
    stats = {"indexed": 0, "current": 0, "measured": 0, "backlog": 0}
    index = _open_index(home)
    if index is None:
        return stats
    try:
        grouped = index.execute(_SESSIONS_SQL).fetchall()
    except sqlite3.Error:
        index.close()
        return stats
    try:
        conn = store.open_store(store.db_path(home))
    except BaseException:
        index.close()
        raise
    try:
        existing = {
            (row[0], row[1]): row[2] for row in conn.execute(
                "SELECT harness, session_id, source_rev FROM session_metrics")}
        copies: dict[tuple, list[tuple]] = {}
        for session_row in grouped:
            _, agent, session_id, _, _ = session_row
            copies.setdefault((agent, session_id), []).append(session_row)
        stats["indexed"] = len(copies)
        backlog = []
        for key, rows in copies.items():
            row_id = _pick_copy(rows)[0]
            rev = _source_rev(
                row_id, index.execute(_REV_SQL, (row_id,)).fetchall())
            if existing.get(key) != rev:
                backlog.append((key, row_id, rev))
        stats["current"] = len(copies) - len(backlog)
        if limit is not None:
            stats["backlog"] = max(0, len(backlog) - limit)
            backlog = backlog[:limit]
        for (agent, session_id), row_id, rev in backlog:
            rows = [metrics.MessageRow(*row) for row in index.execute(
                _ROWS_SQL, (row_id,))]
            store.upsert_counters(conn, replace(
                metrics.compute(session_id, agent, rows), source_rev=rev))
            stats["measured"] += 1
        conn.commit()
    finally:
        conn.close()
        index.close()
    return stats
