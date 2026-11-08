"""`swe mcp set-type`: the hub entry's free-form `type` label.

The label is swe-side metadata like `shared`: it lives on the server's
entry in mcp.json, shows up on `list`/`info --json` rows, and never
reaches a harness config. `type` is also the SSE transport marker on a
URL server, so the transport words stay reserved for the wire.
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

MCP_MODULE = "swe.mcp"
PROJECT_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


def _mcp_env(home: pathlib.Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(PROJECT_SRC) if not existing else f"{PROJECT_SRC}{os.pathsep}{existing}"
    )
    return env


class McpSetTypeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)
        cfg = self.home / ".swe" / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "tools.json").write_text(
            json.dumps({"claude": {"aliases": ["cc"]}}) + "\n"
        )
        self.hub_file = self.home / ".swe" / "mcp.json"
        self.claude_file = self.home / ".claude.json"
        self.hub_write({})
        self.claude_write({})

    def tearDown(self):
        self.tmp.cleanup()

    def run_mcp(self, *args, stdin=""):
        return subprocess.run(
            [sys.executable, "-m", MCP_MODULE, *args],
            env=_mcp_env(self.home),
            input=stdin,
            capture_output=True,
            text=True,
        )

    def hub_write(self, servers, swe=None):
        data = {"mcpServers": servers}
        if swe is not None:
            data["swe"] = swe
        self.hub_file.write_text(json.dumps(data) + "\n")

    def claude_write(self, servers):
        self.claude_file.write_text(json.dumps({"mcpServers": servers}) + "\n")

    def hub(self):
        return json.loads(self.hub_file.read_text())["mcpServers"]

    def claude(self):
        return json.loads(self.claude_file.read_text())["mcpServers"]

    GITHUB = {"command": "npx", "args": ["-y", "gh-mcp"]}
    SSE = {"url": "https://mcp.example.com/sse", "type": "sse"}

    def seed(self, in_hub=True, in_claude=True):
        if in_hub:
            self.hub_write({"github": dict(self.GITHUB)})
        if in_claude:
            self.claude_write({"github": dict(self.GITHUB)})

    def list_row(self, name):
        r = self.run_mcp("list", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = {s["name"]: s for s in json.loads(r.stdout)["data"]["servers"]}
        return rows[name]

    def test_set_type_labels_a_hub_server(self):
        self.seed()
        r = self.run_mcp("set-type", "github", "dev")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.hub()["github"]["type"], "dev")

    def test_label_shows_on_list_and_info_json(self):
        self.seed()
        self.run_mcp("set-type", "github", "research")
        self.assertEqual(self.list_row("github")["type"], "research")
        r = self.run_mcp("info", "github", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["data"]["type"], "research")

    def test_unlabelled_and_unknown_servers_report_none(self):
        self.seed()
        self.assertIsNone(self.list_row("github")["type"])

    def test_value_is_trimmed(self):
        self.seed()
        self.run_mcp("set-type", "github", "  social  ")
        self.assertEqual(self.hub()["github"]["type"], "social")

    def test_clear_flag_removes_the_label(self):
        self.seed()
        self.run_mcp("set-type", "github", "dev")
        r = self.run_mcp("set-type", "github", "--clear")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("type", self.hub()["github"])

    def test_an_empty_value_clears(self):
        self.seed()
        self.run_mcp("set-type", "github", "dev")
        r = self.run_mcp("set-type", "github", "")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("type", self.hub()["github"])

    def test_transport_words_are_not_labels(self):
        self.seed()
        for word in ("sse", "http", "local", "remote", "stdio"):
            r = self.run_mcp("set-type", "github", word)
            self.assertEqual(r.returncode, 1, word)
        self.assertNotIn("type", self.hub()["github"])

    def test_sse_marker_is_not_a_label_and_survives(self):
        # A URL server's "sse" is the wire marker: not a label on the row,
        # and set-type refuses to overwrite or clear it.
        self.hub_write({"remote": dict(self.SSE)})
        self.assertIsNone(self.list_row("remote")["type"])
        self.assertEqual(self.list_row("remote")["transport"], "sse")

        r = self.run_mcp("set-type", "remote", "dev")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.hub()["remote"]["type"], "sse")

        r = self.run_mcp("set-type", "remote", "--clear")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.hub()["remote"]["type"], "sse")

    def test_label_never_reaches_a_harness_on_sync(self):
        self.seed(in_claude=False)
        self.run_mcp("set-type", "github", "dev")
        r = self.run_mcp("sync", "swe", "claude", "--no-interactive")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("type", self.claude()["github"])

    def test_label_survives_a_changed_discover_pull(self):
        self.seed()
        self.run_mcp("set-type", "github", "dev")
        # A hand edit in the harness differs from the hub: the pull must
        # take the new definition but keep the hub's own label.
        self.claude_write({"github": dict(self.GITHUB, args=["-y", "gh-mcp", "--v2"])})
        r = self.run_mcp("discover", "--apply")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        entry = self.hub()["github"]
        self.assertEqual(entry["args"], ["-y", "gh-mcp", "--v2"])
        self.assertEqual(entry["type"], "dev")

    def test_set_type_needs_the_hub(self):
        self.seed(in_hub=False)
        r = self.run_mcp("set-type", "github", "dev")
        self.assertEqual(r.returncode, 1)
        r = self.run_mcp("set-type", "ghost", "dev")
        self.assertEqual(r.returncode, 1)

    def test_json_output(self):
        self.seed()
        r = self.run_mcp("set-type", "github", "dev", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["data"],
                         {"name": "github", "type": "dev"})

    def test_usage_errors(self):
        self.seed()
        self.assertEqual(self.run_mcp("set-type").returncode, 1)
        self.assertEqual(self.run_mcp("set-type", "github").returncode, 1)
        self.assertEqual(
            self.run_mcp("set-type", "github", "dev", "--bogus").returncode, 1)
        self.assertEqual(
            self.run_mcp("set-type", "github", "dev", "--clear").returncode, 1)

    def test_help_page_prints(self):
        r = self.run_mcp("set-type", "help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("swe mcp set-type", r.stdout)


if __name__ == "__main__":
    unittest.main()
