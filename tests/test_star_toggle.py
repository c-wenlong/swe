"""`swe star` toggles, so `swe unstar` is gone.

The two commands did the same job from opposite directions, except
`unstar` could also remove a star whose registry entry no longer existed.
That fallback moved into the toggle, so removing the command loses
nothing.
"""

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from swe.console import strip_ansi


class StarTogglesTest(unittest.TestCase):
    def setUp(self):
        self.stars = []
        p1 = mock.patch("swe.harness.stars.load_stars",
                        side_effect=lambda: list(self.stars))
        p2 = mock.patch("swe.harness.stars.save_stars",
                        side_effect=lambda s: self.stars.__init__(list(s)))
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def _star(self, name):
        from swe.harness.commands import cmd_star

        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_star([name])
        return strip_ansi(buf.getvalue())

    def test_the_command_is_no_longer_registered(self):
        from swe.cli import COMMANDS

        self.assertNotIn("unstar", COMMANDS)

    def test_it_is_not_offered_in_completion(self):
        from swe.completion import get_completions

        for words in ([""], ["unst"], ["harness", ""], ["hs", ""]):
            offered = [word for word, _ in get_completions(words)]
            self.assertNotIn("unstar", offered, words)
        # The toggle itself is still offered where unstar used to sit.
        self.assertIn("star", [word for word, _ in get_completions(["harness", ""])])

    def test_help_no_longer_advertises_it(self):
        from swe.help_text import HELP, cmd_help

        self.assertNotIn("unstar", HELP)
        for name, (summary, detail) in HELP.items():
            self.assertNotIn("unstar", strip_ansi(summary + detail), name)
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cmd_help(["unstar"]), 1)
        self.assertIn("Unknown command", strip_ansi(buf.getvalue()))

    def test_star_then_star_again_returns_to_the_start(self):
        from swe.harness.stars import is_starred, toggle_star

        # setUp's patches keep the stars in self.stars.
        self.assertTrue(toggle_star("claude"))
        self.assertTrue(is_starred("claude", self.stars))
        self.assertFalse(toggle_star("claude"))
        self.assertFalse(is_starred("claude", self.stars))

    def test_an_orphan_star_can_still_be_removed(self):
        """A star can outlive its registry entry; removing one used to
        need `swe unstar`, which fell back to the raw name."""
        from swe.harness.commands import cmd_star

        self.stars.append("ghost-tool")
        with mock.patch("swe.harness.commands.load_registry", return_value={}), \
             mock.patch("swe.harness.commands.is_starred", return_value=True), \
             mock.patch("swe.harness.commands.toggle_star", return_value=False) as tg:
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_star(["ghost-tool"])
            tg.assert_called_once_with("ghost-tool")
        self.assertIn("Unstarred", strip_ansi(buf.getvalue()))

    def test_an_unknown_name_is_still_an_error(self):
        from swe.harness.commands import cmd_star

        with mock.patch("swe.harness.commands.load_registry", return_value={}), \
             mock.patch("swe.harness.commands.is_starred", return_value=False):
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_star(["no-such-tool"])
        self.assertIn("not found", strip_ansi(buf.getvalue()))


if __name__ == "__main__":
    unittest.main()
