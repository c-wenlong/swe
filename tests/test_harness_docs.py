"""docs/harness-support.md and examples/harness.example.json against the code.

The page's Setup and Sessions tables are facts about what the code does, so
each cell that code decides is derived here and compared: a harness added to
the catalog, a skill root or a resume rule changed, and this fails until the
page says the same.
"""

import json
import re
import shutil
import unittest
from pathlib import Path
from unittest import mock

from tests._tempdir import temp_dir

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "docs" / "harness-support.md").read_text(encoding="utf-8")
EXAMPLE = ROOT / "examples" / "harness.example.json"

# The page's row label -> the registry name the code uses.
NAMES = {
    "Claude Code": "claude", "Codex": "codex", "Cursor": "cursor", "Gemini": "gemini",
    "Copilot": "copilot", "Cline": "cline", "Kilo": "kilo", "OpenCode": "opencode",
    "Droid": "droid", "Devin": "devin", "Crush": "crush", "Qwen Code": "qwen-code",
    "Mistral Vibe": "mistral-vibe", "Goose": "goose", "Aider": "aider",
    "Continue": "continue", "Pi": "pi", "Forge": "forge", "Augment": "augment",
    "Kiro": "kiro", "Blackbox": "blackbox", "Freebuff": "freebuff", "Grok": "grok",
    "Kimi": "kimi", "Antigravity": "antigravity", "Command Code": "commandcode",
    "Qoder": "qoder", "Qoder CN": "qoder-cn", "Jules": "jules",
    "Mastracode": "mastracode", "ZCode": "zcode",
}


def table(heading: str) -> dict[str, dict[str, str]]:
    """The first table under ``## heading``: registry name -> {column: cell}."""
    section = PAGE.split("## " + heading + "\n", 1)[1].split("\n## ", 1)[0]
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in section.splitlines() if line.startswith("|")]
    header, body = rows[0], rows[2:]
    out = {}
    for row in body:
        out[NAMES[row[0]]] = dict(zip(header[1:], row[1:]))
    return out


def cell(flag: bool, partial: bool = False) -> str:
    return "Yes" if flag else "Partial" if partial else "No"


class TestHarnessSupportPage(unittest.TestCase):
    def test_every_harness_the_code_knows_has_a_row(self):
        from swe.harness.catalog import HARNESS_CATALOG
        from swe.harness.installers import INSTALLERS

        known = set(HARNESS_CATALOG) | set(INSTALLERS)
        for heading in ("Setup", "Sessions", "Usage in the app"):
            self.assertEqual(set(table(heading)), known, heading)

    def test_setup_columns_follow_the_code(self):
        from swe.harness.catalog import HARNESS_CATALOG
        from swe.harness.installers import INSTALLERS
        from swe.init.layout import HARNESS_SIGNATURES, REGISTRY_ALIASES
        from swe.mcp.cli import MCP_CONFIG_MAP
        from swe.plugins.catalog import ACTIONABLE, HARNESSES
        from swe.skills.layout import HARNESS_ROOTS, SHARED_LABEL

        def registry_name(label):
            return REGISTRY_ALIASES.get(label, label)

        skills = {label for label, _ in HARNESS_ROOTS if label != SHARED_LABEL}
        skills |= {registry_name(label) for label, sig in HARNESS_SIGNATURES.items() if sig.skills}
        instructions = {registry_name(label) for label, sig in HARNESS_SIGNATURES.items()
                        if sig.instructions}
        for name, row in table("Setup").items():
            with self.subTest(name):
                self.assertEqual(row["Install"], cell(name in INSTALLERS))
                self.assertEqual(row["Instructions"], cell(name in instructions))
                self.assertEqual(row["Skills"], cell(name in skills))
                self.assertEqual(row["Plugins"], cell(name in ACTIONABLE, name in HARNESSES))
                self.assertEqual(row["MCP sync"], cell(name in MCP_CONFIG_MAP, name in HARNESS_CATALOG))

    def test_session_columns_follow_the_code(self):
        from swe.sessions.aggregator import PARSER_REGISTRY
        from swe.sessions.resume import LIMITED_RESUME, RESUME_FLAGS

        listed = {name for name, _parser, _keys in PARSER_REGISTRY}
        for name, row in table("Sessions").items():
            with self.subTest(name):
                self.assertEqual(row["Sessions list"], cell(name in listed))
                self.assertEqual(row["Resume a session"],
                                 cell(name in RESUME_FLAGS, name in LIMITED_RESUME))


class TestHarnessExample(unittest.TestCase):
    """examples/harness.example.json is what README points a reader at, so
    the real registry loader has to take it and the names it uses have to be
    the ones the catalog and `swe init` use."""

    def load(self):
        from swe.harness import registry

        home = temp_dir(self)
        shutil.copy(EXAMPLE, home / "harness.json")
        with mock.patch.object(registry, "HARNESS_FILE", home / "harness.json"):
            return registry, registry.load_registry()

    def test_the_registry_loader_reads_it(self):
        registry, reg = self.load()
        self.assertEqual(sorted(reg), ["aider", "claude", "codex", "cursor", "opencode"])
        self.assertEqual(registry.starred_names(reg), ["claude", "codex"])
        self.assertEqual(registry.archived_names(reg), ["aider"])
        self.assertEqual(registry.resolve(reg, "cs"), "cursor")
        self.assertTrue(registry.is_active(reg["opencode"]))

    def test_commands_and_roots_agree_with_the_catalog_and_init(self):
        from swe.harness.catalog import HARNESS_CATALOG
        from swe.init.layout import HARNESS_SIGNATURES

        _registry, reg = self.load()
        for name, entry in reg.items():
            with self.subTest(name):
                self.assertEqual(entry["command"], HARNESS_CATALOG[name]["command"])
                self.assertEqual(entry["aliases"], HARNESS_CATALOG[name]["aliases"])
                sig = HARNESS_SIGNATURES.get(name)
                caps = entry.get("capabilities")
                if sig and caps:
                    self.assertEqual(caps["skills"]["root"], "~/" + sig.skills.as_posix())
                    self.assertEqual(caps["instructions"]["file"], "~/" + sig.instructions.as_posix())

    def test_the_legacy_shape_is_gone(self):
        self.assertFalse((ROOT / "examples" / "tools.example.json").exists())
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("tools.example.json", readme)
        self.assertIn("examples/harness.example.json", readme)
        json.loads(EXAMPLE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
