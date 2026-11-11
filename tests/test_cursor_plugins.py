"""Cursor controls use account RPCs, never SQLite/cache edits as enable state."""
import http.client
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from swe.plugins.cursor import CursorClient, CursorPluginError
from swe.plugins.catalog import read_cursor, build_catalog
from swe.plugins.actions import plugin_action, ActionError


def effective(pid="101", name="example", market="cursor-public", enabled=True, mode="USER"):
    return {"plugin": {"id": pid, "name": name,
                       "marketplace": {"id": "9", "name": market}},
            "isEnabled": enabled, "installMode": "EFFECTIVE_PLUGIN_INSTALL_MODE_" + mode}


class API:
    def __init__(self):
        self.entries = [effective()]
        self.personal = [{"pluginId": "101", "isEnabled": True}]
        self.calls = []
        self.apply = True

    def __call__(self, request, timeout):
        self.calls.append((request.full_url, json.loads(request.data), dict(request.header_items())))
        method = request.full_url.rsplit("/", 1)[-1]
        data = json.loads(request.data)
        if method == "GetEffectiveUserPlugins":
            reply = {"plugins": self.entries}
        elif method == "ListUserPluginInstalls":
            reply = {"installs": self.personal}
        elif method == "UpdateUserPluginInstall":
            if self.apply:
                next(e for e in self.entries if e["plugin"]["id"] == data["pluginId"])["isEnabled"] = data["isEnabled"]
            reply = {"install": {"pluginId": data["pluginId"]}}
        elif method == "UninstallUserPlugin":
            if self.apply:
                self.entries = [e for e in self.entries if e["plugin"]["id"] != data["pluginId"]]
                self.personal = [p for p in self.personal if p["pluginId"] != data["pluginId"]]
            reply = {"success": True}
        else:
            raise AssertionError(method)
        return io.BytesIO(json.dumps(reply).encode())


class CursorPluginTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.api = API()
        self.client = CursorClient(self.home, token="fixture-token", opener=self.api)

    def test_toggle_uses_exact_account_id_and_omits_configured_values(self):
        for verb, enabled in [("disable", False), ("enable", True)]:
            result = self.client.change(verb, "example@cursor-public")
            self.assertTrue(result["changed"])
            self.assertIs(result["state"]["enabled"], enabled)
        writes = [(url, body) for url, body, _ in self.api.calls if url.endswith("UpdateUserPluginInstall")]
        self.assertEqual([body for _, body in writes], [
            {"pluginId": "101", "isEnabled": False, "mergeWithStored": True},
            {"pluginId": "101", "isEnabled": True, "mergeWithStored": True}])
        for url, body, headers in self.api.calls:
            self.assertTrue(url.startswith("https://api2.cursor.sh/aiserver.v1.DashboardService/"))
            self.assertEqual(headers["Authorization"], "Bearer fixture-token")
            if url.endswith("GetEffectiveUserPlugins"):
                self.assertTrue(body["excludeConfiguredVariables"])
        self.assertFalse((self.home / ".cursor").exists())

    def test_removal_is_personal_only_and_verified(self):
        result = self.client.change("uninstall", "example@cursor-public")
        self.assertFalse(result["state"]["installed"])
        writes = [body for url, body, _ in self.api.calls if url.endswith("UninstallUserPlugin")]
        self.assertEqual(writes, [{"pluginId": "101"}])

    def test_noop_and_team_policy_never_write(self):
        self.assertFalse(self.client.change("enable", "example@cursor-public")["changed"])
        for mode in ["TEAM_REQUIRED", "TEAM_DEFAULT", "UNSPECIFIED"]:
            self.api.entries[0]["installMode"] = "EFFECTIVE_PLUGIN_INSTALL_MODE_" + mode
            with self.assertRaises(CursorPluginError):
                self.client.change("disable", "example@cursor-public")
        self.assertFalse(any(url.endswith(("UpdateUserPluginInstall", "UninstallUserPlugin")) for url, _, _ in self.api.calls))

    def test_namespaces_and_sanitized_collisions_do_not_choose_wrong_id(self):
        self.api.entries = [effective("101", market="first"), effective("102", market="second", enabled=False)]
        self.api.personal.append({"pluginId": "102"})
        self.client.change("enable", "example@second")
        write = next(body for url, body, _ in self.api.calls if url.endswith("UpdateUserPluginInstall"))
        self.assertEqual(write["pluginId"], "102")
        self.api.entries = [effective("101", name="a.b"), effective("102", name="a+b")]
        with self.assertRaisesRegex(CursorPluginError, "collide"):
            self.client.change("disable", "a_b@cursor-public")

    def test_server_must_confirm_state_and_never_retries_a_write(self):
        self.api.apply = False
        with self.assertRaisesRegex(CursorPluginError, "not confirmed"):
            self.client.change("disable", "example@cursor-public")
        self.assertEqual(sum(url.endswith("UpdateUserPluginInstall") for url, _, _ in self.api.calls), 1)

    def test_configured_and_pinned_plugins_offer_removal_only(self):
        for changes in [{"configuredVariableKeys": ["TOKEN"]}, {"hasTeamConfiguredVariables": True},
                        {"configuredVariableKeys": "invalid"}, {"pinnedGitRef": "release"}, {"pinnedGitRef": False}]:
            self.api.entries = [effective() | changes]
            self.assertEqual(self.client.plugins()["example@cursor-public"].actions, ("uninstall",))
            with self.assertRaises(CursorPluginError):
                self.client.change("disable", "example@cursor-public")
        self.assertFalse(any(url.endswith("UpdateUserPluginInstall") for url, _, _ in self.api.calls))

    def test_uninstall_can_reveal_a_team_default_without_claiming_failure(self):
        original = self.api.__call__
        def api(request, timeout):
            reply = original(request, timeout)
            if request.full_url.endswith("UninstallUserPlugin"):
                self.api.entries = [effective(mode="TEAM_DEFAULT")]
            return reply
        self.client.opener = api
        result = self.client.change("uninstall", "example@cursor-public")
        self.assertTrue(result["changed"])
        self.assertTrue(result["state"]["installed"])
        self.assertIn("team policy", result["detail"])

    def test_http_errors_do_not_echo_credentials_or_server_body(self):
        for error in [urllib.error.HTTPError("https://fixture", 401, "fixture-token", {}, io.BytesIO(b"private-value")),
                      urllib.error.URLError("fixture-token")]:
            with patch.object(self.client, "opener", side_effect=error):
                with self.assertRaises(CursorPluginError) as raised:
                    self.client.plugins()
            self.assertNotIn("fixture-token", str(raised.exception))
            self.assertNotIn("private-value", str(raised.exception))

    def test_interrupted_response_is_redacted_and_catalog_survives(self):
        class Broken(io.BytesIO):
            def read(self, *args):
                raise http.client.IncompleteRead(b"fixture-token", 50)
        self.client.opener = lambda *args, **kwargs: Broken()
        with self.assertRaises(CursorPluginError) as raised:
            self.client.plugins()
        self.assertNotIn("fixture-token", str(raised.exception))
        (self.home / ".cursor/plugins/cache").mkdir(parents=True)
        with patch("swe.plugins.cursor.CursorClient", return_value=self.client):
            errors = []
            view = read_cursor(self.home, errors)
        self.assertFalse(view.installs_known)
        self.assertTrue(errors)

    def test_uninstalled_download_remains_visible_and_importable(self):
        from swe.plugins.managed import import_plugin
        from swe.webapp.pages.plugins import explore
        cache = self.home / ".cursor/plugins/cache/cursor-public/downloaded/1"
        (cache / ".cursor-plugin").mkdir(parents=True)
        (cache / ".cursor-plugin/plugin.json").write_text('{"name":"downloaded"}')
        (cache / "readme.md").write_text("downloaded bytes")
        with patch("swe.plugins.cursor.CursorClient", return_value=self.client):
            catalog = build_catalog(self.home)
            plugin = next(p for m in catalog["marketplaces"] for p in m['plugins'] if p['name'] == 'downloaded')
            self.assertFalse(plugin['harnesses']['cursor']['installed'])
            self.assertEqual(plugin['harnesses']['cursor']['allowed_actions'], [])
            page = explore(self.home)
            self.assertIn('downloaded@cursor-public', page['plugins'])
            self.assertEqual(page['plugins']['downloaded@cursor-public']['harnesses']['cursor']['status'], 'cached')
            result = import_plugin(self.home, 'downloaded@cursor-public')
        self.assertEqual((Path(result['state']['path']) / 'readme.md').read_text(), 'downloaded bytes')
        self.assertTrue(cache.is_dir())

    def test_transport_and_schema_failures_are_safe(self):
        for code in (403, 429, 500):
            error = urllib.error.HTTPError("https://fixture", code, "private", {}, io.BytesIO(b"fixture-token"))
            with patch.object(self.client, "opener", side_effect=error):
                with self.assertRaises(CursorPluginError) as raised:
                    self.client.plugins()
            self.assertNotIn('fixture-token', str(raised.exception))
        for raw in (b'not-json', b'[]', b'{"error":"private"}', b'{"code":1,"message":"private"}'):
            self.client.opener = lambda *args, raw=raw, **kwargs: io.BytesIO(raw)
            with self.assertRaises(CursorPluginError):
                self.client.plugins()
        self.client.opener = lambda *args, **kwargs: io.BytesIO(b'x' * 20)
        with patch('swe.plugins.cursor._MAX_REPLY', 10):
            with self.assertRaisesRegex(CursorPluginError, 'oversized'):
                self.client.plugins()

    def test_invalid_inventory_and_ids_fail_without_writes(self):
        with patch.object(self.client, 'call', return_value={'plugins': {}}):
            with self.assertRaises(CursorPluginError):
                self.client.plugins()
        for item in [effective(pid=True), effective(pid='0'), effective(name=''),
                     effective() | {'isEnabled': 'bad'}]:
            self.api.entries = [item]
            try:
                self.assertEqual(self.client.plugins(), {})
            except CursorPluginError:
                pass
        for verb in ['install', 'unknown']:
            with self.assertRaises(CursorPluginError):
                self.client.change(verb, 'example@cursor-public')
        self.assertFalse(any(url.endswith(('UninstallUserPlugin','UpdateUserPluginInstall')) for url, _, _ in self.api.calls))

    def test_state_token_is_pinned_to_scratch_home_and_uses_no_keychain(self):
        store = self.home / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
        store.parent.mkdir(parents=True)
        store.write_text("corrupt fixture")
        with patch("swe.harness.rate_limits._read_cursor_state_token", return_value=None) as read, \
             patch("swe.plugins.cursor_auth._keychain_password", side_effect=AssertionError("keychain")):
            with self.assertRaises(CursorPluginError):
                CursorClient(self.home)
        self.assertTrue(all(p.startswith(str(self.home) + "/") for p in read.call_args.args[0]))

    def test_catalog_and_command_share_authoritative_state(self):
        cache = self.home / ".cursor/plugins/cache/cursor-public/example/1"
        cache.mkdir(parents=True)
        (cache / ".cursor-plugin").mkdir()
        (cache / ".cursor-plugin/plugin.json").write_text('{"name":"example"}')
        with patch("swe.plugins.cursor.CursorClient", return_value=self.client):
            catalog = build_catalog(self.home)
            plugin = next(p for m in catalog["marketplaces"] for p in m["plugins"] if p["ref"] == "example@cursor-public")
            self.assertTrue(plugin["harnesses"]["cursor"]["enabled"])
            self.assertEqual(plugin["harnesses"]["cursor"]["allowed_actions"], ["disable", "uninstall"])
            self.assertIn("cursor", catalog["actions"])
            result = plugin_action(self.home, "disable", "example@cursor-public", "cursor")
            self.assertTrue(result["changed"])
        self.assertTrue(cache.is_dir())

    def test_auth_failure_keeps_cache_unknown_and_read_only(self):
        (self.home / ".cursor/plugins/cache").mkdir(parents=True)
        with patch("swe.plugins.cursor.CursorClient", side_effect=CursorPluginError("sign in")):
            view = read_cursor(self.home, [])
        self.assertFalse(view.installs_known)
        self.assertEqual(view.installs, {})


if __name__ == "__main__":
    unittest.main()
