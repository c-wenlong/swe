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

    def codex(self):
        return (self.home / ".codex" / "config.toml").read_text()

    def seed_codex(self):
        (self.home / ".codex").mkdir(exist_ok=True)
        (self.home / ".codex" / "config.toml").write_text(
            'model = "x"\n[mcp_servers.github]\ncommand = "npx"\n'
            'startup_timeout_sec = 30\n')

    def test_disable_for_flag_only_touches_named_harnesses(self):
        self.seed()
        self.seed_codex()
        r = self.run_mcp("disable", "github", "--for=claude")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("github", self.claude())
        self.assertIn("github", self.codex())
        self.assertEqual(self.swe_state()["disabled"], {"claude": ["github"]})

    def test_disable_for_an_unknown_harness_fails(self):
        self.seed()
        r = self.run_mcp("disable", "github", "--for=bogus")
        self.assertEqual(r.returncode, 1)
        self.assertIn("github", self.claude())

    def test_disable_for_a_harness_without_it_is_skipped(self):
        self.seed()
        r = self.run_mcp("disable", "github", "--for=codex")
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn("not defined", r.stdout)

    def test_json_output_reports_the_places(self):
        self.seed()
        r = self.run_mcp("disable", "github", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["data"]["disabled_in"], ["claude"])

    def test_enable_when_nothing_is_disabled_fails(self):
        self.seed()
        r = self.run_mcp("enable", "github")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not disabled", r.stdout)

    def test_enable_without_a_hub_copy_clears_the_mark(self):
        self.seed()
        self.run_mcp("disable", "github")
        # The definition and the kept copy are gone; only the mark remains
        # (an older swe's mark, or a manually edited file).
        self.hub_write({}, swe={"disabled": self.swe_state()["disabled"]})
        r = self.run_mcp("enable", "github")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no copy", r.stdout)
        self.assertEqual(self.swe_state().get("disabled", {}).get("claude"), None)

    def test_enable_json_reports_the_places(self):
        self.seed()
        self.run_mcp("disable", "github")
        r = self.run_mcp("enable", "github", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["data"]["enabled_in"], ["claude"])

    def test_enable_for_an_unknown_harness_fails(self):
        self.seed()
        self.run_mcp("disable", "github")
        self.assertEqual(self.run_mcp("enable", "github", "--for=bogus").returncode, 1)
        self.assertIn("github", json.loads(self.hub_file.read_text())["swe"]["disabled"]["claude"])

    def test_both_reject_a_missing_name_and_bad_flag(self):
        self.assertEqual(self.run_mcp("disable").returncode, 1)
        self.assertEqual(self.run_mcp("enable").returncode, 1)
        self.assertEqual(self.run_mcp("disable", "x", "--bogus").returncode, 1)
        self.assertEqual(self.run_mcp("enable", "x", "--bogus").returncode, 1)

    def test_both_help_pages_print(self):
        for verb in ("disable", "enable"):
            r = self.run_mcp(verb, "help")
            self.assertEqual(r.returncode, 0)
            self.assertIn(f"swe mcp {verb}", r.stdout)

    def test_disable_skip_is_quiet_under_json(self):
        self.seed()
        r = self.run_mcp("disable", "github", "--for=codex", "--json")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.strip(), "")  # the skip note stays off stdout

    def test_enable_restores_harness_specific_keys(self):
        self.seed()
        self.seed_codex()
        self.run_mcp("disable", "github", "--for=codex")
        r = self.run_mcp("enable", "github", "--for=codex")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        # startup_timeout_sec lives in codex's format, not the canonical
        # shape — a canonical restore would drop it.
        self.assertIn("startup_timeout_sec", self.codex())

    def test_disable_shields_plaintext_secrets(self):
        self.seed()
        self.claude_write({"github": dict(self.GITHUB, env={"API_KEY": "sk-live"})})
        r = self.run_mcp("disable", "github")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        kept = self.swe_state()["disabled_defs"]["claude"]["github"]
        self.assertNotIn("sk-live", json.dumps(kept))
        self.assertNotIn("sk-live", self.hub_file.read_text())
        # The restore resolves the reference back to the literal.
        r = self.run_mcp("enable", "github")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.claude()["github"]["env"]["API_KEY"], "sk-live")

    def test_discover_prune_keeps_a_disabled_servers_hub_copy(self):
        self.seed()
        self.run_mcp("disable", "github")
        r = self.run_mcp("discover", "--apply", "--prune")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("github", self.hub())

    def test_disabled_server_missing_secret_does_not_block_sync(self):
        self.seed()
        self.hub_write({"github": dict(self.GITHUB, env={"K": "${NO_SUCH_SECRET}"}),
                        "extra": {"command": "x"}})
        self.claude_write({"github": self.GITHUB})
        self.run_mcp("disable", "github")
        r = self.run_mcp("sync", "swe", "claude", "--no-interactive")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("extra", self.claude())
        self.assertNotIn("github", self.claude())

    def test_enable_with_unresolvable_secret_stays_off(self):
        self.seed()
        self.claude_write({"github": dict(self.GITHUB, env={"K": "${GONE}"})})
        self.run_mcp("disable", "github")
        r = self.run_mcp("enable", "github")
        self.assertEqual(r.returncode, 1)
        self.assertIn("claude", self.swe_state()["disabled"])

    def test_enable_json_failure_still_emits_a_document(self):
        self.seed()
        self.claude_write({"github": dict(self.GITHUB, env={"K": "${GONE}"})})
        self.run_mcp("disable", "github")
        r = self.run_mcp("enable", "github", "--json")
        self.assertEqual(r.returncode, 1)
        data = json.loads(r.stdout)["data"]
        self.assertEqual(data["failed_in"], ["claude"])
        self.assertNotIn("enabled_in", data)


if __name__ == "__main__":
    unittest.main()
