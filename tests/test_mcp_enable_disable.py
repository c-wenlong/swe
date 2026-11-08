"""`swe mcp disable` / `swe mcp enable`: per-harness switches.

Off means the entry leaves the harness file and the hub keeps the copy
``enable`` restores; the hub's ``swe`` block remembers which harnesses
it is off for, and ``sync`` cannot silently turn it back on.
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


class McpEnableDisableTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)
        cfg = self.home / ".swe" / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "tools.json").write_text(
            json.dumps({"claude": {"aliases": ["cc"]}, "codex": {"aliases": ["cx"]}})
            + "\n"
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

    def swe_state(self):
        return json.loads(self.hub_file.read_text()).get("swe", {})

    def claude(self):
        return json.loads(self.claude_file.read_text())["mcpServers"]

    GITHUB = {"command": "npx", "args": ["-y", "gh-mcp"]}

    def seed(self, in_hub=True, in_claude=True):
        if in_hub:
            self.hub_write({"github": self.GITHUB})
        if in_claude:
            self.claude_write({"github": self.GITHUB})

    def test_disable_removes_and_remembers_enable_restores(self):
        self.seed()
        r = self.run_mcp("disable", "github")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("github", self.claude())
        self.assertIn("github", self.hub())
        self.assertEqual(
            self.swe_state()["disabled"], {"claude": ["github"]})

        r = self.run_mcp("enable", "github")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("github", self.claude())
        self.assertEqual(self.swe_state().get("disabled", {}).get("claude"), None)

    def test_disable_stashes_a_hub_only_definition_back(self):
        # The only copy lives in claude: disabling must not lose it.
        self.seed(in_hub=False)
        r = self.run_mcp("disable", "github", "--for=claude")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("github", self.claude())
        self.assertIn("github", self.hub())
        r = self.run_mcp("enable", "github", "--for=claude")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("github", self.claude())

    def test_sync_leaves_a_disabled_server_off(self):
        self.seed()
        self.run_mcp("disable", "github")
        r = self.run_mcp("sync", "swe", "claude", "--no-interactive")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("github", self.claude())
        self.assertIn("left off", r.stdout)

    def test_list_json_reports_disabled_in(self):
        self.seed()
        self.run_mcp("disable", "github")
        r = self.run_mcp("list", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = {s["name"]: s for s in json.loads(r.stdout)["data"]["servers"]}
        self.assertEqual(rows["github"]["disabled_in"], ["claude"])

    def test_disable_unknown_server_fails(self):
        r = self.run_mcp("disable", "nothing")
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
