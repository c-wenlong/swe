import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.skills.catalog_discover import apply_skill_catalog_findings, discover_skill_catalogs
from swe.skills.catalogs import (
    _same_path,
    add_skill_catalog,
    discover_catalog_dirs,
    load_skill_catalogs,
    remove_skill_catalog,
    suggest_catalog_label,
)
from swe.skills.discovery import discover_skills, skill_roots


def _catalog_patches(config_dir: Path, catalogs_file: Path):
    return (
        patch("swe.paths.CONFIG_DIR", config_dir),
        patch("swe.paths.SKILL_CATALOGS_FILE", catalogs_file),
        patch("swe.skills.catalogs.SKILL_CATALOGS_FILE", catalogs_file),
        patch("swe.skills.catalogs.CONFIG_DIR", config_dir),
    )


class SkillsCatalogTest(unittest.TestCase):
    def _write_skill(self, root: Path, *parts: str, name: str = "demo"):
        skill_dir = root.joinpath(*parts)
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test\n---\n",
            encoding="utf-8",
        )

    def test_suggest_label_uses_parent_when_basename_is_skills(self):
        path = Path("/tmp/ai-engineering/skills")
        self.assertEqual(suggest_catalog_label(path), "ai-engineering")

    def test_discover_finds_skills_dir_under_desktop(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            catalog = home / "Desktop" / "Personal" / "Projects" / "ai-engineering" / "skills"
            self._write_skill(catalog, "my-skill", name="my-skill")

            found = discover_catalog_dirs(home)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].resolve(), catalog.resolve())

    def test_catalog_add_and_list_makes_skills_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            catalog = home / "Desktop" / "ai-engineering" / "skills"
            self._write_skill(catalog, "alpha", name="alpha")

            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4:
                add_skill_catalog(catalog, "ai-engineering")
                roots = skill_roots(home=home, cwd=home)
                labels = [label for label, _ in roots]
                self.assertIn("ai-engineering", labels)
                skills = discover_skills(home=home, cwd=home)
                self.assertEqual({s["name"] for s in skills}, {"alpha"})
                self.assertEqual(skills[0]["scope"], "ai-engineering")

    def test_discover_and_apply_registers_new_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            catalog = home / "Documents" / "work" / "skills"
            self._write_skill(catalog, "beta", name="beta")

            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4, patch(
                "swe.skills.catalog_discover.discover_catalog_dirs",
                return_value=[catalog.resolve()],
            ):
                findings = discover_skill_catalogs(home=home, cwd=home)
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0].status, "new")
                added = apply_skill_catalog_findings(findings)
                self.assertEqual(added, ["work"])
                saved = load_skill_catalogs()
                self.assertEqual(len(saved), 1)

    def test_catalog_add_dot_uses_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            catalog = home / "work" / "ai-engineering" / "skills"
            self._write_skill(catalog, "gamma", name="gamma")

            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4, patch("os.getcwd", return_value=str(catalog)):
                from swe.skills.catalog_commands import cmd_skills_catalog

                self.assertEqual(cmd_skills_catalog(["."]), 0)
                saved = load_skill_catalogs()
                self.assertEqual(len(saved), 1)
                self.assertEqual(Path(saved[0]["path"]).resolve(), catalog.resolve())

                catalogs_file.unlink(missing_ok=True)
                self.assertEqual(cmd_skills_catalog(["add"]), 0)
                saved = load_skill_catalogs()
                self.assertEqual(Path(saved[0]["path"]).resolve(), catalog.resolve())

    def test_list_beside_a_folder_named_list_still_lists(self):
        # The path check ran first, so `catalog list` beside ./list tried
        # to add ./list as a catalog.
        import contextlib
        import io
        import os

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            work = home / "work"
            self._write_skill(work / "list", "x", name="x")
            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            before = os.getcwd()
            os.chdir(work)
            try:
                with p1, p2, p3, p4:
                    from swe.skills.catalog_commands import cmd_skills_catalog

                    out = io.StringIO()
                    with contextlib.redirect_stdout(out):
                        self.assertEqual(cmd_skills_catalog(["list"]), 0)
                    self.assertIn("No catalogs configured", out.getvalue())
                    self.assertEqual(load_skill_catalogs(), [])
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(cmd_skills_catalog(["add", "list"]), 0)
                    self.assertEqual([c["label"] for c in load_skill_catalogs()], ["list"])
            finally:
                os.chdir(before)

    def test_remove_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            catalog = home / "skills-tree"
            catalog.mkdir()
            self._write_skill(catalog, "one", name="one")

            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4:
                add_skill_catalog(catalog, "mine")
                self.assertTrue(remove_skill_catalog("mine"))
                self.assertEqual(load_skill_catalogs(), [])


    def test_a_non_object_catalogs_file_reads_as_none(self):
        # `[]` crashed `swe skills catalog list` with AttributeError.
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".config" / "swe"
            config_dir.mkdir(parents=True)
            catalogs_file = config_dir / "skill_catalogs.json"
            catalogs_file.write_text("[]")
            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4:
                self.assertEqual(load_skill_catalogs(), [])

    def test_remove_matches_a_label_or_a_whole_path_never_a_substring(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            eng = home / "engineering" / "skills"
            engines = home / "design-engines" / "skills"
            for root in (eng, engines):
                self._write_skill(root, "one", name="one")

            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4:
                add_skill_catalog(eng, "eng")
                add_skill_catalog(engines, "engines")
                # "eng" is a substring of both paths; only its label matches.
                self.assertTrue(remove_skill_catalog("eng"))
                self.assertEqual([c["label"] for c in load_skill_catalogs()], ["engines"])
                self.assertFalse(remove_skill_catalog("design"))
                self.assertTrue(remove_skill_catalog(str(engines)))
                self.assertEqual(load_skill_catalogs(), [])

    def test_remove_by_a_path_that_cannot_resolve_removes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            root = home / "team" / "skills"
            self._write_skill(root, "one", name="one")

            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4:
                add_skill_catalog(root, "team")
                with patch("swe.skills.catalogs.resolve_catalog_path",
                           side_effect=RuntimeError("symlink loop")):
                    self.assertFalse(remove_skill_catalog("/loop"))
                with patch("swe.skills.catalogs.expand_user",
                           side_effect=OSError("unreadable")):
                    self.assertFalse(_same_path("~/team/skills", root))
                self.assertEqual([c["label"] for c in load_skill_catalogs()], ["team"])

class SkillsCatalogRegistryEdgeTest(unittest.TestCase):
    """Replacing, refusing and pruning catalogs, and hand-edited files."""

    def setUp(self):
        from tests._tempdir import temp_dir

        self.home = temp_dir(self)
        self.config_dir = self.home / ".config" / "swe"
        self.file = self.config_dir / "skill_catalogs.json"
        for p in _catalog_patches(self.config_dir, self.file):
            p.start()
            self.addCleanup(p.stop)

    def _catalog(self, *parts: str, skills=("one",)) -> Path:
        root = self.home.joinpath(*parts)
        for name in skills:
            (root / name).mkdir(parents=True, exist_ok=True)
            (root / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve()

    def test_hand_edited_entries_that_are_not_catalogs_are_dropped(self):
        self.config_dir.mkdir(parents=True)
        for value in ({"label": "x"}, 5, "path"):
            with self.subTest(catalogs=value):
                self.file.write_text(json.dumps({"catalogs": value}))
                self.assertEqual(load_skill_catalogs(), [])
        self.file.write_text(json.dumps({"catalogs": [
            "a string", {"label": "  ", "path": "/x"}, {"label": "l"},
            {"label": "ok", "path": "/x", "extra": 1}]}))
        self.assertEqual(load_skill_catalogs(),
                         [{"label": "ok", "path": "/x", "extra": 1}])

    def test_re_adding_a_label_replaces_it_and_keeps_its_first_date(self):
        first = self._catalog("a", "skills")
        moved = self._catalog("b", "skills")
        entry = add_skill_catalog(first, "team")
        again = add_skill_catalog(moved, "team")
        self.assertEqual(again["added"], entry["added"])
        saved = load_skill_catalogs()
        self.assertEqual([(c["label"], c["path"]) for c in saved],
                         [("team", str(moved))])

    def test_re_adding_a_path_under_a_new_label_renames_it(self):
        root = self._catalog("a", "skills")
        add_skill_catalog(root, "old")
        add_skill_catalog(root, "New Name")
        self.assertEqual([(c["label"], c["path"]) for c in load_skill_catalogs()],
                         [("new-name", str(root))])

    def test_a_path_stored_with_a_tilde_is_still_the_same_catalog(self):
        # Regression: add compared the stored path without expanding ~, so
        # a hand-written "~/a/skills" got a duplicate entry beside it.
        import os

        root = self._catalog("a", "skills")
        self.config_dir.mkdir(parents=True)
        self.file.write_text(json.dumps({"catalogs": [
            {"label": "mine", "path": "~/a/skills", "added": "2026-01-01"}]}))
        with patch.dict(os.environ, {"HOME": str(self.home)}):
            entry = add_skill_catalog(root, "renamed")
        saved = load_skill_catalogs()
        self.assertEqual(len(saved), 1, saved)
        self.assertEqual(saved[0]["label"], "renamed")
        self.assertEqual(entry["added"], "2026-01-01")

    def test_a_missing_folder_or_one_without_skills_is_refused(self):
        empty = self.home / "empty"
        empty.mkdir()
        with self.assertRaises(FileNotFoundError):
            add_skill_catalog(self.home / "absent")
        with self.assertRaises(ValueError):
            add_skill_catalog(empty)
        self.assertFalse(self.file.exists())

    def test_a_corrupt_catalogs_file_is_never_overwritten(self):
        from swe.configuration import ConfigurationError

        root = self._catalog("a", "skills")
        self.config_dir.mkdir(parents=True)
        self.file.write_text("{not json")
        with self.assertRaises(ConfigurationError):
            add_skill_catalog(root)
        self.assertEqual(self.file.read_text(), "{not json")

    def test_discovery_prunes_nested_catalogs_and_skips_noise(self):
        outer = self._catalog("Desktop", "proj", "skills")
        self._catalog("Desktop", "proj", "skills", "vendored", "skills")
        self._catalog("Desktop", "proj", "node_modules", "pkg", "skills")
        self._catalog("Desktop", "proj", ".hidden", "skills")
        docs = self._catalog("Documents", "notes", "Skills")
        # A folder named skills with no SKILL.md is not a catalog.
        (self.home / "Desktop" / "bare" / "skills").mkdir(parents=True)
        self.assertEqual(discover_catalog_dirs(self.home), [outer, docs])

    def test_discovery_keeps_its_alphabetical_order_after_pruning(self):
        # Regression: pruning re-sorted by depth, so a shallow catalog
        # jumped ahead of a deeper one that sorts first.
        deep = self._catalog("Desktop", "a", "b", "c", "skills")
        shallow = self._catalog("Desktop", "z", "skills")
        self.assertEqual(discover_catalog_dirs(self.home), [deep, shallow])

    def test_discovery_stops_at_the_depth_limit(self):
        deep = self._catalog("Desktop", "a", "b", "skills")
        self.assertEqual(discover_catalog_dirs(self.home, max_depth=3), [deep])
        self.assertEqual(discover_catalog_dirs(self.home, max_depth=2), [])

    def test_a_linked_catalog_is_listed_once(self):
        real = self._catalog("Desktop", "real", "skills")
        (self.home / "Documents").mkdir()
        (self.home / "Documents" / "skills").symlink_to(real)
        self.assertEqual(discover_catalog_dirs(self.home), [real])

    def test_findings_name_their_source_and_skip_registered_ones(self):
        desktop = self._catalog("Desktop", "team", "skills", skills=("a", "b"))
        documents = self._catalog("Documents", "notes", "skills")
        add_skill_catalog(documents, "notes")
        found = discover_skill_catalogs(home=self.home, cwd=self.home)
        self.assertEqual([(f.label, f.source, f.status, f.skill_count)
                          for f in found],
                         [("team", "desktop", "new", 2)])
        both = discover_skill_catalogs(home=self.home, cwd=self.home,
                                       include_registered=True)
        self.assertEqual([(f.label, f.source, f.status) for f in both],
                         [("team", "desktop", "new"),
                          ("notes", "documents", "registered")])
        # Applying adds only the new one.
        self.assertEqual(apply_skill_catalog_findings(both), ["team"])
        self.assertEqual(sorted(c["label"] for c in load_skill_catalogs()),
                         ["notes", "team"])
        self.assertEqual(desktop, Path(load_skill_catalogs()[1]["path"]))

    def test_a_catalog_outside_home_is_labelled_catalog(self):
        from swe.skills.catalog_discover import _source_for_path

        self.assertEqual(_source_for_path(Path("/opt/skills"), self.home),
                         "catalog")
        self.assertEqual(_source_for_path(self.home / "work" / "skills",
                                          self.home), "catalog")
        self.assertEqual(_source_for_path(self.home / "desktop" / "skills",
                                          self.home), "desktop")


class SkillsCatalogCommandTest(unittest.TestCase):
    def _run(self, args):
        import io
        from contextlib import redirect_stdout
        from swe.skills.catalog_commands import cmd_skills_catalog
        out = io.StringIO()
        with redirect_stdout(out):
            code = cmd_skills_catalog(args)
        return code, out.getvalue()

    def test_help_runs(self):
        code, out = self._run(["help"])
        self.assertEqual(code, 0)

    def test_list_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4:
                code, out = self._run(["list"])
        self.assertEqual(code, 0)
        self.assertIn("No catalogs configured", out)

    def test_list_renders_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            catalog = home / "skills-tree"
            catalog.mkdir()
            SkillsCatalogTest()._write_skill(catalog, "one", name="one")
            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4:
                add_skill_catalog(catalog, "mine")
                code, out = self._run(["list"])
        self.assertEqual(code, 0)
        self.assertIn("mine", out)
        self.assertIn("SKILLS", out)

    def test_remove_without_arg_is_usage_error(self):
        code, out = self._run(["remove"])
        self.assertEqual(code, 1)
        self.assertIn("Usage", out)

    def test_remove_missing_catalog_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4:
                code, out = self._run(["remove", "ghost"])
        self.assertEqual(code, 1)
        self.assertIn("No catalog matching", out)

    def test_add_missing_path_fails(self):
        code, out = self._run(["add", "/definitely/not/there"])
        self.assertEqual(code, 1)

    def test_unknown_subcommand_fails(self):
        code, out = self._run(["frobnicate"])
        self.assertEqual(code, 1)

    def test_bare_path_argument_adds_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            catalog = home / "skills-tree"
            catalog.mkdir()
            SkillsCatalogTest()._write_skill(catalog, "one", name="one")
            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4:
                code, out = self._run([str(catalog)])
                self.assertEqual(code, 0)
                self.assertEqual(len(load_skill_catalogs()), 1)
                # Bare `swe skills` prints the overview, and `list` skips
                # catalogs: the hint searches by the catalog's label.
                label = load_skill_catalogs()[0]["label"]
                self.assertIn(f"Run `swe skills {label}` to list its skills", out)


class SkillsDiscoverCommandTest(unittest.TestCase):
    def _finding(self, label="cat", status="new", count=2):
        from swe.skills.catalog_discover import SkillCatalogFinding
        return SkillCatalogFinding(path=Path("/x/skills"), label=label,
                                   skill_count=count, source="desktop",
                                   status=status)

    def _run(self, args, findings, apply_return=None):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        from swe.skills import catalog_commands as cc
        apply_fn = mock.Mock(return_value=apply_return if apply_return is not None else [])
        out = io.StringIO()
        with patch.object(cc, "discover_skill_catalogs", lambda **k: findings), \
                patch.object(cc, "apply_skill_catalog_findings", apply_fn), \
                redirect_stdout(out):
            code = cc.cmd_skills_discover(args)
        return code, out.getvalue(), apply_fn

    def test_table_lists_findings(self):
        code, out, _ = self._run([], [self._finding()])
        self.assertEqual(code, 0)
        self.assertIn("Skills Discover", out)
        self.assertIn("cat", out)

    def test_empty_findings_say_so(self):
        code, out, _ = self._run([], [])
        self.assertEqual(code, 0)
        self.assertIn("No new skill catalogs", out)

    def test_apply_reports_added(self):
        code, out, apply_fn = self._run(["--apply"], [self._finding()],
                                        apply_return=["cat"])
        self.assertEqual(code, 0)
        self.assertIn("Registered 1 catalog", out)
        self.assertIn("Run `swe skills <label>` (cat)", out)
        apply_fn.assert_called_once()

    def test_apply_nothing_to_add(self):
        code, out, _ = self._run(["--apply"], [self._finding(status="registered")],
                                 apply_return=[])
        self.assertEqual(code, 0)
        self.assertIn("Nothing to add", out)

    def test_help_runs(self):
        code, _out, _ = self._run(["-h"], [])
        self.assertEqual(code, 0)

    def test_unknown_arg_fails(self):
        code, _out, apply_fn = self._run(["bogus"], [])
        self.assertEqual(code, 1)
        apply_fn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
