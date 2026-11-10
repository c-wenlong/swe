"""Harness-only skill adoption: ``swe skills adopt``, the pass init runs at
the end of a real run, and ``swe skills remove``'s propagation into the
roots adoption linked back through."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from unittest.mock import patch

from swe.init.layout import classify_skill_root
from swe.skills.adopt import adopt_harness_skills
from swe.skills.layout import (
    load_link_records,
    selection_intent,
    selection_record_at,
    skill_adopted_map,
)
from swe.skills.layout_commands import cmd_skills_adopt, cmd_skills_remove
from swe.skills.link_ops import SkillLayoutError, remove_skill

from tests._tempdir import temp_dir


def _run(fn, args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = fn(list(args))
    return code, buf.getvalue()


class AdoptCase(unittest.TestCase):
    """Every test gets a fresh skill_links.json, like SkillsLayoutTest."""

    def setUp(self):
        self.records = temp_dir(self) / "skill_links.json"
        for target, value in (("swe.skills.layout.SKILL_LINKS_FILE", self.records),
                              ("swe.skills.layout.CONFIG_DIR", self.records.parent)):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.home = temp_dir(self)
        self.shared = self.home / ".swe" / "skills"
        self.shared.mkdir(parents=True)

    def _skill(self, root: Path, folder: str, *, body: str = "",
               name: str | None = None) -> Path:
        d = root / folder
        d.mkdir(parents=True, exist_ok=True)
        text = f"---\nname: {name or folder}\ndescription: test\n---\n{body}"
        (d / "SKILL.md").write_text(text, encoding="utf-8")
        return d

    def _linked(self, label: str = "codex") -> Path:
        root = self.home / f".{label}" / "skills"
        root.parent.mkdir(parents=True, exist_ok=True)
        root.symlink_to(self.shared)
        return root

    def _statuses(self):
        from swe.init.layout import plan

        _inst, skills = plan(self.home)
        return skills


class AdoptMoveTest(AdoptCase):
    def test_a_private_skill_moves_into_shared_with_a_link_back(self):
        donor = self._skill(self.home / ".claude/skills", "priv")
        self._linked()

        result = adopt_harness_skills(self.home)

        self.assertEqual(result.adopted, [{"name": "priv", "from": "claude"}])
        moved = self.shared / "priv"
        self.assertTrue(moved.is_dir() and not moved.is_symlink())
        self.assertTrue(donor.is_symlink())
        self.assertEqual(donor.resolve(), moved.resolve())
        self.assertEqual(skill_adopted_map(), {"priv": "claude"})
        # The donor root is now a dir of per-skill links: a selection.
        self.assertEqual(
            classify_skill_root(self.home / ".claude/skills", self.home)[0],
            "selection")

    def test_a_linked_root_is_held_off_the_adopted_name(self):
        self._skill(self.home / ".claude/skills", "priv")
        self._skill(self.shared, "shared-one")
        other = self._linked()

        result = adopt_harness_skills(self.home)

        # The whole-root link becomes an except selection: every shared
        # skill but the adopted one stays linked.
        self.assertTrue(other.is_dir() and not other.is_symlink())
        self.assertFalse((other / "priv").exists())
        self.assertEqual((other / "shared-one").resolve(),
                         (self.shared / "shared-one").resolve())
        record = selection_record_at(other)
        self.assertEqual((record["mode"], record["disabled"]),
                         ("except", ["priv"]))
        self.assertEqual(result.held_off,
                         [{"harness": "codex", "skills": ["priv"]}])

    def test_an_except_selection_takes_the_new_name_onto_disabled(self):
        self._skill(self.home / ".claude/skills", "priv")
        self._skill(self.shared, "one")
        self._skill(self.shared, "two")
        # codex is an except selection holding "two" off already.
        root = self.home / ".codex/skills"
        root.mkdir(parents=True)
        (root / "one").symlink_to(self.shared / "one")
        from swe.skills.layout import record_selection

        record_selection("codex", root, "except", ["two"])

        result = adopt_harness_skills(self.home)

        record = selection_record_at(root)
        self.assertEqual(record["mode"], "except")
        self.assertEqual(record["disabled"], ["priv", "two"])
        self.assertFalse((root / "priv").exists())
        self.assertEqual(result.held_off,
                         [{"harness": "codex", "skills": ["priv"]}])

    def test_an_only_selection_drops_a_handmade_link_for_the_name(self):
        self._skill(self.home / ".claude/skills", "priv")
        # A link for a name that did not exist in shared yet.
        root = self.home / ".codex/skills"
        root.mkdir(parents=True)
        (root / "priv").symlink_to(self.shared / "priv")
        from swe.skills.layout import record_selection

        record_selection("codex", root, "only", ["priv"])

        adopt_harness_skills(self.home)

        record = selection_record_at(root)
        self.assertEqual(record["enabled"], [])
        self.assertFalse((root / "priv").is_symlink())

    def test_skipped_and_ignored_roots_are_never_donors(self):
        self._skill(self.home / ".claude/skills", "priv")
        statuses = self._statuses()
        for s in statuses:
            s.state = "skipped"

        result = adopt_harness_skills(self.home, skills_statuses=statuses)

        self.assertEqual(result.adopted, [])
        self.assertTrue((self.home / ".claude/skills/priv").is_dir())

    def test_the_second_pass_is_a_noop(self):
        self._skill(self.home / ".claude/skills", "priv")
        adopt_harness_skills(self.home)
        before = load_link_records()

        result = adopt_harness_skills(self.home)

        self.assertEqual(result.adopted, [])
        self.assertEqual(load_link_records(), before)

    def test_a_nested_skill_moves_and_links_back_at_its_own_path(self):
        root = self.home / ".claude/skills"
        self._skill(root / "cat", "priv")

        result = adopt_harness_skills(self.home)

        self.assertEqual(result.adopted, [{"name": "priv", "from": "claude"}])
        self.assertTrue((self.shared / "priv").is_dir())
        link = root / "cat" / "priv"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), (self.shared / "priv").resolve())

    def test_a_skill_inside_another_skill_moves_with_its_parent(self):
        root = self.home / ".claude/skills"
        outer = self._skill(root, "outer")
        self._skill(outer, "inner")

        result = adopt_harness_skills(self.home)

        self.assertEqual(result.adopted, [{"name": "outer", "from": "claude"}])
        self.assertTrue((self.shared / "outer/inner/SKILL.md").is_file())
        self.assertFalse((self.shared / "inner").exists())

    def test_harness_owned_dirs_are_not_adopted(self):
        root = self.home / ".codex/skills"
        owned = root / ".system"
        owned.mkdir(parents=True)
        (owned / ".codex-system-skills.marker").write_text("x")
        (owned / "SKILL.md").write_text("---\nname: sys\n---\n")
        self._skill(root, "priv")

        result = adopt_harness_skills(self.home)

        self.assertEqual(result.adopted, [{"name": "priv", "from": "codex"}])
        self.assertTrue((owned / ".codex-system-skills.marker").is_file())
        self.assertFalse((self.shared / ".system").exists())

    def test_only_restricts_the_pass_to_one_skill(self):
        self._skill(self.home / ".claude/skills", "one")
        self._skill(self.home / ".claude/skills", "two")

        result = adopt_harness_skills(self.home, only="two")

        self.assertEqual(result.adopted, [{"name": "two", "from": "claude"}])
        self.assertTrue((self.home / ".claude/skills/one").is_dir())

    def test_only_matching_no_candidate_is_an_error(self):
        self._skill(self.home / ".claude/skills", "one")

        with self.assertRaises(SkillLayoutError):
            adopt_harness_skills(self.home, only="nope")

    def test_standalone_adopt_creates_the_shared_tree(self):
        import shutil

        shutil.rmtree(self.shared)
        self._skill(self.home / ".claude/skills", "priv")

        result = adopt_harness_skills(self.home)

        self.assertEqual(result.adopted, [{"name": "priv", "from": "claude"}])
        self.assertTrue((self.shared / "priv").is_dir())

    def test_dry_run_reports_without_writing(self):
        self._skill(self.home / ".claude/skills", "priv")
        self._linked()

        result = adopt_harness_skills(self.home, dry_run=True)

        self.assertEqual(result.adopted, [{"name": "priv", "from": "claude"}])
        self.assertTrue((self.home / ".claude/skills/priv").is_dir())
        self.assertFalse((self.shared / "priv").exists())
        self.assertTrue((self.home / ".codex/skills").is_symlink())
        self.assertFalse(self.records.exists())


class AdoptConflictTest(AdoptCase):
    def test_the_first_root_in_registry_order_donates(self):
        self._skill(self.home / ".codex/skills", "dup", body="codex")
        self._skill(self.home / ".claude/skills", "dup", body="claude")

        result = adopt_harness_skills(self.home)

        self.assertEqual(result.adopted, [{"name": "dup", "from": "claude"}])
        self.assertEqual(
            (self.shared / "dup/SKILL.md").read_text(),
            (self.home / ".claude/skills/dup/SKILL.md").resolve().read_text())
        self.assertTrue(result.conflicts)
        self.assertEqual(result.conflicts[0]["name"], "dup")
        self.assertEqual(result.conflicts[0]["harnesses"], ["codex"])
        # The divergent copy stays private; nothing is merged.
        self.assertTrue((self.home / ".codex/skills/dup").is_dir())
        self.assertFalse((self.home / ".codex/skills/dup").is_symlink())

    def test_identical_copies_fold_into_links(self):
        self._skill(self.home / ".codex/skills", "dup", body="same")
        self._skill(self.home / ".claude/skills", "dup", body="same")

        result = adopt_harness_skills(self.home)

        self.assertEqual(result.adopted, [{"name": "dup", "from": "claude"}])
        self.assertEqual(result.conflicts, [])
        self.assertEqual(result.deduped,
                         [{"name": "dup", "harness": "codex"}])
        link = self.home / ".codex/skills/dup"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), (self.shared / "dup").resolve())

    def test_a_shared_skill_of_the_same_name_is_never_overwritten(self):
        self._skill(self.shared, "foo", body="shared")
        self._skill(self.home / ".claude/skills", "foo", body="private")

        result = adopt_harness_skills(self.home)

        self.assertEqual(result.adopted, [])
        self.assertTrue(result.conflicts)
        self.assertIn("differs from the shared foo",
                      result.conflicts[0]["detail"])
        self.assertIn("shared", (self.shared / "foo/SKILL.md").read_text())
        self.assertTrue((self.home / ".claude/skills/foo").is_dir())

    def test_an_identical_private_copy_of_a_shared_skill_dedupes(self):
        self._skill(self.shared, "foo", body="same")
        root = self.home / ".claude/skills"
        self._skill(root, "foo", body="same")
        # A second, unique skill keeps the root a donor: one that held only
        # identical copies is "absorb", which init's link pass owns.
        self._skill(root, "mine")

        result = adopt_harness_skills(self.home)

        self.assertEqual(result.adopted, [{"name": "mine", "from": "claude"}])
        self.assertEqual(result.conflicts, [])
        link = root / "foo"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), (self.shared / "foo").resolve())

    def test_an_except_disabled_name_keeps_its_private_copy(self):
        # The root disabled the shared copy on purpose; its private folder
        # of the same name is the deliberate alternative, not a dupe.
        self._skill(self.shared, "foo", body="same")
        root = self.home / ".claude/skills"
        self._skill(root, "foo", body="same")
        from swe.skills.layout import record_selection

        record_selection("claude", root, "except", ["foo"])

        result = adopt_harness_skills(self.home)

        self.assertEqual(result.deduped, [])
        self.assertTrue((root / "foo").is_dir())
        self.assertFalse((root / "foo").is_symlink())


class RemoveSkillTest(AdoptCase):
    def test_remove_drops_per_skill_links_and_purges_state(self):
        self._skill(self.home / ".claude/skills", "priv")
        adopt_harness_skills(self.home)
        self.assertEqual(skill_adopted_map(), {"priv": "claude"})

        out = remove_skill("priv", home=self.home)

        self.assertFalse((self.shared / "priv").exists())
        link = self.home / ".claude/skills/priv"
        self.assertFalse(link.is_symlink() or link.exists())
        self.assertEqual(skill_adopted_map(), {})
        record = selection_record_at(self.home / ".claude/skills")
        self.assertIsNone(record)
        backup = Path(out["backup"])
        self.assertTrue((backup / "SKILL.md").is_file())

    def test_remove_reports_a_private_folder_it_left(self):
        self._skill(self.shared, "foo")
        self._skill(self.home / ".claude/skills", "foo", body="private")

        out = remove_skill("foo", home=self.home)

        self.assertFalse((self.shared / "foo").exists())
        self.assertEqual(out["private_left"], ["claude"])
        self.assertTrue((self.home / ".claude/skills/foo").is_dir())

    def test_remove_a_missing_skill_is_an_error(self):
        with self.assertRaises(SkillLayoutError):
            remove_skill("nope", home=self.home)

    def test_remove_a_nested_skill_is_refused(self):
        self._skill(self.shared / "outer", "inner")

        with self.assertRaises(SkillLayoutError) as ctx:
            remove_skill("inner", home=self.home)
        self.assertIn("not a top-level", str(ctx.exception))


class AdoptCommandTest(AdoptCase):
    def _patched_home(self):
        return mock.patch.multiple(
            Path,
            home=mock.Mock(return_value=self.home),
            cwd=mock.Mock(return_value=self.home),
        )

    def test_adopt_help(self):
        code, out = _run(cmd_skills_adopt, ["--help"])
        self.assertEqual(code, 0)
        self.assertIn("swe skills adopt", out)

    def test_adopt_usage_error(self):
        code, out = _run(cmd_skills_adopt, ["a", "b"])
        self.assertEqual(code, 1)
        self.assertIn("Usage: swe skills adopt", out)

    def test_adopt_unknown_flag(self):
        code, out = _run(cmd_skills_adopt, ["--bogus"])
        self.assertEqual(code, 1)
        self.assertIn("Usage:", out)

    def test_adopt_json_reply(self):
        self._skill(self.home / ".claude/skills", "priv")
        self._linked()
        with self._patched_home():
            code, out = _run(cmd_skills_adopt, ["--all", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)["data"]
        self.assertEqual(data["action"], "adopt")
        self.assertEqual(data["adopted"], [{"name": "priv", "from": "claude"}])
        self.assertEqual(data["held_off"],
                         [{"harness": "codex", "skills": ["priv"]}])

    def test_adopt_one_name_json(self):
        self._skill(self.home / ".claude/skills", "one")
        self._skill(self.home / ".claude/skills", "two")
        with self._patched_home():
            code, out = _run(cmd_skills_adopt, ["two", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)["data"]
        self.assertEqual(data["adopted"], [{"name": "two", "from": "claude"}])
        self.assertTrue((self.home / ".claude/skills/one").is_dir())

    def test_adopt_a_name_with_no_candidate_exits_1(self):
        with self._patched_home():
            code, out = _run(cmd_skills_adopt, ["nope"])
        self.assertEqual(code, 1)
        self.assertIn("No harness-only skill matching", out)

    def test_remove_help(self):
        code, out = _run(cmd_skills_remove, ["--help"])
        self.assertEqual(code, 0)
        self.assertIn("swe skills remove", out)

    def test_remove_usage_error(self):
        code, out = _run(cmd_skills_remove, [])
        self.assertEqual(code, 1)
        self.assertIn("Usage:", out)

    def test_remove_json_reply(self):
        self._skill(self.home / ".claude/skills", "priv")
        with self._patched_home():
            adopt_harness_skills(self.home)
            code, out = _run(cmd_skills_remove, ["priv", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)["data"]
        self.assertEqual(data["action"], "remove")
        self.assertEqual(data["name"], "priv")
        self.assertEqual(data["links_dropped"], ["claude"])


class AdoptInitTest(AdoptCase):
    def test_init_adopts_a_keep_root_and_reports(self):
        self._skill(self.home / ".claude/skills", "priv")
        self._linked()

        from swe.init.commands import run_init

        run = run_init(self.home, quiet=True, ask=False)

        self.assertIsNotNone(run.adopt_result)
        self.assertEqual(run.adopt_result.adopted,
                         [{"name": "priv", "from": "claude"}])
        self.assertTrue((self.shared / "priv").is_dir())
        self.assertTrue((self.home / ".claude/skills/priv").is_symlink())

    def test_init_no_adopt_leaves_the_root_alone(self):
        self._skill(self.home / ".claude/skills", "priv")

        from swe.init.commands import run_init

        run = run_init(self.home, quiet=True, ask=False, adopt=False)

        self.assertIsNone(run.adopt_result)
        self.assertTrue((self.home / ".claude/skills/priv").is_dir())
        self.assertFalse((self.shared / "priv").exists())

    def test_init_check_plans_the_adoption_and_writes_nothing(self):
        self._skill(self.home / ".claude/skills", "priv")
        self._linked()

        from swe.init.commands import run_init

        run = run_init(self.home, check_only=True, quiet=True)

        self.assertTrue(run.adopt_result.dry_run)
        self.assertEqual(run.adopt_result.adopted,
                         [{"name": "priv", "from": "claude"}])
        self.assertTrue((self.home / ".claude/skills/priv").is_dir())
        self.assertFalse((self.shared / "priv").exists())
        self.assertTrue((self.home / ".codex/skills").is_symlink())

    def test_check_document_reports_pending_adoptions(self):
        self._skill(self.home / ".claude/skills", "priv")

        from swe.init.commands import check_document

        doc = check_document(self.home)
        self.assertEqual(doc["skills_adoption"]["adopted"],
                         [{"name": "priv", "from": "claude"}])

    def _cmd_init(self, args):
        from swe.init import commands as init_commands

        buf = io.StringIO()
        with mock.patch.object(Path, "home", staticmethod(lambda: self.home)), \
                redirect_stdout(buf):
            code = init_commands.cmd_init(list(args))
        return code, buf.getvalue()

    def test_init_drops_the_alone_warning_for_a_fully_adopted_root(self):
        self._skill(self.home / ".claude/skills", "priv")

        code, out = self._cmd_init([])

        self.assertEqual(code, 0)
        self.assertIn("Adopted 1 skill", out)
        self.assertNotIn("exist nowhere else", out)

    def test_init_keeps_the_alone_warning_for_a_root_with_a_conflict(self):
        self._skill(self.shared, "foo", body="shared")
        self._skill(self.home / ".claude/skills", "foo", body="private")

        code, out = self._cmd_init([])

        self.assertEqual(code, 0)
        self.assertIn("exist nowhere else", out)
        self.assertTrue((self.home / ".claude/skills/foo").is_dir())


class AdoptInventoryTest(AdoptCase):
    def test_shared_rows_carry_provenance_and_conflict(self):
        self._skill(self.home / ".claude/skills", "adopted")
        self._skill(self.home / ".claude/skills", "diverged")
        self._skill(self.home / ".codex/skills", "diverged", body="different")
        adopt_harness_skills(self.home)

        from swe.skills.inventory import installed_skills

        skills = {
            s["name"]: s
            for s in installed_skills(self.home, self.home)["skills"]
        }
        shared = skills["adopted"]
        self.assertEqual(shared["adopted_from"], "claude")
        self.assertNotIn("conflict", shared)
        diverged = [
            s for s in installed_skills(self.home, self.home)["skills"]
            if s["name"] == "diverged" and s["source"] == "shared"
        ][0]
        self.assertEqual(diverged["adopted_from"], "claude")
        self.assertTrue(diverged["conflict"])

    def test_a_skill_never_adopted_carries_no_provenance(self):
        self._skill(self.shared, "plain")

        from swe.skills.inventory import installed_skills

        row = [
            s for s in installed_skills(self.home, self.home)["skills"]
            if s["name"] == "plain"
        ][0]
        self.assertNotIn("adopted_from", row)
        self.assertNotIn("conflict", row)


if __name__ == "__main__":
    unittest.main()
