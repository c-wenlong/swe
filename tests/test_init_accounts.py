"""accounts.sync.*: shared content linked into SWE's extra account homes.

Every test builds a throwaway home with fake account homes under
~/.swe/accounts/<vendor>/<id>/home and a profiles.json naming them.
"""

import json
import os
import unittest
from pathlib import Path

from swe.configuration import validate_config
from swe.init import accounts
from swe.init import commands as init_commands
from swe.init.layout import LinkStatus, agents_file, hooks_dir, skills_dir

from tests._tempdir import temp_dir


def _home(case) -> Path:
    home = temp_dir(case).resolve()
    q = home / ".swe"
    (q / "skills" / "alpha").mkdir(parents=True)
    (q / "skills" / "alpha" / "SKILL.md").write_text("# alpha\n")
    (q / "AGENTS.md").write_text("# shared\n")
    (home / ".claude").mkdir()
    return home


def _profiles(home: Path, *profiles: dict) -> None:
    root = home / ".swe" / "accounts"
    root.mkdir(parents=True, exist_ok=True)
    (root / "profiles.json").write_text(json.dumps({"schema": 1, "profiles": list(profiles)}))
    for p in profiles:
        if not p.get("isDefault"):
            (root / p["vendor"] / p["id"] / "home").mkdir(parents=True, exist_ok=True)


def _acct(home: Path, vendor: str = "claude", pid: str = "claude-work") -> Path:
    return home / ".swe" / "accounts" / vendor / pid / "home"


def _flags(home: Path, **flags) -> None:
    path = home / ".swe" / "config" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"accounts": {"sync": flags}}))


def _sync(home: Path, check_only: bool = False):
    rows, notes = accounts.sync_accounts(home, [], {}, check_only=check_only)
    return {(r.primitive, str(r.status.path)): r.result for r in rows}, notes


WORK = {"id": "claude-work", "vendor": "claude", "name": "Work", "isDefault": False}
CODEX = {"id": "codex-alt", "vendor": "codex", "name": "Alt", "isDefault": False}


class SwitchedOffTest(unittest.TestCase):
    def test_default_off_reads_nothing_and_links_nothing(self):
        home = _home(self)
        _profiles(home, WORK)
        rows, notes = _sync(home)
        self.assertEqual((rows, notes), ({}, []))
        self.assertEqual(os.listdir(_acct(home)), [])

    def test_off_never_reads_a_broken_profiles_file(self):
        home = _home(self)
        (home / ".swe" / "accounts").mkdir(parents=True)
        (home / ".swe" / "accounts" / "profiles.json").write_text("{nope")
        self.assertEqual(_sync(home), ({}, []))


class SwitchedOnTest(unittest.TestCase):
    def test_skills_link_into_every_account_home(self):
        home = _home(self)
        _profiles(home, WORK, CODEX,
                  {"id": "claude-default", "vendor": "claude", "isDefault": True},
                  {"id": "cursor-x", "vendor": "cursor", "isDefault": False},
                  {"id": "../evil", "vendor": "claude", "isDefault": False})
        _flags(home, skills=True)
        rows, _ = _sync(home)
        work, alt = _acct(home) / "skills", _acct(home, "codex", "codex-alt") / "skills"
        self.assertEqual(rows, {("skills", str(work)): "linked", ("skills", str(alt)): "linked"})
        self.assertEqual(os.readlink(work), str(skills_dir(home)))
        self.assertEqual(os.readlink(alt), str(skills_dir(home)))
        self.assertFalse((home / ".claude" / "skills").exists())
        recorded = accounts.load_manifest(home)
        self.assertEqual({e["path"] for e in recorded}, {str(work), str(alt)})
        # A second run is settled and rewrites nothing.
        self.assertEqual(set(_sync(home)[0].values()), {"linked"})

    def test_instructions_use_each_vendors_filename(self):
        home = _home(self)
        _profiles(home, WORK, CODEX)
        _flags(home, instructions=True)
        _sync(home)
        self.assertEqual(os.readlink(_acct(home) / "CLAUDE.md"), str(agents_file(home)))
        codex = _acct(home, "codex", "codex-alt") / "AGENTS.md"
        self.assertEqual(os.readlink(codex), str(agents_file(home)))

    def test_instructions_follow_the_vendors_compiled_output(self):
        # A chapter only claude reads gives claude AGENTS.claude.md; a
        # claude account's link follows it, codex's stays on the shared file.
        home = _home(self)
        directory = home / ".swe" / "AGENTS_MD"
        directory.mkdir(parents=True)
        (directory / "one.md").write_text("# Claude only\n")
        (directory / "rest.md").write_text("# Everyone\n")
        (directory / "index.txt").write_text("one.md\nrest.md\n")
        (directory / "targets.json").write_text(json.dumps(
            {"version": 1, "chapters": {"one.md": {"only": ["claude"]}}}))
        from swe.instructions.compiler import build

        build(home)
        own = home / ".swe" / "AGENTS.claude.md"
        self.assertTrue(own.is_file())

        _profiles(home, WORK, CODEX)
        _flags(home, instructions=True)
        _sync(home)
        self.assertEqual(os.readlink(_acct(home) / "CLAUDE.md"), str(own))
        codex = _acct(home, "codex", "codex-alt") / "AGENTS.md"
        self.assertEqual(os.readlink(codex), str(agents_file(home)))

        # The rule gone: build removes the output and repoints the account.
        (directory / "targets.json").write_text(
            json.dumps({"version": 1, "chapters": {}}))
        build(home)
        self.assertFalse(own.exists())
        self.assertEqual(os.readlink(_acct(home) / "CLAUDE.md"),
                         str(agents_file(home)))

    def test_a_repointed_account_link_updates_its_record(self):
        # The record holds the source swe gave the link; a build that
        # repoints it must update the record or a later sync-off would
        # keep the link as "not ours anymore".
        home = _home(self)
        directory = home / ".swe" / "AGENTS_MD"
        directory.mkdir(parents=True)
        (directory / "one.md").write_text("# Claude only\n")
        (directory / "rest.md").write_text("# Everyone\n")
        (directory / "index.txt").write_text("one.md\nrest.md\n")
        (directory / "targets.json").write_text(json.dumps(
            {"version": 1, "chapters": {"one.md": {"only": ["claude"]}}}))
        from swe.instructions.compiler import build

        build(home)
        own = home / ".swe" / "AGENTS.claude.md"
        _profiles(home, WORK)
        _flags(home, instructions=True)
        _sync(home)
        link = _acct(home) / "CLAUDE.md"
        self.assertEqual(os.readlink(link), str(own))

        (directory / "targets.json").write_text(
            json.dumps({"version": 1, "chapters": {}}))
        build(home)
        record = {e["path"]: e for e in accounts.load_manifest(home)}
        self.assertEqual(record[str(link)]["source"], str(agents_file(home)))
        # Sync off now removes the link cleanly, since it still matches
        # the recorded source.
        _flags(home, instructions=False)
        rows, _ = _sync(home)
        self.assertEqual(set(rows.values()), {"unlinked"})
        self.assertFalse(os.path.lexists(link))

    def test_hooks_link_per_script_and_skip_disabled_ones(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, hooks=True)
        hub = hooks_dir(home) / "claude"
        hub.mkdir(parents=True)
        (hub / "guard.py").write_text("print(1)\n")
        (hub / "off.py").write_text("print(2)\n")
        (hub / ".disabled").write_text("off.py\n")
        _sync(home)
        self.assertEqual(os.readlink(_acct(home) / "hooks" / "guard.py"), str(hub / "guard.py"))
        self.assertFalse(os.path.lexists(_acct(home) / "hooks" / "off.py"))

    def test_check_mode_writes_nothing(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True, instructions=True)
        rows, _ = _sync(home, check_only=True)
        self.assertEqual(set(rows.values()), {"would-create"})
        self.assertEqual(os.listdir(_acct(home)), [])
        self.assertFalse(accounts.manifest_file(home).exists())

    def test_missing_account_home_is_skipped(self):
        home = _home(self)
        _profiles(home, WORK)
        _acct(home).rmdir()
        _flags(home, skills=True)
        rows, _ = _sync(home)
        self.assertEqual(set(rows.values()), {"skipped"})

    def test_linkignore_leaves_an_account_target_alone(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        rows, _ = accounts.sync_accounts(
            home, [".swe/accounts/claude/*/home/skills"], {}, check_only=False)
        self.assertEqual([r.result for r in rows], ["ignored"])
        self.assertFalse(os.path.lexists(_acct(home) / "skills"))


class ConflictTest(unittest.TestCase):
    def test_a_real_directory_is_never_replaced_even_with_force(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        own = _acct(home) / "skills" / "mine"
        own.mkdir(parents=True)
        (own / "SKILL.md").write_text("# mine\n")
        rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["conflict"])
        init_commands.run_init(home, force=True, quiet=True, ask=False)
        self.assertTrue((own / "SKILL.md").is_file())
        self.assertFalse((_acct(home) / "skills").is_symlink())

    def test_an_identical_file_is_a_conflict_not_an_absorb(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, instructions=True)
        (_acct(home) / "CLAUDE.md").write_text("# shared\n")
        rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["conflict"])
        self.assertFalse((_acct(home) / "CLAUDE.md").is_symlink())

    def test_the_accounts_own_symlink_is_left_alone(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        elsewhere = home / "my-skills"
        elsewhere.mkdir()
        (_acct(home) / "skills").symlink_to(elsewhere)
        rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["conflict"])
        self.assertEqual(os.readlink(_acct(home) / "skills"), str(elsewhere))

    def test_a_dangling_link_is_repointed(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        (_acct(home) / "skills").symlink_to(home / "gone")
        rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["linked"])
        self.assertEqual(os.readlink(_acct(home) / "skills"), str(skills_dir(home)))

    def test_a_hooks_folder_linked_out_of_the_account_is_not_written_through(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, hooks=True)
        hub = hooks_dir(home) / "claude"
        hub.mkdir(parents=True)
        (hub / "guard.py").write_text("print(1)\n")
        (home / ".claude" / "hooks").mkdir()
        (_acct(home) / "hooks").symlink_to(home / ".claude" / "hooks")
        rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["conflict"])
        self.assertEqual(os.listdir(home / ".claude" / "hooks"), [])


class ReviewFixesTest(unittest.TestCase):
    def test_a_hooks_file_is_a_conflict_not_a_crash(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, hooks=True, skills=True)
        hub = hooks_dir(home) / "claude"
        hub.mkdir(parents=True)
        (hub / "guard.py").write_text("print(1)\n")
        (_acct(home) / "hooks").write_text("not a folder\n")
        rows, _ = _sync(home)
        self.assertEqual(rows[("hooks", str(_acct(home) / "hooks" / "guard.py"))], "conflict")
        # Work before and after it still lands and is recorded.
        self.assertEqual(rows[("skills", str(_acct(home) / "skills"))], "linked")
        self.assertEqual(len(accounts.load_manifest(home)), 1)

    def test_a_link_failure_becomes_a_conflict_row(self):
        from unittest import mock

        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        with mock.patch.object(Path, "symlink_to", side_effect=PermissionError(13, "denied")):
            rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["conflict"])
        self.assertEqual(accounts.load_manifest(home), [])

    def test_a_dotdot_link_into_the_shared_tree_is_the_accounts_own(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        private = home / ".swe" / "private-skills"
        private.mkdir()
        (_acct(home) / "skills").symlink_to(f"{skills_dir(home)}/../private-skills")
        rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["conflict"])

    def test_unlink_never_follows_a_hooks_folder_swapped_for_a_link(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, hooks=True)
        hub = hooks_dir(home) / "claude"
        hub.mkdir(parents=True)
        (hub / "guard.py").write_text("print(1)\n")
        _sync(home)
        # The account's hooks/ becomes a link to the default home's, which
        # holds a link with the very same target.
        default_hooks = home / ".claude" / "hooks"
        default_hooks.mkdir()
        (default_hooks / "guard.py").symlink_to(hub / "guard.py")
        acct_hooks = _acct(home) / "hooks"
        (acct_hooks / "guard.py").unlink()
        acct_hooks.rmdir()
        acct_hooks.symlink_to(default_hooks)
        _flags(home, hooks=False)
        rows, _ = _sync(home)
        self.assertEqual(rows, {})
        self.assertTrue((default_hooks / "guard.py").is_symlink())
        self.assertEqual(accounts.load_manifest(home), [])

    def test_a_hooks_folder_linked_within_the_account_is_not_linked_into(self):
        # Cleanup refuses a parent that is a link, so linking must refuse it
        # too, or switching off would leave the links behind.
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, hooks=True)
        hub = hooks_dir(home) / "claude"
        hub.mkdir(parents=True)
        (hub / "guard.py").write_text("print(1)\n")
        (_acct(home) / "my-hooks").mkdir()
        (_acct(home) / "hooks").symlink_to(_acct(home) / "my-hooks")
        rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["conflict"])
        self.assertEqual(os.listdir(_acct(home) / "my-hooks"), [])

    def test_without_flock_the_pass_still_runs(self):
        from unittest import mock

        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        with mock.patch.object(accounts, "fcntl", None):
            rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["linked"])

    def test_a_damaged_record_stops_the_pass(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        _sync(home)
        record = json.loads(accounts.manifest_file(home).read_text())
        record["links"].append({"path": 3})
        accounts.manifest_file(home).write_text(json.dumps(record))
        _flags(home, skills=False)
        rows, notes = _sync(home)
        self.assertEqual((rows, len(notes)), ({}, 1))
        self.assertTrue((_acct(home) / "skills").is_symlink())
        self.assertIn('"path": 3', accounts.manifest_file(home).read_text())

    def test_check_json_says_why_the_pass_was_skipped(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        (home / ".swe" / "accounts" / "profiles.json").write_text("{nope")
        run = init_commands.run_init(home, check_only=True, quiet=True)
        doc = init_commands.check_document(home, run)
        self.assertEqual(doc["accounts"], [])
        self.assertEqual(len(doc["account_notes"]), 1)

    def test_defaults_show_every_switch_off(self):
        from swe.configuration import get_value, resolve_config

        self.assertIs(get_value(resolve_config({}), "accounts.sync.skills"), False)


class SwitchOffTest(unittest.TestCase):
    def test_off_removes_only_the_links_swe_made(self):
        home = _home(self)
        _profiles(home, WORK, CODEX)
        _flags(home, skills=True)
        _sync(home)
        # The user relinks the codex account by hand; swe never made that one.
        alt = _acct(home, "codex", "codex-alt") / "skills"
        alt.unlink()
        alt.symlink_to(os.path.relpath(skills_dir(home), alt.parent))
        _flags(home, skills=False)
        rows, _ = _sync(home)
        self.assertEqual(rows, {("skills", str(_acct(home) / "skills")): "unlinked"})
        self.assertFalse(os.path.lexists(_acct(home) / "skills"))
        self.assertTrue(alt.is_symlink())
        self.assertEqual(accounts.load_manifest(home), [])
        # The shared tree itself is untouched.
        self.assertTrue((skills_dir(home) / "alpha" / "SKILL.md").is_file())

    def test_off_in_check_mode_previews_the_unlink(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, instructions=True)
        _sync(home)
        _flags(home, instructions=False)
        rows, _ = _sync(home, check_only=True)
        self.assertEqual(list(rows.values()), ["would-unlink"])
        self.assertTrue((_acct(home) / "CLAUDE.md").is_symlink())

    def test_a_recorded_link_the_user_replaced_is_forgotten_not_removed(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, instructions=True)
        _sync(home)
        link = _acct(home) / "CLAUDE.md"
        link.unlink()
        link.write_text("# my own\n")
        _flags(home, instructions=False)
        self.assertEqual(_sync(home)[0], {})
        self.assertEqual(link.read_text(), "# my own\n")

    def test_a_disabled_hook_is_unlinked_from_accounts(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, hooks=True)
        hub = hooks_dir(home) / "claude"
        hub.mkdir(parents=True)
        (hub / "guard.py").write_text("print(1)\n")
        _sync(home)
        (hub / ".disabled").write_text("guard.py\n")
        rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["unlinked"])
        self.assertTrue((hub / "guard.py").is_file())

    def test_unreadable_profiles_stop_the_pass_instead_of_unlinking(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        _sync(home)
        (home / ".swe" / "accounts" / "profiles.json").write_text("{nope")
        _flags(home, skills=False)
        rows, notes = _sync(home)
        self.assertEqual(rows, {})
        self.assertEqual(len(notes), 1)
        self.assertTrue((_acct(home) / "skills").is_symlink())

    def test_unreadable_config_stops_the_pass(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        _sync(home)
        (home / ".swe" / "config" / "config.json").write_text("{nope")
        rows, notes = _sync(home)
        self.assertEqual((rows, len(notes)), ({}, 1))
        self.assertTrue((_acct(home) / "skills").is_symlink())


class SweGuardTest(unittest.TestCase):
    """The ~/.swe guard still refuses absorb into an account home."""

    def test_init_apply_refuses_to_absorb_inside_quiver(self):
        home = _home(self)
        _profiles(home, WORK)
        target = _acct(home) / "CLAUDE.md"
        target.write_text("# shared\n")
        status = LinkStatus("claude", target, "absorb", "same text")
        result = init_commands._apply(status, agents_file(home), home, force=True)
        self.assertEqual(result, "blocked")
        self.assertFalse(target.is_symlink())

    def test_hook_apply_refuses_to_absorb_inside_quiver(self):
        from swe.init.hooks import apply_hook_status

        home = _home(self)
        _profiles(home, WORK)
        target = _acct(home) / "hooks" / "guard.py"
        target.parent.mkdir()
        target.write_text("print(1)\n")
        status = LinkStatus("claude", target, "absorb", "identical copy",
                            home / ".swe" / "hooks" / "claude" / "guard.py")
        self.assertEqual(apply_hook_status(status, home, force=True), "blocked")
        self.assertFalse(target.is_symlink())


class InitIntegrationTest(unittest.TestCase):
    def test_check_json_reports_accounts_and_switches(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        run = init_commands.run_init(home, check_only=True, quiet=True)
        doc = init_commands.check_document(home, run)
        self.assertEqual(doc["account_sync"],
                         {"instructions": False, "skills": True, "hooks": False, "mcp": False})
        self.assertEqual(doc["accounts"], [{
            "account": "claude/claude-work", "primitive": "skills",
            "path": str(_acct(home) / "skills"), "state": "would-create", "detail": "",
        }])
        # Account rows never join the paths --force would replace.
        from swe.webapp.pages.doctor import blocked_paths
        self.assertNotIn(str(_acct(home) / "skills"), blocked_paths(run, home))

    def test_accounts_flag_runs_only_the_account_pass(self):
        import io
        from contextlib import redirect_stdout
        from unittest import mock

        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        out = io.StringIO()
        with mock.patch.object(Path, "home", return_value=home), redirect_stdout(out):
            code = init_commands.cmd_init(["--accounts", "--json"])
        self.assertEqual(code, 0)
        doc = json.loads(out.getvalue())
        doc = doc.get("data", doc)
        self.assertEqual([r["state"] for r in doc["accounts"]], ["linked"])
        self.assertTrue((_acct(home) / "skills").is_symlink())
        # The default harness homes were not linked by this run.
        self.assertFalse(os.path.lexists(home / ".claude" / "skills"))
        # --force is refused with --accounts. Kept under the throwaway home
        # so a regression that let it through could not touch the real one.
        out = io.StringIO()
        with mock.patch.object(Path, "home", return_value=home), redirect_stdout(out):
            code = init_commands.cmd_init(["--accounts", "--force"])
        self.assertEqual(code, 1)
        self.assertIn("--accounts only takes --check and --json", out.getvalue())

    def _cmd(self, home: Path, *args: str) -> tuple[int, str]:
        import io
        import re
        from contextlib import redirect_stdout
        from unittest import mock

        out = io.StringIO()
        with mock.patch.object(Path, "home", return_value=home), redirect_stdout(out):
            code = init_commands.cmd_init(list(args))
        return code, re.sub(r"\x1b\[[0-9;]*m", "", out.getvalue())

    def test_init_prints_accounts_and_names_conflicts(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True, instructions=True)
        (_acct(home) / "CLAUDE.md").write_text("# mine\n")
        code, text = self._cmd(home, "--yes")
        self.assertEqual(code, 0)
        self.assertIn("Accounts", text)
        self.assertIn("Not synced, the account already has its own:", text)
        _flags(home, skills=False)
        code, text = self._cmd(home, "--full")
        self.assertIn("unlinked", text)

    def test_accounts_text_view(self):
        home = _home(self)
        code, text = self._cmd(home, "--accounts")
        self.assertEqual(code, 0)
        self.assertIn("No account sync switched on", text)
        _profiles(home, WORK)
        _flags(home, skills=True)
        code, text = self._cmd(home, "--accounts", "--check")
        self.assertIn("would-create", text)
        (home / ".swe" / "accounts" / "profiles.json").write_text("{nope")
        code, text = self._cmd(home, "--accounts")
        self.assertEqual(code, 1)
        self.assertIn("account sync skipped", text)
        (home / ".swe" / "config" / "config.json").write_text("{nope")
        code, text = self._cmd(home, "--accounts")
        self.assertEqual(code, 1)

    def test_config_set_hints_at_init(self):
        import io
        from contextlib import redirect_stdout
        from unittest import mock

        from swe import config_commands

        home = _home(self)
        path = home / ".swe" / "config" / "config.json"
        out = io.StringIO()
        with mock.patch("swe.configuration.CONFIG_FILE", path), redirect_stdout(out):
            config_commands.cmd_config(["set", "accounts.sync.skills", "true"])
            config_commands.cmd_config(["unset", "accounts.sync.skills"])
        self.assertEqual(out.getvalue().count("Run swe init"), 2)

    def test_archived_vendor_is_left_alone(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        rows, _ = accounts.sync_accounts(
            home, [], {"claude": {"state": "archived"}}, check_only=False)
        self.assertEqual([r.result for r in rows], ["ignored"])


class ConfigValidationTest(unittest.TestCase):
    def test_switches_must_be_booleans_with_known_names(self):
        issues = {i.path for i in validate_config(
            {"accounts": {"sync": {"skills": "yes", "plugins": True, "hooks": False}}})}
        self.assertEqual(issues, {"accounts.sync.skills", "accounts.sync.plugins"})

    def test_sync_must_be_an_object(self):
        issues = validate_config({"accounts": {"sync": True}})
        self.assertEqual([i.path for i in issues], ["accounts.sync"])



def _sync_with(home: Path, registry: dict, check_only: bool = False):
    rows, notes = accounts.sync_accounts(home, [], registry, check_only=check_only)
    return {(r.primitive, str(r.status.path)): r.result for r in rows}, notes


class RegistryTargetTest(unittest.TestCase):
    """harness.json decides where (and whether) each primitive lands."""

    def test_skills_off_in_the_registry_links_none(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        registry = {"claude": {"capabilities": {"skills": {"supported": False}}}}
        self.assertEqual(_sync_with(home, registry), ({}, []))
        self.assertEqual(os.listdir(_acct(home)), [])

    def test_a_declared_skills_root_lands_at_the_same_place_in_the_account(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        registry = {"claude": {"capabilities": {"skills": {
            "supported": True, "root": "~/.claude/my-skills"}}}}
        rows, _ = _sync_with(home, registry)
        self.assertEqual(rows, {("skills", str(_acct(home) / "my-skills")): "linked"})

    def test_a_skills_root_outside_the_vendor_folder_links_none(self):
        # ~/.claude-skills is not under ~/.claude, so it has no account-home
        # equivalent: nothing is linked rather than guessing one.
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        registry = {"claude": {"capabilities": {"skills": {
            "supported": True, "root": "~/.claude-skills"}}}}
        self.assertEqual(_sync_with(home, registry), ({}, []))

    def test_declared_instruction_files_inside_and_outside_the_vendor_folder(self):
        home = _home(self)
        _profiles(home, WORK, CODEX)
        _flags(home, instructions=True)
        registry = {
            "claude": {"capabilities": {"instructions": {"file": "~/.claude/rules/MAIN.md"}}},
            "codex": {"capabilities": {"instructions": {"file": "~/notes/AGENTS.md"}}},
        }
        rows, _ = _sync_with(home, registry)
        self.assertEqual(rows, {("instructions", str(_acct(home) / "rules" / "MAIN.md")): "linked"})
        self.assertEqual(os.listdir(_acct(home, "codex", "codex-alt")), [])

    def test_hooks_need_a_hub_folder_a_supported_root_and_one_in_the_vendor_folder(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, hooks=True)
        self.assertEqual(_sync_with(home, {}), ({}, []))  # no ~/.swe/hooks/claude

        hub = hooks_dir(home) / "claude"
        hub.mkdir(parents=True)
        (hub / "guard.py").write_text("print(1)\n")
        for caps in ({"supported": False}, {"supported": True, "root": str(home / "elsewhere")}):
            registry = {"claude": {"capabilities": {"hooks": caps}}}
            self.assertEqual(_sync_with(home, registry), ({}, []), caps)
        self.assertEqual(os.listdir(_acct(home)), [])


class RecordEdgeTest(unittest.TestCase):
    def test_a_profiles_file_without_a_list_or_missing_entirely(self):
        home = _home(self)
        _flags(home, skills=True)
        self.assertEqual(_sync(home), ({}, []))  # no profiles.json: no accounts

        root = home / ".swe" / "accounts"
        root.mkdir(parents=True)
        (root / "profiles.json").write_text(json.dumps({"profiles": {"id": "x"}}))
        rows, notes = _sync(home)
        self.assertEqual(rows, {})
        self.assertEqual(len(notes), 1)
        self.assertIn("no profiles list", notes[0])

    def test_an_unparseable_or_listless_record_stops_the_pass(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        record = accounts.manifest_file(home)
        record.parent.mkdir(parents=True)
        for body, why in (("{nope", "cannot read"), (json.dumps({"links": {}}), "no links list")):
            record.write_text(body)
            rows, notes = _sync(home)
            self.assertEqual(rows, {})
            self.assertIn(why, notes[0])
            self.assertEqual(os.listdir(_acct(home)), [])

    def test_a_failed_record_save_leaves_no_temp_file(self):
        from unittest import mock

        home = _home(self)
        with mock.patch.object(accounts.os, "replace", side_effect=PermissionError(13, "denied")):
            with self.assertRaises(PermissionError):
                accounts.save_manifest(home, [])
        self.assertEqual(os.listdir(accounts.manifest_file(home).parent), [])

    def test_retarget_skips_empty_input_and_unchanged_sources(self):
        from unittest import mock

        home = _home(self)
        _profiles(home, WORK)
        _flags(home, instructions=True)
        _sync(home)
        link = str(_acct(home) / "CLAUDE.md")
        with mock.patch.object(accounts, "save_manifest") as save:
            accounts.retarget_records(home, {})
            accounts.retarget_records(home, {link: str(agents_file(home))})
            accounts.retarget_records(home, {"/not/recorded": "/x"})
        save.assert_not_called()

    def test_a_recorded_link_still_wanted_but_replaced_is_dropped_from_the_record(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        _sync(home)
        link = _acct(home) / "skills"
        link.unlink()
        link.mkdir()
        rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["conflict"])
        self.assertEqual(accounts.load_manifest(home), [])
        self.assertTrue(link.is_dir())

    def test_an_unlink_failure_keeps_the_link_recorded(self):
        from unittest import mock

        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        _sync(home)
        _flags(home, skills=False)
        with mock.patch.object(Path, "unlink", side_effect=PermissionError(13, "denied")):
            rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["conflict"])
        self.assertEqual(len(accounts.load_manifest(home)), 1)
        # Once the folder is writable again the next pass removes it.
        rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["unlinked"])
        self.assertEqual(accounts.load_manifest(home), [])


class LinkShapeTest(unittest.TestCase):
    def test_a_relative_link_into_the_shared_tree_is_repointed(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        (home / ".swe" / "skills" / "beta").mkdir()
        dest = _acct(home) / "skills"
        dest.symlink_to(os.path.relpath(home / ".swe" / "skills" / "beta", dest.parent))
        rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["linked"])
        self.assertEqual(os.readlink(dest), str(skills_dir(home)))

    def test_a_link_that_loops_on_itself_is_repointed(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        dest = _acct(home) / "skills"
        dest.symlink_to(dest)
        rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["linked"])
        self.assertEqual(os.readlink(dest), str(skills_dir(home)))

    def test_link_rechecks_the_disk_before_writing(self):
        # Between plan and apply the path can change under the pass (the
        # app runs the same sync); every such change is a conflict.
        home = _home(self)
        acct = accounts.AccountHome("claude", "claude-work", _acct(home))
        _profiles(home, WORK)
        dest = _acct(home) / "skills"

        def status(state, source=skills_dir(home)):
            return LinkStatus(acct.label, dest, state, "", source)

        self.assertEqual(accounts._link(status("create", None), home), "conflict")
        # A link planned for repointing is the account's own file now:
        # refused before the unlink, so the file is still there.
        dest.write_text("mine\n")
        self.assertEqual(accounts._link(status("relink"), home), "conflict")
        self.assertEqual(dest.read_text(), "mine\n")
        # A path planned as free is taken now: refused without trying,
        # so no "could not link" detail.
        taken = status("create")
        self.assertEqual(accounts._link(taken, home), "conflict")
        self.assertEqual(taken.detail, "")
        dest.unlink()
        outside = temp_dir(self)
        acct_link = _acct(home, "codex", "codex-alt")
        acct_link.parent.mkdir(parents=True)
        acct_link.symlink_to(outside)
        moved = LinkStatus("codex", acct_link / "skills", "create", "", skills_dir(home))
        self.assertEqual(accounts._link(moved, home), "conflict")
        self.assertEqual(os.listdir(outside), [])

    def test_a_path_outside_the_accounts_root_is_not_plain(self):
        home = _home(self)
        self.assertFalse(accounts._plain(home / ".claude", home))


if __name__ == "__main__":
    unittest.main()
