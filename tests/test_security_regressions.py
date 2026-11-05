"""Regressions for three defects found in the architecture audit.

Each one had a way of failing quietly: a token sent over an unverified
connection, a credential file widened to world-readable, and skills
deleted with no copy anywhere. None of them raised, so none would have
surfaced without a test pinning the behaviour.
"""

import ast
import json
import os
import ssl
import stat
import unittest
from pathlib import Path
from unittest import mock

from swe.paths import atomic_write_text, backup_tree

from tests._tempdir import temp_dir

SRC = Path(__file__).resolve().parents[1] / "src" / "swe"


def _names(node) -> set[str]:
    """Every name and attribute name inside ``node`` (``ssl.CERT_NONE``
    gives ``{"ssl", "CERT_NONE"}``)."""
    if node is None:
        return set()
    return {n.id if isinstance(n, ast.Name) else n.attr
            for n in ast.walk(node) if isinstance(n, (ast.Name, ast.Attribute))}


class SslVerificationTest(unittest.TestCase):
    """Requests carry `Authorization: Bearer`, so verification must stay on.

    The old fallback retried with CERT_NONE whenever the first attempt hit
    an SSL error, which is exactly what an interception looks like.
    """

    def test_no_code_path_disables_verification(self):
        """No statement anywhere in the package turns verification off.

        Read from the syntax tree, so comments and docstrings that
        describe the old fallback don't count, and any spelling of the
        assignment (``ssl.CERT_NONE`` or a bare ``CERT_NONE``, on any
        context name) does.
        """
        def disables(node) -> bool:
            if isinstance(node, ast.Attribute):
                return node.attr == "_create_unverified_context"
            if not isinstance(node, ast.Assign):
                return False
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    if target.attr == "verify_mode" and "CERT_NONE" in _names(node.value):
                        return True
                    if target.attr == "check_hostname" and isinstance(node.value, ast.Constant) \
                            and node.value.value is False:
                        return True
            return False

        offenders = []
        for path in sorted(SRC.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            # Only a file that names one of them can do it; skip parsing the rest.
            if not any(word in text for word in ("verify_mode", "check_hostname", "_create_unverified_context")):
                continue
            offenders += [f"{path.relative_to(SRC)}:{node.lineno}"
                          for node in ast.walk(ast.parse(text)) if disables(node)]
        self.assertEqual(offenders, [], "TLS verification is switched off")

    def test_fallback_context_still_verifies(self):
        from swe.harness.rate_limits import _verified_context

        ctx = _verified_context()
        if ctx is None:
            self.skipTest("no CA bundle available here")
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    def test_gives_up_when_no_ca_bundle_exists(self):
        from swe.harness.rate_limits import _verified_context

        real = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def no_certifi(name, *a, **k):
            if name == "certifi":
                raise ImportError(name)
            return real(name, *a, **k)

        # The OS trust store is tried before certifi, so hide it too or the
        # machine's own /etc/ssl/cert.pem satisfies the lookup.
        with mock.patch("builtins.__import__", no_certifi), mock.patch(
            "swe.harness.rate_limits._SYSTEM_CA_BUNDLES",
            ("/nonexistent/cert.pem",),
        ):
            self.assertIsNone(
                _verified_context(),
                "must return None (give up) rather than an unverified context",
            )


class AtomicWritePermissionTest(unittest.TestCase):
    """tmp+rename created the temp file under the umask, so a 0600 config
    came back 0644. Harness MCP configs hold resolved API tokens."""

    def setUp(self):
        self.d = temp_dir(self)

    def _mode(self, p):
        return stat.S_IMODE(p.stat().st_mode)

    def test_existing_private_mode_survives(self):
        p = self.d / "mcp.json"
        p.write_text("{}")
        os.chmod(p, 0o600)
        atomic_write_text(p, '{"a": 1}', private=True)
        self.assertEqual(self._mode(p), 0o600)

    def test_new_private_file_is_not_world_readable(self):
        p = self.d / "new.json"
        atomic_write_text(p, "{}", private=True)
        self.assertEqual(self._mode(p) & 0o077, 0, "group/other bits set")

    def test_non_secret_file_keeps_its_own_mode(self):
        p = self.d / "plain.json"
        p.write_text("{}")
        os.chmod(p, 0o644)
        atomic_write_text(p, "{}")
        self.assertEqual(self._mode(p), 0o644)

    def test_content_is_replaced_and_no_temp_is_left(self):
        p = self.d / "x.json"
        atomic_write_text(p, "second")
        self.assertEqual(p.read_text(), "second")
        self.assertEqual([q for q in self.d.iterdir() if ".tmp" in q.name], [])

    def test_a_failed_write_leaves_no_temp_behind(self):
        p = self.d / "y.json"
        with mock.patch("os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                atomic_write_text(p, "data")
        self.assertEqual([q for q in self.d.iterdir() if ".tmp" in q.name], [])

    def test_the_mcp_writer_uses_it(self):
        from swe.mcp.cli import save_json

        p = self.d / "harness.json"
        p.write_text("{}")
        os.chmod(p, 0o600)
        save_json(p, {"mcpServers": {}})
        self.assertEqual(self._mode(p), 0o600, "swe mcp sync widened the config")

    def test_writes_through_symlink(self):
        """A symlinked config keeps its link; the target takes the write."""
        target = self.d / "real.json"
        target.write_text('{"a": 1}')
        link = self.d / "link.json"
        link.symlink_to(target)
        atomic_write_text(link, '{"a": 2}')
        self.assertTrue(link.is_symlink())
        self.assertEqual(json.loads(target.read_text()), {"a": 2})

    def test_broken_symlink_writes_through_to_target(self):
        target = self.d / "missing-target.json"
        link = self.d / "link.json"
        link.symlink_to(target)
        atomic_write_text(link, '{"a": 3}')
        self.assertTrue(link.is_symlink())
        self.assertEqual(json.loads(target.read_text()), {"a": 3})


class ForcedLinkBacksUpTest(unittest.TestCase):
    """`swe skills link --force` used to rmtree a real skills root with no
    copy anywhere, while the same decision in `swe init` backed up first."""

    def setUp(self):
        self.home = temp_dir(self)
        (self.home / ".swe" / "skills").mkdir(parents=True)
        self.root = self.home / ".codex" / "skills"
        self.root.mkdir(parents=True)
        (self.root / "only-copy").mkdir()
        (self.root / "only-copy" / "SKILL.md").write_text("irreplaceable")

    def _link(self, **kw):
        from swe.skills.link_ops import link_skill_root

        return link_skill_root("codex", home=self.home, cwd=self.home, **kw)

    def test_refuses_without_force(self):
        from swe.skills.link_ops import SkillLayoutError

        with self.assertRaises(SkillLayoutError):
            self._link()
        self.assertTrue((self.root / "only-copy" / "SKILL.md").exists())

    def test_force_backs_up_before_deleting(self):
        from swe.paths import backups_dir_for

        self._link(force=True)
        saved = list(backups_dir_for(self.home).rglob("SKILL.md"))
        self.assertEqual(len(saved), 1, "nothing was backed up")
        self.assertEqual(saved[0].read_text(), "irreplaceable")

    def test_force_still_creates_the_link(self):
        self._link(force=True)
        self.assertTrue(self.root.is_symlink())


class BackupTreeHardeningTest(unittest.TestCase):
    """Three ways the old _backup aborted a run partway through."""

    def setUp(self):
        self.home = temp_dir(self)
        (self.home / ".swe" / "backups").mkdir(parents=True)
        self.tree = self.home / ".h" / "skills"
        self.tree.mkdir(parents=True)
        (self.tree / "ok").mkdir()
        (self.tree / "ok" / "SKILL.md").write_text("x")

    def test_a_broken_symlink_does_not_abort_the_backup(self):
        (self.tree / "broken").symlink_to(self.home / "nowhere")
        dest = backup_tree(self.tree, self.home)
        self.assertTrue((dest / "ok" / "SKILL.md").exists())

    def test_symlinks_are_copied_as_links_not_followed(self):
        (self.tree / "link").symlink_to(self.tree / "ok")
        dest = backup_tree(self.tree, self.home)
        self.assertTrue((dest / "link").is_symlink())

    def test_a_path_outside_home_does_not_raise(self):
        outside = temp_dir(self) / "skills"
        outside.mkdir(parents=True)
        (outside / "s").mkdir()
        self.assertTrue(backup_tree(outside, self.home).exists())

    def test_two_backups_in_the_same_second_do_not_collide(self):
        a = backup_tree(self.tree, self.home)
        b = backup_tree(self.tree, self.home)
        self.assertNotEqual(a, b)
        self.assertTrue(a.exists() and b.exists())


class ParserFailuresAreReportedTest(unittest.TestCase):
    """A crashing parser must read as an error, not as an unused harness.

    A NameError in the cursor parser hid 84 sessions for months because the
    handler caught it, returned [], and the aggregator's safety net never
    saw anything to catch.
    """

    def setUp(self):
        from swe.sessions import failures

        failures.clear()
        self.addCleanup(failures.clear)
        self.failures = failures

    def test_a_failure_inside_a_json_handler_is_recorded(self):
        import json as _json

        from swe.sessions.engines import json_engine
        from swe.sessions.engines.json_engine import JsonParserConfig

        d = temp_dir(self)
        idx = d / "index.json"
        idx.write_text(_json.dumps([{"id": "1"}]))

        def boom(_data):
            raise NameError("uuid_dir")

        cfg = JsonParserConfig(tool_name="demo", agent="Demo",
                               index_path=str(idx), index_items=boom)
        self.assertEqual(json_engine.parse_json_store(cfg), [])
        self.assertIn("demo", self.failures.snapshot())

    def test_an_escaping_failure_is_still_recorded(self):
        from swe.sessions.aggregator import _run_parser

        def boom():
            raise RuntimeError("escaped")

        self.assertEqual(_run_parser("demo2", boom), [])
        self.assertIn("demo2", self.failures.snapshot())

    def test_no_function_level_handler_swallows_silently(self):
        """A handler at function level ends the whole parse, so it must record.

        Matched by indentation: a four-space `except` closes the function's
        own try, while the deeper ones are per-file or per-row and are meant
        to skip quietly. The `conn.close()` cleanup is nested and exempt.
        """
        # The whole-tool shape exactly: a function-level handler that then
        # hands back the session list. Deeper handlers (per file, per row)
        # and ones returning a title or a filename list are meant to be
        # quiet, because one bad record should not blank a harness. Read
        # from the syntax tree, so formatting can't hide one.
        def silent(handler):
            caught = _names(handler.type) if handler.type is not None else {"BaseException"}
            return bool(caught & {"Exception", "BaseException"}) and \
                all(isinstance(stmt, ast.Pass) for stmt in handler.body)

        offenders = []
        for path in sorted((SRC / "sessions" / "engines").glob("*.py")):
            for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for stmt, after in zip(fn.body, fn.body[1:]):
                    if isinstance(stmt, ast.Try) and any(silent(h) for h in stmt.handlers) \
                            and isinstance(after, ast.Return) and _names(after.value) == {"sessions"}:
                        offenders.append(f"{path.name}:{fn.name}")
        self.assertEqual(offenders, [], f"whole-tool failures still silent: {offenders}")


class UnknownRootRefTest(unittest.TestCase):
    """A bare word that is not a known root used to become cwd/<word>.

    `swe skills link codx --force` would then back up and delete whatever
    sat in the current directory under that name.
    """

    def setUp(self):
        from swe.skills.layout import SkillLayoutError

        self.error = SkillLayoutError
        self.home = temp_dir(self)
        (self.home / ".swe" / "skills").mkdir(parents=True)
        self.work = temp_dir(self)
        (self.work / "codx").mkdir()
        self._old = os.getcwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, self._old)

    def _resolve(self, ref):
        from swe.skills.layout import resolve_root_ref

        return resolve_root_ref(ref, home=self.home, cwd=self.home)

    def test_a_typo_is_refused(self):
        with self.assertRaises(self.error):
            self._resolve("codx")

    def test_refused_even_when_a_matching_folder_exists_in_cwd(self):
        self.assertTrue((self.work / "codx").exists())
        with self.assertRaises(self.error):
            self._resolve("codx")

    def test_the_message_lists_valid_roots(self):
        with self.assertRaises(self.error) as ctx:
            self._resolve("codx")
        self.assertIn("codex", str(ctx.exception))

    def test_known_labels_still_resolve(self):
        for ref in ("shared", "codex"):
            self.assertEqual(self._resolve(ref)[0], ref)

    def test_explicit_paths_still_resolve(self):
        for ref in ("./codx", str(self.work / "codx"), "~/x/skills"):
            self.assertTrue(self._resolve(ref)[1])

    def test_link_refuses_a_typo_before_touching_anything(self):
        from swe.skills.link_ops import link_skill_root

        with self.assertRaises(self.error):
            link_skill_root("codx", home=self.home, cwd=self.home, force=True)
        self.assertTrue((self.work / "codx").is_dir(), "cwd folder was touched")


if __name__ == "__main__":
    unittest.main()
