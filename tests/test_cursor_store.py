import hashlib
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.reports.transcripts import read_transcript
from swe.sessions import cursor_store
from swe.sessions.models import Session
from swe.sessions.parsers import parse_cursor
from swe.sessions.status import session_statuses


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | 0x80 if value else byte)
        if not value:
            return bytes(out)


def _field(number: int, payload: bytes) -> bytes:
    return _varint(number << 3 | 2) + _varint(len(payload)) + payload


def _blob_id(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


MESSAGES = [
    {"role": "user", "content": [{"type": "text", "text": "Fix the bug"}]},
    {"role": "assistant", "content": [
        {"type": "reasoning", "text": "let me think"},
        {"type": "text", "text": "Looking into it."},
    ]},
    {"role": "assistant", "content": [
        {"type": "tool-call", "toolName": "Shell", "args": {"command": "ls -la"}},
    ]},
    {"role": "tool", "content": [
        {"type": "tool-result", "toolName": "Shell", "result": "a.py\nb.py"},
    ]},
    {"role": "assistant", "content": [{"type": "text", "text": "All fixed."}]},
]


def _session(sid: str, timestamp: float = 1) -> Session:
    return Session(
        timestamp=timestamp, agent="Cursor", path="/work/project",
        session_id=sid, tool_name="cursor",
    )


class _CursorHome(unittest.TestCase):
    """A throwaway ``~`` whose ``.cursor`` tree the tests fill."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.cursor_home = self.home / ".cursor"
        self.cursor_home.mkdir(parents=True)
        # Captured before the patch: inside it, os.path.expanduser is this
        # fake, so falling back to it would recurse forever.
        real_expanduser = os.path.expanduser

        def fake_expanduser(path: str) -> str:
            if path == "~":
                return str(self.home)
            if path.startswith("~/"):
                return str(self.home / path[2:])
            return real_expanduser(path)

        self._patch = patch("os.path.expanduser", side_effect=fake_expanduser)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def _write_store(self, session_dir, messages, *, base=(), manifest=None,
                     meta_json=None, uri=None) -> Path:
        """A ``store.db``: ``base`` compacted into a chunk chain, ``messages`` live.

        ``meta_json`` writes the ``meta.json`` sidecar; ``manifest`` adds keys
        (``name``, ``createdAt``) to the db's own manifest row.
        """
        session_dir = Path(session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(session_dir / "store.db")
        conn.execute("CREATE TABLE blobs (id TEXT PRIMARY KEY, data BLOB)")
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        ids = []
        for message in list(base) + list(messages):
            data = json.dumps(message).encode()
            bid = _blob_id(data)
            conn.execute("INSERT INTO blobs VALUES (?, ?)", (bid, data))
            ids.append(bid)
        root = b""
        if base:
            chunk = b"".join(
                _field(1, bytes.fromhex(bid)) for bid in ids[: len(base)]
            )
            # Real chains end in an empty newer segment.
            tail = _blob_id(b"")
            conn.execute("INSERT INTO blobs VALUES (?, ?)", (tail, b""))
            chunk += _field(4, bytes.fromhex(tail))
            chunk_id = _blob_id(chunk)
            conn.execute("INSERT INTO blobs VALUES (?, ?)", (chunk_id, chunk))
            root += _field(13, bytes.fromhex(chunk_id))
        for bid in ids[len(base):]:
            root += _field(1, bytes.fromhex(bid))
        if uri:
            root += _field(9, uri.encode())
        root_id = _blob_id(root)
        conn.execute("INSERT INTO blobs VALUES (?, ?)", (root_id, root))
        record = {"agentId": session_dir.name, "latestRootBlobId": root_id}
        record.update(manifest or {})
        # The real store keeps the manifest as hex-encoded JSON text.
        conn.execute("INSERT INTO meta VALUES ('0', ?)",
                     (json.dumps(record).encode().hex(),))
        conn.commit()
        conn.close()
        if meta_json is not None:
            (session_dir / "meta.json").write_text(json.dumps(meta_json))
        return session_dir / "store.db"

    def _acp(self, sid: str, *args, **kwargs) -> Path:
        return self._write_store(self.cursor_home / "acp-sessions" / sid,
                                 *args, **kwargs)

    def _chat(self, sid: str, *args, workspace="wshash", **kwargs) -> Path:
        return self._write_store(
            self.cursor_home / "chats" / workspace / sid, *args, **kwargs)


class StoreDiscoveryTest(_CursorHome):
    def test_session_dirs_cover_acp_and_chats(self):
        self._acp("sid-a", MESSAGES)
        self._chat("sid-c", MESSAGES)
        self.assertEqual(
            set(dict(cursor_store.session_dirs())), {"sid-a", "sid-c"})

    def test_find_store_resolves_both_roots(self):
        acp = self._acp("sid-a", MESSAGES)
        chat = self._chat("sid-c", MESSAGES)
        self.assertEqual(cursor_store.find_store("sid-a"), str(acp))
        self.assertEqual(cursor_store.find_store("sid-c"), str(chat))
        self.assertEqual(cursor_store.find_store("missing"), "")
        self.assertEqual(cursor_store.find_store(""), "")

    def test_dir_without_store_db_is_not_a_session(self):
        only_meta = self.cursor_home / "chats" / "wshash" / "meta-only"
        only_meta.mkdir(parents=True)
        (only_meta / "meta.json").write_text(json.dumps({"title": "x"}))
        self.assertEqual(dict(cursor_store.session_dirs()), {})

    def test_same_id_in_both_roots_yields_acp(self):
        acp = self._acp("sid", MESSAGES)
        self._chat("sid", MESSAGES)
        self.assertEqual(cursor_store.store_index()["sid"], str(acp))
        self.assertEqual(cursor_store.find_store("sid"), str(acp))


class StoreReadTest(_CursorHome):
    def test_messages_follow_manifest_order(self):
        # Blob rows carry no order; the base chain's ids come first, then
        # the root's live segment.
        store = self._acp("sid", MESSAGES[2:], base=MESSAGES[:2])
        got = cursor_store.read_messages(str(store))
        self.assertEqual(
            [m["role"] for m in got],
            ["user", "assistant", "assistant", "tool", "assistant"])
        self.assertEqual(got[0]["content"][0]["text"], "Fix the bug")
        self.assertEqual(got[-1]["content"][0]["text"], "All fixed.")

    def test_last_message_is_the_newest_decodable(self):
        store = self._acp("sid", MESSAGES)
        self.assertEqual(
            cursor_store.last_message(str(store))["content"][0]["text"],
            "All fixed.")

    def test_missing_root_or_garbage_db_answers_empty(self):
        store = self._acp("sid", MESSAGES, manifest={"latestRootBlobId": "nope"})
        self.assertEqual(cursor_store.read_messages(str(store)), [])
        junk = self.cursor_home / "acp-sessions" / "junk"
        junk.mkdir(parents=True)
        (junk / "store.db").write_bytes(b"not a database")
        self.assertEqual(cursor_store.read_messages(str(junk / "store.db")), [])

    def test_session_fields_reads_manifest_and_workspace_uri(self):
        store = self._acp(
            "sid", MESSAGES, uri="file:///work/project%20dir",
            manifest={"name": "Fix login", "createdAt": 1782078208384})
        meta, workspace = cursor_store.session_fields(str(store))
        self.assertEqual(meta["name"], "Fix login")
        self.assertEqual(workspace, "/work/project dir")

    def test_store_stamp_prefers_wal_mtime_and_string_stamps(self):
        session_dir = self.cursor_home / "acp-sessions" / "sid"
        self._write_store(session_dir, MESSAGES)
        # A stamp newer than the file mtimes wins, and meta.json's string
        # form parses like the db manifest's numeric form.
        stamp = cursor_store.store_stamp(
            str(session_dir), {"updatedAtMs": "3000000000000"}, {})
        self.assertEqual(stamp, 3000000000000)
        stamp = cursor_store.store_stamp(
            str(session_dir), {}, {"updatedAt": 3000000000000})
        self.assertEqual(stamp, 3000000000000)
        # Without meta stamps the -wal mtime is the freshest signal: it is
        # the file every write touches.
        wal = session_dir / "store.db-wal"
        wal.write_bytes(b"x")
        os.utime(wal, (2000000000, 2000000000))
        stamp = cursor_store.store_stamp(str(session_dir), {}, {})
        self.assertEqual(stamp, 2000000000 * 1000)


class TranscriptReadTest(_CursorHome):
    def test_store_transcript_normalizes_roles_and_tools(self):
        self._acp("sid-a", MESSAGES)
        t = read_transcript(_session("sid-a"))
        self.assertTrue(t.readable)
        self.assertEqual(
            [m.role for m in t.messages],
            ["human", "assistant", "tool", "tool", "assistant"])
        self.assertIn("Fix the bug", t.messages[0].text)
        self.assertIn("Shell: ls -la", t.messages[2].text)
        self.assertIn("a.py", t.messages[3].text)
        self.assertEqual(t.messages[-1].text, "All fixed.")

    def test_jsonl_transcript_wins_over_store(self):
        self._chat("sid-c", MESSAGES)
        transcripts = (
            self.cursor_home / "projects" / "-work-project"
            / "agent-transcripts" / "sid-c")
        transcripts.mkdir(parents=True)
        (transcripts / "sid-c.jsonl").write_text(
            json.dumps({"role": "user",
                        "message": {"content": "jsonl wins"}}) + "\n")
        t = read_transcript(_session("sid-c"))
        self.assertTrue(t.readable)
        self.assertEqual([m.text for m in t.messages], ["jsonl wins"])

    def test_session_with_neither_format_is_unreadable(self):
        t = read_transcript(_session("missing"))
        self.assertFalse(t.readable)
        self.assertEqual(t.messages, [])
        self.assertIn("not found", t.error)


class ParseCursorTest(_CursorHome):
    def test_acp_session_lists_with_manifest_name_and_meta_cwd(self):
        self._acp(
            "sid-a", MESSAGES,
            manifest={"name": "Fix login bug"},
            meta_json={"schemaVersion": "1", "cwd": "/work/project"})
        sessions = parse_cursor()
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s.session_id, "sid-a")
        self.assertEqual(s.title, "Fix login bug")
        self.assertEqual(s.path, "/work/project")
        self.assertEqual(s.tool_name, "cursor")

    def test_chats_session_lists_with_meta_title_and_uri_cwd(self):
        self._chat(
            "sid-c", MESSAGES,
            uri="file:///work/proj%20one",
            meta_json={"title": "Project Explorer",
                       "updatedAtMs": "3000000000000"})
        sessions = parse_cursor()
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s.title, "Project Explorer")
        self.assertEqual(s.path, "/work/proj one")
        self.assertEqual(s.timestamp, 3000000000000)

    def test_transcript_session_dedupes_its_store_copy(self):
        sid = "sid-both"
        self._acp(sid, MESSAGES, manifest={"name": "store name"})
        transcripts = (
            self.cursor_home / "projects" / "-work-project"
            / "agent-transcripts" / sid)
        transcripts.mkdir(parents=True)
        (transcripts / f"{sid}.jsonl").write_text(
            json.dumps({"role": "user",
                        "message": {"content": "transcript prompt"}}) + "\n")
        sessions = parse_cursor()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].title, "transcript prompt")

    def test_missing_projects_dir_still_lists_stores(self):
        self._acp("sid-a", MESSAGES)
        self.assertEqual(len(parse_cursor()), 1)

    def test_broken_store_still_lists_the_session(self):
        # meta.json alone gives title+path even when the db cannot open.
        session_dir = self.cursor_home / "acp-sessions" / "sid-bad"
        session_dir.mkdir(parents=True)
        (session_dir / "store.db").write_bytes(b"junk")
        (session_dir / "meta.json").write_text(json.dumps(
            {"title": "Broken", "cwd": "/work/p"}))
        sessions = parse_cursor()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].title, "Broken")
        self.assertEqual(sessions[0].path, "/work/p")


class StatusTest(_CursorHome):
    def _status(self, store_messages, timestamp=1, sid="sid"):
        self._acp(sid, store_messages)
        return session_statuses([_session(sid, timestamp)])[0]

    def test_finished_assistant_tail_is_done(self):
        self.assertEqual(self._status(MESSAGES), "done")

    def test_finished_question_is_followup(self):
        messages = MESSAGES[:-1] + [{
            "role": "assistant",
            "content": [{"type": "text", "text": "Want me to keep going?"}],
        }]
        self.assertEqual(self._status(messages), "followup")

    def test_tool_tail_is_interrupted_when_stale(self):
        self.assertEqual(self._status(MESSAGES[:-1]), "interrupted")

    def test_tool_tail_is_active_when_fresh(self):
        status = self._status(MESSAGES[:-1], timestamp=time.time() * 1000)
        self.assertEqual(status, "active")

    def test_user_tail_is_midturn(self):
        self.assertEqual(self._status(MESSAGES[:1]), "interrupted")

    def test_no_transcript_anywhere_is_unknown(self):
        self.assertEqual(session_statuses([_session("ghost")])[0], "")


if __name__ == "__main__":
    unittest.main()
