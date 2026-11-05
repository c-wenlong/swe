"""``swe harness discover``'s command adapter: flag refusals, picking by
name or by ticking, and how a refused pick fails. Discovery and the
registry write are patched; their own tests live in
test_harness_discover.py."""

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from swe.console import strip_ansi
from swe.harness import discover_commands
from swe.harness.discover import HarnessFinding


def finding(name, confidence="high", status="new", path="/usr/local/bin/x"):
    return HarnessFinding(name=name, command=name, path=path,
                          confidence=confidence, source="catalog",
                          status=status, description=f"{name} cli")


FINDINGS = [finding("aider"), finding("goose", confidence="low", path=""),
            finding("claude", status="registered")]


class HarnessDiscoverCommandTest(unittest.TestCase):
    def run_cmd(self, args, findings=FINDINGS, applied=([], []),
                supported=True, picked=None, tty=True):
        out = io.StringIO()
        with patch.object(discover_commands, "discover_harnesses",
                          return_value=list(findings)) as discover, \
                patch.object(discover_commands, "apply_findings",
                             return_value=applied) as apply, \
                patch.object(discover_commands, "_supported",
                             return_value=supported), \
                patch.object(discover_commands, "multiselect",
                             return_value=picked) as multiselect, \
                patch("sys.stdin.isatty", return_value=tty), \
                redirect_stdout(out):
            code = discover_commands.cmd_discover(args)
        self.discover, self.apply, self.multiselect = discover, apply, multiselect
        return code, strip_ansi(out.getvalue())

    def test_help_and_unknown_flags(self):
        code, out = self.run_cmd(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("swe harness discover --pick", out)
        self.discover.assert_not_called()
        code, out = self.run_cmd(["--bogus"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown argument(s): --bogus", out)
        self.discover.assert_not_called()

    def test_conflicting_flags_are_refused_before_scanning(self):
        cases = {
            ("aider",): "Names only go with --apply",
            ("--pick", "--apply"): "--pick chooses for you",
            ("--pick", "--apply-all"): "--pick chooses for you",
        }
        for args, message in cases.items():
            with self.subTest(args=args):
                code, out = self.run_cmd(list(args))
                self.assertEqual(code, 1)
                self.assertIn(message, out)
                self.discover.assert_not_called()

    def test_pick_needs_a_terminal(self):
        code, out = self.run_cmd(["--pick"], supported=False)
        self.assertEqual(code, 1)
        self.assertIn("--pick needs a terminal", out)
        self.discover.assert_not_called()

    def test_all_includes_registered_and_missing(self):
        self.run_cmd(["--all"])
        self.discover.assert_called_once_with(include_registered=True,
                                              include_missing=True)

    def test_a_name_that_is_not_a_new_match_is_refused(self):
        code, out = self.run_cmd(["--apply", "claude", "nope"])
        self.assertEqual(code, 1)
        self.assertIn("Not a new match: claude, nope", out)
        self.assertIn("New matches: aider, goose", out)
        self.apply.assert_not_called()

    def test_a_name_with_no_new_matches_at_all_skips_the_hint(self):
        code, out = self.run_cmd(["--apply", "x"],
                                 findings=[finding("claude", status="registered")])
        self.assertEqual(code, 1)
        self.assertNotIn("New matches", out)

    def test_named_apply_bypasses_the_confidence_floor(self):
        code, out = self.run_cmd(["--apply", "goose"], applied=(["goose"], []))
        self.assertEqual(code, 0)
        self.assertEqual(self.apply.call_args.kwargs,
                         {"min_confidence": "high", "names": {"goose"}})
        self.assertIn("Added 1 tool(s) to registry: goose", out)

    def test_pick_starts_with_high_confidence_ticked(self):
        code, out = self.run_cmd(["--pick"], picked=["goose"],
                                 applied=(["goose"], []))
        self.assertEqual(code, 0)
        choices = self.multiselect.call_args.args[0]
        self.assertEqual([choice.key for choice in choices], ["aider", "goose"])
        # A finding with no path describes itself instead.
        self.assertIn("low · goose cli", choices[1].about)
        self.assertEqual(self.multiselect.call_args.kwargs["selected"], ["aider"])
        self.assertEqual(self.apply.call_args.kwargs["names"], {"goose"})

    def test_cancelling_the_pick_adds_nothing(self):
        code, out = self.run_cmd(["--pick"], picked=None)
        self.assertEqual(code, 0)
        self.assertIn("cancelled, nothing added", out)
        self.apply.assert_not_called()

    def test_a_pick_with_no_new_findings_skips_the_picker(self):
        code, out = self.run_cmd(["--pick"],
                                 findings=[finding("claude", status="registered")])
        self.assertEqual(code, 0)
        self.multiselect.assert_not_called()
        self.assertIn("Nothing to add (nothing ticked)", out)

    def test_apply_all_lowers_the_floor(self):
        code, out = self.run_cmd(["--apply-all"])
        self.assertEqual(code, 0)
        self.assertEqual(self.apply.call_args.kwargs["min_confidence"], "low")
        self.assertIn("no new high-confidence matches", out)

    def test_a_refused_named_pick_fails(self):
        skipped = [{"name": "goose", "reason": "binary does not answer"}]
        code, out = self.run_cmd(["--apply", "goose"], applied=([], skipped))
        self.assertEqual(code, 1)
        self.assertIn("! goose: binary does not answer", out)
        self.assertNotIn("Nothing to add", out)

    def test_a_refused_pick_is_flagged_in_json(self):
        skipped = [{"name": "goose", "reason": "binary does not answer"}]
        code, out = self.run_cmd(["--json", "--apply", "goose"],
                                 applied=([], skipped))
        doc = json.loads(out)["data"]
        self.assertEqual(code, 0)
        self.assertTrue(doc["applied"]["refused"])
        self.assertEqual(doc["applied"]["skipped"], skipped)

    def test_a_skip_under_the_floor_is_not_a_failure(self):
        skipped = [{"name": "goose", "reason": "low confidence"}]
        code, _ = self.run_cmd(["--apply"], applied=(["aider"], skipped))
        self.assertEqual(code, 0)

    def test_the_tip_shows_off_a_terminal_when_the_first_row_is_new(self):
        _, out = self.run_cmd([], tty=False)
        self.assertIn("Tip: pass --apply", out)
        _, out = self.run_cmd([], tty=True)
        self.assertNotIn("Tip:", out)

    def test_an_empty_scan_says_the_registry_is_current(self):
        code, out = self.run_cmd([], findings=[])
        self.assertEqual(code, 0)
        self.assertIn("No new harnesses found", out)


if __name__ == "__main__":
    unittest.main()
