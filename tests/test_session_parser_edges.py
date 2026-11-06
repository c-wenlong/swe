"""Edge cases of sessions/parsers.py helpers: the malformed sidecars,
missing stores and odd shapes the per-harness tests never feed them.

Every path is a temp folder; ``os.path.expanduser`` in
``swe.sessions.parsers`` is patched wherever a parser reads ``~``.
"""

import json
import os
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from swe.sessions import parsers

from tests._tempdir import temp_dir


class CopilotWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir(self)

    def meta(self, text, sid="s1"):
        (self.root / sid).mkdir(exist_ok=True)
        (self.root / sid / "workspace.yaml").write_text(text)
        return parsers._copilot_workspace_meta(str(self.root), sid)

    def test_no_session_or_no_root_reads_nothing(self):
        self.assertEqual(parsers._copilot_workspace_meta(str(self.root), ""), (False, ""))
        self.assertEqual(parsers._copilot_workspace_meta("", "s1"), (False, ""))
        self.assertEqual(parsers._copilot_workspace_meta(str(self.root), "missing"), (False, ""))

    def test_either_key_order_and_quoted_names(self):
        self.assertEqual(self.meta("user_named: true\nname: 'Fix login'\nother: x\n"), (True, "Fix login"))
        self.assertEqual(self.meta('name: "Fix login"\nuser_named: FALSE\n'), (False, "Fix login"))

    def test_a_block_scalar_name_at_the_end_of_the_file_is_folded(self):
        self.assertEqual(self.meta("user_named: true\nname: |-\n  Fix the\n\n  login flow\n"),
                         (True, "Fix the login flow"))

    def test_keys_past_the_line_cap_are_not_read(self):
        filler = "".join(f"k{i}: v\n" for i in range(parsers._COPILOT_YAML_MAX_LINES))
        self.assertEqual(self.meta(filler + "user_named: true\nname: late\n"), (False, ""))


class CopilotFallbackTitleTest(unittest.TestCase):
    def test_the_first_checkpoint_names_a_session_with_no_turn(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript("""
            CREATE TABLE turns (session_id TEXT, turn_index INTEGER, user_message TEXT);
            CREATE TABLE checkpoints (session_id TEXT, checkpoint_number INTEGER, title TEXT);
            INSERT INTO turns VALUES ('s1', 0, '');
            INSERT INTO checkpoints VALUES ('s1', 2, 'Second'), ('s1', 1, 'Wire up auth');
        """)
        fields = {}
        parsers._copilot_fallback_title(conn, "s1", fields)
        self.assertEqual(fields, {"title": "Wire up auth"})
        fields = {}
        parsers._copilot_fallback_title(conn, "s2", fields)
        self.assertEqual(fields, {})

    def test_a_store_without_the_tables_leaves_the_title_alone(self):
        fields = {"title": ""}
        parsers._copilot_fallback_title(sqlite3.connect(":memory:"), "s1", fields)
        self.assertEqual(fields, {"title": ""})


class CodexThreadStoreTest(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)

    def db(self, name, columns, rows=()):
        conn = sqlite3.connect(self.home / name)
        conn.execute(f"CREATE TABLE threads ({', '.join(columns)})")
        marks = ", ".join("?" * len(columns))
        conn.executemany(f"INSERT INTO threads VALUES ({marks})", rows)
        conn.commit()
        conn.close()

    def test_the_highest_numbered_store_wins_and_stray_names_are_ignored(self):
        for name in ("state_3.sqlite", "state_12.sqlite", "state_7.sqlite", "state_x.sqlite"):
            (self.home / name).write_bytes(b"")
        self.assertEqual(parsers._codex_state_db(str(self.home)), str(self.home / "state_12.sqlite"))
        self.assertEqual(parsers._codex_state_db(str(self.home / "none")), "")

    def test_a_store_missing_the_columns_or_unreadable_names_nothing(self):
        self.db("state_1.sqlite", ["id", "title"], [("t1", "x")])
        self.assertEqual(parsers._codex_thread_store(str(self.home)), {})
        (self.home / "state_2.sqlite").write_bytes(b"not a database at all" * 10)
        self.assertEqual(parsers._codex_thread_store(str(self.home)), {})
        with mock.patch("swe.sessions.engines.common.open_sqlite_ro", return_value=None):
            self.assertEqual(parsers._codex_thread_store(str(self.home)), {})

    def test_an_old_store_reads_with_defaults_and_skips_blank_ids(self):
        self.db("state_5.sqlite", ["id", "name", "preview", "agent_nickname"], [
            (" T1 ", " Named ", " hi ", None), ("", "x", "y", None), (None, "x", "y", None),
            ("t2", None, None, " scout "), ("t3", 7, "", None)])
        self.assertEqual(parsers._codex_thread_store(str(self.home)), {
            "t1": ("Named", "hi", False, ""),
            "t2": ("", "", False, "scout"),
            "t3": ("", "", False, ""),
        })


class PathHelpersTest(unittest.TestCase):
    def test_tool_paths_dir(self):
        base = temp_dir(self)
        (base / "src").mkdir()
        (base / "src" / "a.py").write_text("x")
        self.assertEqual(parsers._tool_paths_dir([]), "")
        self.assertEqual(parsers._tool_paths_dir(["/abs/a", "rel/b"]), "")  # mixed: no common path
        self.assertEqual(parsers._tool_paths_dir(["", ""]), "")
        self.assertEqual(parsers._tool_paths_dir([str(base / "src" / "a.py")]), str(base / "src"))
        self.assertEqual(parsers._tool_paths_dir([str(base / "src" / "a.py"), str(base / "b.py")]), str(base))
        self.assertEqual(parsers._tool_paths_dir(["/gone/x/a.py", "/gone/x/b.py"]), "/gone/x")
        self.assertEqual(parsers._tool_paths_dir(["/gone/x/a.py"]), "/gone/x")

    def test_under(self):
        self.assertTrue(parsers._under("/w/app", "/w/"))
        self.assertTrue(parsers._under("/w", "/w"))
        self.assertFalse(parsers._under("/work", "/w"))
        self.assertFalse(parsers._under("", "/w"))
        self.assertFalse(parsers._under("/w", ""))


class ForgeContextTitleTest(unittest.TestCase):
    """An untitled Forge conversation reads its first user message."""

    def parse(self, rows):
        db = temp_dir(self) / ".forge.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE conversations (conversation_id TEXT, title TEXT, "
                     "created_at TEXT, updated_at TEXT, context TEXT)")
        conn.executemany("INSERT INTO conversations VALUES (?, ?, '2026-07-01 10:00:00', "
                         "'2026-07-01 11:00:00', ?)", rows)
        conn.commit()
        conn.close()
        with mock.patch("swe.sessions.parsers.os.path.expanduser",
                        side_effect=lambda p: str(db) if p.endswith(".forge.db") else "/home/test"):
            return {s.session_id: s.title for s in parsers.parse_forge()}

    def test_titles_from_the_first_user_message(self):
        user = {"text": {"role": "user", "content": "rename the flag"}}
        context = {"messages": ["noise", {"message": {"text": {"role": "assistant", "content": "hi"}}},
                                user, {"text": {"role": "user", "content": "second"}}]}
        titles = self.parse([
            ("c1", None, json.dumps(context)),
            ("c2", "", "{not json"),
            ("c3", None, None),
            ("c4", None, json.dumps({"messages": None})),
        ])
        self.assertEqual(titles["c1"], "rename the flag")
        for sid in ("c2", "c3", "c4"):
            self.assertNotIn(titles[sid], ("rename the flag", "second"), sid)


class ContinueUntitledTest(unittest.TestCase):
    def parse(self, title, session_file):
        base = temp_dir(self)
        (base / "sessions.json").write_text(json.dumps([{
            "sessionId": "s1", "title": title, "workspaceDirectory": "/w",
            "dateCreated": "2026-07-10T10:00:00.000Z"}]))
        if session_file is not None:
            (base / "s1.json").write_text(session_file)
        with mock.patch("swe.sessions.parsers.os.path.expanduser",
                        side_effect=lambda p: str(base) if p.endswith("sessions") else p):
            [session] = parsers.parse_continue()
        return session.title

    def test_an_untitled_session_takes_its_first_typed_prompt(self):
        history = {"history": ["noise", {"message": "text"},
                               {"message": {"role": "assistant", "content": "hello"}},
                               {"message": {"role": "user", "content": "   "}},
                               {"message": {"role": "user", "content": "add a cache"}}]}
        self.assertEqual(self.parse("Untitled Session", json.dumps(history)), "add a cache")
        self.assertEqual(self.parse("New Chat Session Open", json.dumps(history)), "add a cache")

    def test_a_broken_session_file_keeps_the_index_title(self):
        self.assertEqual(self.parse("Untitled Session", "{broken"), "Untitled Session")
        self.assertEqual(self.parse("Untitled Session", None), "Untitled Session")


class CursorAndFreebuffScanTest(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir(self)

    def test_a_cursor_transcript_stops_at_a_malformed_line(self):
        path = self.dir / "t.jsonl"
        lines = [
            "",
            json.dumps({"role": "user", "message": {"content": "fix the parser"}}),
            json.dumps({"role": "assistant", "message": {"content": [
                {"input": {"path": "/h/proj/a.py"}}, {"input": "not a dict"}, {"input": {"path": "/etc/x"}}]}}),
            "{broken",
            json.dumps({"role": "assistant", "message": {"content": [{"input": {"path": "/h/proj/b.py"}}]}}),
        ]
        path.write_text("\n".join(lines) + "\n")
        title, found = parsers._cursor_scan(str(path), "/h")
        self.assertEqual(found, ["/h/proj/a.py"])
        self.assertIn("fix the parser", title)
        self.assertEqual(parsers._cursor_scan(str(self.dir / "missing.jsonl"), "/h")[1], [])

    def test_freebuff_run_state(self):
        state = self.dir / "run-state.json"
        state.write_text(json.dumps({"sessionState": {"mainAgentState": {
            "initialCwd": "/w", "messageHistory": [
                {"role": "assistant", "content": "hi"}, {"role": "user", "content": ""},
                {"role": "user", "content": "port the tests"}]}}}))
        self.assertEqual(parsers._freebuff_run_state(str(state)), ["/w", "port the tests"])
        state.write_text(json.dumps({"sessionState": {"mainAgentState": {
            "cwd": "/w2", "messageHistory": ["not a message"]}}}))
        self.assertEqual(parsers._freebuff_run_state(str(state)), ["/w2", None])
        state.write_text("[]")
        self.assertEqual(parsers._freebuff_run_state(str(state)), [None, None])
        self.assertEqual(parsers._freebuff_run_state(str(self.dir / "missing.json")), [None, None])


if __name__ == "__main__":
    unittest.main()
