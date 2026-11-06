"""sessions/opencode_store.py on hand-built stores: the shapes a 2.x-only
database, a store with no legacy ``message`` table and rows that don't
decode take, which the migrated fixture in test_opencode_v2 never has."""

from __future__ import annotations

import json
import sqlite3
import unittest

from swe.sessions import opencode_store as store

_V2 = """
CREATE TABLE session_v2 (id TEXT, title TEXT, directory TEXT, time_updated INTEGER);
CREATE TABLE session_message (
  id TEXT, session_id TEXT, type TEXT, seq INTEGER, time_created INTEGER, data TEXT);
"""


def _connect(script: str = _V2) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(script)
    return conn


def _add(conn, seq, kind, data, session="s1"):
    raw = data if isinstance(data, str) else json.dumps(data)
    conn.execute("INSERT INTO session_message VALUES (?, ?, ?, ?, ?, ?)",
                 (f"m{seq}", session, kind, seq, 1000 + seq, raw))


class TestShapes(unittest.TestCase):
    def test_a_2x_only_store_lists_sessions_from_session_v2(self):
        conn = _connect()
        conn.execute("INSERT INTO session_v2 VALUES ('s1', 'Fix it', '/w', 5)")
        rows = conn.execute(store.sessions_query(conn)).fetchall()
        self.assertEqual(rows, [(5, "Fix it", "/w", "s1")])

    def test_no_legacy_message_table_filters_nothing(self):
        conn = _connect()
        self.assertEqual(store._not_legacy(conn), "")
        self.assertEqual(store.v2_shape(conn), "")
        self.assertIsNone(store.v2_shape(_connect("CREATE TABLE message (id TEXT);")))

    def test_a_bad_table_name_reads_as_no_columns(self):
        conn = _connect()
        self.assertEqual(store.table_columns(conn, "session_message; DROP"), set())
        self.assertFalse(store.has_v2_messages(_connect("CREATE TABLE session_message (id TEXT);")))


class TestV2Rows(unittest.TestCase):
    def setUp(self):
        self.conn = _connect()
        for seq in range(1, 6):
            _add(self.conn, seq, "user", {"n": seq})

    def test_newest_keeps_the_last_rows_oldest_first(self):
        rows = store.v2_rows(self.conn, "s1", newest=2)
        self.assertEqual([r[2] for r in rows], [4, 5])
        rows = store.v2_rows(self.conn, "s1", after_seq=1, newest=10)
        self.assertEqual([r[2] for r in rows], [2, 3, 4, 5])

    def test_rows_that_do_not_decode_to_an_object_are_dropped(self):
        _add(self.conn, 6, "assistant", "{broken")
        _add(self.conn, 7, "assistant", "[1, 2]")
        _add(self.conn, 8, None, {"ok": True})
        rows = store.v2_rows(self.conn, "s1", after_seq=5)
        self.assertEqual([(r[1], r[2], r[4]) for r in rows], [("", 8, {"ok": True})])

    def test_a_store_without_v2_messages_has_no_rows(self):
        self.assertEqual(store.v2_rows(_connect("CREATE TABLE message (id TEXT);"), "s1"), [])


class TestText(unittest.TestCase):
    def test_last_text_skips_empty_and_non_text_blocks(self):
        data = {"content": [{"type": "text", "text": "first"}, "noise",
                            {"type": "text", "text": ""}, {"type": "tool", "text": "x"}]}
        self.assertEqual(store.last_text(data), "first")
        self.assertEqual(store.last_text({"content": [{"type": "text", "text": 3}]}), "")
        self.assertEqual(store.last_text({"content": "not a list"}), "")


class TestNewestPhase(unittest.TestCase):
    def setUp(self):
        self.conn = _connect()

    def phase(self, window=10):
        return store.newest_phase(self.conn, "s1", window, store.v2_shape(self.conn))

    def test_idle_after_a_prompt_with_no_reply_closes_with_no_text(self):
        _add(self.conn, 1, "assistant", {"finish": "stop", "content": [{"type": "text", "text": "old"}]})
        _add(self.conn, 2, "user", {})
        _add(self.conn, 3, "idle", {"outcome": "succeeded"})
        self.assertEqual(self.phase(), ("finished", ""))

    def test_idle_with_nothing_before_it_closes_with_no_text(self):
        _add(self.conn, 1, "idle", {"outcome": "succeeded"})
        self.assertEqual(self.phase(), ("finished", ""))

    def test_a_decisive_row_that_is_not_an_object_is_skipped(self):
        _add(self.conn, 1, "assistant", {"finish": "stop", "content": [{"type": "text", "text": "done"}]})
        _add(self.conn, 2, "user", "[]")
        self.assertEqual(self.phase(), ("finished", "done"))

    def test_nothing_decisive_in_the_window_decides_nothing(self):
        _add(self.conn, 1, "assistant", {"finish": "stop"})
        _add(self.conn, 2, "system", {})
        self.assertEqual(self.phase(window=1), (None, ""))
        self.assertEqual(store.newest_phase(self.conn, "s1", 10, None), (None, ""))


if __name__ == "__main__":
    unittest.main()
