import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from swe.sessions.models_analytics import (
    _drizzle_model_counts,
    classify_provider,
    collect_model_usage,
)


class ModelsAnalyticsTest(unittest.TestCase):
    @staticmethod
    def _expand_for(root: Path, paths: dict[str, Path]):
        def expand(path: str) -> str:
            return str(paths.get(path, root / "missing"))

        return expand

    def test_classify_openai_models(self):
        self.assertEqual(classify_provider("gpt-4.1"), "openai")
        self.assertEqual(classify_provider("openai/gpt-4o"), "openai")

    def test_classify_anthropic_models(self):
        self.assertEqual(classify_provider("claude-sonnet-4"), "anthropic")
        self.assertEqual(classify_provider("anthropic/claude-opus-4"), "anthropic")

    def test_classify_google_models(self):
        self.assertEqual(classify_provider("gemini-2.5-pro"), "google")

    def test_classify_unknown_returns_other(self):
        self.assertEqual(classify_provider("totally-custom-model"), "other")

    def test_collects_claude_models_without_glob_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_dir = root / "claude-projects"
            project_dir = claude_dir / "-Users-test-project"
            project_dir.mkdir(parents=True)
            (project_dir / "session.jsonl").write_text(
                json.dumps({"model": "claude-sonnet-4"}, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )

            def expand(path: str) -> str:
                if path == "~/.claude/projects/":
                    return str(claude_dir)
                return str(root / "missing")

            with mock.patch(
                "swe.sessions.models_analytics.os.path.expanduser",
                side_effect=expand,
            ), mock.patch(
                "swe.sessions.models_analytics.glob.glob",
                side_effect=AssertionError("Claude traversal must use os.scandir"),
            ):
                usage = collect_model_usage()

            self.assertEqual(usage["claude"][("", "claude-sonnet-4")], 1)

    def test_collects_opencode_models_from_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "opencode.db"
            connection = sqlite3.connect(db)
            connection.execute("CREATE TABLE message (data TEXT)")
            connection.executemany(
                "INSERT INTO message VALUES (?)",
                [
                    (json.dumps({"model": {"providerID": "openai", "modelID": "gpt-5"}}),),
                    (json.dumps({"model": {"providerID": "openai", "modelID": "gpt-5"}}),),
                ],
            )
            connection.commit()
            connection.close()

            with mock.patch(
                "swe.sessions.models_analytics.os.path.expanduser",
                side_effect=self._expand_for(
                    root, {"~/.local/share/opencode/opencode.db": db}
                ),
            ):
                usage = collect_model_usage()

            self.assertEqual(usage["opencode"][("openai", "gpt-5")], 2)

    def test_collects_kilo_models_from_sqlite(self):
        # Kilo is an opencode fork: same message.data model JSON under
        # ~/.local/share/kilo/kilo.db.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "kilo.db"
            connection = sqlite3.connect(db)
            connection.execute("CREATE TABLE message (data TEXT)")
            connection.executemany(
                "INSERT INTO message VALUES (?)",
                [
                    (json.dumps({"model": {"providerID": "xai", "modelID": "grok-4.5"}}),),
                    (json.dumps({"model": {"providerID": "xai", "modelID": "grok-4.5"}}),),
                    (json.dumps({"model": {"providerID": "anthropic", "modelID": "claude-sonnet-4"}}),),
                ],
            )
            connection.commit()
            connection.close()

            with mock.patch(
                "swe.sessions.models_analytics.os.path.expanduser",
                side_effect=self._expand_for(
                    root, {"~/.local/share/kilo/kilo.db": db}
                ),
            ):
                usage = collect_model_usage()

            self.assertEqual(usage["kilo"][("xai", "grok-4.5")], 2)
            self.assertEqual(usage["kilo"][("anthropic", "claude-sonnet-4")], 1)

    def test_collects_cline_models_from_session_metadata(self):
        # Cline 3.x stamps provider/model on <id>.json beside the
        # <id>.messages.json transcript, which must not be counted.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / "cline-sessions"
            s1 = sessions / "s1"
            s1.mkdir(parents=True)
            (s1 / "s1.json").write_text(json.dumps({
                "session_id": "s1", "provider": "xai",
                "model": "grok-4-1-fast-reasoning",
            }))
            (s1 / "s1.messages.json").write_text(json.dumps({
                "sessionId": "s1", "messages": [],
            }))
            s2 = sessions / "s2"
            s2.mkdir(parents=True)
            (s2 / "s2.json").write_text(json.dumps({
                "session_id": "s2", "provider": "anthropic",
                "model": "claude-sonnet-4",
            }))

            with mock.patch(
                "swe.sessions.models_analytics.os.path.expanduser",
                side_effect=self._expand_for(
                    root, {"~/.cline/data/sessions/": sessions}
                ),
            ):
                usage = collect_model_usage()

            self.assertEqual(
                usage["cline"][("xai", "grok-4-1-fast-reasoning")], 1
            )
            self.assertEqual(
                usage["cline"][("anthropic", "claude-sonnet-4")], 1
            )

    def test_collects_codex_models_from_nested_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex = root / "codex"
            session_dir = codex / "2026" / "08" / "01"
            session_dir.mkdir(parents=True)
            (session_dir / "session.jsonl").write_text(
                '{"model":"gpt-5-codex"}\n', encoding="utf-8"
            )

            with mock.patch(
                "swe.sessions.models_analytics.os.path.expanduser",
                side_effect=self._expand_for(root, {"~/.codex/sessions/": codex}),
            ):
                usage = collect_model_usage()

            self.assertEqual(usage["codex"][("", "gpt-5-codex")], 1)

    def test_collects_codex_models_from_archived_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "codex" / "sessions"
            archived = root / "codex" / "archived_sessions"
            archived.mkdir(parents=True)
            (archived / "rollout-old.jsonl").write_text(
                '{"model":"gpt-5-codex"}\n', encoding="utf-8"
            )

            with mock.patch(
                "swe.sessions.models_analytics.os.path.expanduser",
                side_effect=self._expand_for(
                    root,
                    {
                        "~/.codex/sessions/": live,
                        "~/.codex/archived_sessions/": archived,
                    },
                ),
            ):
                usage = collect_model_usage()

            self.assertEqual(usage["codex"][("", "gpt-5-codex")], 1)

    def test_collects_freebuff_models_from_chat_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            freebuff = root / "manicode"
            chat = freebuff / "project" / "chats" / "session"
            chat.mkdir(parents=True)
            (chat / "log.jsonl").write_text(
                '{"model":"claude-opus-4"}\n', encoding="utf-8"
            )

            with mock.patch(
                "swe.sessions.models_analytics.os.path.expanduser",
                side_effect=self._expand_for(
                    root, {"~/.config/manicode/projects/": freebuff}
                ),
            ):
                usage = collect_model_usage()

            self.assertEqual(usage["freebuff"][("", "claude-opus-4")], 1)

    def test_broken_opencode_database_does_not_hide_other_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            broken_db = root / "opencode.db"
            broken_db.write_text("not sqlite", encoding="utf-8")
            freebuff = root / "manicode"
            chat = freebuff / "project" / "chats" / "session"
            chat.mkdir(parents=True)
            (chat / "log.jsonl").write_text('{"model":"gpt-4.1"}\n', encoding="utf-8")

            with mock.patch(
                "swe.sessions.models_analytics.os.path.expanduser",
                side_effect=self._expand_for(
                    root,
                    {
                        "~/.local/share/opencode/opencode.db": broken_db,
                        "~/.config/manicode/projects/": freebuff,
                    },
                ),
            ):
                usage = collect_model_usage()

            self.assertNotIn("opencode", usage)
            self.assertEqual(usage["freebuff"][("", "gpt-4.1")], 1)

    def test_opencode_connection_closes_when_query_fails(self):
        connection = mock.MagicMock()
        connection.cursor.return_value.execute.side_effect = sqlite3.DatabaseError(
            "unsupported schema"
        )

        with mock.patch(
            "swe.sessions.models_analytics.os.path.expanduser",
            side_effect=lambda path: "/tmp/opencode.db" if "opencode" in path else "/missing",
        ), mock.patch(
            "swe.sessions.models_analytics.os.path.exists",
            side_effect=lambda path: path == "/tmp/opencode.db",
        ), mock.patch(
            "swe.sessions.models_analytics.sqlite3.connect", return_value=connection
        ):
            usage = collect_model_usage()

        self.assertEqual(usage, {})
        connection.close.assert_called_once_with()


    def test_opencode_db_is_opened_read_only(self):
        # A read-write connection, as the last one open, checkpoints the
        # WAL into OpenCode's database and deletes its -wal/-shm on close.
        # Copying a live store's three files stands in for OpenCode
        # holding them open.
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live.db")
            writer = sqlite3.connect(live)
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("CREATE TABLE message (id TEXT, data TEXT)")
            writer.execute(
                "INSERT INTO message VALUES ('m1', ?)",
                ('{"model":{"providerID":"p","modelID":"m"}}',))
            writer.commit()
            db = os.path.join(tmp, "opencode.db")
            try:
                for suffix in ("", "-wal", "-shm"):
                    shutil.copyfile(live + suffix, db + suffix)
            finally:
                writer.close()
            size = os.path.getsize(db)
            counts = _drizzle_model_counts(db)
            self.assertEqual(counts, {("p", "m"): 1})
            self.assertEqual(os.path.getsize(db), size)
            self.assertTrue(os.path.exists(db + "-wal"))

    def test_one_unreadable_log_does_not_hide_the_rest(self):
        # One undecodable or unreadable file used to raise out of the
        # whole Claude, Codex or Freebuff scan, dropping every count.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude = root / "claude"
            (claude / "-a").mkdir(parents=True)
            (claude / "-b").mkdir()
            (claude / "-a" / "bad.jsonl").write_bytes(b"\xff\xfe\xfa\n" + b'{"model":"claude-x"}\n')
            (claude / "-b" / "good.jsonl").write_text('{"model":"claude-y"}\n')
            codex = root / "codex-archived"
            codex.mkdir()
            (codex / "locked.jsonl").write_text('{"model":"gpt-z"}\n')
            (codex / "ok.jsonl").write_text('{"model":"gpt-y"}\n')
            real_open = open

            def guarded(path, *args, **kwargs):
                if str(path).endswith("locked.jsonl"):
                    raise PermissionError(path)
                return real_open(path, *args, **kwargs)

            with mock.patch(
                "swe.sessions.models_analytics.os.path.expanduser",
                side_effect=self._expand_for(root, {
                    "~/.claude/projects/": claude, "~/.codex/archived_sessions/": codex}),
            ), mock.patch("builtins.open", side_effect=guarded):
                usage = collect_model_usage()

            self.assertEqual(usage["claude"], {("", "claude-x"): 1, ("", "claude-y"): 1})
            self.assertEqual(usage["codex"], {("", "gpt-y"): 1})

    def test_claude_scan_skips_other_folders_and_files_and_reads_past_the_first_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude = root / "claude"
            project = claude / "-proj"
            project.mkdir(parents=True)
            (claude / "not-a-project").mkdir()
            (claude / "not-a-project" / "s.jsonl").write_text('{"model":"nope"}\n')
            (claude / "-stray.jsonl").write_text('{"model":"nope"}\n')
            (project / "notes.txt").write_text('{"model":"nope"}\n')
            (project / "sub.jsonl").mkdir()
            (project / "s.jsonl").write_text('{"type":"summary"}\n{"model":"claude-z"}\n{"model":"later"}\n')
            (project / "empty.jsonl").write_text("")
            with mock.patch(
                "swe.sessions.models_analytics.os.path.expanduser",
                side_effect=self._expand_for(root, {"~/.claude/projects/": claude}),
            ):
                usage = collect_model_usage()
            self.assertEqual(usage, {"claude": {("", "claude-z"): 1}})

    def test_cline_metadata_that_is_not_a_session_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cline = root / "cline"
            folder = cline / "s1"
            folder.mkdir(parents=True)
            for name, text in (("broken.json", "{oops"), ("list.json", "[1]"),
                               ("other.json", json.dumps({"model": "m"})),
                               ("blank.json", json.dumps({"session_id": "s", "model": ""}))):
                (folder / name).write_text(text)
            with mock.patch(
                "swe.sessions.models_analytics.os.path.expanduser",
                side_effect=self._expand_for(root, {"~/.cline/data/sessions/": cline}),
            ):
                self.assertEqual(collect_model_usage(), {})

    def test_freebuff_skips_files_and_projects_without_chats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            freebuff = root / "manicode"
            (freebuff / "no-chats").mkdir(parents=True)
            (freebuff / "loose-file").write_text("x")
            chats = freebuff / "proj" / "chats"
            (chats / "no-log").mkdir(parents=True)
            (chats / "stray.txt").write_text("x")
            with mock.patch(
                "swe.sessions.models_analytics.os.path.expanduser",
                side_effect=self._expand_for(root, {"~/.config/manicode/projects/": freebuff}),
            ):
                self.assertEqual(collect_model_usage(), {})

    def test_empty_model_ids_are_not_counted_in_either_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "opencode.db"
            conn = sqlite3.connect(db)
            conn.executescript("""
                CREATE TABLE message (id TEXT, data TEXT);
                CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT, seq INTEGER, data TEXT);
            """)
            conn.executemany("INSERT INTO message VALUES (?, ?)", [
                ("a", json.dumps({"model": {"providerID": "x", "modelID": ""}})),
                ("b", json.dumps({"model": {"modelID": "m1"}}))])
            conn.executemany("INSERT INTO session_message VALUES (?, 's', 'assistant', 1, ?)", [
                ("c", json.dumps({"model": {"providerID": "x", "id": ""}})),
                ("d", json.dumps({"model": {"id": "m1"}})),
                ("e", json.dumps({"model": {"providerID": "p", "id": "m2"}}))])
            conn.commit()
            conn.close()
            self.assertEqual(_drizzle_model_counts(str(db)), {("", "m1"): 2, ("p", "m2"): 1})


if __name__ == "__main__":
    unittest.main()
