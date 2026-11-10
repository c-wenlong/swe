"""SWE owns adopted bytes; previews and missing installs aren't inventory."""
import json
import unittest
from pathlib import Path

from swe.plugins.actions import ActionError
from swe.plugins.catalog import build_catalog
from swe.plugins.managed import import_plugin, local_plugin, managed_plugin

try:
    from test_plugin_catalog import _CatalogHome
    from test_plugin_drift import _write
except ImportError:
    from tests.test_plugin_catalog import _CatalogHome
    from tests.test_plugin_drift import _write


class ManagedPluginsTest(_CatalogHome, unittest.TestCase):
    def lookup(self, ref):
        catalog = build_catalog(self.home)
        market = next(m for m in catalog["marketplaces"] if m["name"] == ref.split("@")[1])
        return market, next(p for p in market["plugins"] if p["ref"] == ref)

    def test_inventory_ignores_marketplace_preview_and_missing_install(self):
        market, plugin = self.lookup("lsp@official")
        self.assertTrue(local_plugin(market, plugin))
        for state in plugin["harnesses"].values():
            state.update(installed=False, path=None, copies=[])
        self.assertFalse(local_plugin(market, plugin))  # source_path still exists
        plugin["harnesses"]["claude"].update(installed=True, path="/missing-plugin")
        self.assertFalse(local_plugin(market, plugin))

    def test_import_copies_installed_bytes_without_changing_harness(self):
        before = (self.home / ".claude/plugins/installed_plugins.json").read_bytes()
        result = import_plugin(self.home, "lsp@official")
        self.assertTrue(result["changed"])
        target = Path(result["state"]["path"])
        self.assertTrue((target / "skills/hover/SKILL.md").is_file())
        self.assertTrue((target / ".mcp.json").is_file())  # only the installed copy has it
        self.assertEqual(before, (self.home / ".claude/plugins/installed_plugins.json").read_bytes())
        market, plugin = self.lookup("lsp@official")
        self.assertTrue(managed_plugin(market, plugin))
        self.assertEqual(plugin["source_path"], str(target))
        (target / "skills/hover/SKILL.md").write_text("SWE's edits")
        self.assertFalse(import_plugin(self.home, "lsp@official")["changed"])
        self.assertEqual((target / "skills/hover/SKILL.md").read_text(), "SWE's edits")
        self.assertEqual(len(market["plugins"]), 2)  # upstream catalog preserved

    def test_cache_only_cursor_plugin_can_be_imported(self):
        catalog = build_catalog(self.home)
        plugin = next(p for m in catalog["marketplaces"] for p in m["plugins"]
                      if p["harnesses"].get("cursor", {}).get("copies"))
        self.assertTrue(import_plugin(self.home, plugin["ref"])["changed"])

    def test_preview_cannot_be_imported(self):
        with self.assertRaises(ActionError):
            import_plugin(self.home, "remote-one@official")

    def test_conflict_and_external_symlinks_never_overwrite(self):
        target = self.home / ".swe/plugins/official"
        target.mkdir()
        with self.assertRaises(ActionError):
            import_plugin(self.home, "lsp@official")
        target.rmdir()
        market, plugin = self.lookup("lsp@official")
        source = Path(plugin["harnesses"]["claude"]["path"])
        (source / "outside").symlink_to(self.home)
        with self.assertRaises(ActionError):
            import_plugin(self.home, "lsp@official")
        self.assertFalse(target.exists())

    def test_import_adds_to_existing_swe_marketplace(self):
        import_plugin(self.home, "lsp@official")
        cache = self.home / ".claude/plugins/cache/official/remote-one/1"
        _write(cache / "README.md", "local bytes")
        self.installed["plugins"]["remote-one@official"] = [
            {"scope": "user", "installPath": str(cache), "version": "1"}]
        self._write_claude()
        import_plugin(self.home, "remote-one@official")
        data = json.loads((self.home / ".swe/plugins/official/.claude-plugin/marketplace.json").read_text())
        self.assertEqual([p["name"] for p in data["plugins"]], ["lsp", "remote-one"])


if __name__ == "__main__":
    unittest.main()
