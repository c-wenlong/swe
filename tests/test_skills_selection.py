"""Per-skill selections: ``swe skills enable|disable|sync``, the
selection kind in skill_links.json, and how init and inventory read a
root that holds a link per shared skill."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from swe.init.layout import classify_skill_root
from swe.skills.layout import (
    load_link_records,
    selection_record_at,
)
from swe.skills.layout_commands import (
    cmd_skills_disable,
    cmd_skills_enable,
    cmd_skills_sync,
)
from swe.skills.link_ops import (
    SkillLayoutError,
    link_skill_root,
    move_skill,
    reconcile_all_selections,
    set_skill_enabled,
    unlink_skill_root,
)


def _run(fn, args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = fn(list(args))
    return code, buf.getvalue()


class SelectionTest(unittest.TestCase):
    """Every test gets a fresh skill_links.json, like SkillsLayoutTest."""

    def setUp(self):
        self.records = Path(
            self.enterContext(tempfile.TemporaryDirectory())
        ) / "skill_links.json"
        for target, value in (("swe.skills.layout.SKILL_LINKS_FILE", self.records),
                              ("swe.skills.layout.CONFIG_DIR", self.records.parent)):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _home(self) -> Path:
        home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (home / ".swe" / "skills").mkdir(parents=True)
        return home

    def _write_skill(self, root: Path, folder: str, name: str | None = None):
        d = root / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name or folder}\ndescription: test\n---\n",
            encoding="utf-8")

    def _linked(self, home: Path, label: str = "codex") -> Path:
        root = home / f".{label}" / "skills"
        root.parent.mkdir(parents=True, exist_ok=True)
        root.symlink_to(home / ".swe" / "skills")
        return root

    # -- enable/disable -------------------------------------------------

    def test_disable_in_a_linked_root_converts_to_except(self):
        home = self._home()
        shared = home / ".swe" / "skills"
        self._write_skill(shared, "foo")
        self._write_skill(shared, "bar")
        root = self._linked(home)

        out = set_skill_enabled("foo", "codex", enabled=False, home=home, cwd=home)

        self.assertEqual(out["mode"], "except")
        self.assertTrue(out["converted"])
        self.assertTrue(root.is_dir() and not root.is_symlink())
        self.assertFalse((root / "foo").exists())
        self.assertEqual((root / "bar").resolve(), (shared / "bar").resolve())
        record = selection_record_at(root)
        self.assertEqual((record["mode"], record["disabled"]), ("except", ["foo"]))

    def test_enable_in_a_linked_root_is_a_noop(self):
        home = self._home()
        self._write_skill(home / ".swe" / "skills", "foo")
        root = self._linked(home)

        out = set_skill_enabled("foo", "codex", enabled=True, home=home, cwd=home)

        self.assertEqual(out["mode"], "all")
        self.assertFalse(out["changed"])
        self.assertTrue(root.is_symlink())
        self.assertEqual(load_link_records(), [])

    def test_enable_in_a_missing_root_creates_an_only_selection(self):
        home = self._home()
        shared = home / ".swe" / "skills"
        self._write_skill(shared, "foo")
        self._write_skill(shared, "bar")
        (home / ".claude").mkdir()

        out = set_skill_enabled("foo", "claude", enabled=True, home=home, cwd=home)
        root = home / ".claude" / "skills"

        self.assertEqual(out["mode"], "only")
        self.assertTrue(out["converted"])
        self.assertTrue((root / "foo").is_symlink())
        self.assertFalse((root / "bar").exists())
        record = selection_record_at(root)
        self.assertEqual((record["mode"], record["enabled"]), ("only", ["foo"]))

    def test_disable_in_a_missing_root_has_nothing_to_switch(self):
        home = self._home()
        self._write_skill(home / ".swe" / "skills", "foo")
        (home / ".claude").mkdir()

        with self.assertRaises(SkillLayoutError) as ctx:
            set_skill_enabled("foo", "claude", enabled=False, home=home, cwd=home)
        self.assertIn("does not exist", str(ctx.exception))

    def test_enable_in_an_uninstalled_harness_reports_skipped(self):
        home = self._home()
        self._write_skill(home / ".swe" / "skills", "foo")

        with self.assertRaises(SkillLayoutError) as ctx:
            set_skill_enabled("foo", "claude", enabled=True, home=home, cwd=home)
        self.assertIn("not installed", str(ctx.exception))

    def test_toggle_round_trip_inside_an_except_selection(self):
        home = self._home()
        shared = home / ".swe" / "skills"
        self._write_skill(shared, "foo")
        self._write_skill(shared, "bar")
        root = self._linked(home)
        set_skill_enabled("foo", "codex", enabled=False, home=home, cwd=home)

        out = set_skill_enabled("foo", "codex", enabled=True, home=home, cwd=home)

        self.assertEqual(out["mode"], "except")
        self.assertTrue(out["changed"])
        self.assertEqual((root / "foo").resolve(), (shared / "foo").resolve())
        self.assertEqual(selection_record_at(root)["disabled"], [])

    def test_a_private_folder_shadowing_the_name_is_hands_off(self):
        home = self._home()
        self._write_skill(home / ".swe" / "skills", "foo")
        root = self._linked(home)
        set_skill_enabled("foo", "codex", enabled=False, home=home, cwd=home)
        self._write_skill(root, "foo", name="private-foo")

        with self.assertRaises(SkillLayoutError) as ctx:
            set_skill_enabled("foo", "codex", enabled=True, home=home, cwd=home)
        self.assertIn("shadows", str(ctx.exception))
        # The private copy was not touched.
        self.assertIn("private-foo", (root / "foo" / "SKILL.md").read_text())

    def test_a_private_link_pointing_outside_shared_is_hands_off(self):
        home = self._home()
        self._write_skill(home / ".swe" / "skills", "foo")
        root = self._linked(home)
        set_skill_enabled("foo", "codex", enabled=False, home=home, cwd=home)
        (root / "foo").symlink_to(home / "elsewhere" / "foo")

        with self.assertRaises(SkillLayoutError) as ctx:
            set_skill_enabled("foo", "codex", enabled=True, home=home, cwd=home)
        self.assertIn("private link", str(ctx.exception))

    def test_the_shared_root_itself_cannot_be_toggled(self):
        home = self._home()
        self._write_skill(home / ".swe" / "skills", "foo")

        with self.assertRaises(SkillLayoutError) as ctx:
            set_skill_enabled("foo", "shared", enabled=False, home=home, cwd=home)
        self.assertIn("IS the shared tree", str(ctx.exception))

    def test_a_skill_not_in_shared_cannot_be_toggled(self):
        home = self._home()
        root = self._linked(home)

        with self.assertRaises(SkillLayoutError):
            set_skill_enabled("ghost", "codex", enabled=False, home=home, cwd=home)
        self.assertTrue(root.is_symlink())

    def test_disable_on_a_selection_drops_just_that_link(self):
        home = self._home()
        shared = home / ".swe" / "skills"
        self._write_skill(shared, "foo")
        self._write_skill(shared, "bar")
        self._write_skill(shared, "baz")
        root = self._linked(home)
        set_skill_enabled("foo", "codex", enabled=False, home=home, cwd=home)

        set_skill_enabled("bar", "codex", enabled=False, home=home, cwd=home)

        self.assertFalse((root / "bar").exists())
        self.assertTrue((root / "baz").is_symlink())
        self.assertEqual(
            selection_record_at(root)["disabled"], ["bar", "foo"])

    # -- records ---------------------------------------------------------

    def test_two_roots_with_the_same_label_keep_separate_records(self):
        # ~/.foo/skills and ~/.config/foo/skills both label "foo": the
        # second root's record must not erase the first's.
        home = self._home()
        shared = home / ".swe" / "skills"
        self._write_skill(shared, "foo")
        self._write_skill(shared, "bar")
        one = home / ".foo" / "skills"
        two = home / ".config" / "foo" / "skills"
        for root in (one, two):
            root.parent.mkdir(parents=True)
            root.symlink_to(shared)
            set_skill_enabled("foo", str(root), enabled=False, home=home, cwd=home)

        self.assertIsNotNone(selection_record_at(one))
        self.assertIsNotNone(selection_record_at(two))

    def test_record_selection_ignores_a_malformed_names_list(self):
        from swe.skills.layout import record_selection, selection_intent

        home = self._home()
        root = home / ".codex" / "skills"
        root.mkdir(parents=True)
        record_selection("codex", root, "except", "not-a-list")
        mode, intent = selection_intent(selection_record_at(root))
        # Unreadable intent settles on the safe side: except nothing.
        self.assertEqual((mode, intent), ("except", set()))

    # -- link/unlink over selections -------------------------------------

    def test_link_flattens_a_pure_selection_without_force(self):
        home = self._home()
        shared = home / ".swe" / "skills"
        self._write_skill(shared, "foo")
        root = self._linked(home)
        set_skill_enabled("foo", "codex", enabled=False, home=home, cwd=home)

        link_skill_root("codex", home=home, cwd=home)

        self.assertTrue(root.is_symlink())
        self.assertEqual(root.resolve(), shared.resolve())
        # The selection record went too: it would wrongly exempt foo from
        # a root that is now just a link.
        self.assertIsNone(selection_record_at(root))

    def test_link_a_selection_with_private_folders_still_needs_force(self):
        home = self._home()
        self._write_skill(home / ".swe" / "skills", "foo")
        root = self._linked(home)
        set_skill_enabled("foo", "codex", enabled=False, home=home, cwd=home)
        self._write_skill(root, "own")

        with self.assertRaises(SkillLayoutError) as ctx:
            link_skill_root("codex", home=home, cwd=home)
        self.assertIn("--force", str(ctx.exception))
        self.assertTrue((root / "own" / "SKILL.md").exists())

    def test_unlink_a_selection_drops_links_and_keeps_privates(self):
        home = self._home()
        shared = home / ".swe" / "skills"
        self._write_skill(shared, "foo")
        self._write_skill(shared, "bar")
        root = self._linked(home)
        set_skill_enabled("foo", "codex", enabled=False, home=home, cwd=home)
        self._write_skill(root, "own")

        unlink_skill_root("codex", home=home)

        self.assertFalse((root / "bar").exists())
        self.assertTrue((root / "own" / "SKILL.md").exists())
        self.assertIsNone(selection_record_at(root))
        self.assertEqual(load_link_records(), [])

    # -- reconcile ---------------------------------------------------------

    def test_new_shared_skill_links_into_except_not_only(self):
        home = self._home()
        shared = home / ".swe" / "skills"
        self._write_skill(shared, "foo")
        except_root = self._linked(home, "codex")
        only_root = home / ".claude" / "skills"
        (home / ".claude").mkdir()
        set_skill_enabled("foo", "codex", enabled=False, home=home, cwd=home)
        set_skill_enabled("foo", "claude", enabled=True, home=home, cwd=home)

        self._write_skill(shared, "new-skill")
        rows = reconcile_all_selections(home)

        self.assertTrue((except_root / "new-skill").is_symlink())
        self.assertFalse((only_root / "new-skill").exists())
        self.assertEqual([r["label"] for r in rows], ["codex"])

    def test_a_deleted_shared_skill_drops_its_link(self):
        home = self._home()
        shared = home / ".swe" / "skills"
        self._write_skill(shared, "foo")
        root = self._linked(home)
        set_skill_enabled("foo", "codex", enabled=False, home=home, cwd=home)
        set_skill_enabled("foo", "codex", enabled=True, home=home, cwd=home)
        import shutil
        shutil.rmtree(shared / "foo")

        rows = reconcile_all_selections(home)

        self.assertFalse((root / "foo").exists())
        self.assertEqual(rows[0]["removed"], ["foo"])

    def test_a_link_removed_by_hand_comes_back(self):
        home = self._home()
        self._write_skill(home / ".swe" / "skills", "foo")
        root = self._linked(home)
        set_skill_enabled("foo", "codex", enabled=False, home=home, cwd=home)
        (root / "foo").symlink_to(home / ".swe" / "skills" / "foo")
        # hand edit: drop the link the except-record wants
        set_skill_enabled("foo", "codex", enabled=True, home=home, cwd=home)
        (root / "foo").unlink()

        reconcile_all_selections(home)

        self.assertTrue((root / "foo").is_symlink())

    def test_a_selection_record_on_a_now_symlinked_root_is_skipped(self):
        home = self._home()
        self._write_skill(home / ".swe" / "skills", "foo")
        root = self._linked(home)
        set_skill_enabled("foo", "codex", enabled=False, home=home, cwd=home)
        root.rmdir()
        root.symlink_to(home / ".swe" / "skills")

        self.assertEqual(reconcile_all_selections(home), [])
        self.assertTrue(root.is_symlink())

    def test_move_to_shared_reaches_except_roots(self):
        home = self._home()
        shared = home / ".swe" / "skills"
        self._write_skill(shared, "foo")
        except_root = self._linked(home, "codex")
        only_root = home / ".claude" / "skills"
        (home / ".claude").mkdir()
        set_skill_enabled("foo", "codex", enabled=False, home=home, cwd=home)
        set_skill_enabled("foo", "claude", enabled=True, home=home, cwd=home)
        # claude writes a private skill, then promotes it to shared.
        self._write_skill(only_root, "promoted")

        move_skill("promoted", "claude", "shared", home=home, cwd=home)

        self.assertTrue((except_root / "promoted").is_symlink())
        # The root that wrote it keeps loading it: promotion claims the
        # name into the only record, then the reconcile links it back.
        self.assertTrue((only_root / "promoted").is_symlink())
        self.assertIn(
            "promoted", selection_record_at(only_root)["enabled"])

    def test_move_to_shared_keeps_a_promoted_skill_off_an_unrelated_only_root(self):
        # The claiming above is for the root the skill came from; another
        # harness's only selection still holds a promoted skill out.
        home = self._home()
        shared = home / ".swe" / "skills"
        self._write_skill(shared, "foo")
        self._linked(home, "codex")
        writer = home / ".claude" / "skills"
        watcher = home / ".cursor" / "skills"
        (home / ".claude").mkdir()
        (home / ".cursor").mkdir()
        set_skill_enabled("foo", "claude", enabled=True, home=home, cwd=home)
        set_skill_enabled("foo", "cursor", enabled=True, home=home, cwd=home)
        self._write_skill(writer, "promoted")

        move_skill("promoted", "claude", "shared", home=home, cwd=home)

        self.assertTrue((writer / "promoted").is_symlink())
        self.assertFalse((watcher / "promoted").exists())

    # -- classify -----------------------------------------------------------

    def test_classify_reads_a_link_dir_as_selection_not_absorb(self):
        home = self._home()
        shared = home / ".swe" / "skills"
        self._write_skill(shared, "foo")
        self._write_skill(shared, "bar")
        root = self._linked(home)
        set_skill_enabled("foo", "codex", enabled=False, home=home, cwd=home)

        state, detail = classify_skill_root(root, home)

        self.assertEqual(state, "selection")
        self.assertIn("foo", detail)

    def test_classify_a_recorded_selection_with_no_links_still_selection(self):
        # "except every shared skill" leaves an empty dir; absorb would
        # replace it with a link and put every excluded skill back.
        home = self._home()
        self._write_skill(home / ".swe" / "skills", "foo")
        root = self._linked(home)
        set_skill_enabled("foo", "codex", enabled=False, home=home, cwd=home)
        # The dir now holds the record and no links at all (foo, the only
        # shared skill, is the one switched off).

        state, _ = classify_skill_root(root, home)
        self.assertEqual(state, "selection")

    def test_init_apply_repairs_not_flattens(self):
        home = self._home()
        shared = home / ".swe" / "skills"
        self._write_skill(shared, "foo")
        self._write_skill(shared, "bar")
        root = self._linked(home)
        set_skill_enabled("foo", "codex", enabled=False, home=home, cwd=home)

        from swe.init.commands import _apply
        from swe.init.layout import LinkStatus

        status = LinkStatus("codex", root, "selection", "")
        result = _apply(status, shared, home, force=False)

        self.assertEqual(result, "selection")
        self.assertTrue(root.is_dir() and not root.is_symlink())
        self.assertFalse((root / "foo").exists())
        self.assertTrue((root / "bar").is_symlink())

    # -- inventory -----------------------------------------------------------

    def test_inventory_reports_mode_and_per_root_states(self):
        home = self._home()
        shared = home / ".swe" / "skills"
        self._write_skill(shared, "foo")
        self._write_skill(shared, "bar")
        self._linked(home)
        set_skill_enabled("foo", "codex", enabled=False, home=home, cwd=home)

        from swe.skills.inventory import installed_skills
        doc = installed_skills(home=home, cwd=home)

        rows = {h["name"]: h for h in doc["harnesses"]}
        self.assertEqual(rows["codex"]["mode"], "except")
        self.assertEqual(rows["codex"]["disabled"], ["foo"])
        self.assertEqual(rows["codex"]["enabled"], ["bar"])
        skills = {s["name"]: s for s in doc["skills"]}
        self.assertNotIn("codex", skills["foo"]["harnesses"])
        self.assertIn("codex", skills["bar"]["harnesses"])

    def test_inventory_an_unrecorded_link_dir_is_mode_only(self):
        home = self._home()
        shared = home / ".swe" / "skills"
        self._write_skill(shared, "foo")
        root = home / ".codex" / "skills"
        root.mkdir(parents=True)
        (root / "foo").symlink_to(shared / "foo")

        from swe.skills.inventory import installed_skills
        doc = installed_skills(home=home, cwd=home)

        codex = next(h for h in doc["harnesses"] if h["name"] == "codex")
        self.assertEqual(codex["mode"], "only")
        self.assertEqual(codex["enabled"], ["foo"])

    def test_inventory_a_private_shadow_is_not_seen_as_the_shared_skill(self):
        home = self._home()
        shared = home / ".swe" / "skills"
        self._write_skill(shared, "foo")
        root = self._linked(home)
        set_skill_enabled("foo", "codex", enabled=False, home=home, cwd=home)
        self._write_skill(root, "foo", name="private-foo")

        from swe.skills.inventory import installed_skills
        doc = installed_skills(home=home, cwd=home)

        shared_foo = next(s for s in doc["skills"] if s["root"] == "shared")
        self.assertNotIn("codex", shared_foo["harnesses"])


class SkillsToggleCommandTest(unittest.TestCase):
    def setUp(self):
        self.records = Path(
            self.enterContext(tempfile.TemporaryDirectory())
        ) / "skill_links.json"
        for target, value in (("swe.skills.layout.SKILL_LINKS_FILE", self.records),
                              ("swe.skills.layout.CONFIG_DIR", self.records.parent)):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_enable_help(self):
        code, out = _run(cmd_skills_enable, ["--help"])
        self.assertEqual(code, 0)
        self.assertIn("except", out)

    def test_disable_usage_error_needs_name_and_harness(self):
        code, out = _run(cmd_skills_disable, ["foo"])
        self.assertEqual(code, 1)
        self.assertIn("Usage: swe skills disable", out)

    def test_unknown_flag_is_rejected(self):
        code, out = _run(cmd_skills_enable, ["foo", "--harness=codex", "--bogus"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown flag", out)

    def test_layout_error_is_reported(self):
        with patch(
            "swe.skills.layout_commands.set_skill_enabled",
            side_effect=SkillLayoutError("no such skill"),
        ):
            code, out = _run(
                cmd_skills_disable, ["foo", "--harness=codex"])
        self.assertEqual(code, 1)
        self.assertIn("no such skill", out)

    def test_json_output(self):
        result = {"action": "disable", "harness": "codex", "skill": "foo",
                  "mode": "except", "enabled": False, "changed": True,
                  "converted": True}
        with patch(
            "swe.skills.layout_commands.set_skill_enabled", return_value=result
        ) as toggle:
            code, out = _run(
                cmd_skills_disable, ["foo", "--harness=codex", "--json"])
        self.assertEqual(code, 0)
        toggle.assert_called_once_with("foo", "codex", enabled=False)
        data = json.loads(out)["data"]
        self.assertEqual((data["mode"], data["converted"]), ("except", True))

    def test_sync_json_output(self):
        rows = [{"label": "codex", "path": "/p",
                 "added": ["foo"], "removed": []}]
        with patch(
            "swe.skills.layout_commands.reconcile_all_selections",
            return_value=rows,
        ):
            code, out = _run(cmd_skills_sync, ["--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)["data"]
        self.assertEqual(data["action"], "sync")
        self.assertEqual(data["roots"][0]["added"], ["foo"])

    def test_sync_nothing_to_do(self):
        with patch(
            "swe.skills.layout_commands.reconcile_all_selections",
            return_value=[],
        ):
            code, out = _run(cmd_skills_sync, [])
        self.assertEqual(code, 0)
        self.assertIn("already matches", out)


if __name__ == "__main__":
    unittest.main()
