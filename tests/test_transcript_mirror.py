"""The transcript mirror (#1918): Claude Code and Codex transcripts copied
into ``~/.swe/data/transcripts/`` so they outlive the harnesses' pruning.

Everything runs in a throwaway HOME. The CLI checks spawn the real ``swe``
(``run_swe``); the rest call the modules with an explicit ``home``. The
property under test throughout: a mirror copy is never lost or listed,
read or searched twice.
"""

import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

from swe.paths import transcripts_dir_for
from swe.reports.transcripts import read_transcript
from swe.sessions import aggregator
from swe.sessions import search_index
from swe.sessions import search_writer as writer
from swe.sessions import transcript_mirror as mirror
from swe.sessions.models import Session
from tests._tempdir import temp_dir
from tests.test_json_output import decode, run_swe

CLAUDE_ID = "11111111-2222-3333-4444-555555555555"
CODEX_UUID = "0199aaaa-bbbb-cccc-dddd-eeeeffff0001"
CODEX_STEM = f"rollout-2026-09-01T10-00-00-{CODEX_UUID}"
ARCHIVED_UUID = "0199aaaa-bbbb-cccc-dddd-eeeeffff0002"
ARCHIVED_STEM = f"rollout-2026-09-02T10-00-00-{ARCHIVED_UUID}"
MTIME = 1_790_000_000


def claude_records(prompt="fix the login bug", cwd="/work/proj"):
    return [
        {"type": "user", "sessionId": CLAUDE_ID, "cwd": cwd,
         "timestamp": "2026-10-01T10:00:00.000Z",
         "message": {"role": "user", "content": prompt}},
        {"type": "assistant", "timestamp": "2026-10-01T10:00:05.000Z", "message": {
            "content": [{"type": "text", "text": "Merged. CI green."}],
            "stop_reason": "end_turn"}},
    ]


def codex_records(prompt="add dark mode", cwd="/work/other"):
    return [
        {"type": "session_meta", "payload": {"cwd": cwd}},
        {"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": prompt}]}},
    ]


def write_jsonl(path: Path, records, mtime=MTIME) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Compact, as the harnesses write it: the search decoder gates lines on
    # byte markers such as `"type":"user"`.
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records))
    os.utime(path, (mtime, mtime))
    return path


class MirrorCase(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self).resolve()
        self.mirror_root = transcripts_dir_for(self.home)
        # The streamed path everywhere by default, so results do not
        # depend on the filesystem; CloneTests turn the clone back on.
        self.clone_patch = mock.patch.object(
            mirror, "_clone", side_effect=OSError(95, "no clone"))
        self.clone_patch.start()
        self.addCleanup(self.clone_patch.stop)

    def claude(self, session_id=CLAUDE_ID, **kw) -> Path:
        return write_jsonl(
            self.home / ".claude/projects/-work-proj" / f"{session_id}.jsonl",
            claude_records(**kw))

    def codex(self, stem=CODEX_STEM, **kw) -> Path:
        return write_jsonl(
            self.home / ".codex/sessions/2026/09/01" / f"{stem}.jsonl", codex_records(**kw))

    def archived(self, stem=ARCHIVED_STEM, **kw) -> Path:
        return write_jsonl(
            self.home / ".codex/archived_sessions" / f"{stem}.jsonl",
            codex_records(prompt="archived thread", **kw), MTIME + 5)

    def sync(self, **kw) -> mirror.MirrorResult:
        return mirror.sync(self.home, writer.sources(self.home), **kw)

    def sessions(self) -> list[dict]:
        result = run_swe(self.home, "session", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return decode(result)["data"]["sessions"]


class CopyTests(MirrorCase):
    def test_first_mirror_copies_every_claude_and_codex_transcript(self):
        claude, codex, archived = self.claude(), self.codex(), self.archived()
        result = self.sync()
        self.assertEqual((result.copied, result.unchanged, result.failed), (3, 0, 0))
        self.assertEqual(result.copied_bytes,
                         sum(p.stat().st_size for p in (claude, codex, archived)))
        copy = self.mirror_root / "claude/-work-proj" / claude.name
        self.assertEqual(copy.read_bytes(), claude.read_bytes())
        # The copy keeps the source's mtime: a session sorts by it.
        self.assertEqual(copy.stat().st_mtime_ns, claude.stat().st_mtime_ns)
        self.assertEqual(oct(copy.stat().st_mode & 0o777), "0o600")

    def test_codex_dated_and_flat_archived_layouts_keep_their_shape(self):
        self.codex(), self.archived()
        self.sync()
        self.assertTrue((self.mirror_root / "codex/2026/09/01" / f"{CODEX_STEM}.jsonl").is_file())
        self.assertTrue((self.mirror_root / "codex" / f"{ARCHIVED_STEM}.jsonl").is_file())

    def test_the_manifest_records_mirror_path_source_and_stat(self):
        claude = self.claude()
        self.sync()
        entry = mirror.load_manifest(self.home)[f"claude/-work-proj/{claude.name}"]
        st = claude.stat()
        self.assertEqual(entry["source"], str(claude))
        self.assertEqual((entry["ino"], entry["mtime_ns"], entry["size"]),
                         (st.st_ino, st.st_mtime_ns, st.st_size))
        self.assertEqual(entry["mirror_size"], st.st_size)
        self.assertEqual(entry["harness"], "claude")

    def test_other_harnesses_are_not_mirrored(self):
        write_jsonl(self.home / ".pi/agent/sessions/x/a.jsonl", claude_records())
        self.assertEqual(self.sync().files, 0)
        self.assertFalse((self.mirror_root / "pi").exists())

    def test_an_unchanged_source_is_not_copied_again(self):
        claude = self.claude()
        self.sync()
        copy = self.mirror_root / "claude/-work-proj" / claude.name
        before = copy.stat()
        second = self.sync()
        self.assertEqual((second.copied, second.unchanged), (0, 1))
        self.assertEqual(second.unchanged_bytes, claude.stat().st_size)
        self.assertEqual(copy.stat().st_ino, before.st_ino)

    def test_a_changed_source_is_copied_again(self):
        claude = self.claude()
        self.sync()
        with open(claude, "a") as handle:
            handle.write(json.dumps({"type": "user", "message": {"content": "and more"}}) + "\n")
        os.utime(claude, (MTIME + 60, MTIME + 60))
        second = self.sync()
        self.assertEqual(second.copied, 1)
        copy = self.mirror_root / "claude/-work-proj" / claude.name
        self.assertEqual(copy.read_bytes(), claude.read_bytes())
        self.assertEqual(copy.stat().st_mtime_ns, claude.stat().st_mtime_ns)

    def test_a_copy_is_made_again_when_only_the_mtime_moves(self):
        claude = self.claude()
        self.sync()
        os.utime(claude, (MTIME + 1, MTIME + 1))
        self.assertEqual(self.sync().copied, 1)

    def test_a_deleted_mirror_copy_of_a_live_source_is_restored(self):
        claude = self.claude()
        self.sync()
        copy = self.mirror_root / "claude/-work-proj" / claude.name
        copy.unlink()
        self.assertEqual(self.sync().copied, 1)
        self.assertTrue(copy.is_file())

    def test_off_means_no_copies_from_an_index_pass(self):
        self.claude()
        mirror.set_enabled(self.home, False)
        self.assertFalse(mirror.is_enabled(self.home))
        writer.run_pass(self.home)
        self.assertFalse((self.mirror_root / "claude").exists())
        mirror.set_enabled(self.home, True)
        writer.run_pass(self.home)
        self.assertEqual(len(list((self.mirror_root / "claude").rglob("*.jsonl"))), 1)

    def test_it_is_on_until_someone_turns_it_off(self):
        self.assertTrue(mirror.is_enabled(self.home))

    def test_the_command_copies_nothing_while_off(self):
        self.claude()
        self.assertEqual(run_swe(self.home, "session", "mirror", "--off").returncode, 0)
        plain = run_swe(self.home, "session", "mirror")
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertIn("Mirroring is off", plain.stdout)
        self.assertFalse((self.mirror_root / "claude").exists())

    def test_a_claude_account_home_mirrors_under_accounts(self):
        account = self.home / ".swe/accounts/claude/work/home"
        source = write_jsonl(account / "projects/-work-proj" / f"{CLAUDE_ID}.jsonl",
                             claude_records())
        self.sync()
        copy = self.mirror_root / "accounts/claude/work/-work-proj" / source.name
        self.assertTrue(copy.is_file())
        source.unlink()
        # A fresh process finds it: the account home is derived, not saved.
        row = self.sessions()[0]
        self.assertEqual(row["account_home"], str(account))
        self.assertTrue(row["mirrored"])


@unittest.skipUnless(sys.platform == "darwin", "clonefile is macOS only")
class CloneTests(MirrorCase):
    def setUp(self):
        super().setUp()
        self.clone_patch.stop()  # the real clonefile (cleanup stop is a no-op then)
        keep = mock.patch.object(mirror, "_clone", mirror._clone)
        keep.start()
        self.addCleanup(keep.stop)

    def test_a_clone_holds_the_same_bytes_and_is_reported(self):
        claude = self.claude()
        result = self.sync()
        self.assertEqual((result.cloned, result.copied), (1, 0))
        self.assertEqual(result.cloned_bytes, claude.stat().st_size)
        copy = self.mirror_root / "claude/-work-proj" / claude.name
        self.assertEqual(copy.read_bytes(), claude.read_bytes())
        self.assertEqual(copy.stat().st_mtime_ns, claude.stat().st_mtime_ns)
        self.assertEqual(oct(copy.stat().st_mode & 0o777), "0o600")
        self.assertNotEqual(copy.stat().st_ino, claude.stat().st_ino)

    def test_deleting_the_source_leaves_the_clone_readable(self):
        claude = self.claude()
        body = claude.read_bytes()
        self.sync()
        claude.unlink()
        copy = self.mirror_root / "claude/-work-proj" / claude.name
        self.assertEqual(copy.read_bytes(), body)
        self.assertEqual(self.sync().orphaned, 1)

    def test_a_changed_source_is_cloned_again_not_streamed(self):
        claude = self.claude()
        self.sync()
        with open(claude, "a") as handle:
            handle.write("{}\n")
        os.utime(claude, (MTIME + 5, MTIME + 5))
        second = self.sync()
        self.assertEqual((second.cloned, second.copied), (1, 0))
        copy = self.mirror_root / "claude/-work-proj" / claude.name
        self.assertEqual(copy.read_bytes(), claude.read_bytes())

    def test_a_clone_interrupted_leaves_no_partial_file(self):
        self.claude()
        with mock.patch.object(mirror.os, "replace", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.sync()
        self.assertEqual([p for p in (self.mirror_root / "claude").rglob("*") if p.is_file()], [])

    def test_the_recorded_size_is_the_clones_own(self):
        claude = self.claude()
        self.sync()
        entry = mirror.load_manifest(self.home)[f"claude/-work-proj/{claude.name}"]
        self.assertEqual(entry["mirror_size"], claude.stat().st_size)


class FallbackTests(MirrorCase):
    def test_a_failing_clone_streams_with_the_same_result(self):
        # setUp already forces the clone to raise.
        claude = self.claude()
        result = self.sync()
        self.assertEqual((result.copied, result.cloned), (1, 0))
        self.assertEqual(result.copied_bytes, claude.stat().st_size)
        copy = self.mirror_root / "claude/-work-proj" / claude.name
        self.assertEqual(copy.read_bytes(), claude.read_bytes())
        self.assertEqual(copy.stat().st_mtime_ns, claude.stat().st_mtime_ns)

    def test_a_clone_that_half_made_its_target_is_cleaned_up(self):
        claude = self.claude()

        def partial(source, target):
            Path(target).write_bytes(b"partial")
            raise OSError(18, "cross-device")

        with mock.patch.object(mirror, "_clone", side_effect=partial):
            self.assertEqual(self.sync().copied, 1)
        copy = self.mirror_root / "claude/-work-proj" / claude.name
        self.assertEqual(copy.read_bytes(), claude.read_bytes())

    def test_clone_errors_other_than_oserror_are_not_swallowed_as_data_loss(self):
        with mock.patch.object(mirror.sys, "platform", "linux"):
            with self.assertRaises(OSError):
                mirror._clone("a", "b")


class SafetyTests(MirrorCase):
    def test_an_interrupted_copy_leaves_no_partial_file(self):
        claude = self.claude()
        with mock.patch.object(mirror.os, "replace", side_effect=OSError("disk full")):
            result = self.sync()
        self.assertEqual((result.copied, result.failed), (0, 1))
        # No transcript copy and no temp file: the project dir is empty.
        self.assertEqual([p for p in (self.mirror_root / "claude").rglob("*") if p.is_file()], [])
        self.assertNotIn(f"claude/-work-proj/{claude.name}", mirror.load_manifest(self.home))
        # The next pass finishes the job.
        self.assertEqual(self.sync().copied, 1)

    def test_a_crash_while_reading_cleans_up_its_temp_file(self):
        self.claude()
        with mock.patch.object(mirror.os, "fsync", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.sync()
        self.assertEqual([p for p in self.mirror_root.rglob("*.tmp")], [])
        self.assertEqual([p for p in (self.mirror_root / "claude").rglob("*") if p.is_file()], [])

    def test_a_failed_replacement_keeps_the_older_copy_intact(self):
        claude = self.claude()
        self.sync()
        copy = self.mirror_root / "claude/-work-proj" / claude.name
        old = copy.read_bytes()
        with open(claude, "a") as handle:
            handle.write("{}\n")
        os.utime(claude, (MTIME + 9, MTIME + 9))
        with mock.patch.object(mirror.os, "replace", side_effect=OSError("boom")):
            self.assertEqual(self.sync().failed, 1)
        self.assertEqual(copy.read_bytes(), old)

    def test_deleting_the_source_keeps_the_copy(self):
        claude = self.claude()
        self.sync()
        claude.unlink()
        result = self.sync()
        self.assertEqual((result.copied, result.orphaned), (0, 1))
        copy = self.mirror_root / "claude/-work-proj" / claude.name
        self.assertTrue(copy.is_file())
        self.assertEqual(result.orphaned_bytes, copy.stat().st_size)
        self.assertEqual(mirror.status(self.home)["orphaned"], 1)

    def test_a_vanished_harness_directory_deletes_nothing(self):
        self.claude(), self.codex()
        self.sync()
        for name in (".claude", ".codex"):
            import shutil
            shutil.rmtree(self.home / name)
        result = self.sync()
        self.assertEqual(result.orphaned, 2)
        self.assertEqual(len(list(self.mirror_root.rglob("*.jsonl"))), 2)

    def test_a_source_that_shrinks_keeps_the_longer_copy_beside_it(self):
        claude = self.claude()
        self.sync()
        copy = self.mirror_root / "claude/-work-proj" / claude.name
        longer = copy.read_bytes()
        write_jsonl(claude, claude_records()[:1], MTIME + 30)
        self.assertEqual(self.sync().copied, 1)
        self.assertLess(copy.stat().st_size, len(longer))
        kept = list(copy.parent.glob(f"{claude.name}{mirror.SHRUNK_SUFFIX}*"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_bytes(), longer)
        # Not a transcript to anything that lists or indexes by suffix.
        self.assertFalse(kept[0].name.endswith(".jsonl"))

    def test_an_unreadable_manifest_is_set_aside_never_overwritten(self):
        self.claude()
        self.sync()
        manifest = mirror.manifest_path(self.home)
        manifest.write_text("{not json")
        self.sync()
        aside = list(manifest.parent.glob("manifest.json.corrupt-*"))
        self.assertEqual(len(aside), 1)
        self.assertEqual(aside[0].read_text(), "{not json")
        self.assertEqual(len(mirror.load_manifest(self.home)), 1)

    def test_a_second_live_source_cannot_overwrite_a_mirror_path(self):
        claude = self.claude()
        self.sync()
        manifest = mirror.manifest_path(self.home)
        data = json.loads(manifest.read_text())
        key = f"claude/-work-proj/{claude.name}"
        decoy = self.home / "decoy.jsonl"
        decoy.write_text("x")
        data["files"][key]["source"] = str(decoy)
        manifest.write_text(json.dumps(data))
        copy = self.mirror_root / key
        old = copy.read_bytes()
        os.utime(claude, (MTIME + 7, MTIME + 7))
        self.assertEqual(self.sync().conflicts, 1)
        self.assertEqual(copy.read_bytes(), old)

    def test_a_moved_harness_home_takes_over_its_copies(self):
        claude = self.claude()
        self.sync()
        manifest = mirror.manifest_path(self.home)
        data = json.loads(manifest.read_text())
        key = f"claude/-work-proj/{claude.name}"
        data["files"][key]["source"] = "/old/home/.claude/projects/x/y.jsonl"
        manifest.write_text(json.dumps(data))
        result = self.sync()
        self.assertEqual((result.copied, result.conflicts), (1, 0))
        self.assertEqual(mirror.load_manifest(self.home)[key]["source"], str(claude))

    def test_a_second_pass_is_refused_while_one_runs(self):
        self.claude()
        mirror.sync(self.home, [])  # makes the mirror folder
        with mirror._pass_lock(self.home, wait=True):
            with self.assertRaises(mirror.MirrorBusy):
                mirror.sync(self.home, [], wait=False)


class ListingTests(MirrorCase):
    def test_a_live_session_is_listed_once_and_not_mirrored(self):
        self.claude()
        self.sync()
        rows = self.sessions()
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["mirrored"])
        self.assertEqual(rows[0]["resume"]["argv"], ["claude", "--resume", CLAUDE_ID])

    def test_a_deleted_source_stays_listed_and_marked_mirrored(self):
        claude = self.claude()
        self.sync()
        claude.unlink()
        rows = self.sessions()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row["mirrored"])
        self.assertEqual(row["id"], CLAUDE_ID)
        self.assertEqual(row["title"], "fix the login bug")
        self.assertEqual(row["cwd"], "/work/proj")
        self.assertEqual(row["last_active_at"], float(MTIME))
        # The harness cannot resume what it deleted.
        self.assertIsNone(row["resume"])
        self.assertIsNone(row["status"])

    def test_the_table_marks_it_and_the_legend_names_it(self):
        claude = self.claude()
        self.sync()
        claude.unlink()
        plain = run_swe(self.home, "session")
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertIn("◇", plain.stdout)
        self.assertIn("mirrored", plain.stdout)

    def test_the_legend_is_unchanged_when_nothing_is_mirrored(self):
        self.claude()
        self.assertNotIn("mirrored", run_swe(self.home, "session").stdout)

    def test_resuming_a_mirrored_session_is_refused(self):
        claude = self.claude()
        self.sync()
        claude.unlink()
        result = run_swe(self.home, "session", "use", "1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("read-only copy", result.stdout)

    def test_codex_dated_and_archived_rollouts_list_after_deletion(self):
        codex, archived = self.codex(), self.archived()
        self.sync()
        codex.unlink()
        archived.unlink()
        rows = {r["id"]: r for r in self.sessions()}
        self.assertEqual(set(rows), {CODEX_STEM, ARCHIVED_STEM})
        self.assertTrue(all(r["mirrored"] for r in rows.values()))
        self.assertEqual(rows[CODEX_STEM]["title"], "add dark mode")
        self.assertEqual(rows[ARCHIVED_STEM]["title"], "archived thread")

    def test_an_archived_thread_is_listed_once_with_its_dated_copy_left_behind(self):
        # Codex moves the rollout into archived_sessions: the dated copy's
        # source is gone, the flat one is live, and the thread is one row.
        dated = self.codex(stem=f"rollout-2026-09-02T10-00-00-{ARCHIVED_UUID}")
        self.sync()
        dated.unlink()
        self.archived()
        self.sync()
        rows = self.sessions()
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["mirrored"])

    def test_a_lost_manifest_never_lists_a_live_session_twice(self):
        self.claude()
        self.sync()
        mirror.manifest_path(self.home).unlink()
        self.assertEqual(len(self.sessions()), 1)

    def test_the_session_cache_keeps_the_flag(self):
        cache = temp_dir(self) / "session_cache.json"
        session = Session(timestamp=1.0, agent="Claude Code", path="/p", session_id="s",
                          tool_name="claude", mirrored=True)
        with mock.patch("swe.sessions.aggregator.SESSION_CACHE_FILE", cache):
            aggregator._save_cached_sessions([session])
            loaded = aggregator._load_cached_sessions()
        self.assertTrue(loaded[0].mirrored)
        # A cache an older swe wrote has no field and reads as not mirrored.
        payload = json.loads(cache.read_text())
        del payload["sessions"][0]["mirrored"]
        cache.write_text(json.dumps(payload))
        with mock.patch("swe.sessions.aggregator.SESSION_CACHE_FILE", cache):
            self.assertFalse(aggregator._load_cached_sessions()[0].mirrored)


class ReadTests(MirrorCase):
    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patch.start()
        self.addCleanup(patch.stop)

    def test_read_transcript_falls_back_to_the_mirror_copy(self):
        claude = self.claude()
        self.sync()
        session = Session(timestamp=1.0, agent="Claude Code", path="/work/proj",
                          session_id=CLAUDE_ID, tool_name="claude")
        live = read_transcript(session)
        self.assertTrue(live.readable)
        claude.unlink()
        fallback = read_transcript(session)
        self.assertTrue(fallback.readable, fallback.error)
        self.assertEqual([m.text for m in fallback.messages],
                         [m.text for m in live.messages])
        self.assertIn(str(self.mirror_root), fallback.source_paths[0])

    def test_a_codex_rollout_reads_back_after_pruning(self):
        codex = self.codex()
        self.sync()
        codex.unlink()
        session = Session(timestamp=1.0, agent="Codex CLI", path="/work/other",
                          session_id=CODEX_STEM, tool_name="codex")
        transcript = read_transcript(session)
        self.assertTrue(transcript.readable, transcript.error)
        self.assertEqual(transcript.messages[0].text, "add dark mode")

    def test_with_no_copy_it_is_still_not_found(self):
        session = Session(timestamp=1.0, agent="Claude Code", path="/p",
                          session_id="nope", tool_name="claude")
        transcript = read_transcript(session)
        self.assertFalse(transcript.readable)
        self.assertEqual(transcript.error, "transcript file not found")

    def test_a_live_transcript_is_read_from_the_harness_not_the_copy(self):
        self.claude()
        self.sync()
        session = Session(timestamp=1.0, agent="Claude Code", path="/work/proj",
                          session_id=CLAUDE_ID, tool_name="claude")
        self.assertNotIn("transcripts", read_transcript(session).source_paths[0])


class SearchTests(MirrorCase):
    def count(self, text="login") -> int:
        return len({h.session_id for h in search_index.search(self.home, text)})

    def rows(self) -> int:
        import sqlite3
        conn = sqlite3.connect(search_index.index_path(self.home))
        try:
            return conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
        finally:
            conn.close()

    def test_an_indexed_session_is_not_counted_again_after_its_source_is_pruned(self):
        claude = self.claude()
        writer.run_pass(self.home)  # indexes the live file, and mirrors it
        self.assertEqual(self.rows(), 1)
        claude.unlink()
        result = writer.run_pass(self.home)
        self.assertEqual(self.rows(), 1)
        self.assertEqual(result.mirror["orphaned"], 1)
        self.assertEqual(self.count(), 1)

    def test_a_pruned_session_the_index_never_read_is_indexed_from_its_copy(self):
        claude = self.claude()
        mirror.run_pass(self.home)  # mirrored, never indexed
        claude.unlink()
        writer.run_pass(self.home)
        self.assertEqual(self.rows(), 1)
        self.assertEqual(self.count(), 1)
        writer.run_pass(self.home)
        self.assertEqual(self.rows(), 1)

    def test_a_returning_source_retires_the_copys_rows(self):
        claude = self.claude()
        mirror.run_pass(self.home)
        claude.unlink()
        writer.run_pass(self.home)
        self.assertEqual(self.rows(), 1)
        write_jsonl(claude, claude_records(), MTIME + 100)  # restored by hand
        writer.run_pass(self.home)
        self.assertEqual(self.rows(), 1)
        self.assertEqual(self.count(), 1)

    def test_the_index_schema_is_untouched(self):
        self.claude()
        writer.run_pass(self.home)
        import sqlite3
        conn = sqlite3.connect(search_index.index_path(self.home))
        try:
            meta = dict(conn.execute("SELECT key, value FROM meta"))
        finally:
            conn.close()
        self.assertEqual(meta["schema_version"], "1")

    def test_the_index_pass_reports_what_it_mirrored(self):
        self.claude()
        result = writer.run_pass(self.home)
        self.assertEqual(result.mirror["copied"], 1)
        self.assertNotIn("mirror", result.as_dict())


class CommandTests(MirrorCase):
    def test_json_reports_counts_and_bytes(self):
        claude = self.claude()
        result = run_swe(self.home, "session", "mirror", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = decode(result)["data"]
        self.assertEqual(data["pass"]["copied"] + data["pass"]["cloned"], 1)
        self.assertEqual(data["pass"]["copied_bytes"] + data["pass"]["cloned_bytes"],
                         claude.stat().st_size)
        self.assertEqual((data["entries"], data["bytes"], data["enabled"]),
                         (1, claude.stat().st_size, True))
        claude.unlink()
        again = decode(run_swe(self.home, "session", "mirror", "--json"))["data"]
        self.assertEqual((again["pass"]["copied"] + again["pass"]["cloned"],
                          again["pass"]["orphaned"]), (0, 1))
        self.assertEqual(again["pass"]["orphaned_bytes"], data["bytes"])

    def test_status_runs_no_pass(self):
        self.claude()
        data = decode(run_swe(self.home, "session", "mirror", "--status", "--json"))["data"]
        self.assertIsNone(data["pass"])
        self.assertEqual(data["entries"], 0)

    def test_bad_flags_are_refused(self):
        self.assertEqual(run_swe(self.home, "session", "mirror", "--bogus").returncode, 1)
        self.assertEqual(run_swe(self.home, "session", "mirror", "--on", "--off").returncode, 1)

    def test_the_index_command_runs_the_mirror_too(self):
        self.claude()
        data = decode(run_swe(self.home, "session", "index", "--json"))["data"]
        self.assertEqual(data["mirror"]["copied"] + data["mirror"]["cloned"], 1)


class HubLoopTests(MirrorCase):
    class OneCycle(threading.Event):
        def wait(self, timeout=None):
            self.set()
            return True

    def cycle(self):
        from swe.remote import hub

        hub.HubState._search_index_loop(mock.Mock(home=self.home, _stop=self.OneCycle()))

    def test_the_loop_mirrors_even_while_search_indexing_is_off(self):
        self.claude()
        self.cycle()
        self.assertFalse(search_index.index_path(self.home).exists())
        self.assertEqual(len(list((self.mirror_root / "claude").rglob("*.jsonl"))), 1)

    def test_the_loop_leaves_the_mirror_alone_when_it_is_off(self):
        self.claude()
        mirror.set_enabled(self.home, False)
        self.cycle()
        self.assertFalse((self.mirror_root / "claude").exists())


if __name__ == "__main__":
    unittest.main()
