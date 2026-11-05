import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from swe.console import strip_ansi
from swe.mcp.discover import MergeResult, McpFinding
from swe.harness.discover import HarnessFinding
from swe.setup.commands import cmd_setup
from swe.setup.wizard import (
    StageOutcome,
    _ask_yes_no,
    _stage_harnesses,
    _stage_check,
    _stage_mcp,
    _stage_providers,
    _stage_report,
    _stage_skills,
    backup_file,
    run_setup_wizard,
)
from swe.skills.symlinks import SkillsSymlinkHint
from tests._tempdir import temp_dir


class SetupWizardTest(unittest.TestCase):
    def setUp(self):
        # The hub stage joins only while ~/.swe can take a clone, which a
        # throwaway HOME always can; these tests are about the other six.
        patcher = patch("swe.init.hub.can_clone", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_backup_file_copies_into_the_ignored_backups_dir(self):
        # A sibling *.bak.<time> landed in the versioned hub, where git
        # picked it up; backups/ is in the hub's .gitignore.
        from swe.paths import GITIGNORE_BODY, backups_dir_for

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            source = home / ".swe" / "config" / "config.json"
            source.parent.mkdir(parents=True)
            source.write_text('{"before": true}\n')

            backup = backup_file(source, home=home)

            self.assertEqual(source.read_text(), '{"before": true}\n')
            self.assertIsNotNone(backup)
            self.assertEqual(backup.read_text(), source.read_text())
            self.assertEqual(backup.parent, backups_dir_for(home))
            self.assertEqual(list(source.parent.iterdir()), [source])
            self.assertIn("backups/", GITIGNORE_BODY.splitlines())
            self.assertIsNone(backup_file(home / "missing.json", home=home))

    def test_backup_of_a_symlinked_file_keeps_the_old_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            real = home / "dotfiles" / "harness.json"
            real.parent.mkdir()
            real.write_text("old")
            link = home / ".swe" / "config" / "harness.json"
            link.parent.mkdir(parents=True)
            link.symlink_to(real)
            backup = backup_file(link, home=home)
            link.write_text("new")  # the wizard writes through the link
            self.assertFalse(backup.is_symlink())
            self.assertEqual(backup.read_text(), "old")

    def test_yes_no_reprompts_then_accepts_default(self):
        answers = iter(["maybe", ""])
        output = io.StringIO()
        with redirect_stdout(output):
            result = _ask_yes_no("Continue?", default=True, input_fn=lambda _prompt: next(answers))
        self.assertTrue(result)
        self.assertIn("Enter yes or no", output.getvalue())

    @patch("swe.setup.wizard.backup_file")
    @patch("swe.setup.wizard.apply_findings", return_value=(["claude"], []))
    @patch("swe.setup.wizard.discover_harnesses")
    def test_harness_stage_confirms_and_applies_high_confidence_findings(
        self, discover, apply, backup
    ):
        discover.return_value = [
            HarnessFinding(
                name="claude",
                command="claude",
                path="/usr/local/bin/claude",
                confidence="high",
                source="catalog",
                status="new",
            )
        ]
        backup.return_value = Path("/tmp/tools.json.bak")

        outcome = _stage_harnesses(
            1,
            1,
            input_fn=lambda _prompt: "",
            home=Path("/Users/test"),
            quick=False,
        )

        self.assertEqual(outcome.status, "changed")
        discover.assert_called_once_with(include_registered=True, include_missing=True)
        apply.assert_called_once()
        self.assertEqual(outcome.backups, ("/tmp/tools.json.bak",))

    def test_full_wizard_runs_sections_in_order(self):
        calls = []

        def stage(key):
            def run(number, total, **_kwargs):
                calls.append((key, number, total))
                return StageOutcome(key, key, "ready", "ok")
            return run

        stages = {
            key: stage(key)
            for key in ("harnesses", "providers", "mcp", "skills", "report", "check")
        }
        with patch.dict("swe.setup.wizard._STAGES", stages, clear=True):
            result = run_setup_wizard(input_fn=lambda _prompt: "")

        self.assertEqual(result, 0)
        self.assertEqual([key for key, _number, _total in calls], list(stages))
        self.assertTrue(all(total == 6 for _key, _number, total in calls))

    def test_completed_stages_remain_reported_when_user_cancels(self):
        first = Mock(return_value=StageOutcome("harnesses", "Harnesses", "changed", "one"))
        second = Mock(side_effect=KeyboardInterrupt)
        stages = {
            "harnesses": first,
            "providers": second,
            "mcp": Mock(),
            "skills": Mock(),
            "report": Mock(),
            "check": Mock(),
        }
        output = io.StringIO()
        with patch.dict("swe.setup.wizard._STAGES", stages, clear=True), redirect_stdout(output):
            result = run_setup_wizard()

        self.assertEqual(result, 130)
        self.assertIn("Completed stages remain saved", output.getvalue())
        self.assertIn("Harnesses", output.getvalue())

    def test_eof_in_a_stage_stops_the_wizard_at_130(self):
        # Regression: the stage loop's `except Exception` used to catch
        # EOFError before the outer (EOFError, KeyboardInterrupt) handler,
        # so Ctrl-D marked every remaining stage failed yet still exited 0.
        first = Mock(return_value=StageOutcome("harnesses", "Harnesses", "changed", "one"))
        second = Mock(side_effect=EOFError)
        later = Mock(return_value=StageOutcome("mcp", "MCP", "ready", "ok"))
        stages = {
            "harnesses": first,
            "providers": second,
            "mcp": later,
            "skills": Mock(),
            "report": Mock(),
            "check": Mock(),
        }
        output = io.StringIO()
        with patch.dict("swe.setup.wizard._STAGES", stages, clear=True), redirect_stdout(output):
            result = run_setup_wizard()

        self.assertEqual(result, 130)
        later.assert_not_called()
        self.assertIn("Completed stages remain saved", output.getvalue())

    def test_failed_stage_is_reported_and_later_stages_continue(self):
        failed = Mock(side_effect=RuntimeError("broken registry"))
        later = Mock(return_value=StageOutcome("providers", "Providers", "ready", "ok"))
        stages = {
            "harnesses": failed,
            "providers": later,
            "mcp": Mock(return_value=StageOutcome("mcp", "MCP", "ready", "ok")),
            "skills": Mock(return_value=StageOutcome("skills", "Skills", "ready", "ok")),
            "report": Mock(return_value=StageOutcome("report", "Report", "ready", "ok")),
            "check": Mock(return_value=StageOutcome("check", "Check", "ready", "ok")),
        }
        output = io.StringIO()
        with patch.dict("swe.setup.wizard._STAGES", stages, clear=True), redirect_stdout(output):
            result = run_setup_wizard()

        self.assertEqual(result, 0)
        later.assert_called_once()
        self.assertIn("broken registry", output.getvalue())

    @patch("swe.setup.wizard.discover_provider_keys")
    @patch("swe.setup.wizard.load_provider_registry", return_value={"openai": {}})
    def test_provider_stage_distinguishes_configured_and_missing_keys(
        self, _registry, discover
    ):
        discover.return_value = [{"name": "openai", "masked": "sk-***"}]
        ready = _stage_providers(
            1, 1, input_fn=lambda _prompt: "", home=Path("/tmp/home"), quick=False
        )
        discover.return_value = [{"name": "openai", "masked": "-"}]
        attention = _stage_providers(
            1, 1, input_fn=lambda _prompt: "", home=Path("/tmp/home"), quick=False
        )

        self.assertEqual(ready.status, "ready")
        self.assertEqual(attention.status, "attention")

    @patch("swe.setup.wizard.backup_file", return_value=Path("/tmp/mcp.json.bak"))
    @patch(
    "swe.setup.wizard.apply_mcp_findings",
    return_value=MergeResult(added=["notion"], updated=[], orphaned=[], pruned=[]),
)
    @patch("swe.setup.wizard.discover_mcp_servers")
    def test_mcp_stage_applies_confirmed_findings(self, discover, apply, _backup):
        finding = McpFinding(
            name="notion",
            tools=("claude",),
            status="new",
            source_tool="claude",
            server={"command": "notion-mcp"},
        )
        discover.return_value = [finding]

        outcome = _stage_mcp(
            1, 1, input_fn=lambda _prompt: "", home=Path("/tmp/home"), quick=False
        )

        self.assertEqual(outcome.status, "changed")
        self.assertEqual(outcome.changed, ("notion",))
        apply.assert_called_once_with([finding])

    @patch("swe.setup.wizard.apply_skills_symlink_hints", return_value=["symlink codex"])
    @patch("swe.setup.wizard.skills_symlink_hints")
    def test_skills_stage_applies_safe_hint_and_rechecks(self, hints, apply):
        safe = SkillsSymlinkHint(
            label="codex",
            path=Path("/tmp/home/.codex/skills"),
            action="symlink",
            command="ln -s",
            reason="link to shared",
        )
        hints.side_effect = [[safe], []]

        outcome = _stage_skills(
            1, 1, input_fn=lambda _prompt: "", home=Path("/tmp/home"), quick=False
        )

        self.assertEqual(outcome.status, "changed")
        apply.assert_called_once_with([safe], home=Path("/tmp/home"))

    @patch("swe.setup.wizard.report_setup_complete", return_value=True)
    @patch("swe.setup.wizard.load_resolved_config", return_value={})
    def test_report_stage_quick_mode_keeps_valid_configuration(self, _load, _complete):
        outcome = _stage_report(
            1, 1, input_fn=lambda _prompt: "", home=Path("/tmp/home"), quick=True
        )

        self.assertEqual(outcome.status, "ready")
        self.assertEqual(outcome.changed, ())

    @patch("swe.setup.wizard.skills_symlink_hints", return_value=[])
    @patch("swe.setup.wizard.load_resolved_config", return_value={})
    @patch("swe.setup.wizard.report_setup_complete", return_value=False)
    @patch("swe.setup.wizard.check_config", return_value=["broken"])
    @patch("swe.setup.wizard.discover_provider_keys", return_value=[])
    @patch("swe.setup.wizard.load_provider_registry", return_value={})
    @patch("swe.setup.wizard.discover_harnesses", return_value=[])
    def test_check_stage_reports_attention_for_missing_setup(self, *_mocks):
        outcome = _stage_check(
            1, 1, input_fn=lambda _prompt: "", home=Path("/tmp/home"), quick=False
        )

        self.assertEqual(outcome.status, "attention")


def _answers(*values):
    """An input_fn that hands back ``values`` in order and records each
    prompt; running past the end is a test bug, so it raises."""
    queue = list(values)
    prompts = []

    def read(prompt):
        prompts.append(prompt)
        if not queue:
            raise AssertionError(f"unexpected prompt: {prompt!r}")
        value = queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    read.prompts = prompts
    return read


def _finding(name, status, path="/opt/bin/x", confidence="high"):
    return HarnessFinding(name=name, command=name, path=path,
                          confidence=confidence, source="catalog", status=status)


def _hint(label, action, command="ln -s a b", reason="link to shared"):
    return SkillsSymlinkHint(label=label, path=Path("/tmp/home") / label,
                             action=action, command=command, reason=reason)


class YesNoAnswersTest(unittest.TestCase):
    def test_every_spelling_of_yes_and_no_is_taken_whatever_the_default(self):
        for answer, want in (("y", True), ("YES", True), (" yes ", True),
                             ("n", False), ("No", False), (" n ", False)):
            for default in (True, False):
                with redirect_stdout(io.StringIO()) as out:
                    got = _ask_yes_no("Go?", default=default, input_fn=_answers(answer))
                self.assertIs(got, want, (answer, default))
                self.assertNotIn("Enter yes or no", out.getvalue())

    def test_the_default_shows_in_the_suffix_and_blank_takes_it(self):
        read = _answers("")
        self.assertFalse(_ask_yes_no("Go?", default=False, input_fn=read))
        self.assertIn("[y/N]", read.prompts[0])

    def test_cancel_at_the_prompt_is_not_swallowed(self):
        for exc in (EOFError(), KeyboardInterrupt()):
            with self.assertRaises(type(exc)):
                _ask_yes_no("Go?", default=True, input_fn=_answers(exc))


class WizardStageBranchesTest(unittest.TestCase):
    """The branches the happy-path tests above leave out: nothing to do,
    a refused prompt, long lists, and what a declined stage leaves alone."""

    def setUp(self):
        patcher = patch("swe.init.hub.can_clone", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.home = temp_dir(self)

    def _run(self, stage, read, home=None):
        with redirect_stdout(io.StringIO()) as out:
            outcome = stage(2, 6, input_fn=read, home=home or self.home, quick=False)
        return outcome, strip_ansi(out.getvalue())

    # harnesses

    @patch("swe.setup.wizard.apply_findings")
    @patch("swe.setup.wizard.discover_harnesses")
    def test_no_new_harness_asks_nothing_and_counts_what_is_there(self, discover, apply):
        discover.return_value = [
            _finding("claude", "registered"),
            _finding("codex", "missing"),
            # A medium-confidence guess is never offered by the wizard.
            _finding("aider", "new", confidence="medium"),
        ]
        outcome, out = self._run(_stage_harnesses, _answers())
        self.assertEqual((outcome.status, outcome.detail), ("ready", "registry is current"))
        self.assertIn("Step 2/6  AI coding harnesses", out)
        self.assertIn("1 registered and available; 1 registered but missing.", out)
        self.assertIn("No new high-confidence harnesses found", out)
        apply.assert_not_called()

    @patch("swe.setup.wizard.backup_file")
    @patch("swe.setup.wizard.apply_findings")
    @patch("swe.setup.wizard.discover_harnesses")
    def test_a_declined_harness_registration_writes_nothing(self, discover, apply, backup):
        discover.return_value = [_finding("claude", "new", path=str(self.home / "bin" / "claude")),
                                 _finding("codex", "new", path="/opt/bin/codex")]
        outcome, out = self._run(_stage_harnesses, _answers("no"))
        self.assertEqual((outcome.status, outcome.detail), ("skipped", "2 available"))
        # Inside home shows as ~/..., outside it the path is left alone.
        self.assertIn("~/bin/claude", out)
        self.assertIn("/opt/bin/codex", out)
        apply.assert_not_called()
        backup.assert_not_called()

    @patch("swe.setup.wizard.backup_file", return_value=None)
    @patch("swe.setup.wizard.apply_findings", return_value=([], []))
    @patch("swe.setup.wizard.discover_harnesses")
    def test_a_registration_that_added_nothing_is_ready_not_changed(self, discover, _apply, _backup):
        discover.return_value = [_finding("claude", "new")]
        outcome, out = self._run(_stage_harnesses, _answers(""))
        self.assertEqual(outcome.status, "ready")
        self.assertEqual((outcome.changed, outcome.backups), ((), ()))
        self.assertIn("Saved 0 harness(es): none", out)

    # providers

    @patch("swe.setup.wizard.discover_provider_keys")
    @patch("swe.setup.wizard.load_provider_registry", return_value={})
    def test_more_than_eight_providers_are_cut_with_a_count(self, _registry, discover):
        discover.return_value = ([{"name": f"p{i}", "masked": "sk-***"} for i in range(11)]
                                 + [{"name": "none", "masked": "-"}])
        outcome, out = self._run(_stage_providers, _answers())
        self.assertEqual(outcome.detail, "11 provider key(s) discovered")
        self.assertIn("11/12 providers have discoverable keys: p0, p1, p2, p3, p4, p5, p6, p7, +3 more", out)
        self.assertNotIn("p8", out)

    # mcp

    @patch("swe.setup.wizard.apply_mcp_findings")
    @patch("swe.setup.wizard.discover_mcp_servers")
    def test_no_new_mcp_server_asks_nothing(self, discover, apply):
        discover.return_value = [McpFinding("notion", ("claude",), "in_source", "claude", {}),
                                 McpFinding("gone", (), "orphaned", "", {})]
        outcome, out = self._run(_stage_mcp, _answers())
        self.assertEqual((outcome.status, outcome.detail), ("ready", "source-of-truth is current"))
        self.assertIn("already contains every discovered MCP server", out)
        apply.assert_not_called()

    @patch("swe.setup.wizard.backup_file")
    @patch("swe.setup.wizard.apply_mcp_findings")
    @patch("swe.setup.wizard.discover_mcp_servers")
    def test_a_long_mcp_list_is_cut_at_twelve_and_a_no_imports_nothing(self, discover, apply, backup):
        discover.return_value = [McpFinding(f"srv{i:02}", ("claude", "codex"), "new", "claude", {})
                                 for i in range(15)]
        outcome, out = self._run(_stage_mcp, _answers("n"))
        self.assertEqual((outcome.status, outcome.detail), ("skipped", "15 available"))
        self.assertIn("Found 15 server(s)", out)
        self.assertIn("srv11", out)
        self.assertNotIn("srv12", out)
        self.assertIn("... and 3 more; run `swe mcp discover`", out)
        self.assertIn("claude, codex", out)
        apply.assert_not_called()
        backup.assert_not_called()

    @patch("swe.setup.wizard.backup_file", return_value=None)
    @patch("swe.setup.wizard.apply_mcp_findings",
           return_value=MergeResult(added=[], updated=[], orphaned=[], pruned=[]))
    @patch("swe.setup.wizard.discover_mcp_servers")
    def test_an_import_that_added_nothing_is_ready(self, discover, _apply, _backup):
        discover.return_value = [McpFinding("x", ("claude",), "new", "claude", {})]
        outcome, out = self._run(_stage_mcp, _answers("y"))
        self.assertEqual((outcome.status, outcome.backups), ("ready", ()))
        self.assertIn("Saved 0 MCP server(s): none", out)

    # skills

    @patch("swe.setup.wizard.apply_skills_symlink_hints")
    @patch("swe.setup.wizard.skills_symlink_hints")
    def test_linked_skill_roots_ask_nothing(self, hints, apply):
        hints.return_value = [_hint("claude", "ok")]
        outcome, out = self._run(_stage_skills, _answers())
        self.assertEqual((outcome.status, outcome.detail), ("ready", "shared roots are linked"))
        self.assertIn("already share ~/.swe/skills", out)
        apply.assert_not_called()

    @patch("swe.setup.wizard.apply_skills_symlink_hints")
    @patch("swe.setup.wizard.skills_symlink_hints")
    def test_manual_only_skill_roots_say_the_command_and_change_nothing(self, hints, apply):
        hints.return_value = [_hint("cursor", "manual", command="mv ~/.cursor/skills x",
                                    reason="has its own files")]
        # No prompt: there is nothing safe to apply.
        outcome, out = self._run(_stage_skills, _answers())
        self.assertEqual((outcome.status, outcome.detail), ("attention", "1 manual action(s)"))
        self.assertIn("has its own files", out)
        self.assertIn("mv ~/.cursor/skills x", out)
        apply.assert_not_called()

    @patch("swe.setup.wizard.apply_skills_symlink_hints")
    @patch("swe.setup.wizard.skills_symlink_hints")
    def test_declined_skill_changes_are_skipped(self, hints, apply):
        hints.return_value = [_hint("codex", "symlink"), _hint("cursor", "manual")]
        outcome, _ = self._run(_stage_skills, _answers("n"))
        self.assertEqual((outcome.status, outcome.detail), ("skipped", "1 available"))
        apply.assert_not_called()

    @patch("swe.setup.wizard.apply_skills_symlink_hints")
    @patch("swe.setup.wizard.skills_symlink_hints")
    def test_a_follow_up_round_is_applied_and_leftover_manual_work_is_flagged(self, hints, apply):
        # Creating the shared root first can reveal a second safe link and
        # a root only a person can move.
        first = _hint("shared", "create_shared")
        second = _hint("codex", "symlink")
        manual = _hint("cursor", "manual")
        hints.side_effect = [[first], [second, manual]]
        apply.side_effect = [["created shared"], ["linked codex"]]
        outcome, out = self._run(_stage_skills, _answers(""))
        self.assertEqual(apply.call_args_list[1].args, ([second],))
        self.assertEqual(outcome.status, "attention")
        self.assertEqual(outcome.detail, "2 applied; 1 manual")
        self.assertEqual(outcome.changed, ("created shared", "linked codex"))
        self.assertIn("Applied 2 skills action(s).", out)

    @patch("swe.setup.wizard.apply_skills_symlink_hints", return_value=[])
    @patch("swe.setup.wizard.skills_symlink_hints")
    def test_skill_changes_that_applied_nothing_are_ready(self, hints, _apply):
        hints.side_effect = [[_hint("codex", "symlink")], []]
        outcome, _ = self._run(_stage_skills, _answers("y"))
        self.assertEqual((outcome.status, outcome.detail), ("ready", "0 applied"))

    # check

    @patch("swe.setup.wizard.skills_symlink_hints", return_value=[_hint("codex", "ok")])
    @patch("swe.setup.wizard.load_resolved_config", return_value={})
    @patch("swe.setup.wizard.report_setup_complete", return_value=True)
    @patch("swe.setup.wizard.check_config", return_value=[])
    @patch("swe.setup.wizard.discover_provider_keys",
           return_value=[{"name": "openai", "masked": "sk-***"}, {"name": "x", "masked": "-"}])
    @patch("swe.setup.wizard.load_provider_registry", return_value={})
    @patch("swe.setup.wizard.discover_harnesses",
           return_value=[_finding("claude", "registered"), _finding("aider", "new")])
    def test_a_complete_setup_passes_every_check(self, *_mocks):
        outcome, out = self._run(_stage_check, _answers())
        self.assertEqual((outcome.status, outcome.detail), ("ready", "all checks passed"))
        self.assertIn("All setup checks passed.", out)
        self.assertNotIn("!!", out)
        for line in ("OK  Harnesses: 1 available, 0 missing",
                     "OK  Provider keys: 1/2 discovered",
                     "OK  Configuration: valid",
                     "OK  Reports: configured",
                     "OK  Skills: 0 manual action(s)"):
            self.assertIn(line, out)

    @patch("swe.setup.wizard.skills_symlink_hints", return_value=[_hint("cursor", "manual")])
    @patch("swe.setup.wizard.load_resolved_config", return_value={})
    @patch("swe.setup.wizard.report_setup_complete", return_value=True)
    @patch("swe.setup.wizard.check_config", return_value=[])
    @patch("swe.setup.wizard.discover_provider_keys",
           return_value=[{"name": "openai", "masked": "sk-***"}])
    @patch("swe.setup.wizard.load_provider_registry", return_value={})
    @patch("swe.setup.wizard.discover_harnesses",
           return_value=[_finding("claude", "registered"), _finding("codex", "missing")])
    def test_a_missing_harness_and_manual_skill_work_are_counted(self, *_mocks):
        outcome, out = self._run(_stage_check, _answers())
        self.assertEqual((outcome.status, outcome.detail), ("attention", "2 item(s)"))
        self.assertIn("!!  Harnesses: 1 available, 1 missing", out)
        self.assertIn("!!  Skills: 1 manual action(s)", out)
        self.assertIn("OK  Reports: configured", out)


class WizardReportStageTest(unittest.TestCase):
    """The report stage against a real config file in a temp home."""

    def setUp(self):
        self.home = temp_dir(self)
        self.config = self.home / ".swe" / "config" / "config.json"
        self.config.parent.mkdir(parents=True)
        for target in ("swe.configuration.CONFIG_FILE", "swe.setup.wizard.CONFIG_FILE"):
            patcher = patch(target, self.config)
            patcher.start()
            self.addCleanup(patcher.stop)
        real_backup = backup_file
        patcher = patch("swe.setup.wizard.backup_file",
                        side_effect=lambda path: real_backup(path, home=self.home))
        self.backup = patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, data):
        self.config.write_text(json.dumps(data) + "\n")
        return self.config.read_text()

    def _complete(self):
        return {"report": {"session": {"harness": "claude", "model": "sonnet"},
                           "writer": {"harness": "codex", "model": "gpt-5"}}}

    def _run(self, read, quick=False):
        with redirect_stdout(io.StringIO()) as out:
            outcome = _stage_report(1, 1, input_fn=read, home=self.home, quick=quick)
        return outcome, strip_ansi(out.getvalue())

    def _backups(self):
        from swe.paths import backups_dir_for

        folder = backups_dir_for(self.home)
        return sorted(folder.iterdir()) if folder.exists() else []

    def test_runners_are_saved_and_the_old_file_is_backed_up(self):
        before = self._write({"report": {"max_workers": 2}})
        read = _answers("", "Claude", "sonnet", "codex", "gpt-5")
        outcome, out = self._run(read)

        self.assertIn("Configure report runners now? [Y/n]", read.prompts[0])
        self.assertIn("Session summarizer: not set:not set", out)
        self.assertEqual(outcome.status, "changed")
        self.assertEqual(outcome.changed, ("report runners",))
        saved = json.loads(self.config.read_text())
        self.assertEqual(saved["report"]["session"]["harness"], "claude")
        self.assertEqual(saved["report"]["writer"]["model"], "gpt-5")
        # What was already in the file survives the merge.
        self.assertEqual(saved["report"]["max_workers"], 2)
        backups = self._backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), before)
        self.assertEqual(outcome.backups, (str(backups[0]),))
        self.assertIn(f"Saved report configuration to {self.config}", out)

    def test_a_first_save_has_no_backup(self):
        outcome, _ = self._run(_answers("y", "claude", "m1", "claude", "m2"))
        self.assertEqual((outcome.status, outcome.backups), ("changed", ()))
        self.assertTrue(self.config.exists())
        self.assertEqual(self._backups(), [])

    def test_a_refused_harness_leaves_the_file_and_makes_no_backup(self):
        before = self._write({"report": {"max_workers": 2}})
        outcome, out = self._run(_answers("y", "telepathy", "m1", "codex", "m2"))
        self.assertEqual(outcome.status, "attention")
        self.assertIn("Report configuration was not changed", out)
        self.assertIn("report.session.harness", outcome.detail)
        self.assertEqual(self.config.read_text(), before)
        self.backup.assert_not_called()

    def test_declining_an_incomplete_setup_leaves_it_for_attention(self):
        before = self._write({"report": {"max_workers": 2}})
        read = _answers("n")
        outcome, _ = self._run(read)
        self.assertEqual((outcome.status, outcome.detail), ("attention", "report setup incomplete"))
        self.assertEqual(len(read.prompts), 1)
        self.assertEqual(self.config.read_text(), before)

    def test_a_complete_setup_outside_quick_mode_offers_to_reconfigure(self):
        before = self._write(self._complete())
        read = _answers("")
        outcome, out = self._run(read)
        # Blank keeps what works: the default flips to no once it is set.
        self.assertIn("Reconfigure report runners? [y/N]", read.prompts[0])
        self.assertIn("Session summarizer: claude:sonnet", out)
        self.assertIn("Final writer:       codex:gpt-5", out)
        self.assertEqual((outcome.status, outcome.detail), ("ready", "runners configured"))
        self.assertEqual(self.config.read_text(), before)

    def test_reconfiguring_offers_the_saved_answers_as_defaults(self):
        self._write(self._complete())
        read = _answers("yes", "", "opus", "", "")
        outcome, _ = self._run(read)
        self.assertEqual(outcome.status, "changed")
        self.assertIn("[claude]", read.prompts[1])
        saved = json.loads(self.config.read_text())["report"]
        self.assertEqual((saved["session"]["harness"], saved["session"]["model"]), ("claude", "opus"))
        self.assertEqual((saved["writer"]["harness"], saved["writer"]["model"]), ("codex", "gpt-5"))
        self.assertEqual(len(self._backups()), 1)

    def test_cancel_inside_the_runner_prompts_stops_the_wizard(self):
        # The banner promises Ctrl+C at any prompt stops setup, and the
        # stage loop re-raises both for the outer handler to exit 130.
        # The report stage used to swallow them here, so the wizard ran
        # on into verification and exited 0.
        before = self._write({"report": {"max_workers": 2}})
        for exc in (EOFError(), KeyboardInterrupt()):
            with self.subTest(type(exc).__name__):
                read = _answers("y", "claude", exc)
                with patch("swe.init.hub.can_clone", return_value=False), \
                        redirect_stdout(io.StringIO()) as out:
                    code = run_setup_wizard(section="report", input_fn=read, home=self.home)
                self.assertEqual(code, 130)
                self.assertIn("Setup stopped", strip_ansi(out.getvalue()))
                self.assertEqual(self.config.read_text(), before)
                self.backup.assert_not_called()


class WizardRunTest(unittest.TestCase):
    def setUp(self):
        patcher = patch("swe.init.hub.can_clone", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _stages(self, outcomes):
        calls = []

        def stage(key):
            def run(number, total, **kwargs):
                calls.append((key, number, total, kwargs["quick"]))
                result = outcomes.get(key, StageOutcome(key, key, "ready", "ok"))
                if isinstance(result, BaseException):
                    raise result
                return result
            return run

        keys = ("harnesses", "providers", "mcp", "skills", "report", "check")
        return calls, {key: stage(key) for key in keys}

    def test_a_section_alias_runs_only_that_stage(self):
        calls, stages = self._stages({})
        with patch.dict("swe.setup.wizard._STAGES", stages, clear=True), \
                redirect_stdout(io.StringIO()) as out:
            code = run_setup_wizard(section="reports", quick=True, input_fn=_answers())
        self.assertEqual(code, 0)
        self.assertEqual(calls, [("report", 1, 1, True)])
        out = strip_ansi(out.getvalue())
        self.assertIn("SWE Setup - Coding-session reports", out)
        self.assertIn("Quick mode: configured sections keep their current values.", out)

    def test_an_unknown_section_is_refused_before_any_stage(self):
        calls, stages = self._stages({})
        with patch.dict("swe.setup.wizard._STAGES", stages, clear=True), \
                redirect_stdout(io.StringIO()) as out:
            with self.assertRaisesRegex(ValueError, "unknown setup section"):
                run_setup_wizard(section="telepathy", input_fn=_answers())
        self.assertEqual(calls, [])
        self.assertEqual(out.getvalue(), "")

    def test_the_summary_lists_every_backup_and_marks_each_status(self):
        calls, stages = self._stages({
            "harnesses": StageOutcome("harnesses", "AI coding harnesses", "changed", "1 registered",
                                      ("claude",), ("/b/harness.json",)),
            "mcp": StageOutcome("mcp", "MCP servers", "skipped", "2 available"),
            "report": StageOutcome("report", "Coding-session reports", "attention", "incomplete",
                                   (), ("/b/config.json",)),
        })
        with patch.dict("swe.setup.wizard._STAGES", stages, clear=True), \
                redirect_stdout(io.StringIO()) as out:
            code = run_setup_wizard(input_fn=_answers())
        out = strip_ansi(out.getvalue())
        self.assertEqual(code, 0)
        self.assertNotIn("Quick mode", out)
        self.assertIn("++  AI coding harnesses", out)
        self.assertIn("--  MCP servers", out)
        self.assertIn("!!  Coding-session reports", out)
        self.assertIn("Backups created before changes:", out)
        backups = out.split("Backups created before changes:")[1].split("Next commands")[0]
        self.assertEqual(backups.split(), ["/b/harness.json", "/b/config.json"])

    def test_no_backups_means_no_backup_heading(self):
        _calls, stages = self._stages({})
        with patch.dict("swe.setup.wizard._STAGES", stages, clear=True), \
                redirect_stdout(io.StringIO()) as out:
            run_setup_wizard(input_fn=_answers())
        self.assertNotIn("Backups created", out.getvalue())

    def test_cancel_in_the_first_stage_prints_no_empty_summary(self):
        calls, stages = self._stages({"harnesses": KeyboardInterrupt()})
        with patch.dict("swe.setup.wizard._STAGES", stages, clear=True), \
                redirect_stdout(io.StringIO()) as out:
            code = run_setup_wizard(input_fn=_answers())
        out = strip_ansi(out.getvalue())
        self.assertEqual(code, 130)
        self.assertEqual([key for key, *_ in calls], ["harnesses"])
        self.assertIn("Setup stopped", out)
        self.assertNotIn("Setup summary", out)


class SetupCommandRoutingTest(unittest.TestCase):
    @patch("swe.setup.commands.run_setup_wizard", return_value=0)
    @patch("swe.setup.commands.sys.stdin.isatty", return_value=True)
    def test_interactive_setup_routes_section_and_quick_mode(self, _isatty, wizard):
        self.assertEqual(cmd_setup(["report", "--quick"]), 0)
        wizard.assert_called_once_with(section="report", quick=True)

    @patch("swe.setup.commands.run_setup_wizard")
    @patch("swe.setup.commands.sys.stdin.isatty", return_value=False)
    def test_section_requires_interactive_terminal(self, _isatty, wizard):
        output = io.StringIO()
        with redirect_stdout(output):
            result = cmd_setup(["mcp"])
        self.assertEqual(result, 1)
        self.assertIn("requires an interactive terminal", output.getvalue())
        wizard.assert_not_called()

    def test_unknown_section_is_actionable(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = cmd_setup(["telepathy"])
        self.assertEqual(result, 1)
        self.assertIn("Sections: harnesses, providers, mcp, skills, report, check", output.getvalue())


if __name__ == "__main__":
    unittest.main()
