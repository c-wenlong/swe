"""remote/systemd.py: the hub as a systemd user unit, with systemctl faked."""

import shlex
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.remote import systemd

from tests._tempdir import temp_dir


class FakeCtl:
    """Records systemctl/loginctl calls; `linger` flips when enabled."""

    def __init__(self, fail=(), linger="yes", can_linger=True, active="active", loginctl=True,
                 units=None):
        self.calls = []
        # Per-unit is-active answers; a unit not listed answers `active`.
        self.units = dict(units or {})
        self.loginctl = loginctl
        self.fail = set(fail)
        self.linger = linger
        self.can_linger = can_linger
        self.active = active

    def __call__(self, argv, **_kw):
        self.calls.append(argv)
        if argv[0] == "loginctl":
            if not self.loginctl:
                return subprocess.CompletedProcess(argv, 1, stdout="", stderr="no logind")
            if argv[1] == "enable-linger" and self.can_linger:
                self.linger = "yes"
            return subprocess.CompletedProcess(argv, 0, stdout=f"{self.linger}\n", stderr="")
        verb = argv[2]
        if verb == "is-active":
            answer = self.units.get(argv[3], self.active)
            return subprocess.CompletedProcess(argv, 0, stdout=f"{answer}\n", stderr="")
        rc = 1 if verb in self.fail else 0
        if verb == "disable" and not rc and "--now" in argv:
            self.units[argv[-1]] = "inactive"
        return subprocess.CompletedProcess(argv, rc, stdout="", stderr="boom" if rc else "")

    def verbs(self):
        return [a[2] if a[0] == "systemctl" else a[1] for a in self.calls]


class UnitTest(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        self.log = self.home / ".swe/data/remote/serve.log"
        self.program = ["/home/dev/.local/bin/swe", "remote", "serve", "--host=127.0.0.1",
                        "--public-url=https://box.tail.ts.net:8477"]

    def test_install_writes_a_restarting_unit_enables_and_restarts_it(self):
        ctl = FakeCtl()
        path, note = systemd.install(self.program, self.log, "/usr/bin:/bin", home=self.home, run=ctl)
        self.assertEqual(path, self.home / ".config/systemd/user/swe-remote-hub.service")
        text = path.read_text()
        self.assertIn("Restart=always", text)
        self.assertIn(f"StandardOutput=append:{self.log}", text)
        self.assertIn("Environment=PATH=/usr/bin:/bin", text)
        spaced = systemd.build_unit(self.program, self.log, "/usr/bin:/opt/My Tools/bin")
        self.assertIn('Environment="PATH=/usr/bin:/opt/My Tools/bin"', spaced)
        self.assertIn("WantedBy=default.target", text)
        self.assertEqual(ctl.verbs()[:3], ["daemon-reload", "enable", "restart"])
        self.assertEqual(note, "")

    def test_the_command_line_round_trips_with_spaces_and_percents(self):
        program = ["/opt/My Tools/swe", "remote", "serve", "--name=50%"]
        text = systemd.build_unit(program, self.log, "/bin")
        line = next(ln for ln in text.splitlines() if ln.startswith("ExecStart="))
        self.assertIn("50%%", line, "systemd reads % as a specifier")
        words = [w.replace("%%", "%") for w in shlex.split(line[len("ExecStart="):])]
        self.assertEqual(words, program)

    def test_status_reads_the_installed_command_and_activity(self):
        systemd.install(self.program, self.log, "/bin", home=self.home, run=FakeCtl())
        self.assertEqual(systemd.status(self.home, FakeCtl()), (self.program, True))
        self.assertEqual(systemd.status(self.home, FakeCtl(active="failed"))[1], False)
        self.assertEqual(systemd.status(temp_dir(self), FakeCtl()), (None, False))

    def test_a_failing_step_raises_with_systemctls_message(self):
        with self.assertRaises(RuntimeError) as ctx:
            systemd.install(self.program, self.log, "/bin", home=self.home, run=FakeCtl(fail={"restart"}))
        self.assertIn("boom", str(ctx.exception))

    def test_lingering_is_turned_on_when_the_user_may(self):
        ctl = FakeCtl(linger="no")
        _, note = systemd.install(self.program, self.log, "/bin", home=self.home, run=ctl)
        self.assertEqual(note, "")
        self.assertIn("enable-linger", ctl.verbs())

    def test_lingering_that_needs_root_says_what_to_run(self):
        ctl = FakeCtl(linger="no", can_linger=False)
        _, note = systemd.install(self.program, self.log, "/bin", home=self.home, run=ctl)
        self.assertEqual(note, systemd.LINGER_FIX)

    def test_uninstall_stops_and_removes_the_unit(self):
        systemd.install(self.program, self.log, "/bin", home=self.home, run=FakeCtl())
        ctl = FakeCtl()
        self.assertTrue(systemd.uninstall(self.home, ctl))
        self.assertFalse(systemd.unit_path(self.home).exists())
        self.assertEqual(ctl.verbs(), ["disable", "daemon-reload"])
        self.assertFalse(systemd.uninstall(self.home, FakeCtl()))

    def test_a_refused_stop_keeps_the_unit_of_a_running_hub(self):
        systemd.install(self.program, self.log, "/bin", home=self.home, run=FakeCtl())
        with self.assertRaises(RuntimeError) as ctx:
            systemd.uninstall(self.home, FakeCtl(fail={"disable"}))
        self.assertIn("boom", str(ctx.exception))
        self.assertTrue(systemd.unit_path(self.home).exists())
        self.assertTrue(systemd.uninstall(self.home, FakeCtl(fail={"disable"}, active="inactive")),
                        "a stopped hub's unit is removed even if disable complained")
        self.assertFalse(systemd.uninstall(self.home, FakeCtl(fail={"disable"})), "nothing installed")

    def test_lingering_loginctl_cannot_report_is_not_taken_as_on(self):
        ctl = FakeCtl(loginctl=False)
        self.assertIsNone(systemd.lingering(ctl))
        self.assertEqual(systemd.ensure_linger(ctl), systemd.LINGER_FIX)
        self.assertIn("enable-linger", ctl.verbs())

    def _old_unit(self, unit=systemd.OLD_UNIT):
        old = systemd.unit_path(self.home, unit)
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("[Service]\nExecStart=/old/quiver remote serve\n")
        return old

    def test_install_retires_the_pre_rename_unit(self):
        old = self._old_unit()
        ctl = FakeCtl()
        path, _ = systemd.install(self.program, self.log, "/bin", home=self.home, run=ctl,
                                  old_unit=systemd.OLD_UNIT)
        self.assertEqual(path.name, "swe-remote-hub.service")
        self.assertFalse(old.exists())
        sys_calls = [a[2:] for a in ctl.calls if a[0] == "systemctl"]
        # The new unit is up before the old one goes, as launchd does it.
        self.assertLess(sys_calls.index(["restart", systemd.UNIT]),
                        sys_calls.index(["disable", "--now", systemd.OLD_UNIT]))
        self.assertEqual(sys_calls[-1], ["daemon-reload"])

    def test_an_old_unit_that_will_not_stop_keeps_its_file(self):
        old = self._old_unit()
        ctl = FakeCtl(fail={"disable"})
        systemd.install(self.program, self.log, "/bin", home=self.home, run=ctl,
                        old_unit=systemd.OLD_UNIT)
        self.assertTrue(old.exists())

    def test_install_without_an_old_unit_asks_nothing_about_it(self):
        ctl = FakeCtl()
        systemd.install(self.program, self.log, "/bin", home=self.home, run=ctl,
                        old_unit=systemd.OLD_UNIT)
        self.assertNotIn(systemd.OLD_UNIT, [a[-1] for a in ctl.calls])

    def test_uninstall_removes_the_old_unit_too(self):
        systemd.install(self.program, self.log, "/bin", home=self.home, run=FakeCtl())
        old = self._old_unit()
        ctl = FakeCtl()
        self.assertTrue(systemd.uninstall(self.home, ctl, old_unit=systemd.OLD_UNIT))
        self.assertFalse(systemd.unit_path(self.home).exists())
        self.assertFalse(old.exists())
        disabled = [a[-1] for a in ctl.calls if a[0] == "systemctl" and a[2] == "disable"]
        self.assertEqual(disabled, [systemd.UNIT, systemd.OLD_UNIT])
        # A machine with only the old unit still has something to remove.
        old = self._old_unit()
        self.assertTrue(systemd.uninstall(self.home, FakeCtl(), old_unit=systemd.OLD_UNIT))
        self.assertFalse(old.exists())
        self.assertFalse(systemd.uninstall(self.home, FakeCtl(), old_unit=systemd.OLD_UNIT))

    def test_uninstall_keeps_an_old_unit_that_will_not_stop(self):
        old = self._old_unit()
        with self.assertRaises(RuntimeError):
            systemd.uninstall(self.home, FakeCtl(fail={"disable"}), old_unit=systemd.OLD_UNIT)
        self.assertTrue(old.exists())

    def test_status_reports_a_running_old_unit(self):
        self._old_unit()
        ctl = FakeCtl(units={systemd.UNIT: "inactive"})
        self.assertEqual(systemd.status(self.home, ctl, old_unit=systemd.OLD_UNIT),
                         (["/old/quiver", "remote", "serve"], True))
        # Without the old name, a pre-rename machine reads as not installed.
        self.assertEqual(systemd.status(self.home, ctl), (None, False))
        # A stopped new unit beside a running old one: the running one wins.
        systemd.install(self.program, self.log, "/bin", home=self.home, run=FakeCtl())
        self.assertEqual(systemd.status(self.home, ctl, old_unit=systemd.OLD_UNIT),
                         (["/old/quiver", "remote", "serve"], True))
        both_stopped = FakeCtl(active="inactive")
        self.assertEqual(systemd.status(self.home, both_stopped, old_unit=systemd.OLD_UNIT),
                         (self.program, False))

    def test_xdg_config_home_is_honoured(self):
        with patch.dict("os.environ", {"XDG_CONFIG_HOME": "/cfg"}):
            self.assertEqual(systemd.unit_path(), Path("/cfg/systemd/user/swe-remote-hub.service"))


if __name__ == "__main__":
    unittest.main()
