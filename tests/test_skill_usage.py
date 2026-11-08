"""`swe skills usage` and the index readers behind it.

A fixture search index carries ``tool_text`` rows for ``Skill: <name>``
calls, a bare ``Skill`` row from before the ``skill`` input key existed,
``mcp__<server>__<tool>`` calls, and text that merely mentions "skill"
without being a call — the readers must count the call shapes only.
"""

import json
import sqlite3
import unittest

from swe.sessions import search_index
from swe.sessions.search_decode import tool_call_text
from swe.skills import usage
from tests._tempdir import temp_dir
from tests.test_json_output import decode, run_swe


_DDL = """\
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sessions(id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT NOT NULL,
  session_id TEXT NOT NULL, file_path TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
  cwd TEXT, cwd_key TEXT, created_at TEXT, updated_at TEXT, message_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE files(path TEXT PRIMARY KEY, dev INTEGER, ino INTEGER, byte_offset INTEGER NOT NULL,
  mtime_ms REAL NOT NULL, size_bytes INTEGER, session_row_id INTEGER,
  state TEXT NOT NULL DEFAULT 'current', fail_count INTEGER NOT NULL DEFAULT 0, failed_mtime_ms REAL);
CREATE TABLE messages(id INTEGER PRIMARY KEY, session_row_id INTEGER NOT NULL, role TEXT NOT NULL, ts TEXT);
CREATE INDEX messages_session ON messages(session_row_id);
CREATE VIRTUAL TABLE messages_fts USING fts5(user_text, assistant_text, tool_text, identifiers,
  tokenize="unicode61 tokenchars '_.-/+'", detail=full);
"""

_SESSIONS = [(1, "claude", "s-a"), (2, "codex", "s-b")]
# (msg id, session_row_id, ts, tool_text)
_MESSAGES = [
    (1, 1, "2026-10-01T10:00:00Z", "Skill: pdf"),
    (2, 1, "2026-10-02T10:00:00Z", "Skill: pdf"),
    (3, 2, "2026-10-03T10:00:00Z", "Skill: pdf"),
    (4, 1, "2026-10-02T11:00:00Z", "Skill"),          # pre-key row, no name
    (5, 1, "2026-10-02T12:00:00Z", "Skill: other"),
    (6, 2, "2026-10-03T11:00:00Z", "mcp__github__create_issue"),
    (7, 2, "2026-10-03T12:00:00Z", "mcp__github__list_prs"),
    (8, 2, "2026-10-03T13:00:00Z", "mcp__github2__x"),
    (9, 1, "2026-10-04T10:00:00Z", "Bash: skill list"),   # mentions, not a call
    (10, 1, "2026-10-04T11:00:00Z", "mcp__ghub__x"),      # near-prefix, not github
]


def _index(home) -> None:
    path = search_index.index_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_DDL)
        conn.execute("INSERT INTO meta VALUES ('schema_version', '1')")
        for row_id, agent, sid in _SESSIONS:
            conn.execute(
                "INSERT INTO sessions(id, agent, session_id, file_path)"
                " VALUES (?, ?, ?, ?)", (row_id, agent, sid, str(home / sid)))
        for msg_id, srid, ts, text in _MESSAGES:
            conn.execute("INSERT INTO messages VALUES (?, ?, 'assistant', ?)",
                         (msg_id, srid, ts))
            conn.execute("INSERT INTO messages_fts(rowid, tool_text)"
                         " VALUES (?, ?)", (msg_id, text))
        conn.commit()
    finally:
        conn.close()


class _Indexed(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        _index(self.home)


class TestSkillUsage(_Indexed):
    def test_counts(self):
        got = usage.skill_usage("pdf", self.home)
        self.assertTrue(got["tracked"])
        self.assertEqual(got["calls"], 3)
        self.assertEqual(got["sessions"], 2)
        self.assertEqual(got["last_used"], "2026-10-03T10:00:00Z")
        self.assertEqual(got["by_harness"], {"claude": 2, "codex": 1})

    def test_unattributed(self):
        got = usage.skill_usage("pdf", self.home)
        self.assertEqual(got["unattributed"], 1)

    def test_other_skill_not_counted(self):
        got = usage.skill_usage("nope", self.home)
        self.assertEqual(got["calls"], 0)
        self.assertEqual(got["unattributed"], 1)

    def test_mention_is_not_a_call(self):
        # "Bash: skill list" carries the skill token but is not `Skill: x`.
        got = usage.skill_usage("list", self.home)
        self.assertEqual(got["calls"], 0)


class TestMcpUsage(_Indexed):
    def test_prefix_counts(self):
        got = usage.mcp_usage("github", self.home)
        self.assertEqual(got["calls"], 2)
        self.assertEqual(got["by_harness"], {"codex": 2})

    def test_prefix_is_exact(self):
        # `mcp__github2__` must not bleed into `mcp__github__`.
        got = usage.mcp_usage("ghub", self.home)
        self.assertEqual(got["calls"], 1)


class TestPluginUsage(_Indexed):
    def test_rolls_up_servers_and_skills(self):
        got = usage.plugin_usage(["github"], ["pdf"], self.home)
        self.assertEqual(got["calls"], 5)
        self.assertEqual(got["skill_calls"], 3)
        self.assertEqual(got["mcp_calls"], 2)
        self.assertEqual(got["sessions"], 2)
        self.assertEqual(got["by_harness"], {"claude": 2, "codex": 3})

    def test_skills_only(self):
        got = usage.plugin_usage([], ["pdf"], self.home)
        self.assertEqual(got["calls"], 3)
        self.assertEqual(got["mcp_calls"], 0)


class TestUntracked(unittest.TestCase):
    def test_no_index(self):
        home = temp_dir(self)
        for fn in (usage.skill_usage, usage.mcp_usage):
            self.assertEqual(fn("x", home), {"tracked": False})
        self.assertEqual(usage.plugin_usage([], ["x"], home), {"tracked": False})

    def test_wrong_schema_version(self):
        home = temp_dir(self)
        _index(home)
        path = search_index.index_path(home)
        conn = sqlite3.connect(path)
        conn.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
        conn.commit()
        conn.close()
        self.assertEqual(usage.skill_usage("pdf", home), {"tracked": False})


class TestDecode(unittest.TestCase):
    def test_skill_key_reaches_tool_text(self):
        self.assertEqual(tool_call_text("Skill", {"skill": "pdf"}), "Skill: pdf")

    def test_stronger_key_wins_over_skill(self):
        # "skill" sits last in _TOOL_INPUT_KEYS so a call carrying both
        # keeps its existing argument.
        self.assertEqual(tool_call_text("x", {"command": "ls", "skill": "pdf"}),
                         "x: ls")


class TestUsageCommand(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        _index(self.home)

    def test_json(self):
        proc = run_swe(self.home, "skills", "usage", "pdf", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = decode(proc)["data"]
        self.assertEqual(got["skill"], "pdf")
        self.assertEqual(got["calls"], 3)
        self.assertEqual(got["by_harness"]["codex"], 1)
        self.assertEqual(got["unattributed"], 1)

    def test_json_untracked(self):
        proc = run_swe(temp_dir(self), "skills", "usage", "pdf", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(decode(proc)["data"], {"action": "usage", "skill": "pdf",
                                                "tracked": False})

    def test_missing_name(self):
        proc = run_swe(self.home, "skills", "usage", "--json")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertIn("Usage:", proc.stderr)

    def test_human(self):
        proc = run_swe(self.home, "skills", "usage", "pdf")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("3 calls", proc.stdout)
        self.assertIn("codex", proc.stdout)
        self.assertIn("without a skill name", proc.stdout)

    def test_human_untracked(self):
        proc = run_swe(temp_dir(self), "skills", "usage", "pdf")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("index --on", proc.stdout)


class UsageCommandInProcessTest(unittest.TestCase):
    """In-process passes through ``cmd_skills_usage``: the ``run_swe``
    tests above run a subprocess coverage cannot see, so each printed
    branch gets a direct call here with ``skill_usage`` faked."""

    def _run(self, args, reply):
        import io
        import sys
        from contextlib import redirect_stderr, redirect_stdout
        from unittest.mock import patch
        from swe.skills.layout_commands import cmd_skills_usage

        out, err = io.StringIO(), io.StringIO()
        with patch("swe.skills.usage.skill_usage", return_value=reply), \
                redirect_stdout(out), redirect_stderr(err):
            code = cmd_skills_usage(list(args))
        return code, out.getvalue(), err.getvalue()

    _FULL = {"tracked": True, "calls": 3, "sessions": 2,
             "last_used": "2026-10-03T10:00:00Z",
             "by_harness": {"claude": 2, "codex": 1}, "unattributed": 1}

    def test_help(self):
        code, _, _ = self._run(["--help"], {})
        self.assertEqual(code, 0)

    def test_usage_error_json_goes_to_stderr(self):
        code, out, err = self._run(["a", "b", "--json"], {})
        self.assertEqual(code, 1)
        self.assertEqual(out.strip(), "")
        self.assertIn("Usage:", err)

    def test_json_reply(self):
        code, out, _ = self._run(["pdf", "--json"], self._FULL)
        self.assertEqual(code, 0)
        data = json.loads(out)["data"]
        self.assertEqual(data["calls"], 3)
        self.assertEqual(data["action"], "usage")

    def test_human_full(self):
        code, out, _ = self._run(["pdf"], self._FULL)
        self.assertEqual(code, 0)
        self.assertIn("3 calls", out)
        self.assertIn("last used 2026-10-03", out)
        self.assertIn("claude", out)
        self.assertIn("without a skill name", out)

    def test_human_zero_calls_and_untracked(self):
        code, out, _ = self._run(
            ["pdf"], {"tracked": True, "calls": 0, "sessions": 0,
                      "last_used": None, "by_harness": {}, "unattributed": 0})
        self.assertEqual(code, 0)
        self.assertIn("No calls recorded yet", out)
        code, out, _ = self._run(["pdf"], {"tracked": False})
        self.assertEqual(code, 0)
        self.assertIn("not tracked", out)


if __name__ == "__main__":
    unittest.main()
