"""``swe mcp discover``'s command adapter: flags, refusal and the output
around ``--apply``. Discovery and the merge are patched; their own tests
live in test_mcp_discover.py."""

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from swe.console import strip_ansi
from swe.mcp import discover_commands
from swe.mcp.discover import McpFinding, MergeResult

PINNED = "/Users/x/.vscode/extensions/acme.mcp-1.2.3/server/bin"


def finding(name, status="new", command="npx", args=()):
    return McpFinding(name=name, tools=("claude",), status=status,
                      source_tool="claude",
                      server={"command": command, "args": list(args)})


class McpDiscoverCommandTest(unittest.TestCase):
    def run_cmd(self, args, findings=(), result=None, tty=True):
        out = io.StringIO()
        with patch.object(discover_commands, "discover_mcp_servers",
                          return_value=list(findings)) as discover, \
                patch.object(discover_commands, "apply_mcp_findings",
                             return_value=result) as apply, \
                patch("sys.stdin.isatty", return_value=tty), \
                redirect_stdout(out):
            code = discover_commands.cmd_discover(args)
        return code, strip_ansi(out.getvalue()), discover, apply

    def test_help_prints_usage_without_scanning(self):
        for flag in ("-h", "--help"):
            with self.subTest(flag):
                code, out, discover, _ = self.run_cmd([flag])
                self.assertEqual(code, 0)
                self.assertIn("swe mcp discover --prune", out)
                discover.assert_not_called()

    def test_an_unknown_argument_is_refused_without_scanning(self):
        code, out, discover, apply = self.run_cmd(["--aply", "x"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown argument(s): --aply x", out)
        self.assertIn("Source of truth", out)
        discover.assert_not_called()
        apply.assert_not_called()

    def test_all_asks_for_servers_already_in_the_hub(self):
        _, _, discover, _ = self.run_cmd(["--all"])
        discover.assert_called_once_with(include_in_source=True)
        _, _, discover, _ = self.run_cmd([])
        discover.assert_called_once_with(include_in_source=False)

    def test_nothing_found_says_so(self):
        code, out, _, _ = self.run_cmd([])
        self.assertEqual(code, 0)
        self.assertIn("No new MCP servers found", out)

    def test_a_versioned_launch_path_is_named_before_adopting(self):
        code, out, _, _ = self.run_cmd([], [
            finding("pinned", command=PINNED),
            finding("pinned-arg", command="node", args=[PINNED + "/index.js"]),
            # Only new findings warn: this one is already in the hub.
            finding("known", status="changed", command=PINNED),
            finding("plain"),
        ])
        self.assertEqual(code, 0)
        warning = out.split("breaks on upgrade:", 1)[1].split("dry-run", 1)[0]
        self.assertIn("pinned  " + PINNED, warning)
        self.assertIn("pinned-arg", warning)
        self.assertNotIn("known", warning)
        self.assertNotIn("plain", warning)

    def test_no_warning_without_a_versioned_path(self):
        _, out, _, _ = self.run_cmd([], [finding("plain")])
        self.assertNotIn("versioned directory", out)

    def test_the_tip_only_shows_off_a_terminal_with_new_findings(self):
        _, out, _, _ = self.run_cmd([], [finding("a")], tty=False)
        self.assertIn("Tip: pass --apply", out)
        _, out, _, _ = self.run_cmd([], [finding("a")], tty=True)
        self.assertNotIn("Tip:", out)
        _, out, _, _ = self.run_cmd([], [finding("a", status="changed")],
                                    tty=False)
        self.assertNotIn("Tip:", out)

    def test_apply_reports_added_updated_and_orphans_left_in_place(self):
        result = MergeResult(added=["a"], updated=["b"], orphaned=["c", "d"],
                             pruned=[])
        code, out, _, apply = self.run_cmd(
            ["--apply"], [finding("a"), finding("b", status="changed")], result)
        self.assertEqual(code, 0)
        apply.assert_called_once()
        self.assertFalse(apply.call_args.kwargs["prune"])
        self.assertIn("Added 1 server(s)", out)
        self.assertIn("Updated 1 server(s) from their harness config: b", out)
        self.assertIn("2 server(s) in the hub that no harness configures: c, d",
                      out)
        self.assertIn("Pass --prune to remove them.", out)
        self.assertNotIn("Hub already matches", out)

    def test_prune_reports_what_it_removed(self):
        result = MergeResult(added=[], updated=[], orphaned=["c"],
                             pruned=["c"])
        _, out, _, apply = self.run_cmd(["--apply", "--prune"],
                                        [finding("c", status="orphaned")],
                                        result)
        self.assertTrue(apply.call_args.kwargs["prune"])
        self.assertIn("Pruned 1 orphan(s): c", out)
        self.assertNotIn("Pass --prune", out)

    def test_apply_with_nothing_to_do_says_the_hub_matches(self):
        result = MergeResult(added=[], updated=[], orphaned=[], pruned=[])
        _, out, _, _ = self.run_cmd(["--apply"], [], result)
        self.assertIn("Hub already matches every harness.", out)

    def test_json_apply_rides_in_one_document(self):
        result = MergeResult(added=["a"], updated=[], orphaned=["o"],
                             pruned=[])
        code, out, _, _ = self.run_cmd(["--json", "--apply"],
                                       [finding("a", command=PINNED)], result)
        self.assertEqual(code, 0)
        doc = json.loads(out)["data"]
        self.assertEqual([row["name"] for row in doc["findings"]], ["a"])
        self.assertEqual(doc["applied"], {"added": ["a"], "updated": [],
                                          "orphaned": ["o"], "pruned": []})
        # Nothing human-readable leaks around the document.
        self.assertTrue(out.lstrip().startswith("{"))


if __name__ == "__main__":
    unittest.main()
