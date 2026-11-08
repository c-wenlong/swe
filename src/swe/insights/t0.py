"""The T0 metrics pass: drain the index's unmeasured sessions into
``session_metrics`` counters.

Runs after an index pass (docs/insights.md): every ``sessions`` row in
the search index should have one ``session_metrics`` row of counters
computed by :mod:`swe.insights.metrics`. A session is in the backlog when
its ``(agent, session_id)`` pair has no metrics row, or the indexed
message count moved since it was measured — appends, rewrites and
still-running sessions all look like the second case. ``upsert_counters``
rewrites only the counter half of a row, so a score the T2 pass already
wrote survives a recount.

The index is opened read-only and is never created here: no index, no
pass. The store *is* created — it is this feature's own file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..sessions import search_index
from ..sessions.engines.common import open_sqlite_ro
from . import metrics, store

# Counting ``messages`` rows is what ``n_messages`` stores, so the same
# count is the staleness probe.
_SESSIONS_SQL = (
    "SELECT s.agent, s.session_id, COUNT(m.id) FROM sessions s "
    "LEFT JOIN messages m ON m.session_row_id = s.id "
    "GROUP BY s.agent, s.session_id")

# ``messages_fts.rowid`` is ``messages.id``; ``m.id`` order is the
# conversation order the ack heuristic reads.
_ROWS_SQL = (
    "SELECT m.role, m.ts, COALESCE(f.user_text, ''),"
    " COALESCE(f.assistant_text, ''), COALESCE(f.tool_text, '') "
    "FROM sessions s JOIN messages m ON m.session_row_id = s.id "
    "JOIN messages_fts f ON f.rowid = m.id "
    "WHERE s.agent = ? AND s.session_id = ? ORDER BY m.id")


def _open_index(home: Path | None) -> sqlite3.Connection | None:
    """A read-only handle on the index, or None when it is missing or a
    schema version this build cannot read (left alone for its writer)."""
    if not search_index.available(home):
        return None
    return open_sqlite_ro(str(search_index.index_path(home)))


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
    conn = store.open_store(store.db_path(home))
    try:
        existing = {
            (row[0], row[1]): row[2] for row in conn.execute(
                "SELECT harness, session_id, n_messages FROM session_metrics")}
        backlog = [
            (agent, session_id) for agent, session_id, n in grouped
            if existing.get((agent, session_id)) != n]
        stats["indexed"] = len(grouped)
        stats["current"] = len(grouped) - len(backlog)
        if limit is not None:
            stats["backlog"] = max(0, len(backlog) - limit)
            backlog = backlog[:limit]
        for agent, session_id in backlog:
            rows = [metrics.MessageRow(*row) for row in index.execute(
                _ROWS_SQL, (agent, session_id))]
            store.upsert_counters(
                conn, metrics.compute(session_id, agent, rows))
            stats["measured"] += 1
        conn.commit()
    finally:
        conn.close()
        index.close()
    return stats
