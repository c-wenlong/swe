"""The ``insights.sqlite`` store: schema, version guard, and the
counter/score upsert halves that must never clobber each other.
Every test runs in a throwaway HOME."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from swe.insights import store
from swe.paths import insights_db_for


class _Home(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

    def db(self):
        conn = store.open_store(store.db_path(self.home))
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn


def metrics(session_id="sess-1", **kw):
    return store.SessionMetrics(
        session_id=session_id, harness="claude",
        first_ts=kw.pop("first_ts", 1696),
        n_messages=kw.pop("n_messages", 12), n_tool_calls=5, n_tool_errors=2,
        n_ack_unblocks=1, n_guard_refusals=3, n_timeouts=1, n_mcp_errors=0,
        is_probe=0, is_dead=0, machine_share=0.4, **kw)


class SchemaTests(_Home):
    def test_fresh_store_has_the_schema(self):
        conn = self.db()
        meta = dict(conn.execute("SELECT key, value FROM meta"))
        self.assertEqual(meta["schema_version"], "1")
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(
            {"meta", "session_metrics", "daily_rollup", "findings"} <= tables)
        self.assertEqual(
            conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_store_lives_under_data_insights(self):
        self.assertEqual(
            store.db_path(self.home),
            insights_db_for(self.home))
        self.assertEqual(
            store.db_path(self.home).parent,
            self.home / ".swe" / "data" / "insights")

    def test_a_newer_schema_is_left_alone(self):
        path = store.db_path(self.home)
        path.parent.mkdir(parents=True)
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO meta VALUES ('schema_version', '2')")
        conn.commit()
        conn.close()
        before = path.read_bytes()
        with self.assertRaises(store.InsightsUnavailable):
            store.open_store(path)
        self.assertEqual(path.read_bytes(), before)

    def test_a_torn_file_is_rebuilt(self):
        path = store.db_path(self.home)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"this is not sqlite" * 100)
        conn = self.db()
        meta = dict(conn.execute("SELECT key, value FROM meta"))
        self.assertEqual(meta["schema_version"], "1")


class MetricsTests(_Home):
    def test_counters_round_trip(self):
        conn = self.db()
        store.upsert_counters(conn, metrics())
        conn.commit()
        row = dict(conn.execute(
            "SELECT * FROM session_metrics WHERE session_id='sess-1'"
        ).fetchone())
        self.assertEqual(row["n_guard_refusals"], 3)
        self.assertIsNone(row["outcome"])

    def test_counters_preserve_a_score(self):
        conn = self.db()
        store.upsert_counters(conn, metrics())
        store.upsert_score(conn, "sess-1", "claude", outcome=4,
                           first_pass=1, causes="[]", score_confidence="high")
        store.upsert_counters(conn, metrics(n_messages=20))
        conn.commit()
        row = dict(conn.execute(
            "SELECT * FROM session_metrics WHERE session_id='sess-1'"
        ).fetchone())
        self.assertEqual(row["n_messages"], 20)
        self.assertEqual(row["outcome"], 4)

    def test_score_preserves_counters(self):
        conn = self.db()
        store.upsert_counters(conn, metrics())
        store.upsert_score(conn, "sess-1", "claude", outcome=2,
                           first_pass=0, causes='["E"]',
                           score_confidence="medium")
        store.upsert_score(conn, "sess-1", "claude", outcome=5,
                           first_pass=1, causes="[]", score_confidence="high")
        conn.commit()
        row = dict(conn.execute(
            "SELECT * FROM session_metrics WHERE session_id='sess-1'"
        ).fetchone())
        self.assertEqual(row["n_tool_errors"], 2)
        self.assertEqual(row["outcome"], 5)
        self.assertEqual(row["score_confidence"], "high")

    def test_a_score_for_an_unmeasured_session_still_lands(self):
        conn = self.db()
        store.upsert_score(conn, "ghost", "codex", outcome=3,
                           first_pass=None, causes=None, score_confidence="low")
        conn.commit()
        row = dict(conn.execute(
            "SELECT * FROM session_metrics WHERE session_id='ghost'"
        ).fetchone())
        self.assertEqual(row["n_messages"], 0)
        self.assertEqual(row["outcome"], 3)

    def test_unscored_sessions_is_the_t2_backlog(self):
        conn = self.db()
        store.upsert_counters(conn, metrics("a", first_ts=100))
        store.upsert_counters(conn, metrics("b", first_ts=50))
        store.upsert_score(conn, "c", "claude", outcome=4, first_pass=1,
                           causes="[]", score_confidence="high")
        conn.commit()
        self.assertEqual(store.unscored_sessions(conn), ["b", "a"])

    def test_rollup_upserts_per_key(self):
        conn = self.db()
        store.record_rollup(conn, "2026-11-07", "claude", "score_45", 0.91)
        store.record_rollup(conn, "2026-11-07", "claude", "score_45", 0.92)
        store.record_rollup(conn, "2026-11-07", "devin", "score_45", 0.88)
        conn.commit()
        rows = conn.execute(
            "SELECT harness, value FROM daily_rollup ORDER BY harness")
        self.assertEqual([tuple(r) for r in rows],
                         [("claude", 0.92), ("devin", 0.88)])


if __name__ == "__main__":
    unittest.main()
