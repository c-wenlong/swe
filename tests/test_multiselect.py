"""The checklist and tri-state picker, driven key by key.

``keys.read_key`` is replaced by a scripted list of words, and raw mode by
stubs, so no terminal is needed; ``_supported`` is forced on.
"""

import io
import sys
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import patch

from swe import keys, multiselect
from swe.multiselect import Choice, StateChoice


def _drive(widget, choices, script, **kwargs):
    """Run ``widget`` with ``script`` as its keypresses; (result, output)."""
    out = io.StringIO()
    with ExitStack() as stack:
        stack.enter_context(patch.object(multiselect, "_supported", return_value=True))
        stack.enter_context(patch.object(sys, "stdin", **{"fileno.return_value": 0}))
        stack.enter_context(patch("termios.tcgetattr", return_value=[]))
        stack.enter_context(patch("termios.tcsetattr"))
        stack.enter_context(patch("tty.setraw"))
        stack.enter_context(patch.object(keys, "read_key", side_effect=list(script)))
        stack.enter_context(redirect_stdout(out))
        result = widget(choices, height=10, **kwargs)
    return result, out.getvalue()


class SupportedTest(unittest.TestCase):
    def test_without_termios_the_widgets_are_unsupported(self):
        with patch.object(sys, "stdin", **{"isatty.return_value": True}), \
                patch.object(sys, "stdout", **{"isatty.return_value": True}), \
                patch.dict(sys.modules, {"termios": None}):
            self.assertFalse(multiselect._supported())

    def test_the_statepicker_without_a_terminal_changes_nothing(self):
        row = StateChoice("claude", "Claude", state="starred")
        out = io.StringIO()
        with patch.object(multiselect, "_supported", return_value=False), redirect_stdout(out):
            self.assertIsNone(multiselect.statepicker([row]))
        self.assertIn("not a terminal, nothing changed", out.getvalue())
        self.assertEqual(row.state, "starred")


class ChecklistTest(unittest.TestCase):
    def _rows(self):
        return [Choice("a", "Alpha"), Choice("b", "Beta", locked=True), Choice("c", "Gamma")]

    def test_a_locked_row_cannot_be_untoggled(self):
        result, _ = _drive(multiselect.multiselect, self._rows(),
                           ["down", "space", "enter"])
        self.assertEqual(result, ["b"])

    def test_all_selects_everything_and_none_keeps_only_locked_rows(self):
        result, _ = _drive(multiselect.multiselect, self._rows(), ["all", "enter"])
        self.assertEqual(result, ["a", "b", "c"])
        result, _ = _drive(multiselect.multiselect, self._rows(),
                           ["all", "none", "enter"], selected=["a", "c"])
        self.assertEqual(result, ["b"])

    def test_left_and_right_rotate_a_cycling_row_and_leave_others_alone(self):
        windows = ["7d", "30d", "all"]

        def cycle(value, step):
            return windows[(windows.index(value) + step) % len(windows)]

        rows = [Choice("plain", "Plain"),
                Choice("window", "Window", value="7d", cycle=cycle)]
        _drive(multiselect.multiselect, rows, ["next", "down", "next", "next", "prev", "enter"])
        self.assertEqual(rows[1].value, "30d")
        self.assertIsNone(rows[0].value)


class StatePickerTest(unittest.TestCase):
    def _rows(self):
        return [StateChoice("a", "Alpha"), StateChoice("b", "Beta"),
                StateChoice("c", "Gamma", state="archived")]

    def test_letters_set_a_state_directly(self):
        rows = self._rows()
        result, _ = _drive(multiselect.statepicker, rows,
                           ["starred", "down", "archived", "down", "active", "enter"])
        self.assertIs(result, rows)
        self.assertEqual([r.state for r in rows], ["starred", "archived", "active"])

    def test_up_wraps_to_the_last_row_and_cycling_wraps_the_states(self):
        rows = self._rows()
        _drive(multiselect.statepicker, rows, ["up", "next", "up", "prev", "prev", "enter"])
        # Gamma: archived -> active (wraps forward); Beta: active -> archived -> starred.
        self.assertEqual([r.state for r in rows], ["active", "starred", "active"])

    def test_cancel_returns_none(self):
        result, _ = _drive(multiselect.statepicker, self._rows(), ["starred", "cancel"])
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
