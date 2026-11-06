"""swe hooks: refusals, failure paths and --json shapes of the
info / edit / check / delete / disable / sync commands."""

import json
import os
import shutil
import sys
import unittest
from io import StringIO
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from swe.hooks import commands as hooks_commands
from swe.hooks import shared as _shared
from swe.init.layout import hooks_dir, linkignore_file

from tests._tempdir import temp_dir
from tests.test_hooks_shared import _run, _shared_hook

STOP_HOOK = "# events: [Stop]\nimport sys\nsys.exit(0)\n"


class _Home(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        (self.home / ".swe").mkdir()
        (self.home / ".claude").mkdir()

    def hook(self, harness, name, body="x\n"):
        path = hooks_dir(self.home) / harness / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        return path

    def foreign(self, name):
        root = self.home / ".claude/hooks"
        root.mkdir(parents=True, exist_ok=True)
        return root / name

    def run_json(self, *args):
        code, out = _run(self.home, *args, "--json")
        return code, json.loads(out)["data"]


class TestBareHarnessFlag(_Home):
    def test_every_verb_refuses_the_space_form(self):
        self.hook("claude", "gate.py")
        for verb in ("info", "edit", "delete", "disable", "enable"):
            with self.subTest(verb=verb):
                code, out = _run(self.home, verb, "gate", "--harness")
                self.assertEqual(code, 1)
                self.assertIn("--harness takes a value", out)


class TestInfo(_Home):
    def test_disabled_undeclared_hook_scoped_by_harness(self):
        self.hook("claude", "gate.py")
        self.hook("droid", "gate.py")
        _shared.set_disabled(hooks_dir(self.home) / "claude", "gate.py", True)
        code, out = _run(self.home, "info", "gate", "--harness=claude")
        self.assertEqual(code, 0, out)
        self.assertIn("disabled — swe hooks enable restores", out)
        self.assertIn("not declared", out)
        self.assertNotIn("droid", out)

    def test_directory_hook(self):
        (hooks_dir(self.home) / "claude/bundle").mkdir(parents=True)
        code, out = _run(self.home, "info", "bundle")
        self.assertEqual(code, 0, out)
        self.assertIn("a directory hook", out)
        self.assertNotIn("doc:", out)

    def test_harness_filter_that_matches_nothing(self):
        self.hook("claude", "gate.py")
        (self.home / ".factory").mkdir()
        code, out = _run(self.home, "info", "gate", "--harness=droid")
        self.assertEqual(code, 1)
        self.assertIn("No hook script named gate", out)


class TestEditEdges(_Home):
    def test_an_unparseable_editor_falls_back_to_vi(self):
        with mock.patch.dict(os.environ, {"VISUAL": 'code "-w', "EDITOR": ""}):
            self.assertEqual(hooks_commands._editor_argv(), ["vi"])
        with mock.patch.dict(os.environ, {"VISUAL": "   ", "EDITOR": ""}):
            self.assertEqual(hooks_commands._editor_argv(), ["vi"])

    def test_an_unreadable_source_is_no_target(self):
        row = SimpleNamespace(source=self.hook("claude", "gate.py"))
        with mock.patch.object(Path, "is_symlink",
                               side_effect=OSError("denied")):
            self.assertIsNone(hooks_commands._edit_target(row))

    def test_harness_filter_and_foreign_only_refusal(self):
        self.hook("claude", "gate.py")
        (self.home / ".factory").mkdir()
        self.hook("droid", "gate.py")
        seen = {}

        def fake_run(argv, **kw):
            seen["argv"] = argv
            return SimpleNamespace(returncode=3)

        with mock.patch("subprocess.run", fake_run):
            code, _ = _run(self.home, "edit", "gate", "--harness=claude")
            # The editor's own exit status is the command's.
            self.assertEqual(code, 3)
            self.assertIn("/claude/", seen["argv"][-1])
            seen.clear()
            self.foreign("stray.sh").write_text("x")
            code, out = _run(self.home, "edit", "stray.sh")
        self.assertEqual(code, 1)
        self.assertIn("foreign hook", out)
        self.assertEqual(seen, {})  # no editor for a file swe doesn't own


class TestCheckEdges(_Home):
    def test_an_unreadable_linkignore_is_warned_about_not_fatal(self):
        linkignore_file(self.home).write_bytes(b"\xff\xfe not utf-8")
        err = StringIO()
        with redirect_stderr(err):
            code, out = _run(self.home, "check")
        self.assertEqual(code, 0, out)
        self.assertIn("checking without it", err.getvalue())

    def test_a_managed_settings_file_is_a_note_not_drift(self):
        (hooks_dir(self.home) / "claude").mkdir(parents=True)
        real = self.home / "dotfiles/settings.json"
        real.parent.mkdir()
        real.write_text("{}")
        (self.home / ".claude/settings.json").symlink_to(real)
        code, out = _run(self.home, "check")
        self.assertEqual(code, 0, out)
        self.assertIn("[claude]", out)
        self.assertNotIn("findings", out)
        code, data = self.run_json("check")
        self.assertEqual(code, 0)
        self.assertEqual({f["kind"] for f in data["findings"]}, {"managed"})


class TestDeleteRefusals(_Home):
    def test_usage_and_flag_mismatches(self):
        self.hook("claude", "gate.py")
        code, out = _run(self.home, "delete")
        self.assertEqual(code, 1)
        self.assertIn("Usage: swe hooks delete", out)
        code, out = _run(self.home, "delete", "gate", "--foreign", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("not a foreign hook", out)
        err = StringIO()
        with redirect_stderr(err):
            code, out = _run(self.home, "delete", "gate", "--json")
        self.assertEqual(code, 1)
        # stdout stays empty: --json promises one document or nothing.
        self.assertEqual(out, "")
        self.assertIn("needs --yes", err.getvalue())
        self.assertTrue((hooks_dir(self.home) / "claude/gate.py").exists())

    def test_end_of_input_at_the_prompt_refuses(self):
        src = self.hook("claude", "gate.py")
        for exc in (EOFError, KeyboardInterrupt):
            with self.subTest(exc=exc), \
                    mock.patch.object(sys.stdin, "isatty", return_value=True), \
                    mock.patch("swe.prompt.read_line", side_effect=exc):
                code, out = _run(self.home, "delete", "gate")
            self.assertEqual(code, 1)
            self.assertIn("Refused", out)
        self.assertTrue(src.exists())


class TestDeleteFiles(_Home):
    def test_json_reports_deleted_and_the_declarations_left(self):
        src = self.hook("claude", "gate.py")
        dest = self.foreign("gate.py")
        dest.symlink_to(src)
        settings = self.home / ".claude/settings.json"
        settings.write_text(json.dumps({"hooks": {
            "Stop": [{"hooks": [{"command": "gate.py"}]}]}}))
        code, data = self.run_json("delete", "gate", "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(len(data["deleted"]), 1)
        self.assertIn("gate.py", data["deleted"][0])
        self.assertEqual(data["failed"], [])
        self.assertEqual(data["declarations_left"], [str(settings)])
        self.assertFalse(src.exists())
        # The destination link pointed at the deleted source: gone too.
        self.assertFalse(dest.is_symlink())

    def test_a_directory_hook_is_removed_whole(self):
        bundle = hooks_dir(self.home) / "claude/bundle"
        (bundle / "inner").mkdir(parents=True)
        (bundle / "inner/run.sh").write_text("x")
        code, out = _run(self.home, "delete", "bundle", "--yes")
        self.assertEqual(code, 0, out)
        self.assertFalse(bundle.exists())

    def test_a_failed_removal_says_failed_and_exits_nonzero(self):
        bundle = hooks_dir(self.home) / "claude/bundle"
        bundle.mkdir(parents=True)
        src = self.hook("claude", "gate.py")
        with mock.patch.object(shutil, "rmtree",
                               side_effect=OSError("busy")):
            code, out = _run(self.home, "delete", "bundle", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("failed", out)
        self.assertTrue(bundle.exists())
        with mock.patch.object(Path, "unlink",
                               side_effect=PermissionError("read-only")):
            code, data = self.run_json("delete", "gate", "--yes")
        self.assertEqual(code, 1)
        self.assertEqual(data["deleted"], [])
        self.assertIn("read-only", data["failed"][0])
        self.assertTrue(src.exists())

    def test_a_source_already_gone_counts_as_deleted(self):
        src = self.hook("claude", "gate.py")
        real_unlink = Path.unlink

        def unlink(path, *a, **kw):
            if path == src:
                real_unlink(path)  # someone else got there first
                raise FileNotFoundError(str(path))
            return real_unlink(path, *a, **kw)

        with mock.patch.object(Path, "unlink", unlink):
            code, out = _run(self.home, "delete", "gate", "--yes")
        self.assertEqual(code, 0, out)
        self.assertIn("deleted", out)


class TestDeleteForeign(_Home):
    def test_a_foreign_directory_is_skipped(self):
        # A declared command can name a folder; --foreign removes files
        # only, never a tree swe did not put there.
        folder = self.home / "tools"
        folder.mkdir()
        (folder / "run.sh").write_text("x")
        row = SimpleNamespace(tier="foreign", source=folder)
        done = hooks_commands._delete_row_files([row], self.home)
        self.assertEqual(len(done), 1)
        self.assertTrue(done[0].startswith("skipped"), done)
        self.assertTrue((folder / "run.sh").exists())

    def test_foreign_unlink_failures_and_races(self):
        target = self.foreign("other.sh")
        target.write_text("x")
        with mock.patch.object(Path, "unlink",
                               side_effect=PermissionError("denied")):
            code, data = self.run_json("delete", "other.sh", "--foreign",
                                       "--yes")
        self.assertEqual(code, 1)
        self.assertIn("denied", data["failed"][0])
        self.assertTrue(target.exists())
        with mock.patch.object(Path, "unlink",
                               side_effect=FileNotFoundError("gone")):
            code, data = self.run_json("delete", "other.sh", "--foreign",
                                       "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(data["failed"], [])
        self.assertEqual(len(data["deleted"]), 1)

    def test_rows_naming_one_file_delete_it_once(self):
        target = self.home / "tools/bridge.sh"
        rows = [SimpleNamespace(tier="foreign", source=target)] * 2
        target.parent.mkdir()
        target.write_text("x")
        done = hooks_commands._delete_row_files(rows, self.home)
        self.assertEqual(done, [f"deleted {target}"])


class TestToggleAndSync(_Home):
    def test_shared_hook_refuses_a_harness_scope(self):
        _shared_hook(self.home, "gate.py", STOP_HOOK)
        with mock.patch("shutil.which", return_value=None):
            code, out = _run(self.home, "disable", "gate", "--harness=claude")
        self.assertEqual(code, 1)
        self.assertIn("shared hook", out)
        self.assertEqual(_shared.disabled_names(_shared.shared_dir(self.home)),
                         set())

    def test_toggle_json(self):
        src = self.hook("claude", "gate.py")
        code, data = self.run_json("disable", "gate")
        self.assertEqual(code, 0)
        self.assertEqual((data["name"], data["state"], data["sources"]),
                         ("gate", "disabled", [str(src)]))
        code, data = self.run_json("enable", "gate")
        self.assertEqual(data["state"], "enabled")

    def test_sync_json_then_prunes_a_removed_shared_source(self):
        shared = _shared_hook(self.home, "gate.py", STOP_HOOK)
        with mock.patch("shutil.which", return_value=None):
            code, data = self.run_json("sync")
            self.assertEqual(code, 0)
            stub = _shared.generated_dir(self.home, "claude") / "gate.py"
            self.assertIn(str(stub), data["generated"])
            shared.unlink()
            code, out = _run(self.home, "sync")
        self.assertEqual(code, 0, out)
        self.assertIn("pruned", out)
        self.assertFalse(stub.exists())

    def test_sync_reports_link_changes(self):
        self.hook("claude", "gate.py")
        # A regular file where the link goes: sync leaves it and says so.
        self.foreign("gate.py").write_text("someone else's")
        code, out = _run(self.home, "sync")
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"\w+: 1")


if __name__ == "__main__":
    unittest.main()
