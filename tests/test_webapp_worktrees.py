"""The Worktrees page: swe.worktrees called in process through a stub that
returns contract-shaped dicts, the route with auth, and the page script's
logic and its delete safety under node."""

import json
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote

from swe.webapp.pages import PageError
from swe.webapp.pages import worktrees as page

from tests.test_remote_hub import HubBase

HOME = Path("/home/x")
SCRIPT = Path(__file__).resolve().parents[1] / "src/swe/webapp/static/pages/worktrees.js"


def _w(path="/r/main", branch="main", state="main", **extra):
    """One worktree object, the shape of docs the CLI contract pins."""
    w = {
        "path": path, "repo": "acme/app", "main_checkout": "/r/main", "branch": branch,
        "head": "0123456789abcdef", "state": state, "pr": None,
        "last_commit_at": "2026-11-01T10:00:00Z", "size_bytes": 1000, "rebuildable_bytes": 0,
        "dirty": False, "untracked": 0, "locked": False, "lock_reason": None, "harness": "claude",
    }
    w.update(extra)
    return w


class FakeWorktreeError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _fake_module(fail=None):
    """A stand-in for swe.worktrees: records every call, returns the
    contract's shapes, and raises FakeWorktreeError for names in ``fail``
    (``{"remove": ("not_safe", "...")}``)."""
    mod = types.ModuleType("swe.worktrees")
    mod.WorktreeError = FakeWorktreeError
    mod.calls = []
    fail = fail or {}

    def make(name, result):
        def fn(*args, **kwargs):
            mod.calls.append((name, args, kwargs))
            if name in fail:
                raise FakeWorktreeError(*fail[name])
            return result(*args, **kwargs) if callable(result) else result
        return fn

    listing = {"scope": "all", "generated_at": "2026-11-10T00:00:00Z",
               "worktrees": [_w(), _w("/r/wt/a", "feat/a", "merged-clean")]}
    mod.list_worktrees = make("list_worktrees", listing)
    mod.show = make("show", lambda path, **k: {"worktree": _w(path, "feat/a", "merged-clean")})
    mod.add = make("add", lambda repo, **k: {"worktree": _w("/r/wt/new", k.get("branch"), "no-pr")})
    mod.remove = make("remove", lambda path, **k: {"removed": path, "freed_bytes": 1000})
    mod.lock = make("lock", lambda path, **k: {"worktree": _w(path, "x", "no-pr", locked=True)})
    mod.unlock = make("unlock", lambda path, **k: {"worktree": _w(path, "x", "no-pr")})
    mod.move = make("move", lambda path, new, **k: {"worktree": _w(new, "x", "no-pr")})
    mod.prune = make("prune", lambda **k: {"dry_run": not k.get("yes"), "candidates": [_w("/r/wt/a", "feat/a", "merged-clean")],
                                          "removed": [], "skipped": [], "freed_bytes": 1000})
    mod.slim = make("slim", lambda path=None, **k: {"dry_run": not k.get("yes"),
                                                    "dirs": [{"path": "/r/wt/a/node_modules", "bytes": 500}], "freed_bytes": 500})
    return mod


class _Stubbed:
    def setUp(self):
        super().setUp()
        self.wt = _fake_module(getattr(self, "fail_with", None))
        patcher = patch.dict(sys.modules, {"swe.worktrees": self.wt})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ctx = SimpleNamespace(home=Path("/home/x"), machine={"name": "hub-box"})

    def last(self):
        return self.wt.calls[-1]


class TestRead(_Stubbed, unittest.TestCase):
    def test_list_is_all_repos_by_default(self):
        d = page.get(self.ctx, {})
        self.assertEqual(self.last(), ("list_worktrees", (), {"all": True, "home": HOME}))
        self.assertEqual((d["scope"], d["machine"]), ("all", "hub-box"))
        self.assertEqual([w["branch"] for w in d["worktrees"]], ["main", "feat/a"])

    def test_scope_repo_passes_the_checkout_as_repo(self):
        page.get(self.ctx, {"scope": "repo", "repo": "/r/main"})
        self.assertEqual(self.last(), ("list_worktrees", (), {"repo": "/r/main", "home": HOME}))

    def test_scope_repo_needs_a_repo_and_scope_is_checked(self):
        for args in ({"scope": "repo"}, {"scope": "nope"}, {"repo": "relative/path"}):
            with self.subTest(args=args), self.assertRaises(PageError) as cm:
                page.get(self.ctx, args)
            self.assertEqual(cm.exception.status, 400)
        self.assertEqual(self.wt.calls, [])

    def test_show(self):
        d = page.get(self.ctx, {"worktree": "/r/wt/a"})
        self.assertEqual(self.last(), ("show", ("/r/wt/a",), {"home": HOME}))
        self.assertEqual(d["worktree"]["path"], "/r/wt/a")

    def test_show_refuses_a_relative_path(self):
        with self.assertRaises(PageError):
            page.get(self.ctx, {"worktree": "../etc"})
        self.assertEqual(self.wt.calls, [])


class TestWrites(_Stubbed, unittest.TestCase):
    def post(self, action, body):
        return page.post(self.ctx, action, body)

    def test_add_existing_branch(self):
        d = self.post("add", {"repo": "/r/main", "branch": "feat/b", "path": "/r/wt/b"})
        self.assertEqual(self.last(), ("add", ("/r/main",),
                                       {"branch": "feat/b", "new": False, "base": None, "path": "/r/wt/b", "home": HOME}))
        self.assertEqual(d["worktree"]["path"], "/r/wt/new")

    def test_add_new_branch_with_base(self):
        self.post("add", {"repo": "/r/main", "branch": "feat/c", "new": True, "base": "origin/main"})
        self.assertEqual(self.last()[2], {"branch": "feat/c", "new": True, "base": "origin/main", "path": None, "home": HOME})

    def test_add_a_pull_request(self):
        self.post("add", {"repo": "/r/main", "pr": 42})
        self.assertEqual(self.last(), ("add", ("/r/main",), {"pr": 42, "path": None, "home": HOME}))

    def test_add_refusals(self):
        bad = [
            {"repo": "/r/main"},                                   # nothing to check out
            {"repo": "/r/main", "branch": "a", "pr": 1},           # both
            {"repo": "/r/main", "pr": "7"},                        # a string number
            {"repo": "/r/main", "pr": 0},
            {"repo": "/r/main", "pr": 3, "new": True},             # a PR has its own branch
            {"repo": "/r/main", "branch": "a", "base": "main"},    # base without new
            {"repo": "main", "branch": "a"},                       # relative repo
            {"repo": "/r/main", "branch": "a", "path": "rel"},     # relative path
            {"branch": "a"},                                       # no repo
            {"repo": "/r/main", "branch": "a", "new": "yes"},      # not a JSON bool
        ]
        for body in bad:
            with self.subTest(body=body), self.assertRaises(PageError) as cm:
                self.post("add", body)
            self.assertEqual(cm.exception.status, 400)
        self.assertEqual(self.wt.calls, [])

    def test_remove_never_forces_unless_asked(self):
        self.post("remove", {"path": "/r/wt/a"})
        self.assertEqual(self.last(), ("remove", ("/r/wt/a",), {"force": False, "delete_branch": False, "home": HOME}))
        for sneaky in ("true", 1, "yes", [True]):
            with self.subTest(force=sneaky), self.assertRaises(PageError):
                self.post("remove", {"path": "/r/wt/a", "force": sneaky})
        self.assertEqual(len(self.wt.calls), 1)
        self.post("remove", {"path": "/r/wt/a", "force": True, "delete_branch": True})
        self.assertEqual(self.last()[2], {"force": True, "delete_branch": True, "home": HOME})

    def test_remove_needs_an_absolute_path(self):
        for body in ({}, {"path": ""}, {"path": "rel/x"}, {"path": 5}):
            with self.subTest(body=body), self.assertRaises(PageError):
                self.post("remove", body)
        self.assertEqual(self.wt.calls, [])

    def test_lock_unlock_move(self):
        self.post("lock", {"path": "/r/wt/a", "reason": "  agent running "})
        self.assertEqual(self.last(), ("lock", ("/r/wt/a",), {"reason": "agent running", "home": HOME}))
        self.post("lock", {"path": "/r/wt/a", "reason": ""})
        self.assertEqual(self.last()[2], {"reason": None, "home": HOME})
        self.post("unlock", {"path": "/r/wt/a"})
        self.assertEqual(self.last(), ("unlock", ("/r/wt/a",), {"home": HOME}))
        self.post("move", {"path": "/r/wt/a", "new_path": "/r/wt/z"})
        self.assertEqual(self.last(), ("move", ("/r/wt/a", "/r/wt/z"), {"home": HOME}))
        with self.assertRaises(PageError):
            self.post("move", {"path": "/r/wt/a", "new_path": "z"})

    def test_prune_is_a_dry_run_until_yes_is_true(self):
        d = self.post("prune", {"all": True})
        self.assertEqual(self.last()[2], {"all": True, "yes": False, "include": None, "branch": None, "home": HOME})
        self.assertTrue(d["dry_run"])
        d = self.post("prune", {"repo": "/r/main", "include": "merged-dirty"})
        self.assertEqual(self.last()[2]["repo"], "/r/main")
        self.assertNotIn("all", self.last()[2])
        for body in ({"yes": "true"}, {"include": "no-pr", "all": True}, {"all": "1"}, {}, {"all": True, "repo": "/r/main"}):
            with self.subTest(body=body), self.assertRaises(PageError):
                self.post("prune", body)

    def names(self):
        return [c[0] for c in self.wt.calls]

    def test_prune_yes_needs_the_previewed_paths(self):
        for body in ({"all": True, "yes": True}, {"all": True, "yes": True, "paths": []},
                     {"all": True, "yes": True, "paths": ["rel"]}):
            with self.subTest(body=body), self.assertRaises(PageError):
                self.post("prune", body)
        self.assertEqual(self.wt.calls, [])

    def test_prune_yes_removes_exactly_the_previewed_paths_without_force(self):
        d = self.post("prune", {"all": True, "yes": True, "paths": ["/r/wt/a", "/r/wt/b", "/r/wt/a"]})
        # Never prune(yes=True): its confirm hook gates only merged-dirty ones.
        self.assertEqual(self.names(), ["remove", "remove"])
        self.assertEqual([c[1] for c in self.wt.calls], [("/r/wt/a",), ("/r/wt/b",)])
        self.assertEqual([c[2] for c in self.wt.calls],
                         [{"force": False, "home": HOME}] * 2)
        self.assertEqual((d["dry_run"], d["removed"], d["skipped"]), (False, ["/r/wt/a", "/r/wt/b"], []))
        self.assertIsInstance(d["freed_bytes"], int)

    def test_prune_yes_reports_what_swe_refused(self):
        self.wt = _fake_module({"remove": ("not_safe", "feat/a is open-pr; pass --force")})
        sys.modules["swe.worktrees"] = self.wt
        d = self.post("prune", {"all": True, "yes": True, "paths": ["/r/wt/a"]})
        self.assertEqual((d["removed"], d["skipped"]),
                         ([], [{"path": "/r/wt/a", "reason": "feat/a is open-pr; pass --force"}]))

    def test_prune_forces_only_a_ticked_merged_dirty_one_that_is_still_dirty_and_free(self):
        rows = {"/r/wt/dirty": _w("/r/wt/dirty", "d", "merged-dirty"),
                "/r/wt/locked": _w("/r/wt/locked", "l", "merged-dirty", locked=True),
                "/r/wt/held": _w("/r/wt/held", "h", "merged-dirty", hold="in use"),
                "/r/wt/open": _w("/r/wt/open", "o", "open-pr"),
                "/r/wt/clean": _w("/r/wt/clean", "c", "merged-clean")}
        shown = []
        self.wt.show = lambda path, **k: (shown.append(path), {"worktree": rows[path]})[1]
        body = {"all": True, "yes": True, "include": "merged-dirty", "paths": list(rows)}
        d = self.post("prune", body)
        forced = {c[1][0]: c[2]["force"] for c in self.wt.calls if c[0] == "remove"}
        self.assertEqual(forced, {"/r/wt/dirty": True, "/r/wt/open": False, "/r/wt/clean": False})
        self.assertEqual([x["path"] for x in d["skipped"]], ["/r/wt/locked", "/r/wt/held"])
        # Without the include box nothing is forced and show is never asked.
        self.wt.calls.clear()
        shown.clear()
        self.post("prune", {"all": True, "yes": True, "paths": ["/r/wt/dirty"]})
        self.assertEqual((shown, self.wt.calls[0][2]["force"]), ([], False))

    def test_slim_one_or_a_scope_never_both(self):
        self.post("slim", {"path": "/r/wt/a"})
        self.assertEqual(self.last(), ("slim", ("/r/wt/a",), {"yes": False, "home": HOME}))
        self.post("slim", {"all": True, "days": 7})
        self.assertEqual(self.last(), ("slim", (), {"yes": False, "all": True, "days": 7, "home": HOME}))
        self.post("slim", {"repo": "/r/main"})
        self.assertEqual(self.last()[2]["repo"], "/r/main")
        for body in ({}, {"path": "/r/wt/a", "all": True}, {"all": True, "days": -1}, {"all": True, "days": "3"}):
            with self.subTest(body=body), self.assertRaises(PageError):
                self.post("slim", body)

    def test_slim_yes_needs_the_previewed_folders_and_refuses_others(self):
        folder = "/r/wt/a/node_modules"
        with self.assertRaises(PageError):
            self.post("slim", {"all": True, "yes": True})
        self.assertEqual(self.wt.calls, [])
        d = self.post("slim", {"all": True, "yes": True, "paths": [folder]})
        self.assertEqual([c[2]["yes"] for c in self.wt.calls], [False, True])
        self.assertFalse(d["dry_run"])
        self.wt.calls.clear()
        with self.assertRaises(PageError) as cm:
            self.post("slim", {"path": "/r/wt/a", "yes": True, "paths": ["/r/wt/a/.venv"]})
        self.assertEqual((cm.exception.status, cm.exception.code), (409, "changed"))
        self.assertEqual([c[2]["yes"] for c in self.wt.calls], [False])

    def test_unknown_action_and_bad_body(self):
        with self.assertRaises(PageError) as cm:
            self.post("explode", {})
        self.assertEqual(cm.exception.status, 404)
        with self.assertRaises(PageError) as cm:
            self.post("remove", ["/r/wt/a"])
        self.assertEqual(cm.exception.status, 400)

    def test_one_write_at_a_time(self):
        self.assertTrue(page._WRITE_LOCK.acquire(blocking=False))
        try:
            with self.assertRaises(PageError) as cm:
                self.post("unlock", {"path": "/r/wt/a"})
            self.assertEqual((cm.exception.status, cm.exception.code), (409, "busy"))
        finally:
            page._WRITE_LOCK.release()
        self.assertEqual(self.wt.calls, [])


class TestErrors(unittest.TestCase):
    def run_error(self, code, call):
        wt = _fake_module({"remove": (code, f"the {code} message"), "list_worktrees": (code, "x"),
                           "show": (code, "x")})
        with patch.dict(sys.modules, {"swe.worktrees": wt}), self.assertRaises(PageError) as cm:
            call()
        return cm.exception

    def test_worktree_error_becomes_a_page_error_with_its_code_and_message(self):
        ctx = SimpleNamespace(home=None, machine={})
        want = {"bad_args": 400, "not_a_repo": 404, "not_found": 404, "not_safe": 409, "exists": 409,
                "busy": 409, "gh_unavailable": 503, "git_failed": 502, "bad_config": 500, "io_error": 500}
        for code, status in want.items():
            with self.subTest(code=code):
                exc = self.run_error(code, lambda: page.post(ctx, "remove", {"path": "/r/wt/a"}))
                self.assertEqual((exc.status, exc.code), (status, code))
                want_msg = "swe's worktree settings are invalid: the bad_config message" if code == "bad_config" else f"the {code} message"
                self.assertEqual(exc.message, want_msg)
        exc = self.run_error("not_found", lambda: page.get(ctx, {"worktree": "/r/gone"}))
        self.assertEqual((exc.status, exc.code), (404, "not_found"))

    def test_a_build_without_the_package_answers_503(self):
        with patch.dict(sys.modules, {"swe.worktrees": None}), self.assertRaises(PageError) as cm:
            page.get(SimpleNamespace(home=None, machine={}), {})
        self.assertEqual((cm.exception.status, cm.exception.code), (503, "unavailable"))

    def test_the_lock_is_released_after_an_error(self):
        wt = _fake_module({"unlock": ("not_found", "gone")})
        with patch.dict(sys.modules, {"swe.worktrees": wt}), self.assertRaises(PageError):
            page.post(SimpleNamespace(home=None, machine={}), "unlock", {"path": "/r/x"})
        self.assertTrue(page._WRITE_LOCK.acquire(blocking=False))
        page._WRITE_LOCK.release()


class TestRoute(HubBase):
    def setUp(self):
        super().setUp()
        self.wt = _fake_module({"remove": ("not_safe", "Not merged and clean. Pass force to delete it anyway.")})
        patcher = patch.dict(sys.modules, {"swe.worktrees": self.wt})
        patcher.start()
        self.addCleanup(patcher.stop)

    def origin(self):
        return {"Origin": f"http://127.0.0.1:{self.srv.port}"}

    def test_get_needs_a_device(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/worktrees")[0], 401)
        status, data = self.srv.request("GET", "/v1/app/worktrees", headers=self.auth())
        self.assertEqual(status, 200, data)
        body = json.loads(data)
        self.assertEqual(body["machine"], self.srv.state.machine["name"])
        self.assertEqual(len(body["worktrees"]), 2)
        status, data = self.srv.request(
            "GET", "/v1/app/worktrees?worktree=" + quote("/r/wt/a"), headers=self.auth())
        self.assertEqual(json.loads(data)["worktree"]["path"], "/r/wt/a")

    def test_post_needs_a_device_and_our_origin(self):
        self.assertEqual(self.srv.request("POST", "/v1/app/worktrees/unlock", body={"path": "/r/x"})[0], 401)
        status, data = self.srv.request(
            "POST", "/v1/app/worktrees/unlock", body={"path": "/r/x"},
            headers={**self.auth(), "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual([c[0] for c in self.wt.calls], [])
        status, data = self.srv.request(
            "POST", "/v1/app/worktrees/unlock", body={"path": "/r/x"},
            headers={**self.auth(), **self.origin()})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.wt.calls[-1][0], "unlock")

    def test_a_refusal_reaches_the_client_with_its_code(self):
        status, data = self.srv.request(
            "POST", "/v1/app/worktrees/remove", body={"path": "/r/wt/a"},
            headers={**self.auth(), **self.origin()})
        self.assertEqual(status, 409)
        err = json.loads(data)["error"]
        self.assertEqual(err["code"], "not_safe")
        self.assertIn("force", err["message"])
        self.assertEqual(self.wt.calls[-1][2], {"force": False, "delete_branch": False, "home": self.srv.home})


NODE = shutil.which("node")

# The page script runs with the app's globals stubbed, in its own function
# scope as the bundle does. DOM: the delete sheet's controls by id.
HARNESS = r"""
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const posts = [];
const els = {};
const el = (extra = {}) => Object.assign({ checked: false, disabled: false, hidden: false, dataset: {},
  textContent: '', value: '', focus() {}, onchange: null, onclick: null, onsubmit: null }, extra);
const doc = {
  getElementById: (id) => els[id] || null,
  querySelector: () => null,
  querySelectorAll: () => [],
};
let reply = { status: 200, data: { removed: '/r/wt/a', freed_bytes: 1 } };
const globals = {
  registerPage() {}, esc: (s) => String(s == null ? '' : s), safeHref: (u) => u || '',
  Fmt: { relative: () => 'now', tilde: (p) => p, markView: () => '', copyIcon: () => '', copyTick() {},
    shellWord: (s) => s, logo: (h) => `<img alt="${h}">`, harnessName: (h) => h },
  Sk: { bar: () => '', pill: () => '' }, pageHeader: () => '', icon: (n) => `<svg data-icon="${n}"></svg>`,
  api: async () => ({ status: 200, data: {} }),
  apiPost: async (path, body) => { posts.push([path, body]); return reply; },
  failText: () => 'failed', bodySheet: () => null, bindRefresh() {}, pageError() {}, bindSearch() {},
  renderGate() {}, routeGen: 0, location: { hash: '#/worktrees' }, document: doc,
};
const names = Object.keys(globals);
const run = new Function(...names, src + `
;return { wtBytes, wtGroups, wtMatches, wtOpenDelete, wtCanDelete, wtAllows, wtPage, wtShow: (f) => { wtShow = f; }, wtDone: (f) => { wtDone = f; }, wtRows: wtRowHtml };`);
const api = run(...names.map((n) => globals[n]));
module.exports = { api, posts, els, el, setReply: (r) => { reply = r; } };
"""


@unittest.skipUnless(NODE, "node is not installed")
class TestScript(unittest.TestCase):
    def node(self, body):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp) / "lib.js"
            lib.write_text(HARNESS)
            test = Path(tmp) / "t.js"
            test.write_text(f"const h = require({json.dumps(str(lib))});\n" + body)
            out = subprocess.run([NODE, str(test), str(SCRIPT)], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_bytes_groups_and_filters(self):
        ws = json.dumps([
            _w("/r/main", "main", "main", last_commit_at="2026-10-01T00:00:00Z"),
            _w("/r/wt/old", "feat/old", "merged-clean", last_commit_at="2026-09-01T00:00:00Z"),
            _w("/r/wt/new", "feat/new", "open-pr", pr={"number": 12, "url": "https://x/12", "state": "OPEN"},
               last_commit_at="2026-11-01T00:00:00Z", locked=True),
            _w("/o/main", "main", "main", repo="acme/other", main_checkout="/o/main"),
        ])
        out = self.node(f"""
const {{ api }} = h;
const ws = {ws};
const groups = api.wtGroups(ws).map((g) => [g.title, g.rows.map((r) => r.branch)]);
const m = (patch, w) => api.wtMatches(w, Object.assign({{ state: 'all', repo: '', q: '' }}, patch));
console.log(JSON.stringify({{
  bytes: [0, 1023, 1536, 10 * 1024 * 1024, 5 * 1024 ** 3, null].map(api.wtBytes),
  groups,
  merged: ws.filter((w) => m({{ state: 'merged-clean' }}, w)).map((w) => w.branch),
  locked: ws.filter((w) => m({{ state: 'locked' }}, w)).map((w) => w.branch),
  pr: ws.filter((w) => m({{ q: '#12' }}, w)).map((w) => w.branch),
  words: ws.filter((w) => m({{ q: 'feat old' }}, w)).map((w) => w.branch),
  repo: ws.filter((w) => m({{ repo: '/o/main' }}, w)).map((w) => w.path),
  canDelete: ws.map(api.wtCanDelete),
}}));
""")
        self.assertEqual(out["bytes"], ["0 B", "1023 B", "1.5 KB", "10 MB", "5.0 GB", "Not measured"])
        self.assertEqual(out["groups"], [["acme/app", ["main", "feat/new", "feat/old"]], ["acme/other", ["main"]]])
        self.assertEqual(out["merged"], ["feat/old"])
        self.assertEqual(out["locked"], ["feat/new"])
        self.assertEqual(out["pr"], ["feat/new"])
        self.assertEqual(out["words"], ["feat/old"])
        self.assertEqual(out["repo"], ["/o/main"])
        self.assertEqual(out["canDelete"], [False, True, True, False])

    def delete_flow(self, state, **extra):
        """Open the delete sheet for one worktree, tick what the test
        says, submit, and answer the body the page posted."""
        w = json.dumps(_w("/r/wt/a", "feat/a", state, **extra))
        return self.node(f"""
const {{ api, posts, els, el }} = h;
const w = {w};
const sheet = {{ open: true, dataset: {{}}, close() {{ this.open = false; }} }};
api.wtShow((inner) => ({{ sheet, token: '1' }}));
// wtLive compares the token the sheet holds.
sheet.dataset.token = '1';
Object.assign(els, {{
  'wt-d-form': el(), 'wt-d-go': el(), 'wt-cancel': el(), 'wt-sheet-error': el(),
  'wt-d-branch-line': el(), 'wt-d-branch': el(), 'wt-d-force': el(),
}});
let done = null;
api.wtDone((text) => {{ done = text; }});
api.wtOpenDelete(w);
const out = {{ disabledAtOpen: els['wt-d-go'].disabled }};
(async () => {{
  els['wt-d-force'].checked = {json.dumps(getattr(self, '_force', False))};
  els['wt-d-branch'].checked = {json.dumps(getattr(self, '_branch', False))};
  els['wt-d-force'].onchange(); els['wt-d-branch'].onchange();
  out.disabledAfterTicks = els['wt-d-go'].disabled;
  await els['wt-d-form'].onsubmit({{ preventDefault() {{}} }});
  out.posts = posts; out.done = done;
  console.log(JSON.stringify(out));
}})();
""")

    def test_delete_of_a_merged_clean_worktree_needs_no_force(self):
        self._force, self._branch = False, False
        out = self.delete_flow("merged-clean")
        self.assertFalse(out["disabledAtOpen"])
        self.assertEqual(out["posts"], [["/v1/app/worktrees/remove", {"path": "/r/wt/a"}]])
        self.assertIn("Deleted", out["done"])

    def test_delete_of_an_unmerged_worktree_is_blocked_until_force_is_ticked(self):
        for state in ("merged-dirty", "open-pr", "no-pr", "unknown"):
            with self.subTest(state=state):
                self._force, self._branch = False, False
                out = self.delete_flow(state)
                self.assertTrue(out["disabledAtOpen"])
                self.assertTrue(out["disabledAfterTicks"])
                self.assertEqual(out["posts"], [])
                self._force, self._branch = True, True
                out = self.delete_flow(state)
                self.assertFalse(out["disabledAfterTicks"])
                self.assertEqual(out["posts"], [["/v1/app/worktrees/remove",
                                                 {"path": "/r/wt/a", "force": True, "delete_branch": True}]])

    def test_main_protected_and_orphaned_have_no_delete(self):
        for state in ("main", "protected", "orphaned"):
            with self.subTest(state=state):
                self._force, self._branch = True, False
                out = self.node(f"""
const {{ api, posts, els, el }} = h;
const w = {json.dumps(_w("/r/main", "main", state))};
let shown = false;
api.wtShow(() => {{ shown = true; return {{ sheet: {{ open: true, dataset: {{}} }}, token: '1' }}; }});
api.wtOpenDelete(w);
console.log(JSON.stringify({{ shown, rowHasDelete: api.wtRows(w).includes('data-wt="delete"'), posts }}));
""")
                self.assertEqual((out["shown"], out["rowHasDelete"], out["posts"]), (False, False, []))

    def test_rows_show_labels_orphan_note_hold_and_unmeasured_sizes(self):
        ws = json.dumps([
            _w("/r/o", None, "orphaned", head="d52bcc353aa1", harness="codex"),
            _w("/r/h", "feat/h", "merged-clean", hold="locked", harness="swe-pr", size_bytes=None),
            _w("/r/c", "feat/c", "no-pr", harness="claude"),
            _w("/r/l", "feat/l", "merged-clean", locked=True),
        ])
        out = self.node(f"""
const {{ api }} = h;
const rows = {ws}.map(api.wtRows);
console.log(JSON.stringify(rows));
""")
        orphan, hold, claude, locked = out
        self.assertNotIn('data-wt="delete"', orphan)
        self.assertIn("git worktree prune", orphan)
        self.assertIn('data-wt-copy="git -C /r/main worktree prune"', orphan)
        self.assertIn(">Codex<", orphan)
        self.assertIn("Clean up keeps it: locked", hold)
        self.assertIn("Not measured", hold)
        self.assertIn(">swe PR checkout<", hold)
        self.assertIn('data-icon="worktrees"', hold)
        self.assertIn(">Claude Code<", claude)
        self.assertIn('<img alt="claude">', claude)
        self.assertNotIn(">CLI<", claude)
        self.assertIn('data-wt="delete" data-path="/r/l" disabled title="Unlock first"', locked)

    def test_actions_mirror_what_the_cli_accepts(self):
        out = self.node("""
const { api } = h;
const actions = ['lock', 'unlock', 'move', 'slim', 'delete'];
const table = {};
for (const [state, locked] of [['main', false], ['protected', false], ['orphaned', false], ['merged-clean', false],
    ['no-pr', true]]) {
  table[state + (locked ? '-locked' : '')] = actions.filter((a) => api.wtAllows({ state, locked }, a));
}
console.log(JSON.stringify(table));
""")
        self.assertEqual(out, {
            "main": [], "protected": [], "orphaned": [],
            "merged-clean": ["lock", "move", "slim", "delete"],
            "no-pr-locked": ["unlock", "move", "slim", "delete"],
        })
        rows = self.node(f"""
const {{ api }} = h;
console.log(JSON.stringify([{json.dumps(_w("/r/main", "main", "main", rebuildable_bytes=5))},
  {json.dumps(_w("/r/p", "p", "protected", rebuildable_bytes=5))}].map(api.wtRows)));
""")
        for html in rows:
            self.assertNotIn('data-wt="slim"', html)
            self.assertNotIn('data-wt="delete"', html)

    def test_a_locked_worktree_cannot_be_deleted_from_the_sheet(self):
        self._force, self._branch = False, False
        out = self.delete_flow("merged-clean", locked=True, lock_reason="agent")
        self.assertTrue(out["disabledAtOpen"])
        self.assertEqual(out["posts"], [])


if __name__ == "__main__":
    unittest.main()
