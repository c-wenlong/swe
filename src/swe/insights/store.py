"""``~/.swe/data/insights/insights.sqlite`` — the insights store.

One writer: swe (Python). The app reads the file for its Insights tab;
it does not write it, so there is no fencing protocol like the search
index's. The file is rebuildable from the search index, so a schema bump
may delete and recreate it — but a store at a *newer* version than this
swe understands is still refused, so an older build never tramples a
newer one's rows.

``session_metrics`` has two writers at different cadences: the T0 pass
owns the counter columns and must not clobber a score the T2 pass
already wrote, and the scoring pass owns the ``outcome*`` columns and
must not clobber newer counters. ``upsert_counters`` and
``upsert_score`` update only their half, via ``ON CONFLICT`` clauses.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..paths import insights_db_for

SCHEMA_VERSION = 1

TABLES_SQL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS session_metrics(
  session_id TEXT PRIMARY KEY, harness TEXT NOT NULL, first_ts INTEGER,
  n_messages INTEGER NOT NULL DEFAULT 0,
  n_tool_calls INTEGER NOT NULL DEFAULT 0,
  n_tool_errors INTEGER NOT NULL DEFAULT 0,
  n_ack_unblocks INTEGER NOT NULL DEFAULT 0,
  n_guard_refusals INTEGER NOT NULL DEFAULT 0,
  n_timeouts INTEGER NOT NULL DEFAULT 0,
  n_mcp_errors INTEGER NOT NULL DEFAULT 0,
  is_probe INTEGER NOT NULL DEFAULT 0,
  is_dead INTEGER NOT NULL DEFAULT 0,
  machine_share REAL,
  outcome INTEGER, first_pass INTEGER,
  causes TEXT, score_confidence TEXT);
CREATE TABLE IF NOT EXISTS daily_rollup(
  day TEXT NOT NULL, harness TEXT NOT NULL, metric TEXT NOT NULL,
  value REAL, PRIMARY KEY(day, harness, metric));
CREATE TABLE IF NOT EXISTS findings(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, severity TEXT NOT NULL,
  title TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'open',
  followup_id TEXT, first_seen TEXT, last_seen TEXT);
"""

_PRAGMAS = (
    "PRAGMA busy_timeout = 5000",
    "PRAGMA auto_vacuum = INCREMENTAL",
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
)

_COUNTER_COLUMNS = (
    "session_id", "harness", "first_ts", "n_messages", "n_tool_calls",
    "n_tool_errors", "n_ack_unblocks", "n_guard_refusals", "n_timeouts",
    "n_mcp_errors", "is_probe", "is_dead", "machine_share",
)
_SCORE_COLUMNS = ("outcome", "first_pass", "causes", "score_confidence")


class InsightsUnavailable(Exception):
    """The store can't be written here: a newer schema version, or an
    unreadable file. The message says which, for a person."""


def db_path(home: Path | None = None) -> Path:
    return insights_db_for(home)


def _stored_version(conn: sqlite3.Connection) -> str | None:
    """The file's ``schema_version``; '' for a fresh file with no meta."""
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.Error:
        return ""
    return row[0] if row else None


def open_store(path: Path | str) -> sqlite3.Connection:
    """Open (creating if needed) the store at ``path``.

    Raises :class:`InsightsUnavailable` when the file holds a schema
    version this build doesn't write — the same rule the search index
    follows, so a stale binary leaves a newer store alone.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        # The version check runs before any pragma: switching journal_mode
        # writes to the file, and a foreign-version store must stay
        # byte-for-byte untouched.
        version = _stored_version(conn)
        if version not in ("", str(SCHEMA_VERSION), None):
            raise InsightsUnavailable(
                f"{path} holds schema version {version}, "
                f"and this swe writes version {SCHEMA_VERSION}; update swe "
                "or move the file aside")
        for pragma in _PRAGMAS:
            conn.execute(pragma)
    except sqlite3.Error:
        # The store is rebuildable from the index: a file SQLite can't
        # open (torn write, not a database at all) is moved aside and the
        # caller gets a fresh one, like the index's torn-file path.
        conn.close()
        path.unlink(missing_ok=True)
        return open_store(path)
    except Exception:
        conn.close()
        raise
    conn.executescript(TABLES_SQL)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),))
    conn.commit()
    return conn


@dataclass(frozen=True)
class SessionMetrics:
    """One ``session_metrics`` row. Score fields stay ``None`` until a
    T2 pass fills them; ``upsert_counters`` never touches them."""

    session_id: str
    harness: str
    first_ts: int | None = None
    n_messages: int = 0
    n_tool_calls: int = 0
    n_tool_errors: int = 0
    n_ack_unblocks: int = 0
    n_guard_refusals: int = 0
    n_timeouts: int = 0
    n_mcp_errors: int = 0
    is_probe: int = 0
    is_dead: int = 0
    machine_share: float | None = None

    def counter_values(self) -> tuple:
        return tuple(getattr(self, name) for name in _COUNTER_COLUMNS)


def upsert_counters(conn: sqlite3.Connection, m: SessionMetrics) -> None:
    """Write the T0 half of a session's row, preserving any score."""
    updates = ", ".join(f"{c}=excluded.{c}" for c in _COUNTER_COLUMNS[1:])
    conn.execute(
        f"INSERT INTO session_metrics({', '.join(_COUNTER_COLUMNS)}) "
        f"VALUES ({', '.join('?' * len(_COUNTER_COLUMNS))}) "
        f"ON CONFLICT(session_id) DO UPDATE SET {updates}",
        m.counter_values())


def upsert_score(conn: sqlite3.Connection, session_id: str, harness: str, *,
                 outcome: int, first_pass: int | None,
                 causes: str | None, score_confidence: str | None) -> None:
    """Write the T2 half of a session's row, preserving its counters.

    ``harness`` fills only on insert — a score for a session the T0 pass
    never measured lands as a counters-empty row rather than vanishing."""
    conn.execute(
        "INSERT INTO session_metrics(session_id, harness, outcome, "
        "first_pass, causes, score_confidence) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET "
        "outcome=excluded.outcome, first_pass=excluded.first_pass, "
        "causes=excluded.causes, score_confidence=excluded.score_confidence",
        (session_id, harness, outcome, first_pass, causes, score_confidence))


def unscored_sessions(conn: sqlite3.Connection) -> list[str]:
    """Session ids still holding ``outcome=NULL`` — the T2 backlog."""
    return [r[0] for r in conn.execute(
        "SELECT session_id FROM session_metrics WHERE outcome IS NULL "
        "ORDER BY first_ts")]


def record_rollup(conn: sqlite3.Connection, day: str, harness: str,
                  metric: str, value: float | None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO daily_rollup(day, harness, metric, value) "
        "VALUES (?, ?, ?, ?)", (day, harness, metric, value))
