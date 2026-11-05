import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.sessions.parsers import parse_droid

# Captured before any patch: the patch replaces posixpath.expanduser itself,
# so a fallback through ``os.path.expanduser`` would call the mock again.
REAL_EXPANDUSER = os.path.expanduser


def _parse(sessions_root: Path):
    def fake_expanduser(path: str) -> str:
        if path in ("~/.factory/sessions/", "~/.factory/sessions"):
            return str(sessions_root) + "/"
        return REAL_EXPANDUSER(path)

    with patch("swe.sessions.parsers.os.path.expanduser", side_effect=fake_expanduser):
        return parse_droid()


def _write(path: Path, lines) -> None:
    path.write_text("".join(
        (line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines))


class ParseDroidTest(unittest.TestCase):
    def setUp(self):
        self.sessions_root = Path(self.enterContext(tempfile.TemporaryDirectory())) / "sessions"
        self.project_dir = self.sessions_root / "-Users-dev-project"
        self.project_dir.mkdir(parents=True)

    def test_parse_droid_reads_session_start_and_title(self):
        session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        _write(self.project_dir / f"{session_id}.jsonl", [
            {
                "type": "session_start",
                "id": session_id,
                "title": "Wire droid session support",
                "cwd": "/Users/dev/project",
            },
            {
                "type": "message",
                "message": {
                    "role": "user",
                    "content": [],
                    "hookEventName": "SessionStart",
                    "visibility": "user_only",
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Please implement parse_droid"}],
                },
            },
        ])

        sessions = _parse(self.sessions_root)

        self.assertEqual(len(sessions), 1)
        session = sessions[0]
        self.assertEqual(session.tool_name, "droid")
        self.assertEqual(session.agent, "Droid")
        self.assertEqual(session.session_id, session_id)
        self.assertEqual(session.path, "/Users/dev/project")
        self.assertEqual(session.title, "Wire droid session support")

    def test_parse_droid_falls_back_to_encoded_dir_and_user_message(self):
        session_id = "11111111-2222-3333-4444-555555555555"
        _write(self.project_dir / f"{session_id}.jsonl", [
            {
                "type": "session_start",
                "id": session_id,
                "title": "New Session",
            },
            {
                "type": "message",
                "message": {
                    "role": "user",
                    "content": "Add droid resume support",
                },
            },
        ])

        sessions = _parse(self.sessions_root)

        self.assertEqual(len(sessions), 1)
        session = sessions[0]
        self.assertEqual(session.path, "/Users/dev/project")
        self.assertEqual(session.title, "Add droid resume support")
        self.assertEqual(session.session_id, session_id)

    def test_malformed_lines_are_skipped(self):
        session_id = "99999999-8888-7777-6666-555555555555"
        _write(self.project_dir / f"{session_id}.jsonl", [
            "{not json",
            {"type": "session_start", "id": session_id, "cwd": "/Users/dev/project"},
            "[1, 2",
            {"type": "message", "message": {"role": "user", "content": "Survive junk lines"}},
        ])

        sessions = _parse(self.sessions_root)

        self.assertEqual([s.session_id for s in sessions], [session_id])
        self.assertEqual(sessions[0].title, "Survive junk lines")


if __name__ == "__main__":
    unittest.main()
