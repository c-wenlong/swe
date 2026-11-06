import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.skills.layout import (
    enumerate_skill_roots,
    load_link_records,
    sync_link_records_from_filesystem,
)
from swe.skills.link_ops import (
    SkillLayoutError,
    find_skill_directory,
    link_skill_root,
    move_skill,
    unlink_skill_root,
)

from tests._tempdir import temp_dir


class SkillsLayoutTest(unittest.TestCase):
    def setUp(self):
        # skill_links.json's path is fixed at import from the runner's
        # HOME, and link/unlink write it; every test gets its own copy.
        # Tests that check the records file patch these again themselves.
        self.records = temp_dir(self) / "skill_links.json"
        for target, value in (("swe.skills.layout.SKILL_LINKS_FILE", self.records),
                              ("swe.skills.layout.CONFIG_DIR", self.records.parent)):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _write_skill(self, root: Path, folder: str, name: str = "demo"):
        d = root / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test\n---\n", encoding="utf-8")

    def test_enumerate_shows_symlinks_not_deduped(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            self._write_skill(shared, "alpha", "alpha")
            codex = home / ".codex" / "skills"
            codex.parent.mkdir(parents=True)
            codex.symlink_to(shared)

            entries = {e.label: e for e in enumerate_skill_roots(home=home)}
            self.assertEqual(entries["shared"].kind, "directory")
            self.assertEqual(entries["codex"].kind, "symlink")
            self.assertEqual(entries["codex"].link_target_label, "shared")
            self.assertIn("codex", entries["shared"].aliases)

    def test_link_and_unlink_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            self._write_skill(shared, "shared-skill", "shared-skill")
            codex = home / ".codex" / "skills"

            link_skill_root("codex", "shared", home=home)
            self.assertTrue(codex.is_symlink())
            self.assertEqual(codex.resolve(), shared.resolve())
            self.assertEqual(
                [(r["label"], r["target"]) for r in load_link_records()],
                [("codex", str(shared))])

            unlink_skill_root("codex", home=home, mkdir=True)
            self.assertFalse(codex.is_symlink())
            self.assertTrue(codex.is_dir())
            self.assertEqual(load_link_records(), [])

    def test_link_replaces_a_dangling_symlink(self):
        # exists() is False on a dangling link, so the old code fell
        # through to symlink_to() and died with FileExistsError even
        # though the path was only a broken link.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            codex = home / ".codex" / "skills"
            codex.parent.mkdir(parents=True)
            codex.symlink_to(home / ".codex" / "gone")

            link_skill_root("codex", "shared", home=home)
            self.assertTrue(codex.is_symlink())
            self.assertEqual(codex.resolve(), shared.resolve())

    def test_unlink_removes_a_dangling_symlink(self):
        # The inverse bug: exists() reported "does not exist" for a broken
        # link, so `swe skills unlink` could never remove one.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            codex = home / ".codex" / "skills"
            codex.parent.mkdir(parents=True)
            codex.symlink_to(home / ".codex" / "gone")

            unlink_skill_root("codex", home=home)
            self.assertFalse(os.path.lexists(codex))

    def test_move_from_a_literal_path_root(self):
        # --from takes a root ref, so a path must work like a label.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            foo = home / ".foo" / "skills"
            codex = home / ".codex" / "skills"
            foo.mkdir(parents=True)
            codex.mkdir(parents=True)
            self._write_skill(foo, "move-me", "move-me")

            src, dest = move_skill("move-me", str(foo), "codex", home=home)
            self.assertFalse(src.exists())
            self.assertTrue((dest / "SKILL.md").exists())

    def test_move_from_a_discovered_root_label(self):
        # ~/.foo/skills is found by the on-disk scan but has no builtin
        # label; the label `swe find skills` reports must still resolve.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            foo = home / ".foo" / "skills"
            codex = home / ".codex" / "skills"
            foo.mkdir(parents=True)
            codex.mkdir(parents=True)
            self._write_skill(foo, "move-me", "move-me")

            src, dest = move_skill("move-me", "foo", "codex", home=home)
            self.assertFalse(src.exists())
            self.assertTrue((dest / "SKILL.md").exists())

    def test_move_from_a_symlinked_root_by_path(self):
        # foo/skills -> shared, so its skills are reported under "shared";
        # only containment under the resolved root finds them via the
        # link's own path.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            codex = home / ".codex" / "skills"
            shared.mkdir(parents=True)
            codex.mkdir(parents=True)
            self._write_skill(shared, "move-me", "move-me")
            foo = home / ".foo" / "skills"
            foo.parent.mkdir(parents=True)
            foo.symlink_to(shared)

            src, dest = move_skill("move-me", str(foo), "codex", home=home)
            self.assertFalse(src.exists())
            self.assertTrue((dest / "SKILL.md").exists())

    def test_move_from_a_path_ignores_same_label_roots(self):
        # ~/.foo/skills and ~/.config/foo/skills both carry the label
        # "foo"; an explicit path must still mean exactly one root.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            foo = home / ".foo" / "skills"
            other = home / ".config" / "foo" / "skills"
            codex = home / ".codex" / "skills"
            for root in (foo, other, codex):
                root.mkdir(parents=True)
            self._write_skill(foo, "move-me", "move-me")
            self._write_skill(other, "move-me", "move-me")

            src, dest = move_skill("move-me", str(foo), "codex", home=home)
            self.assertFalse(src.exists())
            self.assertTrue((dest / "SKILL.md").exists())
            # The same-named skill under the other "foo" root is untouched.
            self.assertTrue((other / "move-me" / "SKILL.md").exists())

    def test_move_reports_a_broken_destination_symlink(self):
        # The broken-link check ran after the exists() check, so it could
        # never fire: a dangling --to root read as "does not exist".
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            self._write_skill(shared, "move-me", "move-me")
            codex = home / ".codex" / "skills"
            codex.parent.mkdir(parents=True)
            codex.symlink_to(home / ".codex" / "gone")

            with self.assertRaises(SkillLayoutError) as ctx:
                move_skill("move-me", "shared", "codex", home=home)
            self.assertIn("broken", str(ctx.exception))
            self.assertTrue((shared / "move-me" / "SKILL.md").exists())

    def test_move_reports_a_missing_destination_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            self._write_skill(shared, "move-me", "move-me")

            # kilo is a known label whose root is not on disk.
            with self.assertRaises(SkillLayoutError) as ctx:
                move_skill("move-me", "shared", "kilo", home=home)
            self.assertIn("does not exist", str(ctx.exception))
            self.assertTrue((shared / "move-me" / "SKILL.md").exists())

    def test_find_skill_directory_with_an_unresolvable_scope(self):
        # A scope that resolves to nothing answers "No skill matching",
        # not the resolver's "Unknown skill root" error.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with self.assertRaises(SkillLayoutError) as ctx:
                find_skill_directory("x", "bogus", home=home, cwd=home)
            self.assertIn("No skill matching", str(ctx.exception))

    def test_move_skill_between_unlinked_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            codex = home / ".codex" / "skills"
            shared.mkdir(parents=True)
            codex.mkdir(parents=True)
            self._write_skill(shared, "move-me", "move-me")

            src, dest = move_skill("move-me", "shared", "codex", home=home)
            self.assertFalse(src.exists())
            self.assertTrue((dest / "SKILL.md").exists())

    def test_move_rejects_same_resolved_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            self._write_skill(shared, "x", "x")
            codex = home / ".codex" / "skills"
            codex.parent.mkdir(parents=True)
            codex.symlink_to(shared)

            with self.assertRaises(SkillLayoutError):
                move_skill("x", "shared", "codex", home=home)

    def test_move_rejects_same_resolved_tree_even_with_force(self):
        # Same-tree + force used to proceed, then fail (or worse, once
        # force gained a real overwrite path, back up the folder it was
        # about to move). The check is unconditional now.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            self._write_skill(shared, "x", "x")
            codex = home / ".codex" / "skills"
            codex.parent.mkdir(parents=True)
            codex.symlink_to(shared)

            with self.assertRaises(SkillLayoutError):
                move_skill("x", "shared", "codex", home=home, force=True)
            self.assertTrue((shared / "x" / "SKILL.md").exists())

    def test_move_refuses_existing_destination_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            codex = home / ".codex" / "skills"
            shared.mkdir(parents=True)
            codex.mkdir(parents=True)
            self._write_skill(shared, "move-me", "move-me")
            self._write_skill(codex, "move-me", "move-me")

            with self.assertRaises(SkillLayoutError):
                move_skill("move-me", "shared", "codex", home=home)
            self.assertTrue((shared / "move-me" / "SKILL.md").exists())

    def test_move_force_backs_up_and_replaces_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            codex = home / ".codex" / "skills"
            shared.mkdir(parents=True)
            codex.mkdir(parents=True)
            self._write_skill(shared, "move-me", "move-me")
            dest_dir = codex / "move-me"
            self._write_skill(codex, "move-me", "old-version")
            (dest_dir / "extra.txt").write_text("keep me safe", encoding="utf-8")

            src, dest = move_skill("move-me", "shared", "codex", home=home, force=True)
            self.assertFalse(src.exists())
            self.assertTrue((dest / "SKILL.md").exists())
            # The replaced destination was backed up, not deleted.
            backups = list((home / ".swe" / "backups").glob("**/extra.txt"))
            self.assertEqual(len(backups), 1)

    def test_sync_link_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            links_file = config_dir / "skill_links.json"
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            codex = home / ".codex" / "skills"
            codex.parent.mkdir(parents=True)
            codex.symlink_to(shared)

            with patch("swe.paths.SKILL_LINKS_FILE", links_file), patch(
                "swe.paths.CONFIG_DIR", config_dir
            ), patch("swe.skills.layout.SKILL_LINKS_FILE", links_file), patch(
                "swe.skills.layout.CONFIG_DIR", config_dir
            ):
                synced = sync_link_records_from_filesystem(home=home)
                self.assertIn("codex", synced)
                data = json.loads(links_file.read_text())
                self.assertEqual(data["links"][0]["label"], "codex")

    def test_sync_link_records_resolves_relative_target_from_link_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            links_file = config_dir / "skill_links.json"
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            codex = home / ".codex" / "skills"
            codex.parent.mkdir(parents=True)
            codex.symlink_to(Path("../.swe/skills"))

            with patch("swe.skills.layout.SKILL_LINKS_FILE", links_file), patch(
                "swe.skills.layout.CONFIG_DIR", config_dir
            ):
                sync_link_records_from_filesystem(home=home)

            data = json.loads(links_file.read_text())
            codex_record = next(link for link in data["links"] if link["label"] == "codex")
            self.assertEqual(Path(codex_record["target"]), shared.resolve())

    def test_sync_link_records_removes_stale_harness_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            links_file = config_dir / "skill_links.json"
            config_dir.mkdir(parents=True)
            links_file.write_text(
                json.dumps(
                    {
                        "links": [
                            {
                                "label": "codex",
                                "path": str(home / ".codex" / "skills"),
                                "target": str(home / ".swe" / "skills"),
                                "kind": "symlink",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with patch("swe.skills.layout.SKILL_LINKS_FILE", links_file), patch(
                "swe.skills.layout.CONFIG_DIR", config_dir
            ):
                synced = sync_link_records_from_filesystem(home=home)

            self.assertNotIn("codex", synced)
            self.assertEqual(json.loads(links_file.read_text())["links"], [])

    def test_save_link_records_refuses_a_corrupt_file(self):
        """Reads degrade to [] but a write must not commit that []."""
        from swe.configuration import CorruptConfigurationError
        from swe.skills.layout import load_link_records, save_link_records

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".config" / "swe"
            config_dir.mkdir(parents=True)
            links_file = config_dir / "skill_links.json"
            links_file.write_text("{corrupt", encoding="utf-8")

            with patch("swe.skills.layout.SKILL_LINKS_FILE", links_file), patch(
                "swe.skills.layout.CONFIG_DIR", config_dir
            ):
                self.assertEqual(load_link_records(), [])
                with self.assertRaises(CorruptConfigurationError):
                    save_link_records([{"label": "x"}])

            # Untouched: the corrupt file is still there to fix by hand.
            self.assertEqual(links_file.read_text(), "{corrupt")

    def test_save_link_records_writes_through_a_symlinked_file(self):
        # write_text + rename on a fixed skill_links.tmp replaced a linked
        # file (dotfiles layout) with a plain one.
        from swe.skills.layout import load_link_records, save_link_records

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".config" / "swe"
            config_dir.mkdir(parents=True)
            real = Path(tmp) / "dotfiles" / "skill_links.json"
            real.parent.mkdir()
            real.write_text('{"links": []}', encoding="utf-8")
            links_file = config_dir / "skill_links.json"
            links_file.symlink_to(real)

            with patch("swe.skills.layout.SKILL_LINKS_FILE", links_file), patch(
                "swe.skills.layout.CONFIG_DIR", config_dir
            ):
                save_link_records([{"label": "x"}])
                self.assertEqual(load_link_records(), [{"label": "x"}])

            self.assertTrue(links_file.is_symlink())
            self.assertEqual(json.loads(real.read_text())["links"], [{"label": "x"}])
            self.assertEqual(sorted(p.name for p in config_dir.iterdir()),
                             ["skill_links.json"])

    def test_unlink_preflights_the_records_file_before_unlinking(self):
        """A corrupt skill_links.json used to surface only after the
        symlink was already gone, leaving the root missing on retry."""
        from swe.configuration import CorruptConfigurationError

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            config_dir.mkdir(parents=True)
            links_file = config_dir / "skill_links.json"
            links_file.write_text("{corrupt", encoding="utf-8")
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            codex = home / ".codex" / "skills"
            codex.parent.mkdir(parents=True)
            codex.symlink_to(shared)

            with patch("swe.skills.layout.SKILL_LINKS_FILE", links_file), patch(
                "swe.skills.layout.CONFIG_DIR", config_dir
            ):
                with self.assertRaises(CorruptConfigurationError):
                    unlink_skill_root("codex", home=home)

            # The link still stands: nothing moved, so a fixed file can retry.
            self.assertTrue(codex.is_symlink())

    def test_save_link_records_overwrites_a_foreign_json_value(self):
        """A parseable non-dict file holds no records to lose."""
        from swe.skills.layout import save_link_records

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".config" / "swe"
            config_dir.mkdir(parents=True)
            links_file = config_dir / "skill_links.json"
            links_file.write_text("[1, 2]", encoding="utf-8")

            with patch("swe.skills.layout.SKILL_LINKS_FILE", links_file), patch(
                "swe.skills.layout.CONFIG_DIR", config_dir
            ):
                save_link_records([{"label": "x", "path": "/p",
                                    "target": "/t", "kind": "symlink"}])
            self.assertEqual(
                json.loads(links_file.read_text())["links"][0]["label"], "x")



class SkillsLayoutEdgeTest(unittest.TestCase):
    """Root shapes and refusals the happy-path tests above never reach."""

    # The same per-test records file as SkillsLayoutTest, without
    # inheriting (and so rerunning) its tests.
    setUp = SkillsLayoutTest.setUp
    _write_skill = SkillsLayoutTest._write_skill

    def _home(self) -> Path:
        home = temp_dir(self)
        (home / ".swe" / "skills").mkdir(parents=True)
        return home

    def _loop(self, home: Path, rel: str = ".codex/skills") -> Path:
        """A root that is a symlink to itself."""
        link = home / rel
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(link)
        return link

    def test_a_file_where_a_root_goes_is_kind_file(self):
        home = self._home()
        (home / ".codex").mkdir()
        (home / ".codex" / "skills").write_text("not a folder\n")

        entry = {e.label: e for e in enumerate_skill_roots(home=home, cwd=home)}["codex"]
        self.assertEqual((entry.exists, entry.kind, entry.resolved), (True, "file", None))

    def test_a_root_under_a_linked_parent_names_its_canonical_root(self):
        # ~/.claude links to ~/.swe, so ~/.claude/skills is a plain
        # directory (not a link) that resolves to the shared tree.
        home = self._home()
        (home / ".claude").symlink_to(home / ".swe")

        entries = {e.label: e for e in enumerate_skill_roots(home=home, cwd=home)}
        self.assertEqual(entries["claude"].kind, "directory")
        self.assertEqual(entries["claude"].link_target_label, "shared")
        self.assertIsNone(entries["shared"].link_target_label)

    def test_catalog_roots_are_candidates(self):
        from swe.skills.layout import all_skill_root_candidates, resolve_root_ref

        home = self._home()
        catalogs = [{"label": "team", "path": str(home / "team-skills")},
                    {"path": str(home / "unlabelled")}]
        with patch("swe.skills.layout.load_skill_catalogs", return_value=catalogs):
            candidates = all_skill_root_candidates(home, home)
            self.assertEqual(resolve_root_ref("TEAM", home=home, cwd=home),
                             ("team", home / "team-skills"))
        self.assertIn(("catalog", home / "unlabelled"), candidates)

    def test_layout_groups_join_aliases_and_keep_missing_roots_together(self):
        from swe.skills.layout import layout_groups

        home = self._home()
        (home / ".codex").mkdir()
        (home / ".codex" / "skills").symlink_to(home / ".swe" / "skills")

        groups = {g["resolved"]: g for g in layout_groups(home=home, cwd=home)}
        shared = groups[(home / ".swe" / "skills").resolve()]
        self.assertEqual(shared["canonical"].label, "shared")
        self.assertEqual([m.label for m in shared["members"]], ["shared", "codex"])
        # Every root that is not on disk lands in one unresolved group,
        # headed by its first member since none of them can be canonical.
        missing = groups[None]
        self.assertEqual(missing["canonical"].label, "cursor")
        self.assertNotIn("codex", [m.label for m in missing["members"]])

    def test_link_refuses_a_root_that_already_resolves_to_the_target(self):
        home = self._home()
        (home / ".codex").mkdir()
        (home / ".codex" / "skills").symlink_to(home / ".swe" / "skills")

        with self.assertRaises(SkillLayoutError) as ctx:
            link_skill_root("codex", home=home, cwd=home)
        self.assertIn("already resolves", str(ctx.exception))
        self.assertEqual(load_link_records(), [])

    def test_link_replaces_a_file_where_the_root_goes(self):
        home = self._home()
        (home / ".codex").mkdir()
        (home / ".codex" / "skills").write_text("stray\n")

        link_skill_root("codex", home=home, cwd=home)
        self.assertTrue((home / ".codex" / "skills").is_symlink())

    def test_link_replaces_a_root_that_is_a_symlink_loop(self):
        # resolve() raised RuntimeError on the loop before Python 3.13,
        # which reached the user as a traceback.
        home = self._home()
        link = self._loop(home)

        link_skill_root("codex", home=home, cwd=home)
        self.assertEqual(link.resolve(), (home / ".swe" / "skills").resolve())

    def test_a_path_ref_skips_a_candidate_root_that_is_a_symlink_loop(self):
        from swe.skills.layout import resolve_root_ref

        home = self._home()
        self._loop(home)
        other = home / "elsewhere" / "skills"
        other.mkdir(parents=True)

        self.assertEqual(resolve_root_ref(str(other), home=home, cwd=home)[1], other)

    def test_move_from_a_symlink_loop_is_a_layout_error(self):
        home = self._home()
        self._loop(home)

        for from_scope, to_scope in (("codex", "shared"), ("shared", "codex")):
            with self.assertRaises(SkillLayoutError):
                move_skill("x", from_scope, to_scope, home=home, cwd=home)

    def test_find_in_a_symlink_loop_scope_matches_nothing(self):
        home = self._home()
        self._write_skill(home / ".swe" / "skills", "x", "x")
        self._loop(home)

        with self.assertRaises(SkillLayoutError) as ctx:
            find_skill_directory("x", "codex", home=home, cwd=home)
        self.assertIn("No skill matching", str(ctx.exception))

    def test_a_skill_path_that_is_a_symlink_loop_has_no_scopes(self):
        from swe.skills.layout import shared_scopes_for_skill

        home = self._home()
        loop = self._loop(home, ".swe/skills/loop")
        self.assertEqual(shared_scopes_for_skill(loop, home=home), [])

    def test_unlink_refuses_a_missing_root_and_a_real_directory(self):
        home = self._home()
        with self.assertRaises(SkillLayoutError) as ctx:
            unlink_skill_root("codex", home=home, cwd=home)
        self.assertIn("does not exist", str(ctx.exception))

        (home / ".codex" / "skills").mkdir(parents=True)
        with self.assertRaises(SkillLayoutError) as ctx:
            unlink_skill_root("codex", home=home, cwd=home)
        self.assertIn("real directory", str(ctx.exception))
        self.assertTrue((home / ".codex" / "skills").is_dir())

    def test_find_refuses_an_ambiguous_partial_name(self):
        home = self._home()
        shared = home / ".swe" / "skills"
        self._write_skill(shared, "review-code", "review-code")
        self._write_skill(shared, "review-docs", "review-docs")

        with self.assertRaises(SkillLayoutError) as ctx:
            find_skill_directory("review", "shared", home=home, cwd=home)
        self.assertIn("Multiple skills match", str(ctx.exception))

    def test_move_refuses_a_skill_found_outside_the_source_root(self):
        home = self._home()
        (home / ".codex" / "skills").mkdir(parents=True)
        stray = home / "stray" / "x"
        stray.mkdir(parents=True)

        with patch("swe.skills.link_ops.find_skill_directory", return_value=stray):
            with self.assertRaises(SkillLayoutError) as ctx:
                move_skill("x", "shared", "codex", home=home, cwd=home, force=True)
        self.assertIn("is not under", str(ctx.exception))
        self.assertTrue(stray.is_dir())

    def test_move_force_replaces_a_file_and_an_empty_folder_without_backup(self):
        home = self._home()
        shared = home / ".swe" / "skills"
        codex = home / ".codex" / "skills"
        codex.mkdir(parents=True)
        self._write_skill(shared, "one", "one")
        self._write_skill(shared, "two", "two")
        (codex / "one").write_text("a stray file\n")
        (codex / "two").mkdir()

        for name in ("one", "two"):
            _src, dest = move_skill(name, "shared", "codex", home=home, cwd=home, force=True)
            self.assertTrue((dest / "SKILL.md").is_file())
        # Nothing at either destination held data, so nothing was backed up.
        self.assertFalse((home / ".swe" / "backups").exists())


if __name__ == "__main__":
    unittest.main()
