"""Tests for scripts/install.sh, run under stubbed uname/curl/uv/python3.

Each test builds a throwaway stub dir whose tiny sh scripts log their
argv to a file, puts it first on PATH ahead of /usr/bin:/bin, then runs
the installer in a subprocess with HOME pointing at a temp dir.
"""

import hashlib
import os
import shutil
import stat
import subprocess
import unittest
from pathlib import Path

from tests._tempdir import temp_dir

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "install.sh"
WHEEL_NAME = "swe-1.2.3-py3-none-any.whl"
WHEEL_BYTES = b"fake wheel bytes\n"
LATEST_URL = "https://github.com/c-wenlong/swe/releases/tag/v1.2.3"


def _write(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@unittest.skipUnless(os.name == "posix" and shutil.which("sh"),
                     "needs a POSIX sh")
class InstallScriptCase(unittest.TestCase):
    """Shared scaffolding: the stub programs and the run helper."""

    shells = [s for s in ("sh", "dash") if shutil.which(s)]

    # External commands the installer and the stubs call.  Everything the
    # script needs must be reachable from the test's PATH so the suite
    # passes in build sandboxes without /usr/bin or /bin on it (the Nix
    # check, which gates releases).  Tools we stub never go in this list.
    TOOLS = ("mktemp", "cp", "rm", "mkdir", "ln", "chmod", "cat", "awk",
             "env", "dirname", "readlink", "sh", "dash",
             "sha256sum", "shasum")

    def setUp(self):
        self.home = temp_dir(self)
        self.stub_dir = temp_dir(self) / "stubbin"
        self.stub_dir.mkdir()
        self.tools_dir = temp_dir(self) / "toolsbin"
        self.tools_dir.mkdir()
        for name in self.TOOLS:
            found = shutil.which(name)
            if found:
                (self.tools_dir / name).symlink_to(found)
        self.log = temp_dir(self) / "stub.log"
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{self.stub_dir}:{self.tools_dir}",
            "STUB_LOG": str(self.log),
            "STUB_LATEST_URL": LATEST_URL,
            "STUB_UNAME": "Linux",
            "STUB_SUMS": "ok",
            "STUB_WHEEL_NAME": WHEEL_NAME,
            "STUB_SETUP_RC": "0",
            "STUB_PIP_OK": "1",
            "STUB_ASTRAL_OK": "1",
            "STUB_ACTIVE_UNITS": "",
            # Keep XDG vars unset so the defaults below are what the
            # assertions check.
        }

        wheel = temp_dir(self) / WHEEL_NAME
        wheel.write_bytes(WHEEL_BYTES)
        self.wheel = wheel
        self.env["STUB_WHEEL"] = str(wheel)
        self.env["STUB_SUM"] = hashlib.sha256(WHEEL_BYTES).hexdigest()

        _write(self.stub_dir / "uname", 'printf "%s\\n" "${STUB_UNAME:-Linux}"\n')
        _write(self.stub_dir / "curl", self._curl_stub())
        _write(self.stub_dir / "python3", self._python3_stub())
        self.env["STUB_SWE_FILE"] = str(
            _write(temp_dir(self) / "swe-stub", self._swe_body()))
        self.env["STUB_UV_FILE"] = str(
            _write(temp_dir(self) / "uv-stub", self._uv_body()))
        self.assertNotIn("/usr/bin", self.env["PATH"].split(":"))

    def _curl_stub(self) -> str:
        # Logs argv, then answers the three URL shapes the installer
        # fetches: the latest-release redirect, SHA256SUMS and the wheel.
        # A bare (no -o) call is the astral.sh uv bootstrap, answered with
        # a script that installs the generic uv stub.
        return """\
echo "curl $*" >> "$STUB_LOG"
out=""
prev=""
url=""
for a in "$@"; do
    if [ "$prev" = "-o" ]; then out="$a"; prev=""; fi
    if [ "$a" = "-o" ]; then prev="-o"; fi
    url="$a"
done
case "$url" in
    *astral.sh*)
        [ "$STUB_ASTRAL_OK" = "1" ] || exit 22
        printf 'cp "$STUB_UV_FILE" "$UV_INSTALL_DIR/uv"; chmod +x "$UV_INSTALL_DIR/uv"\\n'
        ;;
    */releases/latest)
        printf '%s\\n' "$STUB_LATEST_URL"
        ;;
    */SHA256SUMS)
        case "$STUB_SUMS" in
            404) exit 22 ;;
            bad) printf '0000000000000000000000000000000000000000000000000000000000000000  %s\\n' "$STUB_WHEEL_NAME" > "$out" ;;
            *)   printf '%s  %s\\n' "$STUB_SUM" "$STUB_WHEEL_NAME" > "$out" ;;
        esac
        ;;
    *.whl)
        cp "$STUB_WHEEL" "$out"
        ;;
esac
"""

    def _uv_body(self) -> str:
        # `uv tool install` drops a fake swe into the bin dir; `uv tool dir
        # --bin` reports it.  Installed to a different name, so the log
        # shows which binary ran.
        return """\
echo "uv $*" >> "$STUB_LOG"
case "$1 $2" in
    "tool dir")
        printf '%s\\n' "${STUB_UV_BIN_DIR:-$HOME/.local/bin}"
        ;;
    "tool install")
        d="${STUB_UV_BIN_DIR:-$HOME/.local/bin}"
        mkdir -p "$d"
        cp "$STUB_SWE_FILE" "$d/swe"
        chmod +x "$d/swe"
        ;;
esac
"""

    def _python3_stub(self) -> str:
        # Version check honours STUB_PYTHON_OK (0 = pre-3.11 interpreter).
        # `-m venv DIR` honours STUB_VENV_OK and plants a pip whose
        # `install` drops a fake swe next to itself.
        return """\
echo "python3 $*" >> "$STUB_LOG"
if [ "$1" = "-c" ]; then
    [ "${STUB_PYTHON_OK:-1}" = "1" ] && exit 0 || exit 1
fi
if [ "$1 $2" = "-m venv" ]; then
    if [ "${STUB_VENV_OK:-1}" != "1" ]; then
        exit 1
    fi
    mkdir -p "$3/bin"
    cat > "$3/bin/pip" <<'EOF'
#!/bin/sh
echo "pip $*" >> "$STUB_LOG"
if [ "$STUB_PIP_OK" != "1" ]; then
    exit 1
fi
cp "$STUB_SWE_FILE" "$(dirname "$0")/swe"
chmod +x "$(dirname "$0")/swe"
EOF
    chmod +x "$3/bin/pip"
    exit 0
fi
"""

    def _systemctl_body(self) -> str:
        # `systemctl --user is-active --quiet UNIT` succeeds only for
        # units named in STUB_ACTIVE_UNITS; everything else exits 0.
        return """\
echo "systemctl $*" >> "$STUB_LOG"
if [ "$2" = "is-active" ]; then
    case " $STUB_ACTIVE_UNITS " in
        *" $4 "*) exit 0 ;;
    esac
    exit 1
fi
exit 0
"""

    def _swe_body(self) -> str:
        return """\
echo "swe[$0] $*" >> "$STUB_LOG"
if [ "$1" = "--version" ]; then
    echo "swe 1.2.3"
fi
if [ "$1" = "remote" ]; then
    exit "$STUB_SETUP_RC"
fi
"""

    def run_installer(self, *args: str, shell: str = "sh") -> subprocess.CompletedProcess:
        return subprocess.run(
            [shell, str(SCRIPT), *args],
            env=self.env, capture_output=True, text=True)

    def log_lines(self) -> list[str]:
        if not self.log.exists():
            return []
        return self.log.read_text().splitlines()

    def curl_log(self) -> list[str]:
        return [l for l in self.log_lines() if l.startswith("curl ")]


class TestDarwin(InstallScriptCase):
    def test_darwin_exits_1_and_mentions_brew(self):
        self.env["STUB_UNAME"] = "Darwin"
        for shell in self.shells:
            with self.subTest(shell=shell):
                r = self.run_installer(shell=shell)
                self.assertEqual(r.returncode, 1)
                self.assertIn("brew install", r.stderr)


class TestUvPath(InstallScriptCase):
    def setUp(self):
        super().setUp()
        _write(self.stub_dir / "uv", Path(self.env["STUB_UV_FILE"]).read_text().split("\n", 1)[1])

    def test_uv_install_and_latest_tag(self):
        for shell in self.shells:
            with self.subTest(shell=shell):
                self.log.unlink(missing_ok=True)
                r = self.run_installer(shell=shell)
                self.assertEqual(r.returncode, 0, r.stderr)
                uv_calls = [l for l in self.log_lines() if l.startswith("uv tool install")]
                self.assertEqual(len(uv_calls), 1)
                self.assertIn("--force --python >=3.11", uv_calls[0])
                self.assertTrue(uv_calls[0].endswith(WHEEL_NAME), uv_calls[0])
                self.assertTrue(any("/releases/latest" in l for l in self.curl_log()))
                self.assertIn("swe 1.2.3", r.stdout)

    def test_pinned_version_skips_latest_lookup(self):
        wheel_name = "swe-9.9.9-py3-none-any.whl"
        self.env["STUB_WHEEL_NAME"] = wheel_name
        r = self.run_installer("--version=v9.9.9")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(any("/releases/latest" in l for l in self.curl_log()))
        self.assertTrue(any(l.endswith(wheel_name) for l in self.log_lines()
                            if l.startswith("uv tool install")))

    def test_checksum_mismatch_aborts_before_install(self):
        self.env["STUB_SUMS"] = "bad"
        r = self.run_installer()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("checksum", r.stderr)
        self.assertFalse(any(l.startswith("uv ") for l in self.log_lines()))

    def test_missing_checksum_file_warns_and_installs(self):
        self.env["STUB_SUMS"] = "404"
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SHA256SUMS", r.stderr + r.stdout)
        self.assertTrue(any(l.startswith("uv tool install") for l in self.log_lines()))

    def test_no_systemctl_no_restart(self):
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("restarted", r.stdout)

    def test_bin_dir_not_on_path_prints_export_hint(self):
        self.env["STUB_UV_BIN_DIR"] = str(self.home / ".local" / "bin")
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('export PATH="', r.stdout)
        self.assertIn(str(self.home / ".local" / "bin"), r.stdout)


class TestVenvPath(InstallScriptCase):
    def _data_dir(self) -> Path:
        return self.home / ".local" / "share" / "swe"

    def _venvs(self) -> list[Path]:
        return list(self._data_dir().glob("venv-*"))

    def _plant_old_install(self) -> Path:
        old = self._data_dir() / "venv-old"
        (old / "bin").mkdir(parents=True)
        shutil.copy(self.env["STUB_SWE_FILE"], old / "bin" / "swe")
        (old / "bin" / "swe").chmod(0o755)
        bin_dir = self.home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "swe").symlink_to(old / "bin" / "swe")
        return old

    def test_venv_install_creates_symlink(self):
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        venvs = self._venvs()
        self.assertEqual(len(venvs), 1)
        self.assertTrue((venvs[0] / "bin" / "swe").exists())
        link = self.home / ".local" / "bin" / "swe"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(venvs[0] / "bin" / "swe"))
        self.assertTrue(any(l.startswith("pip install") for l in self.log_lines()))

    def test_venv_upgrade_replaces_old_install(self):
        old = self._plant_old_install()
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        venvs = self._venvs()
        self.assertEqual(len(venvs), 1)
        self.assertNotEqual(venvs[0], old)
        self.assertEqual(os.readlink(self.home / ".local" / "bin" / "swe"),
                         str(venvs[0] / "bin" / "swe"))
        self.assertFalse(old.exists())

    def test_upgrade_leaves_unrelated_venv_dirs(self):
        old = self._plant_old_install()
        other = self._data_dir() / "venv-other"
        (other / "bin").mkdir(parents=True)
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(old.exists())
        self.assertTrue(other.exists())
        target = Path(os.readlink(self.home / ".local" / "bin" / "swe"))
        new_venv = target.parent.parent
        self.assertTrue(new_venv.name.startswith("venv-"))
        self.assertNotIn(new_venv, (old, other))

    def test_failed_upgrade_keeps_old_install(self):
        old = self._plant_old_install()
        self.env["STUB_PIP_OK"] = "0"
        self.env["STUB_ASTRAL_OK"] = "0"
        r = self.run_installer()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self._venvs(), [old])
        self.assertTrue((old / "bin" / "swe").exists())
        self.assertEqual(os.readlink(self.home / ".local" / "bin" / "swe"),
                         str(old / "bin" / "swe"))

    def test_python_310_bootstraps_uv(self):
        self.env["STUB_PYTHON_OK"] = "0"
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(any("astral.sh" in l for l in self.curl_log()))
        self.assertTrue(any(l.startswith("uv tool install") for l in self.log_lines()))

    def test_broken_venv_bootstraps_uv(self):
        self.env["STUB_VENV_OK"] = "0"
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(any("astral.sh" in l for l in self.curl_log()))
        self.assertFalse((self.home / ".local" / "share" / "swe" / "venv").exists())

    def test_pip_failure_bootstraps_uv(self):
        self.env["STUB_PIP_OK"] = "0"
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(any("astral.sh" in l for l in self.curl_log()))
        self.assertTrue(any(l.startswith("uv tool install") for l in self.log_lines()))
        self.assertFalse((self.home / ".local" / "share" / "swe" / "venv").exists())
        self.assertFalse((self.home / ".local" / "bin" / "swe").is_symlink())

    def test_astral_failure_dies_with_message(self):
        self.env["STUB_PYTHON_OK"] = "0"
        self.env["STUB_ASTRAL_OK"] = "0"
        r = self.run_installer()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("could not install uv from astral.sh", r.stderr)


class TestSystemdRestart(InstallScriptCase):
    def setUp(self):
        super().setUp()
        _write(self.stub_dir / "uv", Path(self.env["STUB_UV_FILE"]).read_text().split("\n", 1)[1])
        _write(self.stub_dir / "systemctl", self._systemctl_body())

    def test_active_hub_unit_is_restarted(self):
        self.env["STUB_ACTIVE_UNITS"] = "quiver-remote-hub.service"
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("restarted quiver-remote-hub.service", r.stdout)
        self.assertTrue(any("restart quiver-remote-hub.service" in l
                            for l in self.log_lines()))
        self.assertFalse(any("restart quiver-remote-agent.service" in l
                             for l in self.log_lines()))

    def test_no_active_units_no_restart(self):
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(any("restart" in l for l in self.log_lines()
                             if l.startswith("systemctl")))


class TestWheelFlag(InstallScriptCase):
    def setUp(self):
        super().setUp()
        _write(self.stub_dir / "uv", Path(self.env["STUB_UV_FILE"]).read_text().split("\n", 1)[1])

    def test_wheel_skips_downloads(self):
        r = self.run_installer(f"--wheel={self.wheel}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.curl_log(), [])
        self.assertTrue(any(l.startswith("uv tool install") for l in self.log_lines()))


class TestHubFlag(InstallScriptCase):
    def setUp(self):
        super().setUp()
        _write(self.stub_dir / "uv", Path(self.env["STUB_UV_FILE"]).read_text().split("\n", 1)[1])

    def test_hub_runs_remote_setup_with_absolute_path(self):
        r = self.run_installer("--hub")
        self.assertEqual(r.returncode, 0, r.stderr)
        setup_calls = [l for l in self.log_lines() if "remote setup --apply" in l]
        self.assertEqual(len(setup_calls), 1)
        # The systemd unit records argv[0]: it must be the absolute path.
        self.assertTrue(setup_calls[0].startswith("swe[/"), setup_calls[0])

    def test_hub_person_step_still_exits_0_with_followup(self):
        self.env["STUB_SETUP_RC"] = "1"
        r = self.run_installer("--hub")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("finish the steps above, then run: swe remote setup --apply", r.stdout)


class TestFlags(InstallScriptCase):
    def test_unknown_flag_exits_2(self):
        r = self.run_installer("--bogus")
        self.assertEqual(r.returncode, 2)

if __name__ == "__main__":
    unittest.main()
