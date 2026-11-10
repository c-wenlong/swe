"""`swe wt`: discovery, classification, prune and slim (issue 1909).

Every repo lives in a temp folder. ``origin`` reads as a GitHub URL (so
the PR lookups run) but ``url.<path>.insteadOf`` sends git itself to a
local bare repo, and a fake ``gh`` on PATH answers PR queries from a JSON
file, so nothing touches the network.
"""

import io
import json
import os
import subprocess
import sys
import textwrap
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests._tempdir import temp_dir

from swe.worktrees import actions, discover, sizing
from swe.worktrees.classify import PrLookup, classify_all
from swe.worktrees.models import (MERGED_CLEAN, MERGED_DIRTY, NO_PR, OPEN_PR,
                                  ORPHANED, UNKNOWN)
from swe.worktrees.settings import Settings

SLUG = "acme/widget"
URL = f"https://github.com/{SLUG}.git"

FAKE_GH = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    if os.environ.get("FAKE_GH_FAIL"):
        sys.stderr.write("API rate limit exceeded\\n")
        sys.exit(1)
    data = json.load(open(os.environ["FAKE_GH_DATA"]))
    args = sys.argv[1:]
    if args[:2] == ["pr", "list"]:
        head = args[args.index("--head") + 1]
        print(json.dumps(data.get(head, [])))
    elif args[:2] == ["pr", "view"]:
        pr = data.get("#" + args[2])
        if pr is None:
            sys.stderr.write("no pull requests found\\n")
            sys.exit(1)
        print(json.dumps(pr))
    else:
        sys.exit(2)
""")


def run(*args, cwd=None):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True)
    return done.stdout.strip()


def pr(number, state, head=None):
    return {"number": number, "state": state, "headRefOid": head,
            "url": f"https://github.com/{SLUG}/pull/{number}",
            "mergedAt": "2026-11-01T00:00:00Z" if state == "MERGED" else None}


class Fixture:
    """A repo with one worktree per case."""

    def __init__(self, base: Path):
        self.base = base
        self.home = base / "home"
        self.home.mkdir()
        origin = base / "origin.git"
        run("init", "--bare", "-q", "-b", "main", str(origin))
        self.root = str((base / "widget").resolve())
        run("init", "-q", "-b", "main", self.root)
        for key, value in (("user.email", "t@example.com"), ("user.name", "t"),
                           ("commit.gpgsign", "false"),
                           ("remote.origin.url", URL),
                           ("remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"),
                           (f"url.{origin}.insteadOf", URL)):
            run("config", key, value, cwd=self.root)
        Path(self.root, ".gitignore").write_text("node_modules/\n.venv/\n")
        Path(self.root, "README").write_text("hi\n")
        run("add", ".", cwd=self.root)
        run("commit", "-qm", "init", cwd=self.root)
        run("push", "-q", "origin", "main", cwd=self.root)
        run("fetch", "-q", "origin", cwd=self.root)
        self.trees = Path(self.root, ".claude", "worktrees")
        self.gh_data: dict = {}
        self.bin = base / "bin"
        self.bin.mkdir()
        fake = self.bin / "gh"
        fake.write_text(FAKE_GH)
        fake.chmod(0o755)
        self.data_file = base / "gh.json"

    def worktree(self, name, branch=None, *, commit=True, detach=False):
        path = str(self.trees / name)
        if detach:
            run("worktree", "add", "-q", "--detach", path, "origin/main", cwd=self.root)
        else:
            run("worktree", "add", "-q", "-b", branch, path, "main", cwd=self.root)
            if commit:
                Path(path, f"{name}.txt").write_text(name)
                run("add", ".", cwd=path)
                run("commit", "-qm", name, cwd=path)
        return os.path.realpath(path)

    def head(self, path):
        return run("rev-parse", "HEAD", cwd=path)

    def env(self, **extra):
        self.data_file.write_text(json.dumps(self.gh_data))
        env = {"PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
               "FAKE_GH_DATA": str(self.data_file)}
        env.update(extra)
        return mock.patch.dict(os.environ, env)

    def settings(self, **block):
        return Settings({"worktree": block} if block else {}, self.home)

    def scan(self, settings=None):
        discover.clear_cache()
        settings = settings or self.settings()
        found = discover.Discovery(home=self.home, cwd=str(self.base),
                                   settings=settings)
        found.scope_roots([self.root])
        worktrees = found.collect()
        classify_all(worktrees, PrLookup(), settings, self.home)
        return {w.path: w for w in worktrees}

    def registered(self):
        out = run("worktree", "list", "--porcelain", cwd=self.root)
        return {os.path.realpath(l[9:]) for l in out.splitlines()
                if l.startswith("worktree ")}


def make_old(path):
    """Push a worktree's last use two days back."""
    old = time.time() - 2 * 86400
    gitdir = Path(path, ".git").read_text().split("gitdir:")[1].strip()
    for p in (Path(gitdir) / "HEAD", Path(gitdir) / "index",
              Path(gitdir) / "logs" / "HEAD", Path(path)):
        if p.exists():
            os.utime(p, (old, old))


class WorktreeCases(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture(temp_dir(self))
        fx = self.fx
        self.squash = fx.worktree("squash", "feat/squash")
        self.dirty = fx.worktree("dirty", "feat/dirty")
        Path(self.dirty, "README").write_text("changed\n")
        self.open = fx.worktree("open", "feat/open")
        self.none = fx.worktree("none", "feat/none")
        self.ignored = fx.worktree("ignored", "feat/ignored")
        Path(self.ignored, ".env.local").write_text("X=1\n")
        self.detached = fx.worktree("detached", detach=True)
        make_old(self.detached)
        self.fresh = fx.worktree("fresh", detach=True)
        ghost = fx.trees / "ghost"
        ghost.mkdir(parents=True)
        (ghost / ".git").write_text(f"gitdir: {fx.root}/.git/worktrees/ghost-gone\n")
        self.ghost = os.path.realpath(ghost)
        fx.gh_data = {
            "feat/squash": [pr(1, "MERGED", fx.head(self.squash))],
            "feat/dirty": [pr(2, "MERGED")],
            "feat/open": [pr(3, "OPEN")],
            "feat/ignored": [pr(4, "MERGED", fx.head(self.ignored))],
        }
        self.settings = fx.settings(repos={SLUG: {"ignore": [".env.local"]}})

    def test_states(self):
        with self.fx.env():
            got = self.fx.scan(self.settings)
        self.assertEqual(got[self.squash].state, MERGED_CLEAN)  # squash merge
        self.assertEqual(got[self.squash].pr["number"], 1)
        self.assertEqual(got[self.dirty].state, MERGED_DIRTY)
        self.assertEqual(got[self.dirty].changes, 1)
        self.assertEqual(got[self.open].state, OPEN_PR)
        self.assertEqual(got[self.none].state, NO_PR)
        self.assertEqual(got[self.ignored].state, MERGED_CLEAN)
        self.assertEqual(got[self.detached].state, MERGED_CLEAN)
        self.assertIsNone(got[self.detached].hold)
        self.assertEqual(got[self.fresh].state, MERGED_CLEAN)
        self.assertIn("24h", got[self.fresh].hold)
        self.assertEqual(got[self.ghost].state, ORPHANED)
        self.assertFalse(got[self.ghost].registered)
        self.assertNotIn(self.fx.root, got)  # never the main checkout
        # The ancestry check the issue warns about really does say "unmerged".
        rc = subprocess.run(["git", "merge-base", "--is-ancestor", "feat/squash",
                             "origin/main"], cwd=self.fx.root).returncode
        self.assertNotEqual(rc, 0)

    def test_untracked_counts_without_ignore_list(self):
        with self.fx.env():
            got = self.fx.scan(self.fx.settings())
        self.assertEqual(got[self.ignored].state, MERGED_DIRTY)
        self.assertEqual(got[self.ignored].untracked, 1)

    def test_gh_unavailable_is_unknown_and_never_pruned(self):
        with self.fx.env(FAKE_GH_FAIL="1"):
            got = self.fx.scan(self.settings)
            self.assertEqual(got[self.squash].state, UNKNOWN)
            plan = actions.prune(list(got.values()), settings=self.settings,
                                 apply=True, home=self.fx.home)
        removed = {r["path"] for r in plan.acted}
        self.assertNotIn(self.squash, removed)
        self.assertEqual(removed, {self.detached})  # ancestry needs no gh
        self.assertTrue(os.path.isdir(self.squash))

    def test_dry_run_changes_nothing(self):
        before = self.fx.registered()
        branches = run("branch", "--format=%(refname:short)", cwd=self.fx.root)
        with self.fx.env():
            got = self.fx.scan(self.settings)
            plan = actions.prune(list(got.values()), settings=self.settings,
                                 apply=False, delete_branches=True,
                                 home=self.fx.home)
        self.assertEqual({r["path"] for r in plan.acted},
                         {self.squash, self.ignored, self.detached})
        self.assertEqual(self.fx.registered(), before)
        self.assertEqual(run("branch", "--format=%(refname:short)", cwd=self.fx.root),
                         branches)
        self.assertTrue(all(os.path.isdir(p) for p in before))
        self.assertIsNone(plan.log)
        self.assertFalse((self.fx.home / ".swe" / "logs").exists())

    def test_yes_removes_only_merged_clean_and_keeps_branches(self):
        with self.fx.env():
            got = self.fx.scan(self.settings)
            plan = actions.prune(list(got.values()), settings=self.settings,
                                 apply=True, home=self.fx.home)
        gone = {self.squash, self.ignored, self.detached}
        self.assertEqual({r["path"] for r in plan.acted}, gone)
        for path in gone:
            self.assertFalse(os.path.exists(path))
        for path in (self.dirty, self.open, self.none, self.fresh, self.ghost):
            self.assertTrue(os.path.isdir(path), path)
        self.assertEqual(self.fx.registered() & gone, set())
        for branch in ("feat/squash", "feat/ignored"):
            run("rev-parse", "--verify", branch, cwd=self.fx.root)
        self.assertTrue(plan.log and Path(plan.log).is_file())
        logged = json.loads(Path(plan.log).read_text())
        self.assertEqual(len(logged["acted"]), 3)

    def test_protected_is_kept(self):
        settings = self.fx.settings(protect=[self.squash],
                                    repos={SLUG: {"ignore": [".env.local"]}})
        with self.fx.env():
            got = self.fx.scan(settings)
            self.assertEqual(got[self.squash].hold, "protected")
            plan = actions.prune(list(got.values()), settings=settings,
                                 apply=True, home=self.fx.home)
        self.assertNotIn(self.squash, {r["path"] for r in plan.acted})
        self.assertTrue(os.path.isdir(self.squash))

    def test_install_cache_is_protected_by_default(self):
        settings = Settings({}, self.fx.home)
        cache = os.path.realpath(self.fx.home / ".swe" / "cache" / "install" / "src")
        self.assertIn(cache, settings.protect())

    def test_branch_filter_and_delete_branches(self):
        with self.fx.env():
            got = self.fx.scan(self.settings)
            plan = actions.prune(list(got.values()), settings=self.settings,
                                 apply=True, branch="feat/squash",
                                 delete_branches=True, home=self.fx.home)
        self.assertEqual([r["path"] for r in plan.acted], [self.squash])
        self.assertTrue(os.path.isdir(self.ignored))
        self.assertEqual([b["branch"] for b in plan.branches], ["feat/squash"])
        rc = subprocess.run(["git", "rev-parse", "--verify", "--quiet", "feat/squash"],
                            cwd=self.fx.root).returncode
        self.assertNotEqual(rc, 0)
        run("rev-parse", "--verify", "feat/ignored", cwd=self.fx.root)
        # Remote branches are never touched.
        self.assertIn("main", run("ls-remote", "--heads", "origin", cwd=self.fx.root))

    def test_merged_dirty_needs_flag_and_a_yes(self):
        with self.fx.env():
            got = self.fx.scan(self.settings)
            plan = actions.prune(list(got.values()), settings=self.settings,
                                 apply=True, include_dirty=True,
                                 confirm=lambda wt: False, home=self.fx.home)
            self.assertTrue(os.path.isdir(self.dirty))
            self.assertIn("not confirmed", " ".join(r["reason"] for r in plan.skipped))
            got = self.fx.scan(self.settings)
            plan = actions.prune(list(got.values()), settings=self.settings,
                                 apply=True, include_dirty=True,
                                 confirm=lambda wt: True, home=self.fx.home)
        self.assertIn(self.dirty, {r["path"] for r in plan.acted})
        self.assertFalse(os.path.exists(self.dirty))

    def test_state_is_read_again_before_removal(self):
        with self.fx.env():
            got = self.fx.scan(self.settings)
            Path(self.squash, "late.txt").write_text("new work\n")
            plan = actions.prune(list(got.values()), settings=self.settings,
                                 apply=True, home=self.fx.home)
        self.assertTrue(os.path.isdir(self.squash))
        reasons = {r["path"]: r["reason"] for r in plan.skipped}
        self.assertIn("state changed to merged-dirty", reasons[self.squash])

    def test_auto_prune_follows_repo_config(self):
        with self.fx.env():
            got = self.fx.scan(self.settings)
            plan = actions.prune(list(got.values()), settings=self.settings,
                                 apply=True, auto=True, home=self.fx.home)
            self.assertEqual(plan.acted, [])
            on = self.fx.settings(repos={SLUG: {"ignore": [".env.local"],
                                                "auto_prune": True}})
            got = self.fx.scan(on)
            plan = actions.prune(list(got.values()), settings=on, apply=True,
                                 auto=True, branch="feat/squash", home=self.fx.home)
        self.assertEqual([r["path"] for r in plan.acted], [self.squash])

    def test_slim_deletes_only_stale_ignored_dirs_in_unmerged(self):
        for path in (self.none, self.squash):
            mods = Path(path, "node_modules", "pkg")
            mods.mkdir(parents=True)
            (mods / "index.js").write_text("x")
            old = time.time() - 30 * 86400
            for p in (mods / "index.js", mods, mods.parent):
                os.utime(p, (old, old))
        Path(self.open, "build").mkdir()  # not ignored: never a candidate
        with self.fx.env():
            got = self.fx.scan(self.settings)
            sizing.measure_all(list(got.values()))
            dry = actions.slim(list(got.values()), apply=False, days=14)
            self.assertEqual([r["folder"] for r in dry.acted],
                             [os.path.join(self.none, "node_modules")])
            self.assertTrue(os.path.isdir(os.path.join(self.none, "node_modules")))
            done = actions.slim(list(got.values()), apply=True, days=14,
                                home=self.fx.home)
        self.assertEqual(len(done.acted), 1)
        self.assertFalse(os.path.exists(os.path.join(self.none, "node_modules")))
        self.assertTrue(os.path.isdir(self.none))
        self.assertTrue(os.path.isdir(os.path.join(self.squash, "node_modules")))
        self.assertTrue(os.path.isdir(os.path.join(self.open, "build")))

    def test_list_json_contract(self):
        from swe.worktrees import commands
        out = io.StringIO()
        with self.fx.env(), redirect_stdout(out), \
                mock.patch.object(commands.Settings, "load", return_value=self.settings):
            discover.clear_cache()
            rc = commands.cmd_worktree(["list", f"--root={self.fx.root}", "--json"])
        self.assertEqual(rc, 0)
        doc = json.loads(out.getvalue())
        self.assertEqual(doc["schema"], 1)
        data = doc["data"]
        self.assertEqual(set(data), {"scope", "worktrees", "totals"})
        row = next(w for w in data["worktrees"] if w["path"] == self.squash)
        for key in ("repo", "repo_name", "branch", "state", "pr", "last_commit",
                    "size_kb", "rebuildable_kb", "hold", "location", "registered"):
            self.assertIn(key, row)
        self.assertEqual(row["repo_name"], SLUG)
        self.assertEqual(row["location"], "claude")
        self.assertEqual(data["totals"]["by_state"][MERGED_CLEAN], 4)
        self.assertEqual(data["totals"]["prunable"], 3)

    def test_prune_cli_dry_run_by_default(self):
        from swe.worktrees import commands
        out = io.StringIO()
        with self.fx.env(), redirect_stdout(out), \
                mock.patch.object(commands.Settings, "load", return_value=self.settings):
            discover.clear_cache()
            rc = commands.cmd_worktree(["prune", f"--root={self.fx.root}", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out.getvalue())["data"]
        self.assertTrue(data["dry_run"])
        self.assertEqual(len(data["acted"]), 3)
        self.assertTrue(os.path.isdir(self.squash))

    def test_bad_flags(self):
        from swe.worktrees import commands
        err = io.StringIO()
        with mock.patch.object(sys, "stdout", err):
            self.assertEqual(commands.cmd_worktree(["prune", "--include=open-pr"]), 2)
            self.assertEqual(commands.cmd_worktree(["prune", "--branch"]), 2)
            self.assertEqual(commands.cmd_worktree(["list", "--yes"]), 2)
            self.assertEqual(commands.cmd_worktree(["nope"]), 2)


class HarnessFolders(unittest.TestCase):
    def test_codex_layout_and_pr_checkout_name(self):
        fx = Fixture(temp_dir(self))
        accounts = fx.home / ".swe" / "accounts" / "codex" / "work" / "home" / "worktrees"
        (accounts / "task").mkdir(parents=True)
        path = os.path.realpath(accounts / "task" / "widget")
        run("worktree", "add", "-q", "-b", "codex/task", path, "main", cwd=fx.root)
        prs = Path(fx.home / ".swe" / "worktrees")
        prs.mkdir(parents=True)
        pr_path = os.path.realpath(prs / "acme__widget-pr7")
        run("worktree", "add", "-q", "--detach", pr_path, "main", cwd=fx.root)
        fx.gh_data = {"#7": pr(7, "MERGED")}
        settings = fx.settings()
        with fx.env():
            discover.clear_cache()
            found = discover.Discovery(home=fx.home, cwd=str(fx.base), settings=settings)
            found.scope_everywhere()
            got = {w.path: w for w in found.collect()}
            classify_all(list(got.values()), PrLookup(), settings, fx.home)
        self.assertEqual(got[path].location, "codex")
        self.assertEqual(got[pr_path].location, "swe")
        self.assertEqual(got[pr_path].state, MERGED_CLEAN)
        self.assertEqual(got[pr_path].pr["number"], 7)


class Parsing(unittest.TestCase):
    def test_porcelain(self):
        rows = discover.parse_porcelain(
            "worktree /r\nHEAD a\nbranch refs/heads/main\n\n"
            "worktree /r/w\nHEAD b\ndetached\nlocked reason\n\n"
            "worktree /gone\nHEAD c\nbranch refs/heads/x\nprunable gitdir file points to non-existent location\n")
        self.assertEqual(rows[0]["branch"], "main")
        self.assertTrue(rows[1]["detached"] and rows[1]["locked"])
        self.assertTrue(rows[2]["prunable"])

    def test_github_slug(self):
        self.assertEqual(discover.github_slug("git@github.com:a/b.git"), "a/b")
        self.assertEqual(discover.github_slug("https://github.com/a/b"), "a/b")
        self.assertIsNone(discover.github_slug("https://gitlab.com/a/b"))

    def test_config_validation(self):
        from swe.configuration import validate_config
        issues = validate_config({"worktree": {"roots": "x", "repos": {
            "a/b": {"ignore": [1], "auto_prune": "yes"}}}})
        paths = {i.path for i in issues}
        self.assertEqual(paths, {"worktree.roots", "worktree.repos.a/b.ignore",
                                 "worktree.repos.a/b.auto_prune"})
        self.assertEqual(validate_config({"worktree": {"roots": ["~/code"]}}), [])

    def test_human(self):
        self.assertEqual(sizing.human(None), "-")
        self.assertEqual(sizing.human(2 * 1024 * 1024), "2.0 GB")


if __name__ == "__main__":
    unittest.main()
