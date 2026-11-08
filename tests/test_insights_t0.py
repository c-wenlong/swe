"""The T0 backlog pass in ``insights/t0.py``: sessions the search index
holds but ``session_metrics`` doesn't (or whose ``source_rev`` moved —
an append or a file rewrite) get measured and upserted — counters only,
so a T2 score on the row survives. Every test builds its index and store
in a throwaway HOME."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from swe.insights import store, t0
from swe.paths import insights_db_for
from swe.sessions import search_index
from swe.sessions import search_writer as writer


def _session(conn, agent, session_id, rows, file_path=None):
    """Insert one indexed session plus its message/fts rows. ``rows`` are
    ``(role, ts, user_text, assistant_text, tool_text)`` tuples; the fts
    rowid mirrors ``messages.id`` as the writers do."""
    file_path = file_path or f"/t/{agent}-{session_id}.jsonl"
    cur = conn.execute(
        "INSERT INTO sessions(agent, session_id, file_path) VALUES (?, ?, ?)",
        (agent, session_id, file_path))
    row_id = cur.lastrowid
    for role, ts, user_text, assistant_text, tool_text in rows:
        cur = conn.execute(
            "INSERT INTO messages(session_row_id, role, ts) VALUES (?, ?, ?)",
            (row_id, role, ts))
        conn.execute(
            "INSERT INTO messages_fts(rowid, user_text, assistant_text,"
            " tool_text) VALUES (?, ?, ?, ?)",
            (cur.lastrowid, user_text, assistant_text, tool_text))
    conn.execute(
        "INSERT OR REPLACE INTO files(path, byte_offset, mtime_ms,"
        " session_row_id) VALUES (?, 0, 1, ?)", (file_path, row_id))
    return row_id


class _Home(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

    def index(self):
        conn = writer.open_index(search_index.index_path(self.home))
        self.addCleanup(conn.close)
        return conn

    def metrics(self, session_id):
        conn = store.open_store(insights_db_for(self.home))
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        row = conn.execute(
            "SELECT * FROM session_metrics WHERE session_id = ?",
            (session_id,)).fetchone()
        return dict(row) if row else None

    def test_no_index_means_no_pass(self):
        stats = t0.run_pass(self.home)
        self.assertEqual(stats, {"indexed": 0, "current": 0,
                                 "measured": 0, "backlog": 0})
        self.assertFalse(insights_db_for(self.home).exists())

    def test_the_backlog_is_measured(self):
        conn = self.index()
        _session(conn, "claude", "s1", [
            ("user", "2026-10-01T10:00:00.000Z", "fix the bug", "", ""),
            ("assistant", "2026-10-01T10:00:01.000Z", "", "on it", ""),
            ("tool", "2026-10-01T10:00:02.000Z", "", "", "Bash: make test"),
            ("tool", "2026-10-01T10:00:03.000Z", "", "",
             "Exit code 1\nCommand timed out after 2m 0s"),
        ])
        _session(conn, "devin", "s2", [
            ("user", "2026-10-01T10:00:00.000Z", "hi", "", ""),
            ("assistant", "2026-10-01T10:00:01.000Z", "", "Hi!", ""),
        ])
        conn.commit()
        stats = t0.run_pass(self.home)
        self.assertEqual(stats["measured"], 2)
        self.assertEqual(stats["indexed"], 2)
        s1 = self.metrics("s1")
        self.assertEqual(s1["harness"], "claude")
        self.assertEqual(s1["n_messages"], 4)
        self.assertEqual(s1["n_tool_calls"], 1)
        self.assertEqual(s1["n_tool_errors"], 1)
        self.assertEqual(s1["n_timeouts"], 1)
        self.assertEqual(s1["first_ts"], 1790848800)
        s2 = self.metrics("s2")
        self.assertEqual(s2["is_probe"], 1)
        self.assertEqual(s2["is_dead"], 0)

    def test_ack_refusal_and_dead_from_index_rows(self):
        conn = self.index()
        _session(conn, "claude", "s1", [
            ("user", None, "yes", "", ""),                 # opener, not an unblock
            ("assistant", None, "", "Want me to push?", ""),
            ("user", None, "go ahead", "", ""),
            ("tool", None, "", "",
             "This agent is isolated in the worktree /w, but this command "
             "is too complex to verify that it stays inside the worktree. "
             "Refusing to run it — a worktree-isolated agent's git "
             "operations must target its own."),
            ("tool", None, "", "", "MCP error -32602: Invalid arguments"),
        ])
        _session(conn, "claude", "s2", [
            ("tool", None, "", "", "Bash: ls"),
            ("user", None, "is anyone there", "", ""),
        ])
        conn.commit()
        t0.run_pass(self.home)
        s1 = self.metrics("s1")
        self.assertEqual(s1["n_ack_unblocks"], 1)
        self.assertEqual(s1["n_guard_refusals"], 1)
        self.assertEqual(s1["n_mcp_errors"], 1)
        s2 = self.metrics("s2")
        self.assertEqual(s2["is_dead"], 1)
        self.assertEqual(s2["is_probe"], 0)

    def test_a_rerun_is_idempotent_and_counts_current(self):
        conn = self.index()
        _session(conn, "claude", "s1", [
            ("user", None, "fix it", "", ""),
            ("assistant", None, "", "ok", ""),
        ])
        conn.commit()
        t0.run_pass(self.home)
        stats = t0.run_pass(self.home)
        self.assertEqual(stats["measured"], 0)
        self.assertEqual(stats["current"], 1)
        self.assertEqual(self.metrics("s1")["n_messages"], 2)

    def test_duplicate_copies_measure_the_owning_file_only(self):
        conn = self.index()
        # an ssh-style directory: the session's own file plus agent side
        # files sharing its session_id.
        _session(conn, "claude", "s1", [
            ("user", None, "fix the bug", "", ""),
            ("assistant", None, "", "on it", ""),
        ], file_path="/t/ssh-s1/s1.jsonl")
        _session(conn, "claude", "s1", [
            ("user", None, "side agent prompt one", "", ""),
            ("user", None, "side agent prompt two", "", ""),
            ("tool", None, "", "", "Bash: ls"),
            ("tool", None, "", "", "Exit code 1"),
        ], file_path="/t/ssh-s1/agent-aaa.jsonl")
        _session(conn, "claude", "s1", [
            ("user", None, "side agent prompt three", "", ""),
        ], file_path="/t/ssh-s1/agent-bbb.jsonl")
        conn.commit()
        t0.run_pass(self.home)
        s1 = self.metrics("s1")
        self.assertEqual(s1["n_messages"], 2)
        self.assertEqual(s1["n_tool_calls"], 0)
        self.assertEqual(s1["n_tool_errors"], 0)

    def test_a_same_count_rewrite_is_still_stale(self):
        conn = self.index()
        old_id = _session(conn, "claude", "s1", [
            ("user", None, "fix it", "", ""),
            ("assistant", None, "", "ok", ""),
        ])
        conn.commit()
        t0.run_pass(self.home)
        self.assertEqual(self.metrics("s1")["n_guard_refusals"], 0)
        # The writer's REPLACE: mint a fresh sessions row (fresh message
        # ids — the old ones are still in the table as orphans) and cut
        # the old sessions row loose. Same session_id, same row count,
        # different rows.
        _session(conn, "claude", "s1", [
            ("tool", None, "", "",
             "This agent is isolated in the worktree /w. Refusing to run it."),
            ("user", None, "again", "", ""),
        ])
        conn.execute("DELETE FROM sessions WHERE id = ?", (old_id,))
        conn.commit()
        stats = t0.run_pass(self.home)
        self.assertEqual(stats["measured"], 1)
        s1 = self.metrics("s1")
        self.assertEqual(s1["n_messages"], 2)
        self.assertEqual(s1["n_guard_refusals"], 1)

    def test_new_rows_make_a_session_stale(self):
        conn = self.index()
        row_id = _session(conn, "claude", "s1", [
            ("user", None, "hi", "", ""),
            ("assistant", None, "", "Hi!", ""),
        ])
        conn.commit()
        t0.run_pass(self.home)
        self.assertEqual(self.metrics("s1")["is_probe"], 1)
        cur = conn.execute(
            "INSERT INTO messages(session_row_id, role, ts) VALUES (?, 'tool', NULL)",
            (row_id,))
        conn.execute(
            "INSERT INTO messages_fts(rowid, tool_text) VALUES (?, 'Bash: ls')",
            (cur.lastrowid,))
        conn.commit()
        stats = t0.run_pass(self.home)
        self.assertEqual(stats["measured"], 1)
        s1 = self.metrics("s1")
        self.assertEqual(s1["n_messages"], 3)
        self.assertEqual(s1["is_probe"], 0)

    def test_a_scored_session_keeps_its_score(self):
        conn = self.index()
        _session(conn, "claude", "s1", [
            ("user", None, "fix it", "", ""),
            ("assistant", None, "", "ok", ""),
        ])
        conn.commit()
        out = store.open_store(insights_db_for(self.home))
        self.addCleanup(out.close)
        store.upsert_score(out, "s1", "claude", outcome=4, first_pass=1,
                           causes="[]", score_confidence="high")
        out.commit()
        t0.run_pass(self.home)
        s1 = self.metrics("s1")
        self.assertEqual(s1["outcome"], 4)
        self.assertEqual(s1["first_pass"], 1)
        self.assertEqual(s1["n_messages"], 2)

    def test_limit_spreads_the_drain(self):
        conn = self.index()
        for i in range(4):
            _session(conn, "claude", f"s{i}", [
                ("user", None, f"task {i}", "", "")])
        conn.commit()
        stats = t0.run_pass(self.home, limit=3)
        self.assertEqual(stats["measured"], 3)
        self.assertEqual(stats["backlog"], 1)
        self.assertEqual(t0.run_pass(self.home)["measured"], 1)


if __name__ == "__main__":
    unittest.main()
