"""``swe providers`` dispatch: help, an unknown subcommand, exit codes.

The subcommands themselves are tested in test_providers_commands.py; the
corrupt-registry refusal in test_corrupt_config.py.
"""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from swe.console import strip_ansi
from swe.providers import cli


class ProvidersDispatchTest(unittest.TestCase):
    def run_main(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(argv)
        return code, strip_ansi(out.getvalue())

    def test_no_args_and_help_flags_print_the_group_help(self):
        for argv in ([], ["-h"], ["--help"], ["help"]):
            with self.subTest(argv=argv):
                code, out = self.run_main(argv)
                self.assertEqual(code, 0)
                self.assertIn("swe providers", out)
                self.assertIn("Manage AI provider API keys", out)

    def test_an_unknown_subcommand_lists_the_real_ones(self):
        code, out = self.run_main(["lsit"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown providers subcommand: 'lsit'", out)
        self.assertIn("Available: list, ls, info, add, remove, rm, help", out)

    def test_exit_codes_pass_through_and_none_means_success(self):
        for returned, expected in ((None, 0), (0, 0), (3, 3), ("text", 0)):
            with self.subTest(returned=returned), \
                    patch.dict(cli.PROVIDERS_COMMANDS,
                               {"list": lambda args, r=returned: r}):
                self.assertEqual(self.run_main(["list"])[0], expected)

    def test_the_rest_of_argv_reaches_the_subcommand(self):
        seen = []
        with patch.dict(cli.PROVIDERS_COMMANDS,
                        {"info": lambda args: seen.append(args) or 0}):
            self.run_main(["info", "openai", "--json"])
        self.assertEqual(seen, [["openai", "--json"]])


if __name__ == "__main__":
    unittest.main()
