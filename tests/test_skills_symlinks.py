import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.skills.symlinks import (
    SkillsSymlinkHint,
    apply_skills_symlink_hints,
    skills_symlink_hints,
)


class SkillsSymlinksTest(unittest.TestCase):
    def setUp(self):
        # No harness binary resolves unless a test says so: one on the
        # developer's PATH is install evidence and would add hints.
        self.on_path: set[str] = set()
        which = patch("swe.init.layout.shutil.which",
                      side_effect=lambda cmd: f"/bin/{cmd}" if cmd in self.on_path else None)
        which.start()
        self.addCleanup(which.stop)

    def test_suggests_symlink_when_cursor_skills_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            (shared / "demo-skill").mkdir()
            (shared / "demo-skill" / "SKILL.md").write_text("---\nname: demo\n---\n")
            (home / ".cursor").mkdir()

            hints = skills_symlink_hints(home=home)
            cursor = [h for h in hints if h.label == "cursor"]
            self.assertEqual(len(cursor), 1)
            self.assertEqual(cursor[0].action, "symlink")
            self.assertIn("ln -sf", cursor[0].command)

    def test_apply_creates_symlink_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            (home / ".cursor").mkdir()

            hints = skills_symlink_hints(home=home)
            applied = apply_skills_symlink_hints(hints, home=home)
            self.assertTrue(any("cursor" in item for item in applied))
            cursor_link = home / ".cursor" / "skills"
            self.assertTrue(cursor_link.is_symlink())
            self.assertEqual(cursor_link.resolve(), shared.resolve())

    def test_apply_replaces_a_dangling_skills_link(self):
        # A root still pointing at the removed ~/.agents/skills: exists()
        # is False, so it is hinted, and applying used to raise
        # FileExistsError because the dead link still held the path.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            (home / ".cursor").mkdir()
            link = home / ".cursor" / "skills"
            link.symlink_to(home / ".agents" / "skills")

            hints = skills_symlink_hints(home=home)
            applied = apply_skills_symlink_hints(hints, home=home)
            self.assertIn("symlink cursor", applied)
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), shared.resolve())

    def test_no_hint_for_a_harness_whose_home_is_missing(self):
        # No ~/.augment means augment is not installed: offering a link
        # there would make the dir, and init then reads it as evidence.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".swe" / "skills").mkdir(parents=True)
            (home / ".claude").mkdir()

            labels = {h.label for h in skills_symlink_hints(home=home)}
            self.assertEqual(labels, {"claude"})

    def test_apply_never_creates_a_harness_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".swe" / "skills").mkdir(parents=True)
            stale = SkillsSymlinkHint(
                label="augment", path=home / ".augment" / "skills",
                action="symlink", command="", reason="")

            self.assertEqual(apply_skills_symlink_hints([stale], home=home), [])
            self.assertFalse((home / ".augment").exists())

    def test_a_binary_on_path_without_its_home_folder_is_linked(self):
        # Installed but never run: no ~/.codex yet, so the binary is the
        # only evidence, as in swe init. The wizard never runs init, so
        # this link is the only one that harness gets there.
        self.on_path = {"codex"}
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)

            hints = skills_symlink_hints(home=home)
            self.assertEqual([h.label for h in hints], ["codex"])
            self.assertEqual(apply_skills_symlink_hints(hints, home=home), ["symlink codex"])
            self.assertEqual((home / ".codex" / "skills").resolve(), shared.resolve())
            self.assertEqual(sorted(p.name for p in home.iterdir()), [".codex", ".swe"])

    def test_an_archived_harness_on_path_is_not_linked(self):
        self.on_path = {"codex"}
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".swe" / "skills").mkdir(parents=True)
            config = home / ".swe" / "config"
            config.mkdir()
            (config / "harness.json").write_text(
                json.dumps({"codex": {"command": "codex", "state": "archived"}}))

            self.assertEqual(skills_symlink_hints(home=home), [])
            stale = SkillsSymlinkHint(label="codex", path=home / ".codex" / "skills",
                                      action="symlink", command="", reason="")
            self.assertEqual(apply_skills_symlink_hints([stale], home=home), [])
            self.assertFalse((home / ".codex").exists())

    def test_an_unreadable_registry_links_nothing_by_path(self):
        # The archive can't be read, so an archived harness can't be told
        # apart: only roots whose folder exists are linked.
        self.on_path = {"codex"}
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".swe" / "skills").mkdir(parents=True)
            (home / ".swe" / "config").mkdir()
            (home / ".swe" / "config" / "harness.json").write_text("{not json")
            (home / ".claude").mkdir()

            self.assertEqual([h.label for h in skills_symlink_hints(home=home)], ["claude"])

    def test_a_file_where_the_harness_folder_goes_is_left_alone(self):
        # A blocked folder must not stop the other links in the same run.
        self.on_path = {"codex", "cursor", "crush"}
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            (home / ".codex").write_text("not a folder\n")
            (home / ".cursor").symlink_to(home / "gone")
            (home / ".config").write_text("not a folder either\n")
            (home / ".claude").mkdir()

            hints = skills_symlink_hints(home=home)
            self.assertEqual([h.label for h in hints], ["claude", "crush"])
            self.assertEqual(apply_skills_symlink_hints(hints, home=home), ["symlink claude"])
            self.assertEqual((home / ".codex").read_text(), "not a folder\n")
            self.assertTrue((home / ".cursor").is_symlink())
            self.assertEqual((home / ".claude" / "skills").resolve(), shared.resolve())


    def test_a_missing_shared_tree_is_the_only_hint_and_apply_creates_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()

            hints = skills_symlink_hints(home=home)
            self.assertEqual([(h.label, h.action) for h in hints], [("shared", "create_shared")])
            self.assertEqual(apply_skills_symlink_hints(hints, home=home), ["mkdir shared"])
            self.assertTrue((home / ".swe" / "skills").is_dir())
            self.assertFalse((home / ".claude" / "skills").exists())

    def test_existing_roots_are_classified_ok_symlink_or_manual(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            (home / ".codex").mkdir()
            (home / ".codex" / "skills").symlink_to(shared)        # linked
            (home / "old").mkdir()
            (home / ".cursor").mkdir()
            (home / ".cursor" / "skills").symlink_to(home / "old")  # linked elsewhere
            (home / ".forge" / "skills").mkdir(parents=True)        # its own tree
            # ~/.cline is a link to ~/.swe, so ~/.cline/skills is a real
            # directory path that still resolves to the shared tree.
            (home / ".cline").symlink_to(home / ".swe")

            actions = {h.label: h.action for h in skills_symlink_hints(home=home)}
            self.assertEqual(actions, {"codex": "ok", "cursor": "symlink",
                                       "forge": "manual", "cline": "ok"})

            # Apply only fills gaps: the link that points elsewhere and the
            # separate tree are left for the user.
            hints = skills_symlink_hints(home=home)
            self.assertEqual(apply_skills_symlink_hints(hints, home=home), [])
            self.assertEqual((home / ".cursor" / "skills").resolve(), (home / "old").resolve())

    def test_an_unresolvable_shared_tree_gives_no_hints(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".swe" / "skills").mkdir(parents=True)
            (home / ".claude").mkdir()
            with patch("swe.skills.symlinks.Path.resolve", side_effect=PermissionError("denied")):
                self.assertEqual(skills_symlink_hints(home=home), [])

    def test_a_root_that_stops_resolving_is_skipped(self):
        # Both roots exist, then resolve() fails on each: the hint is
        # dropped instead of raising, whichever kind of root it is.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            (home / ".codex").mkdir()
            (home / ".codex" / "skills").symlink_to(shared)
            (home / ".forge" / "skills").mkdir(parents=True)
            real_resolve = Path.resolve

            def resolve(path, *args, **kwargs):
                if path.parent.name in (".codex", ".forge"):
                    raise PermissionError("denied")
                return real_resolve(path, *args, **kwargs)

            with patch("swe.skills.symlinks.Path.resolve", resolve):
                self.assertEqual(skills_symlink_hints(home=home), [])


if __name__ == "__main__":
    unittest.main()
