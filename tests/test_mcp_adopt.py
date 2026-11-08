"""Auto-adoption of harness-only MCP servers into the hub, and remove
propagation.

``swe mcp sync`` is the write path where the hub meets the harness
files, so it is also where a server the hub does not know is pulled in:
the first holder's definition (sorted harness order, so disagreeing
harnesses resolve the same way every time) lands in mcp.json
canonicalised and credential-shielded, ``swe.adopted_from`` records the
donor, and a disabled mark for every harness not already holding the
server keeps later hub syncs from spreading it anywhere new. ``mcp
remove NAME`` is the inverse: hub and every file copy gone, state
purged, so nothing re-adopts it.
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


class McpAdoptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)
        cfg = self.home / ".swe" / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "tools.json").write_text(
            json.dumps({"claude": {"aliases": ["cc"]},
                        "cursor": {"aliases": ["cs"]},
                        "codex": {"aliases": ["cx"]}})
            + "\n"
        )
        self.hub_file = self.home / ".swe" / "mcp.json"
        self.claude_file = self.home / ".claude.json"
        self.cursor_file = self.home / ".cursor" / "mcp.json"
        self.cursor_file.parent.mkdir(exist_ok=True)
        self.hub_write({})
        self.claude_write({})
        self.cursor_write({})

    def tearDown(self):
        # A test may have made the cursor config unwritable; restore the
        # mode so cleanup can remove the tree.
        try:
            os.chmod(self.cursor_file.parent, 0o755)
        except OSError:
            pass
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

    def cursor_write(self, servers):
        self.cursor_file.write_text(json.dumps({"mcpServers": servers}) + "\n")

    def hub_data(self):
        return json.loads(self.hub_file.read_text())

    def hub(self):
        return self.hub_data()["mcpServers"]

    def swe_state(self):
        return self.hub_data().get("swe", {})

    def claude(self):
        return json.loads(self.claude_file.read_text())["mcpServers"]

    def cursor(self):
        return json.loads(self.cursor_file.read_text())["mcpServers"]

    GITHUB = {"command": "npx", "args": ["-y", "gh-mcp"]}
    EXTRA = {"command": "local-x", "args": ["--flag"],
             "env": {"REGION": "us", "API_KEY": "${API_KEY}"}}

    def seed(self):
        """github in the hub and both harnesses; extra only in claude."""
        self.hub_write({"github": self.GITHUB})
        self.claude_write({"github": self.GITHUB, "extra": dict(self.EXTRA)})
        self.cursor_write({"github": self.GITHUB})

    # ---------------------------------------------------------- adoption

    def test_sync_adopts_a_harness_only_server_scoped_to_its_holder(self):
        self.seed()
        r = self.run_mcp("sync", "swe", "cursor", "--no-interactive")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("Adopted 1 server(s) into the hub: extra", r.stdout)

        # The hub gains the definition (env and the ${NAME} reference kept).
        self.assertEqual(self.hub()["extra"], self.EXTRA)
        self.assertEqual(self.swe_state().get("adopted_from"), {"extra": "claude"})

        # Held off everywhere it was not found — never pushed sideways.
        disabled = self.swe_state()["disabled"]
        self.assertNotIn("claude", disabled)
        for tool in ("cursor", "codex"):
            self.assertIn("extra", disabled.get(tool, []), tool)

        # The originating file is untouched.
        self.assertEqual(self.claude()["extra"]["env"]["REGION"], "us")

    def test_a_second_sync_does_not_push_the_adopted_server(self):
        self.seed()
        self.run_mcp("sync", "swe", "cursor", "--no-interactive")
        self.run_mcp("secret", "set", "API_KEY", stdin="sk-test123")
        r = self.run_mcp("sync", "swe", "--all", "--no-interactive", "--force")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("left off (disabled: extra)", r.stdout)
        self.assertNotIn("extra", self.cursor())

    def test_no_adopt_leaves_the_hub_and_state_alone(self):
        self.seed()
        r = self.run_mcp("sync", "swe", "cursor", "--no-interactive",
                         "--no-adopt")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("Adopted", r.stdout)
        self.assertEqual(self.hub(), {"github": self.GITHUB})
        self.assertNotIn("disabled", self.swe_state())

    def test_read_only_commands_never_adopt(self):
        self.seed()
        for args in (("list",), ("list", "--json"), ("doctor",)):
            r = self.run_mcp(*args)
            self.assertEqual(self.hub(), {"github": self.GITHUB}, args)
            self.assertNotIn("adopted_from", self.swe_state(), args)
            self.assertEqual(r.returncode, 0, args)

    def test_doctor_names_pending_harness_only_servers(self):
        self.seed()
        r = self.run_mcp("doctor")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("adopts them", r.stdout)
        self.assertIn("extra", r.stdout)

    def test_dry_run_reports_but_writes_nothing(self):
        self.seed()
        r = self.run_mcp("sync", "swe", "cursor", "--no-interactive",
                         "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("Would adopt 1 server(s) into the hub: extra", r.stdout)
        self.assertEqual(self.hub(), {"github": self.GITHUB})
        self.assertNotIn("disabled", self.swe_state())

    def test_deterministic_donor_is_the_first_sorted_holder(self):
        # claude and cursor disagree on "tool": claude sorts first, so its
        # definition is the one the hub keeps — same answer every run.
        self.claude_write({"tool": {"command": "from-claude"}})
        self.cursor_write({"tool": {"command": "from-cursor"}})
        r = self.run_mcp("sync", "swe", "cursor", "--no-interactive")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.hub()["tool"]["command"], "from-claude")
        self.assertEqual(self.swe_state().get("adopted_from"),
                         {"tool": "claude"})
        # Both files hold it, so neither is marked; the split still shows
        # up through the normal conflict reporting.
        disabled = self.swe_state()["disabled"]
        self.assertNotIn("claude", disabled)
        self.assertNotIn("cursor", disabled)
        self.assertIn("tool", disabled.get("codex", []))
        r = self.run_mcp("list", "--json")
        row = next(s for s in json.loads(r.stdout)["data"]["servers"]
                   if s["name"] == "tool")
        self.assertTrue(row["conflict"])
        self.assertEqual(row["adopted_from"], "claude")

    def test_a_copy_just_synced_counts_as_held_when_scoping(self):
        # `sync claude cursor` writes extra into cursor first; adoption
        # then sees it held in both and marks only the rest.
        self.seed()
        r = self.run_mcp("sync", "claude", "cursor", "--no-interactive",
                         "--force")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("extra", self.cursor())
        disabled = self.swe_state()["disabled"]
        self.assertNotIn("claude", disabled)
        self.assertNotIn("cursor", disabled)
        self.assertIn("extra", disabled.get("codex", []))

    def test_sync_json_reports_the_adopted_names(self):
        self.seed()
        r = self.run_mcp("sync", "swe", "cursor", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)["data"]
        self.assertEqual(data["adopted"], ["extra"])

    def test_adoption_shields_a_plaintext_credential(self):
        self.seed()
        self.claude_write({"github": self.GITHUB,
                           "extra": {"command": "local-x",
                                     "env": {"API_KEY": "sk-literal-0123456789"}}})
        r = self.run_mcp("sync", "swe", "cursor", "--no-interactive")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        # The versioned hub file must never hold the literal: the value is
        # moved to the secrets store and the entry keeps a ${NAME} ref.
        self.assertNotIn("sk-literal", self.hub_file.read_text())
        self.assertIn("${", json.dumps(self.hub()["extra"]))
        secrets = (self.home / ".swe" / "secrets" / "mcp" / ".api_keys")
        self.assertIn("sk-literal-0123456789", secrets.read_text())

    def test_disable_stash_scopes_the_hub_copy_like_an_adoption(self):
        # extra only lives in claude; disabling it stashes a hub copy —
        # scoped so a later hub sync does not spread it to cursor/codex.
        self.seed()
        r = self.run_mcp("disable", "extra", "--for=claude")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("extra", self.hub())
        self.assertEqual(self.swe_state().get("adopted_from"),
                         {"extra": "claude"})
        disabled = self.swe_state()["disabled"]
        for tool in ("claude", "cursor", "codex"):
            self.assertIn("extra", disabled.get(tool, []), tool)
        self.run_mcp("secret", "set", "API_KEY", stdin="sk-test123")
        r = self.run_mcp("sync", "swe", "--all", "--no-interactive")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("extra", self.cursor())

    def test_discover_apply_scopes_a_new_finding_the_same_way(self):
        self.seed()
        r = self.run_mcp("discover", "--apply")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("extra", self.hub())
        self.assertEqual(self.swe_state().get("adopted_from"),
                         {"extra": "claude"})
        disabled = self.swe_state()["disabled"]
        self.assertNotIn("claude", disabled)
        self.assertIn("extra", disabled.get("cursor", []))

    def test_info_and_list_json_expose_the_provenance(self):
        self.seed()
        self.run_mcp("sync", "swe", "cursor", "--no-interactive")
        r = self.run_mcp("info", "extra")
        self.assertIn("Adopted", r.stdout)
        self.assertIn("claude", r.stdout)
        r = self.run_mcp("info", "extra", "--json")
        self.assertEqual(json.loads(r.stdout)["data"]["adopted_from"],
                         "claude")

    # ------------------------------------------------------------- remove

    def test_bare_remove_takes_it_out_of_the_hub_and_every_file(self):
        self.seed()
        self.run_mcp("sync", "swe", "cursor", "--no-interactive")
        r = self.run_mcp("remove", "extra")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("Removed extra from: swe, claude", r.stdout)
        self.assertNotIn("extra", self.hub())
        self.assertNotIn("extra", self.claude())
        # The whole swe-block trail goes with the hub entry.
        state = self.swe_state()
        for key in ("disabled", "adopted_from", "master_off"):
            self.assertNotIn("extra", json.dumps(state.get(key, {})))

    def test_nothing_re_adopts_after_a_full_remove(self):
        self.seed()
        self.run_mcp("sync", "swe", "cursor", "--no-interactive")
        self.run_mcp("remove", "extra")
        r = self.run_mcp("sync", "swe", "--all", "--no-interactive")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("extra", self.hub())
        self.assertNotIn("adopted_from", self.swe_state())

    def test_remove_from_the_hub_only_warns_what_stays(self):
        self.seed()
        self.run_mcp("sync", "swe", "cursor", "--no-interactive")
        r = self.run_mcp("remove", "extra", "--from=swe")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("still in claude", r.stdout)
        self.assertIn("extra", self.claude())
        # And the warning is true: the next sync adopts it back.
        r = self.run_mcp("sync", "swe", "cursor", "--no-interactive")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("extra", self.hub())

    def test_remove_only_from_a_harness_keeps_the_hub_entry(self):
        self.seed()
        self.run_mcp("sync", "swe", "cursor", "--no-interactive")
        r = self.run_mcp("remove", "extra", "--from=claude")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("extra", self.claude())
        self.assertIn("extra", self.hub())

    def test_a_write_that_fails_is_warned_and_the_rest_still_go(self):
        self.seed()
        self.run_mcp("sync", "swe", "cursor", "--no-interactive")
        # extra now lives in the hub and claude; put a copy in cursor too
        # and make that directory unwritable.
        self.cursor_write({"github": self.GITHUB, "extra": dict(self.EXTRA)})
        os.chmod(self.cursor_file.parent, 0o555)
        try:
            r = self.run_mcp("remove", "extra", "--json")
        finally:
            os.chmod(self.cursor_file.parent, 0o755)
        data = json.loads(r.stdout)["data"]
        self.assertEqual(data["failed"], ["cursor"])
        self.assertNotIn("extra", self.hub())
        self.assertNotIn("extra", self.claude())
        self.assertIn("extra", self.cursor())

    def test_remove_a_name_nowhere_still_exits_one(self):
        r = self.run_mcp("remove", "ghost")
        self.assertEqual(r.returncode, 1)
        self.assertIn("ghost not in: swe", r.stdout)


if __name__ == "__main__":
    unittest.main()
