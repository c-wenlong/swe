"""Journal: seq numbering, replay, ring trim."""

import json
import os
import stat
import threading
import unittest
from pathlib import Path

from swe.remote import protocol
from swe.remote import journal as journal_mod
from swe.remote.journal import Journal

from tests._tempdir import temp_dir


class TestJournal(unittest.TestCase):
    def setUp(self):
        self.tmp = str(temp_dir(self))
        self.journal = Journal(Path(self.tmp))

    def _append(self, key, n):
        return self.journal.append(
            key, protocol.event("session.status", key, "m", {"n": n})
        )

    def test_seq_is_per_session_and_monotonic(self):
        e1 = self._append("a:x", 1)
        e2 = self._append("a:x", 2)
        e3 = self._append("a:y", 3)
        self.assertEqual((e1["seq"], e2["seq"], e3["seq"]), (1, 2, 1))

    def test_read_since_filters(self):
        for i in range(5):
            self._append("a:x", i)
        events, truncated = self.journal.read_since("a:x", 3)
        self.assertFalse(truncated)
        self.assertEqual([e["seq"] for e in events], [4, 5])
        self.assertEqual([e["data"]["n"] for e in events], [3, 4])

    def test_seq_survives_new_journal(self):
        self._append("a:x", 1)
        journal2 = Journal(Path(self.tmp))
        self.assertEqual(journal2.last_seq("a:x"), 1)

    def test_ring_trim_keeps_newest(self):
        # Force a trim by writing a file past the byte threshold.
        key = "a:big"
        path = self.journal.dir / f"{protocol.key_slug(key)}.jsonl"
        with open(path, "w") as fh:
            for i in range(5000):
                fh.write(json.dumps({"seq": i + 1, "type": "t",
                                     "data": "x" * 300}) + "\n")
        self._append(key, 1)  # triggers _maybe_trim
        events, _truncated = self.journal.read_since(key, 0)
        # The ring kept its newest RING_KEEP lines, the append included.
        self.assertEqual(len(events), journal_mod.RING_KEEP)
        seqs = [e["seq"] for e in events]
        self.assertEqual(seqs[-1], 5001)  # newest survived
        self.assertLess(len(events), 5001)  # ring was trimmed

    def test_journal_is_private_to_this_account(self):
        # Transcripts hold every prompt and reply: a 0700 folder and
        # 0600 files, also for a trimmed file and a folder made 0755
        # before modes were set.
        def mode(path):
            return stat.S_IMODE(os.stat(path).st_mode)

        self.assertEqual(mode(self.journal.dir), 0o700)
        self._append("a:x", 1)
        path = self.journal.dir / f"{protocol.key_slug('a:x')}.jsonl"
        self.assertEqual(mode(path), 0o600)
        os.chmod(self.journal.dir, 0o755)
        again = Journal(Path(self.tmp))
        self.assertEqual(mode(again.dir), 0o700)
        big = again.dir / f"{protocol.key_slug('a:big')}.jsonl"
        fd = os.open(big, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, "w") as fh:
            for i in range(5000):
                fh.write(json.dumps({"seq": i + 1, "type": "t",
                                     "data": "x" * 300}) + "\n")
        again.append("a:big", protocol.event("session.status", "a:big",
                                              "m", {}))
        self.assertEqual(mode(big), 0o600)

    def test_big_journal_under_ring_max_is_not_reread_per_append(self):
        # Large tool events: >1 MB on disk but far fewer than RING_MAX
        # lines. Each append used to readlines() the whole file.
        key = "a:fat"
        path = self.journal.dir / f"{protocol.key_slug(key)}.jsonl"
        with open(path, "w") as fh:
            for i in range(300):
                fh.write(json.dumps({"seq": i + 1, "type": "t", "data": "x" * 5000}) + "\n")
        from unittest.mock import patch
        from swe.remote import journal as journal_mod
        real_open = open
        reads = []

        def counting_open(file, mode="r", *a, **kw):
            if str(file) == str(path) and "r" in mode:
                reads.append(mode)
            return real_open(file, mode, *a, **kw)

        with patch("builtins.open", counting_open):
            for n in range(50):
                self._append(key, n)
        self.assertLessEqual(len(reads), 2, f"journal re-read {len(reads)} times for 50 appends")
        self.assertEqual(self.journal.last_seq(key), 350)

    def test_counted_journal_still_trims_past_ring_max(self):
        from swe.remote import journal as journal_mod
        key = "a:grow"
        path = self.journal.dir / f"{protocol.key_slug(key)}.jsonl"
        with open(path, "w") as fh:
            for i in range(journal_mod.RING_MAX - 5):
                fh.write(json.dumps({"seq": i + 1, "type": "t", "data": "x" * 300}) + "\n")
        for n in range(10):
            self._append(key, n)
        events, _ = self.journal.read_since(key, 0)
        self.assertLessEqual(len(events), journal_mod.RING_KEEP + 10)
        self.assertEqual(events[-1]["seq"], journal_mod.RING_MAX + 5)

    def test_missing_file_reads_empty(self):
        events, truncated = self.journal.read_since("ghost", 5)
        self.assertEqual(events, [])
        self.assertFalse(truncated)

    def test_truncated_when_client_behind_ring(self):
        key = "a:x"
        path = self.journal.dir / f"{protocol.key_slug(key)}.jsonl"
        with open(path, "w") as fh:
            for i in range(10, 15):
                fh.write(json.dumps({"seq": i, "type": "t"}) + "\n")
        events, truncated = self.journal.read_since(key, 2)
        self.assertTrue(truncated)
        self.assertEqual([e["seq"] for e in events], [10, 11, 12, 13, 14])

    def test_read_since_limit_and_corrupt_lines(self):
        key = "a:y"
        path = self.journal.dir / f"{protocol.key_slug(key)}.jsonl"
        with open(path, "w") as fh:
            fh.write("not json\n")
            fh.write(json.dumps([1, 2]) + "\n")          # non-dict
            fh.write(json.dumps({"type": "t"}) + "\n")   # no seq
            for i in range(1, 5):
                fh.write(json.dumps({"seq": i, "type": "t"}) + "\n")
        events, _ = self.journal.read_since(key, 0, limit=2)
        self.assertEqual([e["seq"] for e in events], [1, 2])

    def test_append_oserror_returns_event(self):
        from unittest.mock import patch
        j = Journal(Path(self.tmp) / "nowhere")
        with patch("builtins.open", side_effect=OSError("read-only")):
            ev = j.append("k", protocol.event("t", "k", "m", {}))
        self.assertEqual(ev["seq"], 1)

    def test_read_since_runs_under_the_lock(self):
        # The read holds the file's lock like appends and trims do, so
        # it can't answer from a file a trim is mid-way through
        # replacing. While the lock is held elsewhere the read cannot
        # even start.
        self._append("a:x", 1)
        done = []
        t = threading.Thread(
            target=lambda: done.append(self.journal.read_since("a:x", 0))
        )
        with journal_mod._lock_for(self.journal._file("a:x")):
            t.start()
            t.join(timeout=0.5)
            self.assertEqual(done, [])
        t.join(timeout=5)
        self.assertEqual(len(done), 1)
        self.assertEqual([e["seq"] for e in done[0][0]], [1])

    def test_one_sessions_lock_does_not_hold_another(self):
        self._append("a:x", 1)
        self._append("b:y", 1)
        done = []
        t = threading.Thread(
            target=lambda: done.append(self.journal.read_since("b:y", 0)))
        with journal_mod._lock_for(self.journal._file("a:x")):
            t.start()
            t.join(timeout=5)
        self.assertEqual(len(done), 1)

    def test_locks_are_not_kept_once_released(self):
        import gc
        for i in range(50):
            self._append(f"a:s{i}", 1)
        gc.collect()
        mine = [k for k in journal_mod._LOCKS.keys()
                if k.startswith(str(self.journal.dir))]
        self.assertEqual(mine, [])

    def test_ring_is_bounded_by_bytes(self):
        # A few hundred large tool events stay under RING_MAX lines but
        # grew the file without bound.
        from unittest.mock import patch
        key = "a:fat"
        big = protocol.event("message.appended", key, "m",
                             {"kind": "assistant", "text": "x" * 10_000})
        with patch.object(journal_mod, "RING_MAX_BYTES", 200_000), \
                patch.object(journal_mod, "RING_KEEP_BYTES", 100_000):
            for _ in range(60):
                self.journal.append(key, big)
        size = self.journal._file(key).stat().st_size
        self.assertLessEqual(size, 200_000 + 11_000)
        events, _ = self.journal.read_since(key, 0)
        self.assertEqual(events[-1]["seq"], 60)  # newest survived
        self.assertLess(len(events), 60)

    def test_seq_recovers_past_large_final_event(self):
        # A final record bigger than the initial tail window must not
        # reset numbering: the reader grows the window until it parses.
        key = "a:big"
        self._append(key, 1)
        big = protocol.event(
            "message.appended", key, "m",
            {"kind": "assistant", "text": "x" * 200_000},
        )
        written = self.journal.append(key, big)
        self.assertEqual(written["seq"], 2)
        journal2 = Journal(Path(self.tmp))
        self.assertEqual(journal2.last_seq(key), 2)
        # And a subsequent append keeps counting, not restarting at 1.
        self.assertEqual(journal2.append(key, big)["seq"], 3)


class TestReadTail(unittest.TestCase):
    """``read_tail``: the newest events, read backward from the end."""

    def setUp(self):
        self.tmp = str(temp_dir(self))
        self.journal = Journal(Path(self.tmp))
        self.key = "a:t"
        self.path = self.journal.dir / f"{protocol.key_slug(self.key)}.jsonl"

    def _write(self, records, prefix=""):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(prefix)
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def test_newest_events_oldest_first(self):
        self._write([{"seq": i, "type": "t"} for i in range(1, 11)])
        events = self.journal.read_tail(self.key, 3)
        self.assertEqual([e["seq"] for e in events], [8, 9, 10])

    def test_before_seq_and_keep(self):
        self._write([{"seq": i, "type": "m" if i % 2 else "s"}
                     for i in range(1, 11)])
        events = self.journal.read_tail(
            self.key, 2, before_seq=9,
            keep=lambda e: dict(e, kept=True) if e["type"] == "m" else None,
        )
        # Odd seqs below 9, the newest two, mapped by keep.
        self.assertEqual([e["seq"] for e in events], [5, 7])
        self.assertTrue(all(e["kept"] for e in events))

    def test_lines_across_chunk_boundaries(self):
        # Multibyte text and one line longer than a whole chunk: every
        # record must come back whole, in order, wherever chunks split.
        records = [{"seq": i, "type": "t", "text": "\u00e9\u6f22" * (i * 997)}
                   for i in range(1, 40)]
        records.append({"seq": 40, "type": "t",
                        "text": "y" * (journal_mod._TAIL_CHUNK * 3)})
        self._write(records)
        events = self.journal.read_tail(self.key, 100)
        self.assertEqual([e["seq"] for e in events], list(range(1, 41)))
        self.assertEqual(events[10]["text"], records[10]["text"])
        self.assertEqual(events[-1]["text"], records[-1]["text"])

    def test_skips_corrupt_and_partial_lines(self):
        self._write([{"seq": 1, "type": "t"}, {"seq": 2, "type": "t"}],
                    prefix='not json\n[1, 2]\n{"type": "t"}\n')
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write('{"seq": 3, "ty')  # a write cut short
        self.assertEqual(
            [e["seq"] for e in self.journal.read_tail(self.key, 10)], [1, 2])

    def test_missing_file_and_zero_limit(self):
        self.assertEqual(self.journal.read_tail("ghost", 5), [])
        self._write([{"seq": 1, "type": "t"}])
        self.assertEqual(self.journal.read_tail(self.key, 0), [])

    def test_reads_only_the_end_of_a_large_journal(self):
        # A page of the newest events must not parse the whole ring.
        self._write([{"seq": i, "type": "t", "pad": "x" * 200}
                     for i in range(1, 5001)])
        from unittest.mock import patch
        real = journal_mod._parse_line
        calls = []

        def counting(raw):
            calls.append(raw)
            return real(raw)

        with patch.object(journal_mod, "_parse_line", side_effect=counting):
            events = self.journal.read_tail(self.key, 5)
        self.assertEqual([e["seq"] for e in events], [4996, 4997, 4998, 4999, 5000])
        self.assertLess(len(calls), 400)

    def test_read_since_near_the_head_reads_only_the_end(self):
        # An SSE reconnect a few events behind must not parse the ring.
        self._write([{"seq": i, "type": "t", "pad": "x" * 200}
                     for i in range(1, 5001)])
        from unittest.mock import patch
        real = json.loads
        calls = []

        def counting(raw, *args, **kwargs):
            calls.append(raw)
            return real(raw, *args, **kwargs)

        with patch.object(journal_mod.json, "loads", side_effect=counting):
            events, truncated = self.journal.read_since(self.key, 4995)
        self.assertEqual([e["seq"] for e in events], [4996, 4997, 4998, 4999, 5000])
        self.assertFalse(truncated)
        self.assertLess(len(calls), 400)

    def test_bounded_replay_from_the_start_stops_at_its_limit(self):
        self._write([{"seq": i, "type": "t"} for i in range(1, 2001)])
        from unittest.mock import patch
        real = journal_mod._parse_line
        calls = []

        def counting(raw):
            calls.append(raw)
            return real(raw)

        with patch.object(journal_mod, "_parse_line", side_effect=counting):
            events, truncated = self.journal.read_since(self.key, 0, limit=3)
        self.assertEqual([e["seq"] for e in events], [1, 2, 3])
        self.assertFalse(truncated)
        self.assertEqual(len(calls), 3)

    def test_read_since_matches_a_forward_read_at_every_cursor(self):
        # Large multibyte events across chunk boundaries, corrupt lines,
        # a cut-short last write and a ring that starts at seq 20.
        records = [{"seq": i, "type": "t", "text": "é漢" * ((i % 7) * 9000)}
                   for i in range(20, 60)]
        self._write(records, prefix='not json\n[1, 2]\n{"type": "t"}\n')
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write('{"seq": 60, "ty')
        for after in (0, 1, 18, 19, 20, 35, 58, 59, 70):
            want = [r["seq"] for r in records if r["seq"] > after]
            events, truncated = self.journal.read_since(self.key, after)
            self.assertEqual([e["seq"] for e in events], want, after)
            self.assertEqual(truncated, bool(after and after < 19), after)
            events, _ = self.journal.read_since(self.key, after, limit=3)
            self.assertEqual([e["seq"] for e in events], want[:3], after)
        self.assertEqual(self.journal.read_since(self.key, 30)[0][0]["text"],
                         records[11]["text"])


if __name__ == "__main__":
    unittest.main()
