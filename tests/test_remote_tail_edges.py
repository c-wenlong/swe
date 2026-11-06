"""tail.py edges: malformed records, cursors that don't fit the file,
stores that vanish or fail mid-read, and callbacks that raise."""

import base64
import hashlib
import json
import os
import sqlite3
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from swe.remote import tail
from swe.sessions.models import Session

from tests._tempdir import temp_dir


def _user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def _line(rec) -> bytes:
    return (json.dumps(rec) + "\n").encode()


class TestDecoderEdges(unittest.TestCase):
    def test_timestamps_of_every_shape(self):
        self.assertEqual(tail._rec_ts({"ts": 1_700_000_000_000}),
                         1_700_000_000.0)
        self.assertEqual(tail._rec_ts({"ts": 1_700_000_000}), 1_700_000_000.0)
        # A field of the wrong type or an unparseable string moves on to
        # the next candidate key instead of answering None.
        self.assertEqual(
            tail._rec_ts({"timestamp": ["x"], "time": "not a date",
                          "created_at": 12.5}), 12.5)
        self.assertIsNone(tail._rec_ts({"timestamp": True}))
        self.assertIsNone(tail._rec_ts({}))

    def test_summarize_args_odd_inputs(self):
        # A JSON string that decodes to a scalar stays the string.
        self.assertEqual(tail._summarize_args("Bash", "42"), ("Bash", '"42"'))
        # Something json can't encode falls back to str().
        summary, full = tail._summarize_args("Tool", {1, 2})
        self.assertEqual(summary, "Tool")
        self.assertIn("1", full)
        self.assertEqual(tail._summarize_args("Tool", None), ("Tool", ""))
        summary, _ = tail._summarize_args(
            "Read", json.dumps({"file_path": "/a/b.py"}))
        self.assertEqual(summary, "Read · /a/b.py")

    def test_blocks_of_unknown_shapes(self):
        self.assertEqual(tail._decode_blocks("user", 42, None), [])
        self.assertEqual(tail._decode_blocks("user", None, None), [])
        out = tail._decode_blocks("assistant", ["plain", {"type": "text",
                                                          "text": "more"}],
                                  None)
        self.assertEqual([m["kind"] for m in out], ["assistant"])
        self.assertIn("plain", out[0]["text"])
        self.assertIn("more", out[0]["text"])

    def test_claude_records_that_show_nothing_or_system_text(self):
        meta = dict(_user("caveat"), isMeta=True)
        self.assertEqual(tail.decode_record("claude", meta), [])
        out = tail.decode_record(
            "claude", {"type": "system", "content": "hook ran"})
        self.assertEqual([(m["kind"], m["text"]) for m in out],
                         [("system", "hook ran")])
        self.assertEqual(tail.decode_record("claude", {"type": "system"}), [])
        self.assertEqual(
            tail.decode_record("claude", {"type": "summary"}), [])
        self.assertEqual(tail.decode_record("claude", {"type": "other"}), [])

    def test_codex_compaction_records(self):
        for rtype, ptype in (("response_item", "compacted"),
                             ("event_msg", "context_compacted")):
            out = tail.decode_record("codex", {
                "type": rtype, "payload": {"type": ptype}})
            self.assertEqual([m["kind"] for m in out], ["compaction"], rtype)
        self.assertEqual(tail.decode_record("codex", {
            "type": "event_msg", "payload": {"type": "token_count"}}), [])
        self.assertEqual(tail.decode_record("codex", {"type": "other"}), [])

    def test_generic_nested_message_without_blocks(self):
        rec = {"message": {"role": "user", "content": "hello"}}
        out = tail.decode_record("droid", rec)
        self.assertEqual([m["kind"] for m in out], ["user"])
        # Blocks that decode to nothing fall through to the normalizer.
        rec = {"message": {"role": "user", "content": [{"type": "text",
                                                         "text": ""}]},
               "text": "fallback"}
        self.assertIsInstance(tail.decode_record("droid", rec), list)

    def test_tool_messages_from_the_normalizer(self):
        msg = SimpleNamespace(role="tool", text="Bash: ls -la", timestamp=5.0)
        out = tail._normalized_to_payload(msg)
        self.assertEqual(out["kind"], "tool_use")
        self.assertEqual(out["tool"]["name"], "Bash")
        self.assertEqual(out["tool"]["args_summary"], "Bash: ls -la")
        blank = SimpleNamespace(role="tool", text=": x", timestamp=None)
        self.assertEqual(tail._normalized_to_payload(blank)["tool"]["name"],
                         "tool")

    def test_a_decoder_that_raises_drops_the_record(self):
        with patch.dict(tail._DECODERS,
                        {"claude": lambda rec: 1 / 0}):
            self.assertEqual(tail.decode_record("claude", _user("x")), [])


class TestJsonlRestoreEdges(unittest.TestCase):
    def setUp(self):
        self.path = temp_dir(self) / "t.jsonl"
        self.path.write_bytes(_line(_user("one")) + _line(_user("two")))
        self.size = self.path.stat().st_size

    def _src(self):
        return tail.JsonlTailSource(self.path, "claude")

    def _state(self, **kw):
        return dict({"source": "jsonl", "path": str(self.path)}, **kw)

    def test_bad_offset_reads_as_zero(self):
        src = self._src()
        self.assertTrue(src.restore(self._state(offset="nan", since=True)))
        self.assertEqual(src.offset, 0)
        # A bool is not a timestamp.
        self.assertIsNone(src._since)

    def test_undecodable_head_falls_back_to_the_legacy_check(self):
        src = self._src()
        self.assertTrue(src.restore(self._state(offset=5, head="abc")))
        self.assertEqual(src.offset, 5)

    def test_legacy_cursor_with_a_matching_head_hash(self):
        head = self.path.read_bytes()[:16]
        src = self._src()
        self.assertTrue(src.restore(self._state(
            offset=self.size, since=3.0, head_len=16,
            head_sha=hashlib.sha256(head).hexdigest())))
        self.assertEqual((src.offset, src._head, src._since),
                         (self.size, head, 3.0))
        self.assertEqual(src.poll(), [])

    def test_legacy_cursor_for_a_rewritten_file_is_refused(self):
        src = self._src()
        self.assertFalse(src.restore(self._state(
            offset=self.size, head_len=16,
            head_sha=hashlib.sha256(b"x" * 16).hexdigest())))
        self.assertEqual(src.offset, 0)
        # And one whose file is now shorter than the sampled head.
        self.assertFalse(src.restore(self._state(
            offset=self.size, head_len=self.size + 100,
            head_sha=hashlib.sha256(b"").hexdigest())))
        # A head length that isn't a number leaves nothing to verify.
        self.assertTrue(src.restore(self._state(
            offset=self.size, head_len="many", head_sha="abc")))
        self.assertEqual(src.offset, self.size)

    def test_stored_head_that_no_longer_matches_is_refused(self):
        src = self._src()
        head = base64.b64encode(b"something else").decode()
        self.assertFalse(src.restore(self._state(offset=self.size,
                                                 head=head)))

    def test_cursor_for_another_file_or_source_is_refused(self):
        src = self._src()
        self.assertFalse(src.restore({"source": "jsonl", "path": "/x"}))
        self.assertFalse(src.restore({"source": "opencode",
                                      "path": str(self.path)}))


class TestJsonlPlacementEdges(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir(self)
        self.path = self.dir / "t.jsonl"

    def test_placing_on_a_missing_file_is_harmless(self):
        src = tail.JsonlTailSource(self.path, "claude")
        src.skip_to_end()
        self.assertEqual((src.offset, src._head), (0, b""))
        src.start_near_end()
        self.assertEqual(src.offset, 0)
        self.assertFalse(src._align)
        self.assertEqual(src.poll(), [])

    def test_a_file_unreadable_after_stat_reads_nothing(self):
        self.path.write_bytes(_line(_user("one")))
        src = tail.JsonlTailSource(self.path, "claude")
        with patch("builtins.open", side_effect=OSError("EIO")):
            self.assertEqual(src.poll(), [])
        self.assertEqual(src.offset, 0)
        self.assertEqual([m["text"] for m in src.poll()], ["one"])

    def test_aligned_start_inside_a_huge_record_steps_past_it(self):
        big = b'{"pad": "' + b"x" * (tail._MAX_CHUNK * 2) + b'"}\n'
        self.path.write_bytes(big + _line(_user("after")))
        src = tail.JsonlTailSource(self.path, "claude")
        src.offset, src._align = 10, True
        # The first read is one full chunk with no newline: skip it.
        self.assertEqual(src.poll(), [])
        self.assertEqual(src.offset, 10 + tail._MAX_CHUNK)
        out = []
        for _ in range(4):
            out.extend(src.poll())
        self.assertEqual([m["text"] for m in out], ["after"])

    def test_blank_lines_and_non_object_records_are_skipped(self):
        self.path.write_bytes(b"\n   \n[1, 2]\n\"text\"\n" + _line(_user("ok")))
        src = tail.JsonlTailSource(self.path, "claude")
        self.assertEqual([m["text"] for m in src.poll()], ["ok"])

    def test_skip_oversized_walks_several_chunks(self):
        # A record three chunks long: the skip finds its newline in the
        # third piece, not the first.
        self.path.write_bytes(b"x" * (tail._MAX_CHUNK * 3) + b"\n"
                              + _line(_user("after")))
        src = tail.JsonlTailSource(self.path, "claude")
        self.assertEqual(src._skip_oversized(), tail._MAX_CHUNK * 3 + 1)

    def test_skip_oversized_without_a_newline_ends_at_the_size(self):
        self.path.write_bytes(b"x" * (tail._MAX_CHUNK * 2 + 7))
        src = tail.JsonlTailSource(self.path, "claude")
        self.assertEqual(src._skip_oversized(), tail._MAX_CHUNK * 2 + 7)


class TestContinueFromEdges(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir(self)
        self.old_path = self.dir / "old.jsonl"
        self.new_path = self.dir / "new.jsonl"
        self.body = b"".join(_line(_user(f"turn {i}")) for i in range(5))
        self.old_path.write_bytes(self.body)
        self.old = tail.JsonlTailSource(self.old_path, "claude")
        self.old.poll()
        self.new = tail.JsonlTailSource(self.new_path, "claude")

    def test_an_unpolled_or_empty_old_source_does_not_place(self):
        fresh = tail.JsonlTailSource(self.old_path, "claude")
        self.assertFalse(self.new.continue_from(fresh))
        # Placed near the end but never polled: its offset is a byte
        # position, not the end of a line it read, even where a line
        # happens to end there.
        self.new_path.write_bytes(self.body)
        fresh.offset = len(_line(_user("turn 0")))
        fresh._align = True
        self.assertFalse(self.new.continue_from(fresh))
        self.assertEqual(self.new.offset, 0)

    def test_old_file_gone_or_rewritten_does_not_place(self):
        self.new_path.write_bytes(self.body)
        self.old_path.unlink()
        self.assertFalse(self.new.continue_from(self.old))
        # Rewritten shorter: the boundary can't be read whole.
        self.old_path.write_bytes(self.body[:20])
        self.assertFalse(self.new.continue_from(self.old))
        # Same length but the read stops mid-line.
        self.old_path.write_bytes(self.body[:-1] + b"x")
        self.assertFalse(self.new.continue_from(self.old))

    def test_new_file_missing_or_without_the_boundary(self):
        self.assertFalse(self.new.continue_from(self.old))
        self.new_path.write_bytes(_line(_user("unrelated")) * 3)
        self.assertFalse(self.new.continue_from(self.old))
        self.assertEqual(self.new.offset, 0)

    def test_boundary_found_at_a_shifted_offset(self):
        self.old._since = 4.0
        self.new_path.write_bytes(_line(_user("preamble")) + self.body
                                  + _line(_user("new turn")))
        self.assertTrue(self.new.continue_from(self.old))
        self.assertEqual(self.new._since, 4.0)
        self.assertEqual([m["text"] for m in self.new.poll()], ["new turn"])


class TestOpencodeEdges(unittest.TestCase):
    def setUp(self):
        self.db = temp_dir(self) / "opencode.db"

    def _make(self, message_rows=(), parts=()):
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, data TEXT)")
        conn.execute("CREATE TABLE part (id TEXT, message_id TEXT, "
                     "session_id TEXT, time_created INTEGER, data TEXT)")
        conn.executemany("INSERT INTO message VALUES (?, ?)", message_rows)
        conn.executemany("INSERT INTO part VALUES (?, ?, 's1', ?, ?)", parts)
        conn.commit()
        conn.close()

    def test_restore_with_a_bad_seq(self):
        src = tail.OpencodeSource(self.db, "s1")
        self.assertTrue(src.restore({"source": "opencode", "session_id": "s1",
                                     "cursor": [5, "p1"], "seq": "x"}))
        self.assertEqual((src.cursor, src.seq), ((5, "p1"), None))
        self.assertFalse(src.restore({"source": "opencode",
                                      "session_id": "other"}))
        self.assertFalse(src.restore({"source": "jsonl"}))

    def test_part_cursor_shapes(self):
        self.assertEqual(tail._part_cursor([7]), (7, ""))
        self.assertEqual(tail._part_cursor(["x", "p"]), (0, ""))
        self.assertEqual(tail._part_cursor("junk"), (0, ""))
        self.assertEqual(tail._part_cursor(None), (0, ""))
        self.assertEqual(tail._part_cursor(9), (9, ""))

    def test_a_missing_db_reads_nothing_and_places_nothing(self):
        src = tail.OpencodeSource(self.db, "s1")
        src.skip_to_end()
        src.start_near_end()
        self.assertEqual((src.cursor, src.seq), ((0, ""), None))
        self.assertEqual(src.poll(), [])
        self.assertFalse(self.db.exists())

    def test_a_db_without_the_tables_keeps_the_cursor(self):
        sqlite3.connect(self.db).close()
        src = tail.OpencodeSource(self.db, "s1")
        src.skip_to_end()
        self.assertEqual(src.cursor, (0, ""))
        self.assertEqual(src.seq, -1)  # no v2 rows: nothing to skip
        src.start_near_end()
        self.assertEqual(src.cursor, (0, ""))
        self.assertEqual(src.poll(), [])

    def test_unreadable_rows_still_advance_the_cursor(self):
        self._make(
            message_rows=[("m1", "{not json"), ("m2", json.dumps(["user"]))],
            parts=[("p1", "m1", 1000, "{bad"),
                   ("p2", "m1", 1001, json.dumps(["not", "a", "dict"])),
                   ("p3", "m1", 1002, json.dumps({"type": "text",
                                                  "text": "hi"})),
                   ("p4", "m2", 1003, json.dumps({"type": "text",
                                                  "text": "yo"})),
                   ("p5", "m1", 1004, json.dumps({"type": "compaction"})),
                   ("p6", "m1", 1005, json.dumps({"type": "text",
                                                  "text": ""}))])
        src = tail.OpencodeSource(self.db, "s1")
        out = src.poll()
        # A message row whose JSON is broken or not an object reads as
        # the assistant.
        self.assertEqual([(m["kind"], m["text"]) for m in out],
                         [("assistant", "hi"), ("assistant", "yo"),
                          ("compaction", "context compacted")])
        self.assertEqual(src.cursor, (1005, "p6"))
        self.assertEqual(src._roles, {"m1": "assistant", "m2": "assistant"})

    def test_a_part_before_its_message_row_is_not_pinned(self):
        self._make(parts=[("p1", "m9", 1000, json.dumps(
            {"type": "text", "text": "early"}))])
        src = tail.OpencodeSource(self.db, "s1")
        self.assertEqual([m["kind"] for m in src.poll()], ["assistant"])
        # The fallback was not cached: the real role resolves later.
        self.assertNotIn("m9", src._roles)
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO message VALUES ('m9', ?)",
                     (json.dumps({"role": "user"}),))
        conn.execute("INSERT INTO part VALUES ('p2', 'm9', 's1', 1001, ?)",
                     (json.dumps({"type": "text", "text": "late"}),))
        conn.commit()
        conn.close()
        self.assertEqual([m["kind"] for m in src.poll()], ["user"])

    def test_v2_compaction_and_empty_prompt_rows(self):
        self._make()
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE session_message (id TEXT, session_id TEXT,"
                     " type TEXT, seq INTEGER, time_created INTEGER,"
                     " data TEXT)")
        rows = [("a", "user", 0, {"text": ""}),
                ("b", "compaction", 1, {}),
                ("c", "assistant", 2, {"time": {"completed": 5},
                                       "content": [{"type": "text",
                                                    "text": ""},
                                                   {"type": "reasoning",
                                                    "text": ""}]})]
        for rid, kind, seq, data in rows:
            conn.execute("INSERT INTO session_message VALUES"
                         " (?, 's1', ?, ?, 1000, ?)",
                         (rid, kind, seq, json.dumps(data)))
        conn.commit()
        conn.close()
        src = tail.OpencodeSource(self.db, "s1")
        out = src.poll()
        self.assertEqual([m["kind"] for m in out], ["compaction"])
        self.assertEqual(src.seq, 2)


class TestSnapshotEdges(unittest.TestCase):
    def _src(self):
        return tail.SnapshotSource(Session(
            timestamp=0, agent="x", path="/tmp", session_id="s1",
            tool_name="gemini"))

    def test_restore_only_takes_a_snapshot_cursor(self):
        src = self._src()
        self.assertFalse(src.restore({"source": "jsonl", "fps": []}))
        self.assertFalse(src.restore({"source": "snapshot", "fps": "abc"}))
        self.assertTrue(src.restore({"source": "snapshot", "fps": [1, "b"]}))
        self.assertEqual(src._fps, ["1", "b"])

    def test_a_failing_read_leaves_skip_to_end_unplaced(self):
        src = self._src()
        src.restore({"source": "snapshot", "fps": ["a"]})
        with patch.object(src, "_read", side_effect=RuntimeError("locked")):
            src.skip_to_end()
        self.assertEqual(src._fps, ["a"])


class TestTailerCursorFile(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir(self)
        self.path = self.tmp / "s.jsonl"
        self.path.write_bytes(_line(_user("hi")))
        self.sess = Session(timestamp=0, agent="Claude Code", path="/tmp",
                            session_id=str(self.path), tool_name="claude")

    def _tailer(self, cursors):
        return tail.Tailer("claude:s1", self.sess,
                           tail.JsonlTailSource(self.path, "claude"),
                           cursors, False)

    def test_a_cursor_file_that_is_not_an_object_is_ignored(self):
        cursors = self.tmp / "cursors"
        cursors.mkdir()
        t = self._tailer(cursors)
        t._cursor_path.write_text("[1, 2]")
        self.assertEqual(t._load_cursor(), {})
        t._cursor_path.write_text("{broken")
        self.assertEqual(t._load_cursor(), {})

    def test_an_unwritable_cursor_dir_keeps_the_last_saved_state(self):
        blocker = self.tmp / "cursors"
        blocker.write_text("a file where the folder should be")
        t = self._tailer(blocker)
        t.source.poll()
        t.save_cursor()
        self.assertIsNone(t._saved)


class TestManagerEdges(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir(self)
        self.events = []

    def _session(self, name, tool="claude"):
        path = self.tmp / f"{name}.jsonl"
        path.write_bytes(_line(_user(name)))
        return Session(timestamp=0, agent="x", path="/tmp",
                       session_id=str(path), tool_name=tool)

    def _manager(self, **kw):
        return tail.TailManager(lambda k, ev: self.events.append(ev), "mbp",
                                home=self.tmp, **kw)

    def test_ensure_ignores_a_session_with_no_source(self):
        mgr = self._manager()
        sess = Session(timestamp=0, agent="x", path="/tmp", session_id="z",
                       tool_name="no-such-harness")
        mgr.ensure("x:z", sess, "done")
        self.assertEqual(mgr.tailers, {})
        self.assertEqual(mgr.statuses, {})

    def test_a_poll_that_raises_backs_off_to_the_idle_cadence(self):
        mgr = self._manager()
        mgr.sync([("claude:a", self._session("a"), "active")])
        tailer = mgr.tailers["claude:a"]
        now = 1_000_000.0
        with patch.object(tailer, "poll", side_effect=RuntimeError("boom")):
            self.assertEqual(mgr.tick(now), 0)
        self.assertEqual(tailer.next_poll_at, now + tail.POLL_IDLE_S)
        self.assertIn("claude:a", mgr.tailers)

    def test_a_status_probe_that_raises_changes_nothing(self):
        def status_of(_sessions):
            raise RuntimeError("registry locked")

        mgr = self._manager(status_of=status_of)
        mgr.sync([("claude:a", self._session("a"), "done")])
        mgr.wake("claude:a", now=1_000_000.0)
        mgr.tick(1_000_000.0)
        self.assertEqual(mgr.statuses["claude:a"], "done")

    def test_an_on_status_callback_that_raises_keeps_the_probe(self):
        def on_status(*_a):
            raise RuntimeError("catalog gone")

        mgr = self._manager(status_of=lambda ss: ["active"] * len(ss),
                            on_status=on_status)
        mgr.sync([("claude:a", self._session("a"), "done")], as_of=0)
        mgr.wake("claude:a", now=1_000_000.0)
        mgr.tick(1_000_000.0)
        self.assertEqual(mgr.statuses["claude:a"], "active")

    def test_freshen_survives_a_vanished_transcript(self):
        mgr = self._manager()
        sess = self._session("a")
        mgr.sync([("claude:a", sess, "done")])
        Path(sess.session_id).unlink()
        self.assertIs(mgr.fresh_session("claude:a", sess), sess)

    def test_moved_check_survives_a_vanished_transcript(self):
        mgr = self._manager()
        sess = self._session("a")
        mgr.sync([("claude:a", sess, "done")])
        tailer = mgr.tailers["claude:a"]
        Path(sess.session_id).unlink()
        newer = Session(timestamp=5_000, agent="x", path="/tmp",
                        session_id=sess.session_id, tool_name="claude")
        self.assertTrue(mgr._may_have_moved(tailer, newer))

    def test_reresolve_without_a_source_keeps_the_tailer(self):
        mgr = self._manager()
        sess = self._session("a")
        mgr.sync([("claude:a", sess, "done")])
        tailer = mgr.tailers["claude:a"]
        other = self.tmp / "b.jsonl"
        other.write_bytes(_line(_user("b")))
        with patch.object(tail, "resolve_jsonl_path", return_value=other), \
                patch.object(tail, "make_source", return_value=None):
            mgr._reresolve(tailer, sess)
        self.assertIs(tailer.next_source(), tailer.source)

    def test_sweep_survives_unlistable_and_undeletable_cursors(self):
        mgr = self._manager()
        journal = SimpleNamespace(sweep=lambda cutoff, kept: 0,
                                  dir=self.tmp / "journal")
        mgr.cursors_dir.mkdir(parents=True, exist_ok=True)
        stale = mgr.cursors_dir / "gone.json"
        stale.write_text("{}")
        old = time.time() - 30 * 86400
        os.utime(stale, (old, old))
        with patch.object(Path, "glob", side_effect=OSError("denied")):
            self.assertEqual(mgr._sweep_stale(journal, [], time.time()), 0)
        with patch.object(Path, "unlink", side_effect=OSError("busy")):
            self.assertEqual(mgr._sweep_stale(journal, [], time.time()), 0)
        self.assertTrue(stale.exists())
        self.assertEqual(mgr._sweep_stale(journal, [], time.time()), 1)
        self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()
