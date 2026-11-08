"""`swe plugins info <ref>` — the detail sheet behind the catalog row.

The fake home is test_plugin_catalog's: plugin ``lsp@official`` ships a
``hover`` skill and declares MCP server ``lsp`` with an env key. The
tests add a fixture search index so usage counts have something to read.
"""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
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


class TestInfoHelpers(unittest.TestCase):
    """The small helpers behind the sheet, driven in-process: ``run_swe``
    covers the subprocess shape, these cover every printed line."""

    def test_latest_mtime_edges(self):
        from swe.plugins.info import _iso, _latest_mtime
        from tests._tempdir import temp_dir

        self.assertIsNone(_latest_mtime(None))
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "f.txt").write_text("x")
            (folder / "g.txt").write_text("x")
            self.assertIsNotNone(_latest_mtime(folder))
            self.assertIsNotNone(_latest_mtime(folder, cap=1))
            self.assertIsNone(_latest_mtime(folder / "f.txt"))  # not a dir
        self.assertIsNone(_iso(None))
        self.assertIn("T", _iso(1700000000.0))

    def test_latest_mtime_skips_undead_entries(self):
        # A walk entry that vanishes before stat() is skipped, not raised.
        import os
        from swe.plugins.info import _latest_mtime

        with tempfile.TemporaryDirectory() as td:
            Path(td, "ok").write_text("x")
            real_walk = os.walk

            def fake_walk(root):
                yield from real_walk(root)
                yield (td, ["ghost"], [])

            with patch("swe.plugins.info.os.walk", side_effect=fake_walk):
                self.assertIsNotNone(_latest_mtime(Path(td)))

    def test_inventory_rows_degrade_on_failure(self):
        import swe.plugins.info as info_mod

        with patch.dict("sys.modules", {"swe.mcp.cli": None}):
            self.assertEqual(info_mod._inventory_rows(), [])


class TestRenderInfo(unittest.TestCase):
    def _render(self, data):
        import io
        from contextlib import redirect_stdout
        from swe.plugins.commands import _render_info

        buf = io.StringIO()
        with redirect_stdout(buf):
            _render_info(data)
        return buf.getvalue()

    def test_every_branch(self):
        out = self._render({
            "ref": "lsp@official",
            "updated": "2026-11-01T00:00:00+00:00",
            "plugin": {"name": "lsp", "version": "1.2.3",
                       "description": "Language smarts",
                       "source_path": "/src/lsp",
                       "harnesses": {"claude": {"installed": True, "enabled": True,
                                                "path": "/p"}},
                       "components": {"skills": 2, "mcp_servers": 1}},
            "skills": [{"name": "hover", "harnesses": ["claude"],
                        "usage": {"tracked": True, "calls": 5}},
                       {"name": "jump", "harnesses": [],
                        "usage": {"tracked": False}}],
            "mcp_servers": [{"name": "lsp", "in_hub": True,
                             "harnesses": ["claude"], "disabled_in": ["codex"],
                             "env_keys": ["TOKEN", "GONE"],
                             "header_keys": ["X-Key"],
                             "secrets_missing": ["GONE"],
                             "usage": {"tracked": True, "calls": 3}},
                            {"name": "other", "usage": {"tracked": True, "calls": 0}}],
            "usage": {"tracked": True, "calls": 8, "sessions": 2,
                      "last_used": "2026-11-02T00:00:00Z",
                      "by_harness": {"claude": 8}},
        })
        for needle in ("lsp@official", "v1.2.3", "updated 2026-11-01",
                       "Language smarts", "2 skills", "1 mcp servers",
                       "hover", "5 calls", "untracked", "in hub", "in claude",
                       "off in codex", "TOKEN", "missing", "GONE", "X-Key",
                       "3 calls", "declared only", "8 calls across 2 sessions"):
            self.assertIn(needle, out)

    def test_bare_minimum(self):
        out = self._render({"ref": "x@y", "plugin": {"name": "x", "harnesses": {}}})
        self.assertIn("x@y", out)

    def test_info_command_in_process(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from swe.plugins.commands import _info

        def fail(message):
            print(message)
            return 1

        data = {"ref": "x@y", "plugin": {"name": "x", "harnesses": {}}}
        out = io.StringIO()
        with patch("swe.plugins.info.plugin_info", return_value=data), \
                redirect_stdout(out):
            self.assertEqual(_info(["x@y"], False, fail), 0)
        self.assertIn("x@y", out.getvalue())
        out = io.StringIO()
        with patch("swe.plugins.info.plugin_info", return_value=None), \
                redirect_stdout(out):
            self.assertEqual(_info(["none@y"], False, fail), 1)
        self.assertIn("No plugin named", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(_info(["a", "b"], False, fail), 1)
        self.assertIn("Usage:", out.getvalue())


class TestCopyFallback(unittest.TestCase):
    def test_folder_falls_back_to_the_newest_cached_copy(self):
        # A plugin whose install record points nowhere and whose
        # marketplace is gone still has its cached copies to read.
        import swe.plugins.info as info_mod

        with tempfile.TemporaryDirectory() as td:
            old = Path(td) / "cache" / "gone" / "plug" / "1.0.0"
            new = Path(td) / "cache" / "gone" / "plug" / "2.0.0"
            old.mkdir(parents=True)
            (new / ".mcp.json").parent.mkdir(parents=True)
            (new / ".mcp.json").write_text(
                '{"mcpServers": {"srv": {"env": {"KEY": "v"}}}}')
            catalog = {"harnesses": ["claude"], "errors": [], "marketplaces": [{
                "name": "gone", "harnesses": {}, "plugins": [{
                    "name": "plug", "ref": "plug@gone", "source_path": None,
                    "harnesses": {"claude": {"installed": None, "path": None,
                                             "copies": [str(old), str(new)]}},
                    "skills": []}]}]}
            with patch.object(info_mod, "build_catalog", return_value=catalog):
                data = plugin_info("plug@gone")
        self.assertIsNotNone(data)
        self.assertEqual([s["name"] for s in data["mcp_servers"]], ["srv"])
        self.assertEqual(data["mcp_servers"][0]["env_keys"], ["KEY"])


class TestSourcePathFallback(unittest.TestCase):
    def test_folder_falls_back_to_source_path(self):
        # A plugin listed but installed nowhere has no harness load path;
        # declared servers then come from `source_path` (the marketplace
        # clone). No fixture plugin sits in that state, so the row is
        # fabricated here.
        import swe.plugins.info as info_mod

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "clone" / "plug"
            (src / ".claude-plugin").mkdir(parents=True)
            catalog = {"harnesses": ["claude"], "errors": [], "marketplaces": [{
                "name": "mk", "harnesses": {}, "plugins": [{
                    "name": "plug", "ref": "plug@mk", "source_path": str(src),
                    "harnesses": {"claude": {"installed": False, "path": None}},
                    "skills": []}]}]}
            with patch.object(info_mod, "build_catalog", return_value=catalog):
                data = plugin_info("plug@mk")
        self.assertIsNotNone(data)
        self.assertIsNotNone(data["updated"])


if __name__ == "__main__":
    unittest.main()
