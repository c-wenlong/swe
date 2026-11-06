"""`swe find -i`: browse a resource instead of printing it.

The printed listing tells you a plugin has 11 skills and then gives you
no way to see which. Nothing else in swe answers that either, which is
the gap this closes.

Below the top level a plugin is just a directory, so the browser walks
the real filesystem and only the roots differ per resource. That is why
descending into skills/, commands/ and agents/ needs no plugin-specific
code, and keeps working when a harness invents another one.
"""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from swe.console import strip_ansi
from swe.find.entries import HIDE_DIRS, Entry
from tests._tempdir import temp_dir


class EntryContractTest(unittest.TestCase):
    def test_a_grouping_row_descends_without_a_path(self):
        """Marketplaces have no directory of their own, but you still
        navigate through them."""
        e = Entry("dv", children=[Entry("cloudflare")])
        self.assertIsNone(e.path)
        self.assertTrue(e.can_descend)

    def test_a_childless_pathless_row_is_a_dead_end(self):
        self.assertFalse(Entry("gone").can_descend)

    def test_a_directory_descends(self):
        d = temp_dir(self)
        self.assertTrue(Entry("d", d).can_descend)

    def test_a_file_does_not(self):
        d = temp_dir(self)
        f = d / "SKILL.md"
        f.write_text("x")
        self.assertFalse(Entry("f", f).can_descend)

    def test_an_unreadable_path_does_not_raise(self):
        e = Entry("x", Path("/nope/does/not/exist"))
        self.assertFalse(e.is_dir)


class BrowserSafetyTest(unittest.TestCase):
    def test_it_returns_rather_than_hanging_without_a_terminal(self):
        """Piping `swe find plugins -i` must not block a script forever."""
        from swe.find.browser import browse

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = browse([Entry("a")], title="T")
        self.assertEqual(code, 0)
        self.assertIn("not a terminal", strip_ansi(buf.getvalue()))

    def test_empty_roots_return_cleanly(self):
        from swe.find.browser import browse

        with redirect_stdout(io.StringIO()):
            self.assertEqual(browse([], title="T"), 0)

    def test_noise_directories_are_hidden(self):
        """A plugin cache keeps lock and marker dirs beside real content;
        showing them buries what the reader opened the browser to find."""
        from swe.find.browser import _visible_children

        for name in (".in_use", ".git", "__pycache__", "node_modules"):
            self.assertIn(name, HIDE_DIRS)
        d = temp_dir(self)
        for name in (".in_use", ".git", "__pycache__", "node_modules", "skills"):
            (d / name).mkdir()
        (d / "README.md").write_text("x")
        self.assertEqual([p.name for p in _visible_children(d)],
                         ["skills", "README.md"])

    def test_entry_labels_and_details_cannot_drive_the_terminal(self):
        """A directory can be named anything; the panes must not let an
        escape payload through while the browser owns the screen."""
        from swe.find.browser import _left_cell

        entry = Entry(
            "x\x1b]8;;https://evil\x07y\x1b[2J\x07",
            detail="\x1b]52;c;AAAA\x07detail",
        )
        cell = _left_cell(entry, 40, active=False)
        self.assertNotIn("\x1b]8", cell)
        self.assertNotIn("\x07", cell)
        self.assertNotIn("\x1b[2J", cell)
        active = _left_cell(entry, 40, active=True)
        self.assertNotIn("\x1b]8", active)
        self.assertNotIn("\x07", active)

    def test_a_file_preview_cannot_smuggle_escapes(self):
        """The right pane prints file bytes; a crafted SKILL.md must not
        carry an OSC link or a clipboard write into the browser."""
        from swe.find.browser import _file_preview

        d = temp_dir(self)
        f = d / "SKILL.md"
        f.write_text(
            "ok line\n"
            "evil \x1b]8;;https://evil\x07link\x1b]8;;\x07\n"
            "clip \x1b]52;c;AAAA\x07\n"
            "clear \x1b[2J \x07\n"
        )
        rows = _file_preview(f, 10)
        for row in rows:
            self.assertNotIn("\x1b", row)
            self.assertNotIn("\x07", row)
        self.assertEqual(rows[0], "ok line")
        self.assertIn("evil link", rows[1])

    def test_preview_tabs_expand_before_sanitizing(self):
        """Sanitize maps \t to one space, so it has to run after
        expandtabs or indented files lose their shape."""
        from swe.find.browser import _file_preview

        d = temp_dir(self)
        f = d / "x.md"
        f.write_text("one\n\tdeep\n\t\tdeeper\n")
        rows = _file_preview(f, 10)
        self.assertEqual(rows[1], "    deep")
        self.assertEqual(rows[2], "        deeper")

    def test_child_preview_labels_are_sanitized(self):
        from swe.find.browser import _preview

        e = Entry("p", children=[Entry("kid\x1b]52;c;AAAA\x07")])
        rows = _preview(e, 10)
        for row in rows:
            self.assertNotIn("\x1b", row)
            self.assertNotIn("\x07", row)


class BrowseDispatchTest(unittest.TestCase):
    def _run(self, argv):
        from swe.find.commands import cmd_find

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cmd_find(argv)
        return code, strip_ansi(buf.getvalue())

    # The roots getters walk the real HOME, and cmd_find short-circuits to
    # "nothing to browse" before reaching the browser when they come back
    # empty. Tests run against a throwaway HOME, which has no plugins,
    # skills or AGENTS.md anywhere, so dispatch has to be tested over a
    # stubbed tree or it only passes on the author's own machine.
    ROOT_GETTERS = {"plugins": "plugins_roots", "skills": "skills_roots",
                    "amd": "agents_roots"}

    def test_each_browsable_topic_reaches_the_browser(self):
        for topic, getter in self.ROOT_GETTERS.items():
            with mock.patch(f"swe.find.roots.{getter}",
                            return_value=[Entry("x")]), \
                 mock.patch("swe.find.browser.browse", return_value=0) as m:
                code, _ = self._run([topic, "-i"])
            self.assertEqual(code, 0, topic)
            m.assert_called_once()

    def test_the_long_flag_works_too(self):
        with mock.patch("swe.find.roots.plugins_roots",
                        return_value=[Entry("x")]), \
             mock.patch("swe.find.browser.browse", return_value=0) as m:
            self._run(["plugins", "--interactive"])
        m.assert_called_once()

    def test_scope_is_passed_through(self):
        with mock.patch("swe.find.roots.plugins_roots",
                        return_value=[Entry("x")]) as roots, \
             mock.patch("swe.find.browser.browse", return_value=0):
            self._run(["plugins", "-i", "--scope=all"])
        self.assertEqual(roots.call_args.kwargs.get("scope"), "all")

    def test_a_topic_with_no_tree_is_refused(self):
        """mcps has no filesystem tree to walk, and guessing another
        resource would land you somewhere you did not ask for."""
        code, out = self._run(["mcps", "-i"])
        self.assertEqual(code, 1)
        self.assertIn("Cannot browse", out)

    def test_nothing_to_browse_says_so_rather_than_opening_blank(self):
        with mock.patch("swe.find.roots.plugins_roots", return_value=[]):
            code, out = self._run(["plugins", "-i"])
        self.assertEqual(code, 0)
        self.assertIn("nothing to browse", out)


class PluginRootsTest(unittest.TestCase):
    """The root getters never raise on a bad home or scope.

    How plugins nest (harness, marketplace, plugin) and which carry a
    directory is pinned against fixed plugin lists in
    tests/test_find_roots.py::PluginsRootsTest.
    """

    def test_every_adapter_survives_a_bogus_home(self):
        from swe.find import roots

        # The registry lives under the real home, so it is pinned empty:
        # with nothing registered and no folders, there is nothing to list.
        with mock.patch.object(roots, "load_registry_if_present",
                               return_value={}):
            for fn in (roots.agents_roots, roots.skills_roots,
                       roots.plugins_roots):
                self.assertEqual(fn(home=Path("/nope/nowhere")), [],
                                 fn.__name__)

    def test_an_unknown_scope_does_not_raise(self):
        from swe.find import roots

        home = temp_dir(self)
        for fn in (roots.agents_roots, roots.skills_roots, roots.plugins_roots):
            self.assertIsInstance(fn(home=home, scope="nonsense"), list)


class ThreePaneLayoutTest(unittest.TestCase):
    """Parent, current, preview.

    Two panes lost the sense of where back would take you, so left and
    right felt like jumps rather than movement. The parent pane is only
    for orientation, which is why it is dimmed and why it gives up its
    width first when the window is tight.
    """

    def _widths(self, width, ratio):
        from swe.find.browser import _pane_widths

        return _pane_widths(width, ratio)

    def test_the_three_panes_fit_the_row(self):
        """Assert the invariant, not the overhead arithmetic: the panes
        plus the separators between them must not exceed the window."""
        from swe.find.browser import MIN_PANE

        separators = 7          # two " │ " plus the leading margin
        for width in (40, 80, 120, 200):
            p, c_, v = self._widths(width, (2, 3, 5))
            budget = max(3 * MIN_PANE + separators, width)
            self.assertLessEqual(p + c_ + v + separators, budget, width)

    def test_no_pane_collapses_to_nothing(self):
        from swe.find.browser import MIN_PANE

        for width in (20, 40, 200):
            for ratio in ((2, 3, 5), (1, 1, 20), (20, 1, 1)):
                for w in self._widths(width, ratio):
                    self.assertGreaterEqual(w, MIN_PANE, (width, ratio))

    def test_a_bigger_weight_gets_a_wider_pane(self):
        narrow = self._widths(200, (1, 3, 5))[0]
        wide = self._widths(200, (6, 3, 5))[0]
        self.assertGreater(wide, narrow)

    def test_weights_survive_a_window_resize(self):
        """Relative widths, not fixed columns, so the split a reader
        chose still means the same thing in a different window."""
        small = self._widths(100, (2, 3, 5))
        large = self._widths(200, (2, 3, 5))
        self.assertLess(small[2], large[2])

    def test_a_zero_ratio_does_not_divide_by_zero(self):
        self.assertTrue(all(w > 0 for w in self._widths(120, (0, 0, 0))))

    def test_the_resize_keys_are_bound(self):
        import os

        from swe.find.browser import _read_key

        for seq, want in ((b"[", "wider_parent"), (b"]", "narrower_parent"),
                          (b"{", "wider_preview"), (b"}", "narrower_preview")):
            r, w = os.pipe()
            os.write(w, seq)
            os.close(w)
            self.assertEqual(_read_key(r), want, seq)
            os.close(r)

    def test_the_footer_mentions_resizing(self):
        from swe.find.browser import _render

        buf = io.StringIO()
        with redirect_stdout(buf):
            _render([], 0, [Entry("a")], 0, [], "T", "crumb", 0, 10, 140,
                    (3, 9, 8))
        footer = strip_ansi(buf.getvalue()).rstrip().splitlines()[-1]
        self.assertIn("[ ] { } resize", footer)


class PreviewTest(unittest.TestCase):
    """Arrowing onto a file shows the file, not the next folder."""

    def setUp(self):
        self.d = temp_dir(self)

    def test_a_directory_previews_its_children(self):
        from swe.find.browser import _preview

        (self.d / "skills").mkdir()
        (self.d / "commands").mkdir()
        lines = _preview(Entry("p", self.d), limit=20)
        self.assertTrue(any("skills" in ln for ln in lines))

    def test_a_text_file_previews_its_contents(self):
        from swe.find.browser import _preview

        f = self.d / "SKILL.md"
        f.write_text("---\nname: wrangler\n---\n\n# Wrangler\n\nDeploy Workers.\n")
        lines = _preview(Entry("f", f), limit=20)
        self.assertTrue(any("Wrangler" in ln for ln in lines),
                        f"file contents not shown: {lines[:3]}")

    def test_an_oversized_file_is_described_not_dumped(self):
        from swe.find.browser import MAX_PREVIEW_BYTES, _preview

        f = self.d / "big.md"
        f.write_bytes(b"x" * (MAX_PREVIEW_BYTES + 1))
        lines = _preview(Entry("f", f), limit=20)
        self.assertLess(len(lines), 20)

    def test_an_unreadable_path_does_not_raise(self):
        from swe.find.browser import _preview

        self.assertEqual(_preview(Entry("x", Path("/nope/nope")), 10),
                         ["(unreadable)"])

    def test_a_grouping_row_previews_its_children(self):
        from swe.find.browser import _preview

        e = Entry("dv", children=[Entry("cloudflare"), Entry("blaxel")])
        lines = _preview(e, limit=10)
        self.assertTrue(any("cloudflare" in ln for ln in lines))


class SelectionBarTest(unittest.TestCase):
    """A filled bar, not coloured text.

    A foreground-only highlight is easy to lose in a pane you are not
    driving, and impossible to find at a glance across three of them.
    """

    def test_no_glyph_precedes_the_name(self):
        """The bar says which row is selected and colour says which rows
        descend, so a triangle on every line only ate the name column."""
        from swe.console import strip_ansi
        from swe.find.browser import _left_cell

        for active in (True, False):
            plain = strip_ansi(_left_cell(Entry("dv"), 20, active=active))
            self.assertNotIn("▸", plain)
            self.assertNotIn("·", plain)
            self.assertTrue(plain.lstrip().startswith("dv"), plain)

    def test_the_selected_row_fills_its_whole_cell(self):
        from swe.console import visible_len
        from swe.find.browser import _left_cell

        cell = _left_cell(Entry("x", detail="1 item"), 30, active=True)
        self.assertIn("\x1b[48;5;", cell, "no background set")
        self.assertEqual(visible_len(cell), 30, "bar does not span the cell")

    def test_an_unselected_row_has_no_bar(self):
        from swe.find.browser import _left_cell

        self.assertNotIn("48;5;", _left_cell(Entry("x"), 30, active=False))

    def test_the_parent_uses_a_quieter_bar(self):
        """Two bars of the same colour would compete; the parent is
        context, not the pane being driven."""
        from swe.find.browser import _left_cell

        live = _left_cell(Entry("x"), 30, active=True, muted=False)
        parent = _left_cell(Entry("x"), 30, active=True, muted=True)
        self.assertNotEqual(live, parent)
        self.assertIn("48;5;", parent)

    def test_the_bar_closes_its_escape(self):
        """An unterminated background bleeds into the rest of the row."""
        from swe.find.browser import _left_cell

        self.assertTrue(_left_cell(Entry("x"), 20, active=True).endswith("\x1b[0m"))

    def test_a_long_label_still_fits_the_bar(self):
        from swe.console import visible_len
        from swe.find.browser import _left_cell

        cell = _left_cell(Entry("z" * 90, detail="d" * 40), 24, active=True)
        self.assertEqual(visible_len(cell), 24)


class ResizeTest(unittest.TestCase):
    """The browser follows the window while it is open."""

    def test_the_panes_fill_the_window_height(self):
        """Sizing rows to the longest pane left the dividers stopping
        partway down a tall terminal, so the layout read as a small box
        floating at the top rather than a browser using the screen."""
        import io
        from contextlib import redirect_stdout

        from swe.find.browser import _render

        for height in (6, 20, 38):
            buf = io.StringIO()
            with redirect_stdout(buf):
                _render([], 0, [Entry("a"), Entry("b")], 0, ["x"], "T",
                        "crumb", 0, height, 140, (2, 3, 5))
            rows = [ln for ln in buf.getvalue().split("\r\n") if "│" in ln]
            self.assertEqual(len(rows), height, height)

    def test_every_row_spans_the_full_width(self):
        """The preview pane used to stop wherever its text ran out, so a
        wide window looked unused even though the dividers were placed
        correctly."""
        import io
        from contextlib import redirect_stdout

        from swe.console import visible_len
        from swe.find.browser import _render

        buf = io.StringIO()
        with redirect_stdout(buf):
            _render([], 0, [Entry("a")], 0, ["short"], "T", "crumb", 0,
                    8, 140, (2, 3, 5))
        rows = [ln for ln in buf.getvalue().split("\r\n") if "│" in ln]
        widths = {visible_len(r) for r in rows}
        self.assertEqual(len(widths), 1, f"ragged right edge: {sorted(widths)}")

    def test_the_returned_count_is_the_lines_written(self):
        """The count is how far the next redraw rewinds. It stayed at the
        old two-line header's figure after the header became one line, so
        every redraw climbed one line and ate whatever sat above it."""
        import io
        from contextlib import redirect_stdout

        from swe.find.browser import _render

        many = [Entry(str(i)) for i in range(30)]
        for entries, ratio in (([Entry("a")], (3, 9, 8)),     # no range line
                               (many, (3, 9, 8)),             # range line
                               ([Entry("a")], (2, 3, 5))):    # ratio note
            buf = io.StringIO()
            with redirect_stdout(buf):
                drawn = _render([], 0, entries, 0, ["x"], "T", "crumb", 0,
                                10, 140, ratio)
            self.assertEqual(drawn, buf.getvalue().count("\r\n"))

    def test_widths_are_recomputed_from_the_ratio(self):
        from swe.find.browser import _pane_widths

        self.assertNotEqual(_pane_widths(80, (2, 3, 5)),
                            _pane_widths(160, (2, 3, 5)))


class SupportedTerminalTest(unittest.TestCase):
    """Raw mode needs a terminal on both ends and the termios modules."""

    def _supported(self, stdin_tty, stdout_tty):
        from swe.find.browser import _supported

        stdin, stdout = mock.Mock(), mock.Mock()
        stdin.isatty.return_value = stdin_tty
        stdout.isatty.return_value = stdout_tty
        with mock.patch("sys.stdin", stdin), mock.patch("sys.stdout", stdout):
            return _supported()

    def test_both_ends_must_be_terminals(self):
        self.assertTrue(self._supported(True, True))
        # `swe find -i | less` and `swe find -i < file` both refuse.
        self.assertFalse(self._supported(True, False))
        self.assertFalse(self._supported(False, True))

    def test_a_platform_without_termios_is_not_supported(self):
        import sys

        for missing in ("termios", "tty"):
            with mock.patch.dict(sys.modules, {missing: None}):
                self.assertFalse(self._supported(True, True), missing)

    def test_empty_roots_on_a_terminal_say_so_without_raw_mode(self):
        """The no-terminal check runs first, so the existing empty-roots
        test never reached this branch."""
        from swe.find.browser import browse

        buf = io.StringIO()
        with mock.patch("swe.find.browser._supported", return_value=True), \
             mock.patch("swe.keys.raw_terminal") as raw, redirect_stdout(buf):
            self.assertEqual(browse([], title="T"), 0)
        self.assertIn("nothing to browse", strip_ansi(buf.getvalue()))
        self.assertNotIn("not a terminal", strip_ansi(buf.getvalue()))
        raw.assert_not_called()


class ListingTest(unittest.TestCase):
    """What a level shows for a real directory, including the parts of it
    that cannot be read."""

    def setUp(self):
        self.d = temp_dir(self)

    def test_sizes_step_up_through_the_units(self):
        from swe.find.browser import _human_size

        self.assertEqual(_human_size(0), "0 B")
        self.assertEqual(_human_size(1023), "1023 B")
        self.assertEqual(_human_size(1024), "1.0 KB")
        self.assertEqual(_human_size(1536 * 1024), "1.5 MB")
        self.assertEqual(_human_size(3 * 1024 ** 3), "3.0 GB")
        # Nothing past GB: a terabyte still reads in GB.
        self.assertEqual(_human_size(2 * 1024 ** 4), "2048.0 GB")

    def test_a_missing_directory_is_unreadable_not_empty(self):
        from swe.find.browser import _child_entries, _visible_children

        gone = self.d / "gone"
        self.assertIsNone(_visible_children(gone))
        self.assertEqual(_visible_children(self.d), [])
        self.assertEqual(_child_entries(gone), [])

    def test_folders_come_first_then_names_ignoring_case(self):
        from swe.find.browser import _visible_children

        (self.d / "b.md").write_text("x")
        (self.d / "A.md").write_text("x")
        (self.d / "zeta").mkdir()
        (self.d / "Beta").mkdir()
        self.assertEqual([p.name for p in _visible_children(self.d)],
                         ["Beta", "zeta", "A.md", "b.md"])

    def test_each_child_carries_a_count_or_a_size(self):
        from swe.find.browser import _child_entries

        one = self.d / "one"
        one.mkdir()
        (one / "SKILL.md").write_text("x")
        two = self.d / "two"
        two.mkdir()
        (two / "a").write_text("x")
        (two / "b").write_text("x")
        (two / ".hidden").write_text("x")      # not counted
        (self.d / "empty").mkdir()
        (self.d / "f.md").write_bytes(b"x" * 2048)
        details = {e.label: e.detail for e in _child_entries(self.d)}
        self.assertEqual(details, {"empty": "0 items", "one": "1 item",
                                   "two": "2 items", "f.md": "2.0 KB"})

    def test_a_dangling_link_is_listed_as_unreadable(self):
        from swe.find.browser import _child_entries

        (self.d / "dead").symlink_to(self.d / "nowhere")
        [entry] = _child_entries(self.d)
        self.assertEqual((entry.label, entry.detail), ("dead", "unreadable"))

    def test_a_folder_that_refuses_listing_is_marked_unreadable(self):
        import os

        from swe.find.browser import _child_entries, _preview

        if os.geteuid() == 0:
            self.skipTest("root reads a mode-000 folder anyway")
        locked = self.d / "locked"
        locked.mkdir()
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        [entry] = _child_entries(self.d)
        self.assertEqual(entry.detail, "unreadable")
        self.assertEqual(_preview(entry, 10), ["(unreadable)"])

    def test_descend_follows_children_then_a_real_folder_and_nothing_else(self):
        from swe.find.browser import _descend

        kids = [Entry("a"), Entry("b")]
        self.assertEqual(_descend(Entry("g", children=kids)), kids)
        self.assertEqual(_descend(Entry("none")), [])
        f = self.d / "f.md"
        f.write_text("x")
        self.assertEqual(_descend(Entry("f", f)), [])
        (self.d / "sub").mkdir()
        self.assertEqual([e.label for e in _descend(Entry("d", self.d))],
                         ["sub", "f.md"])

    def test_a_path_that_raises_on_stat_opens_and_previews_nothing(self):
        """Path.is_dir lets PermissionError through on older Pythons, which
        would otherwise end the browse with a traceback."""
        from swe.find.browser import _descend, _preview, _preview_language

        entry = Entry("x", self.d / "x.md")
        with mock.patch.object(Path, "is_dir", side_effect=PermissionError):
            self.assertEqual(_descend(entry), [])
            self.assertEqual(_preview(entry, 5), ["(unreadable)"])
            self.assertEqual(_preview_language(entry), "")


class PreviewEdgeTest(unittest.TestCase):
    def setUp(self):
        self.d = temp_dir(self)

    def test_a_file_that_cannot_be_opened_is_unreadable(self):
        from swe.find.browser import _file_preview

        f = self.d / "SKILL.md"
        f.write_text("x")
        with mock.patch.object(Path, "open", side_effect=PermissionError):
            self.assertEqual(_file_preview(f, 5), ["(unreadable)"])
        self.assertEqual(_file_preview(self.d / "missing.md", 5), ["(unreadable)"])

    def test_an_empty_file_says_so(self):
        from swe.find.browser import _file_preview

        f = self.d / "empty.md"
        f.write_text("")
        self.assertEqual(_file_preview(f, 5), ["(empty file)"])

    def test_a_non_text_file_is_described_by_name_and_size(self):
        from swe.find.browser import _file_preview

        f = self.d / "logo.png"
        f.write_bytes(b"\x89PNG" + b"\x00" * 1020)
        self.assertEqual(_file_preview(f, 5),
                         ["logo.png", "1.0 KB", "binary or too large"])

    def test_only_the_first_lines_are_read(self):
        from swe.find.browser import _file_preview

        f = self.d / "long.md"
        f.write_text("".join(f"line {i}\n" for i in range(1000)))
        rows = _file_preview(f, 3)
        self.assertEqual(rows, ["line 0", "line 1", "line 2"])

    def test_an_empty_folder_and_an_empty_grouping_row(self):
        from swe.find.browser import _preview

        self.assertEqual(_preview(Entry("d", self.d), 10), ["(empty)"])
        self.assertEqual(_preview(Entry("g"), 10), ["(empty)"])

    def test_a_long_folder_fills_the_pane_and_counts_the_rest(self):
        from swe.find.browser import _preview

        for i in range(25):
            (self.d / f"f{i:02}.md").write_text("x")
        (self.d / "sub").mkdir()
        rows = _preview(Entry("d", self.d), 10)
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0], "sub/")
        self.assertEqual(rows[-1], "... 17 more")
        # Exactly the limit is shown whole, with no "more" line.
        rows = _preview(Entry("d", self.d), 26)
        self.assertEqual(len(rows), 26)
        self.assertFalse(any(r.startswith("...") for r in rows))

    def test_a_grouping_row_marks_rows_that_descend_and_stops_at_the_limit(self):
        from swe.find.browser import _preview

        e = Entry("g", children=[Entry("leaf"), Entry("dir", children=[Entry("x")])]
                  + [Entry(f"k{i}") for i in range(20)])
        rows = _preview(e, 5)
        self.assertEqual(rows, ["leaf", "dir/", "k0", "k1", "k2"])

    def test_the_language_is_only_set_for_a_file(self):
        from swe.find.browser import _preview_language
        from swe.find.highlight import language_for

        f = self.d / "x.py"
        f.write_text("x = 1\n")
        self.assertEqual(_preview_language(Entry("f", f)), language_for(f))
        self.assertNotEqual(language_for(f), "")
        self.assertEqual(_preview_language(Entry("d", self.d)), "")
        self.assertEqual(_preview_language(Entry("g", children=[Entry("a")])), "")
        self.assertEqual(_preview_language(Entry("none")), "")


class RenderEdgeTest(unittest.TestCase):
    def _render(self, entries, cursor=0, preview=(), height=10, width=140):
        from swe.find.browser import _render

        buf = io.StringIO()
        with redirect_stdout(buf):
            drawn = _render([], 0, entries, cursor, list(preview), "T", "crumb",
                            0, height, width, (3, 9, 8))
        return drawn, buf.getvalue()

    def test_an_empty_level_says_so_once(self):
        _, out = self._render([])
        self.assertEqual(strip_ansi(out).count("(empty)"), 1)

    def test_a_long_level_scrolls_to_keep_the_cursor_on_screen(self):
        from swe.console import strip_ansi as plain

        entries = [Entry(f"row{i:02}") for i in range(30)]
        _, out = self._render(entries, cursor=29, height=10)
        text = plain(out)
        self.assertIn("row29", text)
        self.assertIn("row20", text)
        self.assertNotIn("row19", text)
        self.assertIn("21–30 of 30", text)

    def test_a_long_preview_line_is_cut_to_the_pane(self):
        from swe.console import visible_len

        _, out = self._render([Entry("a")], preview=["x" * 500, "short"],
                              width=120)
        rows = [ln for ln in out.split("\r\n") if "│" in ln]
        widths = {visible_len(r) for r in rows}
        self.assertEqual(len(widths), 1, sorted(widths))
        self.assertLessEqual(widths.pop(), 120)

    def test_a_wide_middle_weight_gives_back_the_overshoot_from_the_middle(self):
        from swe.find.browser import MIN_PANE, _pane_widths

        # At 40 columns the floors overshoot; the widest pane pays for it.
        parent, current, preview = _pane_widths(40, (1, 20, 1))
        self.assertEqual((parent, preview), (MIN_PANE, MIN_PANE))
        self.assertEqual(parent + current + preview, 40 - 7)


class BrowserMouseTest(unittest.TestCase):
    """The wheel scrolls the pane; it never quits the browser.

    The older reader took a fixed two bytes after the Esc, so one scroll of
    the wheel read as an escape and the rest of the report came back as
    loose letters, two of which resize a pane. The reader now goes through
    swe.keys.
    """

    def _drive(self, key_names, roots=None, select_side_effect=None,
               sizes=None):
        import os

        from swe.find import browser as browser_mod
        from swe.find.browser import browse

        out = io.StringIO()
        stdin = mock.Mock()
        stdin.fileno.return_value = 0
        seen = []
        renders = [0]
        real_render = browser_mod._render
        # The labels of the level on screen at each draw, so a test can
        # tell which level a key left the browser on.
        self.levels = []

        self.widths = []

        def count_render(*a, **k):
            renders[0] += 1
            self.levels.append([e.label for e in a[2]])
            self.widths.append(a[9])
            return real_render(*a, **k)

        sel_kwargs = ({"side_effect": select_side_effect}
                      if select_side_effect is not None
                      else {"return_value": ([0], [], [])})
        size_kwargs = ({"side_effect": sizes}
                       if sizes is not None
                       else {"side_effect": OSError("no tty")})
        with mock.patch("swe.find.browser._supported", return_value=True), \
             mock.patch("sys.stdout", out), \
             mock.patch("sys.stdin", stdin), \
             mock.patch("termios.tcgetattr", return_value=["saved"]), \
             mock.patch("termios.tcsetattr",
                        side_effect=lambda *a: seen.append(out.getvalue())), \
             mock.patch("tty.setraw"), \
             mock.patch("select.select", **sel_kwargs), \
             mock.patch.object(os, "get_terminal_size", **size_kwargs), \
             mock.patch.object(browser_mod, "_render",
                               side_effect=count_render), \
             mock.patch("swe.find.browser._read_key", side_effect=key_names):
            code = browse(roots or [Entry("a"), Entry("b")], title="T")
        return code, out.getvalue(), seen, renders[0]

    def test_a_wheel_report_is_a_move_rather_than_a_cancel(self):
        import os

        from swe.find.browser import _read_key

        for report, want in ((b"\x1b[<64;10;5M", "up"),
                             (b"\x1b[<65;10;5M", "down")):
            r, w = os.pipe()
            os.write(w, report)
            os.close(w)
            self.assertEqual(_read_key(r), want, report)
            os.close(r)

        # A notch on either side of a quit still leaves through the quit.
        code, _, _, _ = self._drive(["down", "up", "cancel"])
        self.assertEqual(code, 0)

    def test_a_bare_esc_quits(self):
        # It was ignored, so the next read came round with no key left.
        code, _, _, renders = self._drive(["escape"])
        self.assertEqual((code, renders), (0, 1))

    def test_esc_alone_and_an_arrow_read_as_different_keys(self):
        import os

        from swe.find.browser import _read_key

        # The writer stays open, as a terminal does: a lone Esc is told
        # from an arrow by nothing else being queued behind it.
        for raw, want in ((b"\x1b", "escape"), (b"\x1b[A", "up"),
                          (b"\x1bOB", "down"), (b"\x1b[C", "open")):
            r, w = os.pipe()
            os.write(w, raw)
            self.assertEqual(_read_key(r), want, raw)
            os.close(w)
            os.close(r)

    def test_tracking_is_on_for_the_browse_and_off_before_termios(self):
        from swe import keys

        code, out, seen, _ = self._drive(["cancel"])
        self.assertEqual(code, 0)
        self.assertIn(keys.MOUSE_ON, out)
        self.assertEqual(len(seen), 1)
        # A shell left reporting the wheel spews escape sequences at the
        # prompt, so the off switch has to precede the termios restore.
        self.assertIn(keys.MOUSE_OFF, seen[0])

    def test_every_dispatch_key_runs(self):
        """Move, jump, resize and descend/back all through the one loop."""
        with tempfile.TemporaryDirectory() as td:
            sub = Path(td) / "child"
            sub.mkdir()
            (sub / "leaf.txt").write_text("x")
            roots = [Entry("dir", Path(td), "one entry"), Entry("b")]
            code, _, _, _ = self._drive(
                ["down", "up", "bottom", "top",
                 "wider_parent", "narrower_parent",
                 "wider_preview", "narrower_preview",
                 "back",          # top level: no-op, stays
                 "open",          # descends into dir
                 "back",          # pops the level
                 "cancel"],
                roots=roots,
            )
        self.assertEqual(code, 0)
        visited = [lv for i, lv in enumerate(self.levels)
                   if i == 0 or lv != self.levels[i - 1]]
        self.assertEqual(visited, [["dir", "b"], ["child"], ["dir", "b"]])

    def test_an_open_on_a_dead_end_stays_put(self):
        """Entries that cannot descend leave the level stack alone."""
        code, _, _, _ = self._drive(["open", "cancel"],
                                 roots=[Entry("leaf", None, "no path")])
        self.assertEqual(code, 0)
        self.assertEqual(self.levels, [["leaf"], ["leaf"]])

    def test_a_resize_tick_redraws_with_the_new_size(self):
        """select returning nothing on a tick is the resize path: the loop
        re-measures and redraws without waiting for a keypress."""
        import os

        ticks = [0]

        def slow(rlist, _w, _x, _t):
            ticks[0] += 1
            # Two idle ticks: the first consumes the startup resize flag,
            # the second sees a genuinely different size.
            return (rlist, [], []) if ticks[0] > 2 else ([], [], [])

        sizes = [os.terminal_size((100, 30)), os.terminal_size((120, 30)),
                 os.terminal_size((140, 30))]
        calls = [0]

        def size(_fd):
            calls[0] += 1
            return sizes[min(calls[0] - 1, len(sizes) - 1)]

        code, out, _, renders = self._drive(
            ["cancel"], select_side_effect=slow, sizes=size)
        self.assertEqual(code, 0)
        # Initial frame + startup-flag tick + genuine size-change tick:
        # a footer check would pass on the first frame alone, so count
        # the redraws the ticks forced.
        self.assertEqual(renders, 3)

    def test_the_size_comes_from_the_terminal_not_the_environment(self):
        """shutil.get_terminal_size prefers COLUMNS, which a resize never
        updates, so a shell exporting it would pin the browser to the size
        it started at."""
        import os

        with mock.patch.dict(os.environ, {"COLUMNS": "50", "LINES": "10"}):
            self._drive(["cancel"],
                        sizes=lambda _fd: os.terminal_size((120, 30)))
        self.assertEqual(self.widths, [120])

    def test_a_resize_handler_is_installed_and_restored(self):
        """Leaving the handler installed would outlive the browser."""
        import signal

        previous = object()
        with mock.patch("signal.signal", return_value=previous) as sig:
            self._drive(["down", "cancel"])
        first, last = sig.call_args_list[0].args, sig.call_args_list[-1].args
        self.assertEqual(first[0], signal.SIGWINCH)
        self.assertTrue(callable(first[1]))
        self.assertEqual(last, (signal.SIGWINCH, previous))

    def test_it_waits_on_select_with_a_timeout(self):
        """PEP 475 retries an interrupted read, so a blocking os.read
        would never notice the signal. The wait has to be a select that
        wakes on its own."""
        waits = []

        def record(rlist, w, x, timeout=None):
            waits.append((rlist, timeout))
            return (rlist, [], [])

        self._drive(["cancel"], select_side_effect=record)
        self.assertEqual(len(waits), 1)
        rlist, timeout = waits[0]
        self.assertEqual(rlist, [0])
        self.assertIsNotNone(timeout)
        self.assertGreater(timeout, 0)

    def test_a_resize_signal_redraws_and_a_quiet_tick_does_not(self):
        """The SIGWINCH handler is what wakes a redraw when the size the
        loop measures has not changed yet."""
        import os

        import signal

        handlers = []

        def install(sig, handler):
            if sig == signal.SIGWINCH:
                handlers.append(handler)
            return signal.SIG_DFL

        ticks = [0]

        def tick(rlist, _w, _x, _t):
            ticks[0] += 1
            if ticks[0] == 3:
                handlers[0](None, None)      # the window was resized
            return (rlist, [], []) if ticks[0] > 3 else ([], [], [])

        with mock.patch("signal.signal", side_effect=install):
            code, _, _, renders = self._drive(
                ["cancel"], select_side_effect=tick,
                sizes=lambda _fd: os.terminal_size((120, 30)))
        self.assertEqual(code, 0)
        # Initial frame, the startup flag on tick 1, nothing on the quiet
        # tick 2, and the signal on tick 3.
        self.assertEqual(renders, 3)

    def _winch_signal(self, outcomes):
        """A signal.signal whose SIGWINCH calls take ``outcomes`` in turn
        (a value to return or an error to raise). raw_terminal installs
        its own SIGTERM and SIGHUP handlers, which pass straight through."""
        import signal

        winch = []

        def fake(sig, handler):
            if sig != signal.SIGWINCH:
                return signal.SIG_DFL
            winch.append(handler)
            result = outcomes[len(winch) - 1]
            if isinstance(result, BaseException):
                raise result
            return result

        return fake, winch

    def test_no_resize_handler_still_browses_and_restores_nothing(self):
        for error in (ValueError("not the main thread"), OSError("no")):
            fake, winch = self._winch_signal([error])
            with mock.patch("signal.signal", side_effect=fake):
                code, _, _, _ = self._drive(["down", "cancel"])
            self.assertEqual(code, 0)
            # Installed once, and with nothing saved nothing is put back.
            self.assertEqual(len(winch), 1)

    def test_a_failed_handler_restore_does_not_raise(self):
        previous = object()
        fake, winch = self._winch_signal([previous, ValueError("late")])
        with mock.patch("signal.signal", side_effect=fake):
            code, _, seen, _ = self._drive(["cancel"])
        self.assertEqual(code, 0)
        self.assertEqual(winch[1], previous)
        self.assertEqual(len(seen), 1)    # the terminal was still restored

    def test_unbound_keys_change_nothing_and_do_not_quit(self):
        code, _, _, renders = self._drive(["", "space", "pageup", "down", "cancel"])
        self.assertEqual(code, 0)
        self.assertEqual(renders, 5)
        self.assertEqual(self.levels[-1], ["a", "b"])

    def test_up_from_the_top_wraps_to_the_bottom_and_down_wraps_back(self):
        cursors = []
        from swe.find import browser as browser_mod

        real = browser_mod._preview

        def spy(entry, limit):
            cursors.append(entry.label)
            return real(entry, limit)

        with mock.patch.object(browser_mod, "_preview", side_effect=spy):
            self._drive(["up", "down", "bottom", "top", "cancel"],
                        roots=[Entry("a"), Entry("b"), Entry("c")])
        self.assertEqual(cursors, ["a", "c", "a", "c", "a"])

    def test_the_resize_keys_move_the_split_and_stop_at_one(self):
        from swe.find import browser as browser_mod

        ratios = []
        real = browser_mod._pane_widths

        def spy(width, ratio):
            ratios.append(tuple(ratio))
            return real(width, ratio)

        keys = (["wider_parent"] * 10 + ["narrower_parent"]
                + ["wider_preview", "narrower_preview", "narrower_preview", "cancel"])
        with mock.patch.object(browser_mod, "_pane_widths", side_effect=spy):
            self._drive(keys)
        # The middle weight floors at 1 and the parent keeps growing.
        self.assertEqual(ratios[8], (11, 1, 8))
        self.assertEqual(ratios[10], (13, 1, 8))
        self.assertEqual(ratios[11], (12, 2, 8))
        self.assertEqual(ratios[12], (12, 1, 9))
        self.assertEqual(ratios[13], (12, 2, 8))
        self.assertEqual(ratios[14], (12, 3, 7))

    def test_the_breadcrumb_follows_descent_and_back(self):
        roots = [Entry("dv", children=[Entry("cloudflare", children=[Entry("skill")])])]
        code, out, _, _ = self._drive(["open", "open", "back", "cancel"], roots=roots)
        self.assertEqual(code, 0)
        self.assertEqual(self.levels, [["dv"], ["cloudflare"], ["skill"], ["cloudflare"]])
        text = strip_ansi(out)
        self.assertIn("top level", text)
        self.assertIn("dv / cloudflare", text)


if __name__ == "__main__":
    unittest.main()
