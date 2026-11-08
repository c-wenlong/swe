"""sessions/search_containers.py on hand-built stores: the schema refusals,
older OpenCode shapes, malformed rows and the Devin append that has to
reach below the span for a call's name. The writer-level suites in
test_search_harnesses cover the common paths."""

from __future__ import annotations

import json
import sqlite3
import unittest

from swe.sessions import search_containers as containers
from swe.sessions import search_decode as decode

from tests.test_search_harnesses import _DEVIN_SCHEMA


def _connect(script: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(script)
    return conn


def _texts(messages):
    return [(m.role, m.text) for m in messages]


_OPENCODE_LEGACY = """
CREATE TABLE session (id, time_created INTEGER, time_updated INTEGER);
CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, data TEXT);
CREATE TABLE part (id TEXT, message_id TEXT, time_created INTEGER, data TEXT);
"""


class OpenCodeSessionsTest(unittest.TestCase):
    def test_a_store_without_the_tables_is_a_schema_error(self):
        with self.assertRaises(containers.SchemaError):
            containers.opencode_sessions(_connect("CREATE TABLE session (id TEXT);"))
        with self.assertRaises(containers.SchemaError):
            containers.devin_sessions(_connect("CREATE TABLE sessions (id TEXT);"))

    def test_a_1x_store_with_no_title_directory_or_v2_tables(self):
        conn = _connect(_OPENCODE_LEGACY)
        conn.executemany("INSERT INTO session VALUES (?, ?, ?)", [
            ("s1", 1000, 0), ("s2", 500, 3000), (7, 9000, 9000)])
        conn.execute("INSERT INTO message VALUES ('m1', 's1', 1000, '{}')")
        sessions = containers.opencode_sessions(conn)
        self.assertEqual([(s.id, s.title, s.directory, s.activity_ms, s.count) for s in sessions],
                         [("s2", "", None, 3000, 0), ("s1", "", None, 1000, 1)])

    def test_column_ints_read_as_sqlite_would(self):
        self.assertEqual([containers._int(v) for v in (None, True, "5", 2.9, 7)], [0, 0, 0, 2, 7])


class OpenCodeMessagesTest(unittest.TestCase):
    def setUp(self):
        self.conn = _connect(_OPENCODE_LEGACY + """
            CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT, seq INTEGER,
              time_created INTEGER, data TEXT);
        """)
        self.session = containers.OpenCodeSession("s1", "", None, 1000, 1000, 0)

    def message(self, mid, role, created, *parts):
        self.conn.execute("INSERT INTO message VALUES (?, 's1', ?, ?)",
                          (mid, created, json.dumps({"role": role})))
        for i, part in enumerate(parts):
            self.conn.execute("INSERT INTO part VALUES (?, ?, ?, ?)",
                              (f"{mid}-{i}", mid, created + i,
                               part if isinstance(part, str) else json.dumps(part)))

    def v2(self, seq, kind, data):
        self.conn.execute("INSERT INTO session_message VALUES (?, 's1', ?, ?, ?, ?)",
                          (f"v{seq}", kind, seq, 2000 + seq,
                           data if isinstance(data, str) else json.dumps(data)))

    def test_legacy_parts_that_carry_nothing_are_skipped(self):
        self.message("m1", "user", 1000, {"type": "text", "text": "find the cache"},
                     {"type": "text", "text": 5})
        self.message("m2", "assistant", 1100,
                     {"type": "tool", "tool": "read", "state": {"input": {"filePath": "/w/a.py"},
                                                                 "error": "  ", "output": "body"}},
                     {"type": "tool", "tool": "bash", "state": {"error": "denied"}},
                     {"type": "tool", "tool": "x", "state": "broken"})
        self.message("m3", "assistant", 1200, {"type": "reasoning", "text": "thinking"})
        texts = _texts(containers.opencode_messages(self.conn, self.session))
        self.assertEqual(texts[0], (decode.USER, "find the cache"))
        tools = [t for r, t in texts if r == decode.TOOL]
        self.assertEqual(len(tools), 3)
        self.assertIn("/w/a.py", tools[0])
        self.assertTrue(tools[0].endswith("body"))
        self.assertTrue(tools[1].endswith("denied"))
        self.assertEqual(texts[-1], (decode.ASSISTANT, "thinking"))

    def test_v2_rows_that_carry_nothing_are_skipped(self):
        self.v2(1, "user", "{broken")
        self.v2(2, "user", {"text": 3})
        self.v2(3, "synthetic", {"text": "port the tests"})
        self.v2(4, "assistant", {"content": [{"type": "text", "text": "kept"}, "not a block"]})
        self.v2(5, "assistant", {"content": [
            {"type": "text", "text": None}, {"type": "image"},
            {"type": "tool", "name": "shell", "state": {"input": {"command": "ls"},
                                                       "content": [{"type": "text", "text": "a.py"}, {"x": 1}]}},
            {"type": "tool", "name": "shell", "state": {"input": {"command": "pwd"}, "output": "/w",
                                                       "content": [{"text": "ignored"}]}},
            {"type": "tool", "name": "noop", "state": {"content": ["not an object"]}},
        ]})
        texts = _texts(containers._opencode_v2_messages(self.conn, self.session))
        self.assertEqual(texts[0], (decode.USER, "port the tests"))
        self.assertNotIn((decode.ASSISTANT, "kept"), texts)  # one non-object block voids the list
        tools = [t for r, t in texts if r == decode.TOOL]
        self.assertTrue(tools[0].endswith("a.py"))
        self.assertTrue(tools[1].endswith("/w"))
        self.assertNotIn("ignored", "".join(tools))

    def test_a_store_without_session_message_has_no_v2_turns(self):
        conn = _connect(_OPENCODE_LEGACY)
        self.assertEqual(containers._opencode_v2_messages(conn, self.session), [])

    def test_json_objects_and_outcomes(self):
        self.assertIsNone(containers._json_object(None))
        self.assertIsNone(containers._json_object("[1]"))
        self.assertIsNone(containers._json_object("{"))
        self.assertIsNone(containers._tool_outcome(None))
        self.assertIsNone(containers._tool_outcome({"output": 3}))


class DevinTest(unittest.TestCase):
    def setUp(self):
        self.conn = _connect(_DEVIN_SCHEMA)

    def node(self, sid, node, parent, message):
        self.conn.execute("INSERT INTO message_nodes(session_id, node_id, parent_node_id, chat_message,"
                          " created_at) VALUES (?, ?, ?, ?, 0)",
                          (sid, node, parent, message if isinstance(message, str) else json.dumps(message)))

    def test_a_session_row_with_a_blank_id_is_not_listed(self):
        for sid in ("", "s1"):
            self.conn.execute("INSERT INTO sessions(id, working_directory, created_at, last_activity_at)"
                              " VALUES (?, '/w', 1, 2)", (sid,))
        self.assertEqual([s.id for s in containers.devin_sessions(self.conn)], ["s1"])

    def test_tool_call_ids_and_names_from_raw_records(self):
        self.assertIsNone(containers._tool_call_id(None))
        self.assertIsNone(containers._tool_call_id(b"{broken"))
        self.assertIsNone(containers._tool_call_id(b'{"role": "user"}'))
        self.assertEqual(containers._tool_call_id(b'{"role": "tool", "tool_call_id": "c1"}'), "c1")
        self.assertEqual(containers._call_names(None), {})

    def test_an_append_names_a_call_assigned_below_the_last_read_node(self):
        call = {"role": "assistant", "content": "Looking.",
                "tool_calls": [{"id": "c1", "name": "exec", "arguments": {"command": "ls"}}]}
        self.node("s1", 0, None, {"role": "user", "content": "list it"})
        self.node("s1", 1, 0, call)
        self.node("s1", 2, 1, {"role": "assistant", "content": "Still going."})
        self.conn.execute("INSERT INTO sessions(id, working_directory, created_at, last_activity_at,"
                          " main_chain_id) VALUES ('s1', '/w', 1, 2, 2)")
        [session] = containers.devin_sessions(self.conn)
        first = containers.devin_read(self.conn, session)
        self.assertFalse(first.appended)
        self.node("s1", 3, 2, {"role": "tool", "content": "a.py", "tool_call_id": "c1"})
        self.node("s1", 4, 3, {"role": "tool", "content": "b.py", "tool_call_id": "c9"})
        self.conn.execute("UPDATE sessions SET main_chain_id = 4")
        [session] = containers.devin_sessions(self.conn)
        read = containers.devin_read(self.conn, session, first.mark)
        self.assertTrue(read.appended)
        self.assertEqual(_texts(read.messages), [(decode.TOOL, "→ exec: a.py"), (decode.TOOL, "b.py")])


if __name__ == "__main__":
    unittest.main()
