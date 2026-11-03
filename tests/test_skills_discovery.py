import json
import tempfile
import unittest
from pathlib import Path
import unittest.mock
from unittest.mock import patch

from swe.skills.discovery import discover_skills, parse_skill_md, skill_roots


class SkillsDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.skills = self.home / ".swe" / "skills"
        self.builtin = self.home / ".cursor" / "skills-cursor"

    def tearDown(self):
        self.tmp.cleanup()

    def _write_skill(self, root: Path, folder: str, name: str, description: str):
        skill_dir = root / folder
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
            encoding="utf-8",
        )

    def test_parse_skill_md_reads_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "demo-skill" / "SKILL.md"
            md.parent.mkdir()
            md.write_text("---\nname: demo\ndescription: A demo skill\n---\n")
            name, desc = parse_skill_md(md)
            self.assertEqual(name, "demo")
            self.assertEqual(desc, "A demo skill")

    def test_skill_roots_dedupes_symlinked_paths(self):
        self.skills.mkdir(parents=True)
        self.builtin.mkdir(parents=True)
        linked = self.home / ".cursor" / "skills"
        linked.symlink_to(self.skills)

        roots = skill_roots(home=self.home, cwd=self.home)
        labels = [label for label, _ in roots]
        self.assertIn("shared", labels)
        self.assertIn("cursor-builtin", labels)
        # shared and cursor both point at same real path — first label wins
        real_paths = [str(p.resolve()) for _, p in roots]
        self.assertEqual(len(real_paths), len(set(real_paths)))

    def test_discover_skills_finds_multiple_scopes(self):
        self._write_skill(self.skills, "alpha", "alpha", "First skill")
        self._write_skill(self.builtin, "beta", "beta", "Builtin skill")

        with patch("swe.skills.discovery.load_skill_catalogs", return_value=[]):
            found = discover_skills(home=self.home, cwd=self.home)
        names = {s["name"] for s in found}
        scopes = {s["scope"] for s in found}
        self.assertEqual(names, {"alpha", "beta"})
        self.assertIn("shared", scopes)
        self.assertIn("cursor-builtin", scopes)

    def test_codex_system_skills_in_the_shared_tree_are_not_listed(self):
        # ~/.codex/skills links to the shared tree, so Codex's bundled
        # skills land in ~/.swe/skills/.system. Other dot folders are
        # still skills the user put there.
        self._write_skill(self.skills, "alpha", "alpha", "Hub skill")
        self._write_skill(self.skills, ".system/imagegen", "imagegen", "Codex's own")
        self._write_skill(self.skills, ".sources/beta", "beta", "Private copy")
        self._write_skill(self.skills, "team/.system", "gamma", "Not at the top")
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "skills").symlink_to(self.skills)

        with patch("swe.skills.discovery.load_skill_catalogs", return_value=[]):
            found = discover_skills(home=self.home, cwd=self.home)
        self.assertEqual(sorted(s["name"] for s in found), ["alpha", "beta", "gamma"])

    def test_roots_are_enumerated_once_for_every_skill(self):
        # #1537: visible_via enumerated and resolved every root per SKILL.md.
        from swe.skills import layout

        for name in ("alpha", "beta", "gamma"):
            self._write_skill(self.skills, name, name, "A skill")
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "skills").symlink_to(self.skills)
        counted = unittest.mock.MagicMock(wraps=layout.enumerate_skill_roots)
        with patch("swe.skills.discovery.load_skill_catalogs", return_value=[]), \
                patch.object(layout, "enumerate_skill_roots", counted), \
                patch("swe.skills.discovery.enumerate_skill_roots", counted, create=True):
            found = discover_skills(home=self.home, cwd=self.home)
        self.assertEqual(counted.call_count, 1)
        alpha = next(s for s in found if s["name"] == "alpha")
        self.assertIn("shared", alpha["visible_via"])
        self.assertIn("codex", alpha["visible_via"])


if __name__ == "__main__":
    unittest.main()
