"""`swe plugins info <ref>` — the detail sheet behind the catalog row.

The fake home is test_plugin_catalog's: plugin ``lsp@official`` ships a
``hover`` skill and declares MCP server ``lsp`` with an env key. The
tests add a fixture search index so usage counts have something to read.
"""

import json
import sqlite3
import unittest
from unittest.mock import patch

import swe.plugins.info as info_mod
from swe.plugins.catalog import mcp_server_details
from swe.plugins.info import _find_plugin, plugin_info
from swe.sessions import search_index

try:  # `unittest discover -s tests` puts tests/ itself on sys.path
    from test_json_output import decode
    from test_plugin_catalog import _CatalogHome
    from test_plugin_fix import run_swe
    from test_skill_usage import _index
except ImportError:  # `python -m unittest tests.test_plugins_info`
    from tests.test_json_output import decode
    from tests.test_plugin_catalog import _CatalogHome
    from tests.test_plugin_fix import run_swe
    from tests.test_skill_usage import _index


class _InfoHome(_CatalogHome):
    def setUp(self):
        super().setUp()
        # Index a couple of calls for the shipped skill and the server.
        _index(self.home)
        # The inventory join reads the real registry; inject the row the
        # fake home would produce so the tests stay hermetic.
        patcher = patch.object(info_mod, "_inventory_rows", return_value=[{
            "name": "lsp", "env_keys": ["EXTRA"], "header_keys": [],
            "harnesses": ["claude"], "disabled_in": [], "in_hub": True,
            "secrets_missing": ["LSP_KEY"], "variants": [], "conflict": False,
            "remote_in": []}])
        patcher.start()
        self.addCleanup(patcher.stop)


class TestMcpDetails(_InfoHome, unittest.TestCase):
    def test_declared_keys_only(self):
        folder = self.home / ".claude" / "plugins" / "cache" / "official" / "lsp" / "1.0.0"
        got = mcp_server_details(folder)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["name"], "lsp")
        # The env dict's key surfaces; the secret value never does.
        self.assertEqual(got[0]["env_keys"], ["TOKEN"])
        self.assertNotIn("sk-secret", json.dumps(got))


class TestPluginInfo(_InfoHome, unittest.TestCase):
    def test_finds_ref(self):
        data = plugin_info("lsp@official", self.home)
        self.assertIsNotNone(data)
        self.assertEqual(data["ref"], "lsp@official")

    def test_finds_bare_name(self):
        self.assertEqual(plugin_info("lsp", self.home)["ref"], "lsp@official")

    def test_unknown_is_none(self):
        self.assertIsNone(plugin_info("nope@official", self.home))
        self.assertIsNone(_find_plugin({"marketplaces": []}, "x@y"))

    def test_mcp_servers(self):
        data = plugin_info("lsp@official", self.home)
        server = data["mcp_servers"][0]
        self.assertEqual(server["name"], "lsp")
        # Declared TOKEN plus the inventory's EXTRA; state from the join.
        self.assertEqual(server["env_keys"], ["EXTRA", "TOKEN"])
        self.assertTrue(server["in_hub"])
        self.assertEqual(server["harnesses"], ["claude"])
        self.assertEqual(server["secrets_missing"], ["LSP_KEY"])
        self.assertEqual(server["usage"]["calls"], 0)

    def test_skill_usage_attached(self):
        data = plugin_info("lsp@official", self.home)
        skill = next(s for s in data["skills"] if s["name"] == "hover")
        self.assertTrue(skill["usage"]["tracked"])
        self.assertEqual(skill["usage"]["calls"], 0)
        # The fixture index has pdf calls, not hover calls.
        self.assertEqual(data["usage"]["calls"], 0)

    def test_updated_and_secrets_never_leak(self):
        data = plugin_info("lsp@official", self.home)
        self.assertIsNotNone(data["updated"])
        self.assertNotIn("sk-secret", json.dumps(data))

    def test_untracked_without_index(self):
        import shutil
        shutil.rmtree(search_index.index_path(self.home).parent)
        data = plugin_info("lsp@official", self.home)
        self.assertEqual(data["usage"], {"tracked": False})

    def test_usage_counts(self):
        path = search_index.index_path(self.home)
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO messages VALUES (90, 1, 'assistant', '2026-11-01T00:00:00Z')")
        conn.execute("INSERT INTO messages_fts(rowid, tool_text) VALUES (90, 'Skill: hover')")
        conn.execute("INSERT INTO messages VALUES (91, 2, 'assistant', '2026-11-02T00:00:00Z')")
        conn.execute("INSERT INTO messages_fts(rowid, tool_text) VALUES (91, 'mcp__lsp__restart')")
        conn.commit()
        conn.close()
        data = plugin_info("lsp@official", self.home)
        self.assertEqual(data["usage"]["calls"], 2)
        self.assertEqual(data["usage"]["skill_calls"], 1)
        self.assertEqual(data["usage"]["mcp_calls"], 1)


class TestInfoCommand(_InfoHome, unittest.TestCase):
    def test_json(self):
        proc = run_swe(self.home, "plugins", "info", "lsp@official", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = decode(proc)["data"]
        self.assertEqual(got["ref"], "lsp@official")
        # The subprocess has no injected row: only the declared key.
        self.assertEqual(got["mcp_servers"][0]["env_keys"], ["TOKEN"])
        self.assertNotIn("sk-secret", proc.stdout)

    def test_json_unknown(self):
        proc = run_swe(self.home, "plugins", "info", "nope@x", "--json")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.strip(), "")

    def test_human(self):
        proc = run_swe(self.home, "plugins", "info", "lsp@official")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("lsp@official", proc.stdout)
        self.assertIn("MCP servers", proc.stdout)
        self.assertIn("TOKEN", proc.stdout)
        self.assertNotIn("sk-secret", proc.stdout)

    def test_human_unknown(self):
        proc = run_swe(self.home, "plugins", "info", "nope@x")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("No plugin named", proc.stdout)

    def test_missing_ref(self):
        proc = run_swe(self.home, "plugins", "info")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Usage:", proc.stdout)


if __name__ == "__main__":
    unittest.main()
