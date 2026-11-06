"""swe sync edges: git that fails, hangs or is missing, hubs sync can't
work with, and malformed merge input. Throwaway repositories only, as in
test_sync.py."""

import builtins
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from swe.sync import repo
from tests._tempdir import temp_dir
from tests.test_sync import SyncTestBase, git


def _done(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["git"], returncode, stdout, stderr)


class TestGitRunner(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir(self)

    def test_timeout_and_missing_git_are_sync_errors(self):
        cases = [
            (subprocess.TimeoutExpired(["git"], 5), "git fetch took longer than"),
            (OSError("no such file"), "could not run git: no such file"),
        ]
        for exc, text in cases:
            with self.subTest(exc=exc), \
                    mock.patch.object(repo.subprocess, "run", side_effect=exc):
                with self.assertRaises(repo.SyncError) as cm:
                    repo._git(self.root, "fetch")
                self.assertIn(text, str(cm.exception))

    def test_a_failure_names_gits_last_line(self):
        with mock.patch.object(repo.subprocess, "run", return_value=_done(
                128, stderr="hint: one\nfatal: bad object\n")):
            with self.assertRaises(repo.SyncError) as cm:
                repo._git(self.root, "show", "x")
        self.assertEqual(str(cm.exception), "git show failed: fatal: bad object")
        with mock.patch.object(repo.subprocess, "run",
                               return_value=_done(1)):
            with self.assertRaises(repo.SyncError) as cm:
                repo._git(self.root, "commit")
        self.assertEqual(str(cm.exception), "git commit failed")
        with mock.patch.object(repo.subprocess, "run",
                               return_value=_done(1, stdout="partial")):
            self.assertEqual(repo._git(self.root, "x", check=False).returncode, 1)

    def test_count_of_unreadable_output_is_zero(self):
        with mock.patch.object(repo, "_out", return_value="not a number"):
            self.assertEqual(repo._count(self.root, "a..b"), 0)
        with mock.patch.object(repo, "_out", return_value=""):
            self.assertEqual(repo._count(self.root, "a..b"), 0)


class TestMessagesAndParsing(unittest.TestCase):
    def test_commit_message_caps_the_file_list(self):
        state = mock.Mock(machine="desk")
        changes = [("M", f"skills/s{i}/SKILL.md")
                   for i in range(repo.MESSAGE_FILE_LIMIT + 3)]
        text = repo.commit_message(state, changes)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith(
            f"swe sync from desk: {len(changes)} files (skills "))
        self.assertEqual(lines[-1], "... and 3 more")
        self.assertEqual(len(lines), 2 + repo.MESSAGE_FILE_LIMIT + 1)
        one = repo.commit_message(state, [("A", "mcp.json")])
        self.assertIn("1 file (mcp.json 1)", one)

    def test_parse_name_status_skips_empty_codes(self):
        raw = "M\0a.txt\0\0ignored\0D\0b.txt\0"
        self.assertEqual(repo._parse_name_status(raw),
                         [("M", "a.txt"), ("D", "b.txt")])

    def test_parse_stages_stops_at_the_end_marker(self):
        raw = ("100644 aaa 1\tconfig/harness.json\0"
               "100644 bbb 2\tconfig/harness.json\0\0"
               "trailing message text")
        self.assertEqual(repo._parse_stages(raw), {
            "config/harness.json": {1: ("100644", "aaa"),
                                    2: ("100644", "bbb")}})

    def test_never_synced_always_lists_secrets(self):
        with mock.patch.object(repo, "GITIGNORE_BODY", "cache/\n# note\n\n"):
            self.assertEqual(repo.never_synced(), ["cache", "secrets"])
        self.assertTrue(repo._is_never_synced("secrets/token"))
        self.assertFalse(repo._is_never_synced("secretsfile"))


class TestResolve(unittest.TestCase):
    """_resolve settles only the registry, and only clean JSON objects."""

    REG = repo.REGISTRY_PATH

    def setUp(self):
        self.root = temp_dir(self)

    def _blobs(self, **texts):
        def fake_git(root, *args, **kw):
            return _done(stdout=texts[args[-1]])

        return mock.patch.object(repo, "_git", side_effect=fake_git)

    def test_other_paths_or_a_side_that_deleted_it(self):
        self.assertIsNone(repo._resolve(self.root, {"AGENTS.md": {}}))
        self.assertIsNone(repo._resolve(self.root, {
            self.REG: {}, "AGENTS.md": {}}))
        self.assertIsNone(repo._resolve(self.root, {
            self.REG: {1: ("100644", "a"), 2: ("100644", "b")}}))

    def test_a_link_or_executable_is_not_merged(self):
        stages = {self.REG: {2: ("120000", "b"), 3: ("100644", "c")}}
        with self._blobs(b="{}", c="{}"):
            self.assertIsNone(repo._resolve(self.root, stages))

    def test_versions_that_are_not_json_objects(self):
        stages = {self.REG: {2: ("100644", "b"), 3: ("100644", "c")}}
        for ours in ("{broken", "[1, 2]"):
            with self.subTest(ours=ours), self._blobs(b=ours, c="{}"):
                self.assertIsNone(repo._resolve(self.root, stages))

    def test_both_sides_added_it_merges_against_empty(self):
        stages = {self.REG: {2: ("100644", "b"), 3: ("100644", "c")}}
        ours = json.dumps({"claude": {"bin": "claude"}})
        theirs = json.dumps({"codex": {"bin": "codex"}})
        with self._blobs(b=ours, c=theirs):
            got = repo._resolve(self.root, stages)
        merged = json.loads(got[self.REG])
        self.assertEqual(set(merged), {"claude", "codex"})


class TestSyncLock(unittest.TestCase):
    def test_no_fcntl_is_a_sync_error(self):
        real_import = builtins.__import__

        def no_fcntl(name, *a, **kw):
            if name == "fcntl":
                raise ImportError("no fcntl here")
            return real_import(name, *a, **kw)

        with mock.patch.dict(sys.modules), \
                mock.patch.object(builtins, "__import__", no_fcntl):
            sys.modules.pop("fcntl", None)
            with self.assertRaises(repo.SyncError) as cm:
                with repo._sync_lock(temp_dir(self)):
                    pass
        self.assertIn("needs fcntl", str(cm.exception))

    def test_an_unopenable_lock_file_is_a_sync_error(self):
        missing = temp_dir(self) / "no-such-git-dir"
        with self.assertRaises(repo.SyncError) as cm:
            with repo._sync_lock(missing):
                pass
        self.assertIn("can't open", str(cm.exception))


class TestInspectRefusals(SyncTestBase):
    def test_git_not_installed(self):
        home = self.machine("a")
        with mock.patch.object(repo.shutil, "which", return_value=None):
            with self.assertRaises(repo.SyncError) as cm:
                repo.inspect(home)
        self.assertIn("git is not installed", str(cm.exception))

    def test_a_hub_inside_another_repository(self):
        home = self.root / "nested"
        home.mkdir()
        git(home, "init", "-q")
        (home / ".swe").mkdir()
        with self.assertRaises(repo.SyncError) as cm:
            repo.inspect(home)
        self.assertIn("inside another git repository", str(cm.exception))

    def test_detached_head(self):
        home = self.machine("a")
        hub = home / ".swe"
        git(hub, "checkout", "-q", "--detach")
        with self.assertRaises(repo.SyncError) as cm:
            repo.inspect(home)
        self.assertIn("detached HEAD", str(cm.exception))
        doc = repo.status(home)
        self.assertFalse(doc["ready"])
        self.assertIn("detached HEAD", doc["problem"])

    def test_device_branch_with_no_default_branch_on_the_remote(self):
        home = self.machine("a", branch="device/desk")
        hub = home / ".swe"
        git(hub, "remote", "set-head", "origin", "-d")
        git(hub, "update-ref", "-d", "refs/remotes/origin/main")
        state = repo.inspect(home)
        self.assertEqual(state.mode, "device")
        self.assertIsNone(state.base)


class TestStatusProblems(SyncTestBase):
    def test_no_remote(self):
        home = self.machine("a")
        git(home / ".swe", "remote", "remove", "origin")
        doc = repo.status(home, do_fetch=True)
        self.assertFalse(doc["ready"])
        self.assertIn("no remote named origin", doc["problem"])
        self.assertFalse(doc["fetched"])
        self.assertIn("no remote named origin", doc["fetch_error"])
        with self.assertRaises(repo.SyncError):
            repo.fetch(repo.inspect(home))

    def test_a_merge_in_progress(self):
        home = self.machine("a")
        hub = home / ".swe"
        head = git(hub, "rev-parse", "HEAD")
        (Path(git(hub, "rev-parse", "--absolute-git-dir")) / "MERGE_HEAD") \
            .write_text(head + "\n")
        doc = repo.status(home)
        self.assertFalse(doc["ready"])
        self.assertIn("in the middle of a merge", doc["problem"])
        with self.assertRaises(repo.SyncError) as cm:
            self.sync(home)
        self.assertIn("in the middle of a merge", str(cm.exception))

    def test_a_change_read_that_fails_is_the_problem(self):
        home = self.machine("a")
        with mock.patch.object(repo, "pending_changes",
                               side_effect=repo.SyncError("index locked")):
            doc = repo.status(home)
        self.assertFalse(doc["ready"])
        self.assertEqual(doc["problem"], "index locked")
        self.assertEqual(doc["changes"], [])

    def test_unborn_branch_counts_nothing_ahead(self):
        home = self.root / "fresh"
        hub = home / ".swe"
        hub.mkdir(parents=True)
        git(hub, "init", "-q", "-b", "main")
        git(hub, "remote", "add", "origin", str(self.remote))
        doc = repo.status(home)
        self.assertEqual((doc["ahead"], doc["behind"]), (0, 0))
        self.assertIsNone(doc["upstream"])


class TestPushFailure(SyncTestBase):
    def test_a_refused_push_reports_gits_reason(self):
        home = self.machine("a")
        (home / ".swe" / "x.txt").write_text("x\n")
        real = repo._git

        def refusing(root, *args, **kw):
            if args[:1] == ("push",):
                return _done(1, stderr="To remote\n ! [rejected] (fetch first)\n")
            return real(root, *args, **kw)

        with mock.patch.object(repo, "_git", refusing):
            doc = self.sync(home)
        self.assertEqual(doc["result"], "push-failed")
        self.assertEqual(doc["push_error"].strip(), "! [rejected] (fetch first)")
        with mock.patch.object(repo, "_git", lambda root, *a, **kw: (
                _done(1) if a[:1] == ("push",) else real(root, *a, **kw))):
            (home / ".swe" / "y.txt").write_text("y\n")
            doc = self.sync(home)
        self.assertEqual(doc["push_error"], "git push failed")


class TestMcpServerChanges(SyncTestBase):
    def test_missing_unreadable_and_malformed_mcp_json(self):
        home = self.machine("a")
        hub = home / ".swe"
        base = git(hub, "rev-parse", "HEAD")  # no mcp.json at all
        (hub / "mcp.json").write_text("{not json")
        git(hub, "add", "mcp.json")
        git(hub, "commit", "-q", "-m", "broken")
        self.assertEqual(repo.mcp_server_changes(home, base), ([], []))
        broken = git(hub, "rev-parse", "HEAD")
        (hub / "mcp.json").write_text(json.dumps({"mcpServers": ["x"]}))
        git(hub, "commit", "-qam", "list")
        self.assertEqual(repo.mcp_server_changes(home, broken), ([], []))
        listed = git(hub, "rev-parse", "HEAD")
        (hub / "mcp.json").write_text(json.dumps(
            {"mcpServers": {"docs": {"command": "x"}}}))
        git(hub, "commit", "-qam", "real")
        self.assertEqual(repo.mcp_server_changes(home, listed),
                         (["docs"], []))
        self.assertEqual(repo.mcp_server_changes(home, None), (["docs"], []))


if __name__ == "__main__":
    unittest.main()
