"""launch_yolo.py: which argv turns a harness's approval checks off."""

import unittest
from unittest.mock import patch

from swe.remote import launch_yolo


def _reg(name, yolo):
    return {name: {"capabilities": {"yolo": yolo}}}


class TestCapability(unittest.TestCase):
    def test_fallback_without_a_registry_entry(self):
        self.assertEqual(launch_yolo.capability("claude", {}),
                         ["--dangerously-skip-permissions"])
        self.assertIsNone(launch_yolo.capability("gemini", {}))

    def test_fallback_is_a_copy(self):
        argv = launch_yolo.capability("devin", {})
        argv.append("--extra")
        self.assertEqual(launch_yolo.YOLO_FALLBACK["devin"],
                         ["--permission-mode", "dangerous"])

    def test_registry_argv_replaces_the_fallback(self):
        reg = _reg("claude", {"argv": ["--yolo-now"]})
        self.assertEqual(launch_yolo.capability("claude", reg), ["--yolo-now"])
        # A harness the fallback never heard of can declare one.
        reg = _reg("newtool", {"supported": True, "argv": ["--auto"]})
        self.assertEqual(launch_yolo.capability("newtool", reg), ["--auto"])

    def test_supported_false_switches_it_off(self):
        reg = _reg("claude", {"supported": False, "argv": ["--x"]})
        self.assertIsNone(launch_yolo.capability("claude", reg))

    def test_malformed_argv_keeps_the_fallback(self):
        for argv in (None, [], "--yolo", ["--ok", ""], ["--ok", 3], ["  "]):
            with self.subTest(argv=argv):
                reg = _reg("cursor", {"argv": argv})
                self.assertEqual(launch_yolo.capability("cursor", reg),
                                 ["--yolo"])
                self.assertIsNone(
                    launch_yolo.capability("gemini", _reg("gemini",
                                                          {"argv": argv})))

    def test_malformed_entries_are_ignored(self):
        for reg in ([], {"claude": "x"}, {"claude": {"capabilities": []}},
                    {"claude": {"capabilities": {"yolo": "on"}}}):
            with self.subTest(reg=reg):
                self.assertEqual(launch_yolo.capability("claude", reg),
                                 ["--dangerously-skip-permissions"])

    def test_reads_the_registry_when_none_is_given(self):
        with patch("swe.harness.registry.load_registry_readonly",
                   return_value=_reg("claude", {"argv": ["--from-disk"]})):
            self.assertEqual(launch_yolo.capability("claude"),
                             ["--from-disk"])

    def test_an_unreadable_registry_falls_back(self):
        for exc in (OSError("denied"), ValueError("bad json")):
            with self.subTest(exc=exc), patch(
                    "swe.harness.registry.load_registry_readonly",
                    side_effect=exc):
                self.assertEqual(launch_yolo.capability("codex"),
                                 ["--dangerously-bypass-approvals-and-sandbox"])


class TestErrorsAndArgv(unittest.TestCase):
    def test_no_pick_is_never_an_error(self):
        self.assertEqual(launch_yolo.yolo_error("gemini", None, {}), "")
        self.assertEqual(launch_yolo.yolo_error("gemini", False, {}), "")

    def test_refusal_names_the_harness(self):
        self.assertEqual(launch_yolo.yolo_error("claude", True, {}), "")
        note = launch_yolo.yolo_error("gemini", True, {})
        self.assertIn("gemini launches can't run autonomously", note)

    def test_yolo_argv_raises_for_a_harness_without_a_switch(self):
        self.assertEqual(launch_yolo.yolo_argv("droid", {}), ["--auto", "high"])
        with self.assertRaises(ValueError) as cm:
            launch_yolo.yolo_argv("gemini", {})
        self.assertIn("capabilities.yolo", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
