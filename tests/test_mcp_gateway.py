"""The MCP gateway's swe side: shared-server flags and daemon status.

The daemon itself is the standalone ``warp-gate`` package — its own repo
tests the server — so these tests cover what stays here: the
``swe.mcp.gateway`` helpers, ``swe mcp gateway share/unshare/status`` in
process, and the end-to-end ``python -m swe.mcp`` path against a
throwaway HOME, the way ``test_mcp_sync_integration`` does it.
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from swe.mcp import gateway
from swe.remote import launchd

FAKE = pathlib.Path(__file__).resolve().parent / "fixtures" / "fake_stdio_mcp.py"
PROJECT_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


class GatewayHelpersTest(unittest.TestCase):
    def test_settings_default_and_bad_port(self):
        self.assertEqual(gateway.settings_of(None), {"enabled": False, "port": gateway.DEFAULT_PORT})
        self.assertEqual(gateway.settings_of({"gateway": {"enabled": True, "port": 9100}}),
                         {"enabled": True, "port": 9100})
        for bad in (0, 70000, "9100", True):
            self.assertEqual(gateway.settings_of({"gateway": {"port": bad}})["port"],
                             gateway.DEFAULT_PORT)

    def test_runs_on_gateway_needs_shared_stdio_and_enabled(self):
        on = {"enabled": True, "port": 9100}
        stdio = {"command": "node", "args": ["s.js"], "shared": True}
        self.assertTrue(gateway.runs_on_gateway(stdio, on))
        self.assertFalse(gateway.runs_on_gateway(stdio, {**on, "enabled": False}))
        self.assertFalse(gateway.runs_on_gateway({"command": "node"}, on))
        self.assertFalse(gateway.runs_on_gateway({"url": "https://x", "shared": True}, on))

    def test_matches_hub_accepts_the_command_or_the_gateway_url(self):
        settings = {"enabled": False, "port": 9100}
        hub = {"command": "node", "args": ["s.js"], "shared": True}
        self.assertTrue(gateway.matches_hub("gh", hub, {"command": "node", "args": ["s.js"]}, settings))
        # Still ours after uninstall, so discover never pulls it into the hub.
        self.assertTrue(gateway.matches_hub("gh", hub, {"url": "http://127.0.0.1:9100/mcp/gh"}, settings))
        self.assertFalse(gateway.matches_hub("gh", hub, {"url": "http://127.0.0.1:9100/mcp/other"}, settings))
        # A URL left from before an unshare or a port change is still ours:
        # importing it would swap the hub's command for a dead URL.
        self.assertTrue(gateway.matches_hub("gh", {"command": "node", "args": ["s.js"]},
                                            {"url": "http://127.0.0.1:9100/mcp/gh"}, settings))
        self.assertTrue(gateway.matches_hub("gh", hub, {"url": "http://127.0.0.1:7000/mcp/gh"}, settings))
        # Only over a hub command, and only a loopback /mcp/<name> URL.
        self.assertFalse(gateway.matches_hub("gh", {"url": "https://api.example/mcp"},
                                             {"url": "http://127.0.0.1:9100/mcp/gh"}, settings))
        for url in ("http://127.0.0.1:9100/mcp/gh/x", "http://localhost:9100/mcp/gh",
                    "http://127.0.0.1:port/mcp/gh", "http://127.0.0.1:9100/gh"):
            self.assertFalse(gateway.matches_hub("gh", hub, {"url": url}, settings), url)

    def test_looks_stateful(self):
        self.assertTrue(gateway.looks_stateful("dv__playwright", {"command": "node"}))
        self.assertTrue(gateway.looks_stateful("web", {"command": "npx", "args": ["@playwright/mcp"]}))
        self.assertFalse(gateway.looks_stateful("dv__linear", {"command": "node", "args": ["l.js"]}))


class GatewayCommandsInProcessTest(unittest.TestCase):
    """The command handlers, run in-process against a throwaway HOME."""

    def setUp(self):
        from swe.mcp import cli as mcp_cli

        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)
        self.hub = self.home / ".swe" / "mcp.json"
        self.hub.parent.mkdir(parents=True)
        self.hub.write_text(json.dumps({"mcpServers": {
            "api": {"command": sys.executable, "args": [str(FAKE)], "env": {"T": "${API_TOKEN}"}},
            "nokey": {"command": "node", "args": ["n.js"], "env": {"T": "${MISSING}"}, "shared": True},
            "web": {"url": "https://mcp.example.com/mcp", "shared": True},
            "dv__playwright": {"command": "node"},
        }}) + "\n")
        secrets = self.home / ".swe" / "secrets" / "mcp"
        secrets.mkdir(parents=True)
        (secrets / ".api_keys").write_text("export API_TOKEN='tok'\n")
        for p in (patch.dict(os.environ, {"HOME": str(self.home)}),
                  patch.object(mcp_cli, "MCP_SOURCE_FILE", self.hub)):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cmd(self, *args):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        from swe.mcp.gateway_commands import cmd_gateway

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cmd_gateway(list(args))
        return code, out.getvalue(), err.getvalue()

    def hub_data(self):
        return json.loads(self.hub.read_text())

    def test_share_unshare_and_refusals(self):
        self.assertEqual(self.run_cmd("share", "api")[0], 0)
        self.assertIs(self.hub_data()["mcpServers"]["api"]["shared"], True)
        code, out, _ = self.run_cmd("share", "api")
        self.assertIn("already shared", out)
        self.assertEqual(self.run_cmd("share")[0], 1)
        self.assertEqual(self.run_cmd("share", "ghost")[0], 1)
        self.assertEqual(self.run_cmd("share", "dv__playwright")[0], 1)
        self.assertEqual(self.run_cmd("unshare", "api")[0], 0)
        self.assertNotIn("shared", self.hub_data()["mcpServers"]["api"])
        self.assertEqual(self.run_cmd("bogus")[0], 1)
        self.assertIn("swe mcp gateway share", self.run_cmd("help")[1])

    def test_removed_daemon_verbs_are_unknown(self):
        for verb in ("serve", "install", "uninstall"):
            code, out, _ = self.run_cmd(verb)
            self.assertEqual(code, 1, verb)
            self.assertIn(f"Unknown gateway command: {verb}", out)

    def test_status_text_and_json(self):
        from swe.mcp import gateway_commands as gc

        self.run_cmd("share", "api")
        live = {"error": "cannot read the hub, keeping the last good servers: bad",
                "servers": [{"name": "api", "state": "running", "pid": 42, "uptime_s": 9,
                             "starts": 1, "error": None, "calls": 5,
                             "calls_by_tool": {"echo": 5}, "call_errors": 0,
                             "last_call": "2026-11-09T00:12:34"},
                            {"name": "nokey", "state": "error", "pid": None, "uptime_s": None,
                             "starts": 0, "error": "no value for MISSING"}]}
        with patch.object(gc, "_fetch", return_value=live), \
                patch.object(gc.launchd, "loaded_pid", return_value=(True, 7)):
            code, out, _ = self.run_cmd("status")
            self.assertEqual(code, 0)
            self.assertIn("running on http://127.0.0.1:8478", out)
            self.assertIn("pid 42, up 9s, 5 calls", out)
            self.assertIn("no value for MISSING", out)
            self.assertIn("keeping the last good servers", out)
            code, out, _ = self.run_cmd("status", "--json")
            rows = {r["name"]: r for r in json.loads(out)["data"]["servers"]}
            self.assertEqual(rows["api"]["pid"], 42)
            self.assertEqual(rows["api"]["calls"], 5)
            self.assertEqual(rows["api"]["calls_by_tool"], {"echo": 5})
            self.assertEqual(rows["api"]["last_call"], "2026-11-09T00:12:34")
            self.assertEqual(rows["nokey"]["calls"], 0)
            self.assertIsNone(rows["nokey"]["last_call"])
        with patch.object(gc, "_fetch", return_value=None):
            code, out, _ = self.run_cmd()
            self.assertIn("not installed", out)
            self.assertIn("down", out)

    def test_share_with_the_gateway_on_hints_a_sync_of_just_those(self):
        from swe.mcp import gateway_commands as gc

        gc._save_settings(enabled=True)
        code, out, _ = self.run_cmd("share", "api")
        self.assertEqual(code, 0)
        self.assertIn("swe mcp sync swe --all --only=api --force", out)

    def test_sharing_without_a_daemon_points_at_warp_gate(self):
        code, out, _ = self.run_cmd("share", "api")
        self.assertEqual(code, 0)
        self.assertIn("warp-gate install", out)


class GatewayCommandEdgeTest(unittest.TestCase):
    """_fetch and the in-between states of share and status. launchctl,
    the network and sleeps are all stubbed."""

    setUp = GatewayCommandsInProcessTest.setUp
    tearDown = GatewayCommandsInProcessTest.tearDown
    run_cmd = GatewayCommandsInProcessTest.run_cmd
    hub_data = GatewayCommandsInProcessTest.hub_data

    def gc(self):
        from swe.mcp import gateway_commands as gc

        return gc

    def test_fetch_reads_json_and_treats_garbage_as_nothing(self):
        import io

        gc = self.gc()

        def answering(body):
            return patch.object(gc.urllib.request, "urlopen",
                                return_value=io.BytesIO(body))

        with answering(b'{"ok": true}'):
            self.assertEqual(gc._fetch(9300, "/health"), {"ok": True})
        with answering(b""):
            self.assertIsNone(gc._fetch(9300, "/health"))
        with answering(b"<html>"):
            self.assertIsNone(gc._fetch(9300, "/health"))

    def _status(self, *, platform="darwin", plists=(), loaded=(False, None)):
        gc = self.gc()
        for label in plists:
            path = launchd.plist_path(label=label)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("<plist/>")
        with patch.object(gc.sys, "platform", platform), \
                patch.object(gc, "_fetch", return_value=None), \
                patch.object(gc.launchd, "loaded_pid", return_value=loaded) as pid:
            code, out, _ = self.run_cmd("status")
        self.assertEqual(code, 0)
        return out, pid

    def test_status_off_macos_never_asks_launchd(self):
        out, pid = self._status(platform="linux", plists=[self.gc().WARP_GATE_LABEL])
        pid.assert_not_called()
        self.assertIn("not installed", out)

    def test_status_of_a_warp_gate_plist_that_is_not_running(self):
        gc = self.gc()
        out, pid = self._status(plists=[gc.WARP_GATE_LABEL])
        self.assertEqual([c.kwargs["label"] for c in pid.call_args_list],
                         [gc.WARP_GATE_LABEL])
        self.assertIn("installed but not answering on port 8478", out)
        self.assertNotIn("retired", out)

    def test_status_of_a_legacy_agent_plist_that_is_not_running(self):
        gc = self.gc()
        out, pid = self._status(plists=[gc.LEGACY_LABELS[0]])
        self.assertEqual([c.kwargs["label"] for c in pid.call_args_list],
                         [gc.LEGACY_LABELS[0]])
        self.assertIn("installed but not answering on port 8478", out)
        self.assertIn("retired", out)
        self.assertIn("installed, but sync still writes commands", out)

    def test_status_enabled_without_an_agent_says_to_install_warp_gate(self):
        self.gc()._save_settings(enabled=True)
        out, _ = self._status()
        self.assertIn("no LaunchAgent runs it: warp-gate install", out)

    def test_status_with_nothing_shared(self):
        hub = self.hub_data()
        for server in hub["mcpServers"].values():
            server.pop("shared", None)
        self.hub.write_text(json.dumps(hub))
        out, _ = self._status()
        self.assertIn("no shared servers yet: swe mcp gateway share <server...>", out)


def _env(home: pathlib.Path) -> dict:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PYTHONPATH"] = str(PROJECT_SRC) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


class GatewayCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)
        (self.home / ".swe" / "config").mkdir(parents=True)
        (self.home / ".swe" / "config" / "tools.json").write_text(
            json.dumps({"claude": {"aliases": ["cc"]}}) + "\n")
        self.hub = self.home / ".swe" / "mcp.json"
        self.hub.write_text(json.dumps({"mcpServers": {
            "api": {"command": "node", "args": ["/srv/api.js"], "env": {"TOKEN": "${API_TOKEN}"}},
            "dv__playwright": {"command": "node", "args": ["/srv/pw.js"]},
            "remote": {"url": "https://mcp.example.com/mcp"},
        }}) + "\n")
        secrets = self.home / ".swe" / "secrets" / "mcp"
        secrets.mkdir(parents=True)
        (secrets / ".api_keys").write_text("export API_TOKEN='tok'\n")
        self.claude = self.home / ".claude.json"
        self.claude.write_text(json.dumps({"mcpServers": {}}) + "\n")
        self.desktop = self.home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        self.desktop.parent.mkdir(parents=True)
        self.desktop.write_text(json.dumps({"mcpServers": {}}) + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_mcp(self, *args):
        return subprocess.run([sys.executable, "-m", "swe.mcp", *args], capture_output=True,
                              text=True, env=_env(self.home), timeout=60)

    def hub_data(self):
        return json.loads(self.hub.read_text())

    def enable(self, port=9100):
        data = self.hub_data()
        data["gateway"] = {"enabled": True, "port": port}
        self.hub.write_text(json.dumps(data) + "\n")

    def test_share_and_unshare(self):
        result = self.run_mcp("gateway", "share", "api")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIs(self.hub_data()["mcpServers"]["api"]["shared"], True)
        self.assertIn("warp-gate install", result.stdout)
        result = self.run_mcp("gateway", "unshare", "api")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("shared", self.hub_data()["mcpServers"]["api"])

    def test_share_refusals(self):
        result = self.run_mcp("gateway", "share", "nope")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not in the hub: nope", result.stderr)
        result = self.run_mcp("gateway", "share", "remote")
        self.assertEqual(result.returncode, 1)
        self.assertIn("only stdio servers", result.stderr)
        result = self.run_mcp("gateway", "share", "dv__playwright")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--force", result.stderr)
        self.assertNotIn("shared", self.hub_data()["mcpServers"]["dv__playwright"])
        result = self.run_mcp("gateway", "share", "dv__playwright", "--force")
        self.assertEqual(result.returncode, 0)

    def test_sync_writes_the_gateway_url_only_once_it_is_enabled(self):
        self.run_mcp("gateway", "share", "api")
        result = self.run_mcp("sync", "quiver", "claude", "--no-interactive")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        api = json.loads(self.claude.read_text())["mcpServers"]["api"]
        self.assertEqual(api["command"], "node")
        self.enable()
        result = self.run_mcp("sync", "quiver", "claude", "claude-desktop", "--no-interactive",
                              "--force", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = {t["name"]: t for t in json.loads(result.stdout)["data"]["targets"]}
        self.assertEqual(report["claude"]["via_gateway"], ["api"])
        claude = json.loads(self.claude.read_text())["mcpServers"]
        self.assertEqual(claude["api"], {"type": "http", "url": "http://127.0.0.1:9100/mcp/api"})
        self.assertEqual(claude["dv__playwright"]["command"], "node")
        # Claude Desktop takes no URL server, so it keeps the command.
        desktop = json.loads(self.desktop.read_text())["mcpServers"]
        self.assertEqual(desktop["api"]["env"], {"TOKEN": "tok"})

    def test_discover_and_list_read_the_gateway_url_as_the_hub_server(self):
        self.run_mcp("gateway", "share", "api")
        self.enable()
        self.run_mcp("sync", "quiver", "claude", "claude-desktop", "--no-interactive", "--force")
        result = self.run_mcp("discover", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        findings = json.loads(result.stdout)["data"]["findings"]
        self.assertNotIn("api", [f["name"] for f in findings])
        result = self.run_mcp("list", "--json")
        servers = json.loads(result.stdout)["data"]["servers"]
        api = next(s for s in servers if s["name"] == "api")
        self.assertEqual(sorted(api["harnesses"]), ["claude", "claude-desktop"])
        self.assertFalse(api["conflict"])

    def test_discover_apply_keeps_the_flag_on_a_real_edit(self):
        self.run_mcp("gateway", "share", "api")
        self.claude.write_text(json.dumps({"mcpServers": {
            "api": {"command": "node", "args": ["/srv/api-v2.js"], "env": {"TOKEN": "tok"}}}}) + "\n")
        result = self.run_mcp("discover", "--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        api = self.hub_data()["mcpServers"]["api"]
        self.assertEqual(api["args"], ["/srv/api-v2.js"])
        self.assertIs(api["shared"], True)

    def test_status_json_lists_shared_servers(self):
        self.run_mcp("gateway", "share", "api")
        self.enable(port=1)  # nothing listens there
        result = self.run_mcp("gateway", "status", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)["data"]
        self.assertEqual(data["port"], 1)
        self.assertFalse(data["reachable"])
        self.assertEqual([(r["name"], r["state"], r["url"]) for r in data["servers"]],
                         [("api", "down", "http://127.0.0.1:1/mcp/api")])

    def test_removed_daemon_verbs_are_unknown(self):
        for verb in ("serve", "install", "uninstall"):
            result = self.run_mcp("gateway", verb)
            self.assertEqual(result.returncode, 1, verb)
            self.assertIn(f"Unknown gateway command: {verb}", result.stdout)


if __name__ == "__main__":
    unittest.main()
