"""scripts/demo_vm.sh: the UTM virtual machine for recording the primitives demo (#524).

A real run needs UTM and a macOS 27 Mac, so these tests stop short of the
VM: argument handling, and the refusal on a machine that cannot run it,
which must leave nothing behind.
"""

import os
import shutil
import subprocess
import unittest
from pathlib import Path

from tests._tempdir import temp_dir

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "demo_vm.sh"
# Prefer macOS's bash 3.2, the oldest one the script promises to run on.
BASH = "/bin/bash" if os.path.exists("/bin/bash") else shutil.which("bash")


@unittest.skipUnless(SCRIPT.exists(), "scripts/ is not part of this source tree")
@unittest.skipUnless(BASH, "bash not installed")
class DemoVmTest(unittest.TestCase):
    def setUp(self):
        self.scratch = temp_dir(self).resolve()
        # No utmctl on this PATH, so every step refuses before touching a VM.
        self.bin = self.scratch / "bin"
        self.bin.mkdir()
        tool_dirs = {os.path.dirname(shutil.which(tool) or f"/usr/bin/{tool}")
                     for tool in ("sed", "uname", "dirname")}
        self.path = ":".join([str(self.bin), *sorted(tool_dirs)])
        self.state = self.scratch / "state"
        self.cache = self.scratch / "cache"

    def _script(self, *args):
        env = {"PATH": self.path, "HOME": str(self.scratch / "home"),
               "SWE_DEMO_VM_STATE": str(self.state), "SWE_DEMO_VM_CACHE": str(self.cache)}
        return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True,
                              env=env, timeout=60)

    def test_parses_under_the_oldest_bash(self):
        run = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_help_lists_every_step(self):
        run = self._script("--help")
        self.assertEqual(run.returncode, 0, run.stderr)
        for step in ("create", "provision", "save", "take", "reset", "ssh", "shot", "all"):
            self.assertIn(f"scripts/demo_vm.sh {step}", run.stderr)
        self.assertEqual(run.stdout, "")

    def test_no_step_prints_the_usage_and_fails(self):
        run = self._script()
        self.assertEqual(run.returncode, 1)
        self.assertIn("scripts/demo_vm.sh create", run.stderr)

    def test_unknown_step_is_refused(self):
        run = self._script("record")
        self.assertEqual(run.returncode, 1)
        self.assertIn("demo_vm: unknown command: record", run.stderr)

    def test_every_step_refuses_without_utm_and_writes_nothing(self):
        for step in ("create", "provision", "save", "take", "reset", "ssh", "shot", "all"):
            with self.subTest(step=step):
                run = self._script(step)
                self.assertEqual(run.returncode, 1, run.stdout)
                # Linux CI stops at the platform check, a Mac at the missing utmctl.
                self.assertRegex(run.stderr, r"^demo_vm: (needs a Mac|needs macOS 27|utmctl is not on PATH)")
                self.assertFalse(self.state.exists())
                self.assertFalse(self.cache.exists())


if __name__ == "__main__":
    unittest.main()
