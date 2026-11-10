"""Credential selection is account-pinned and keychain reads cannot show UI."""
import base64
import ctypes
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from swe.plugins.cursor_auth import cursor_token, _keychain_password


def jwt():
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "fixture", "exp": 9999999999}).encode()).decode().rstrip("=")
    return "header." + payload + ".signature"


class Function:
    def __init__(self, callback): self.callback = callback
    def __call__(self, *args): return self.callback(*args)


class FakeSecurity:
    def __init__(self, status=0):
        self.ui = True
        self.states = []
        self.password = ctypes.create_string_buffer(b"fixture-password")
        def get(out): out._obj.value = int(self.ui); return 0
        def set_ui(value): self.ui = bool(value); self.states.append(self.ui); return 0
        def find(_, length, service, account_len, account, out_len, out_password, item):
            assert not self.ui, "Keychain UI must be disabled before any read"
            out_len._obj.value = len(b"fixture-password")
            out_password._obj.value = ctypes.addressof(self.password)
            return status
        self.SecKeychainGetUserInteractionAllowed = Function(get)
        self.SecKeychainSetUserInteractionAllowed = Function(set_ui)
        self.SecKeychainFindGenericPassword = Function(find)
        self.SecKeychainItemFreeContent = Function(lambda *_: 0)


class CursorAuthTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_failed_editor_read_never_selects_cli_account(self):
        store = self.home / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
        store.parent.mkdir(parents=True)
        store.write_text("corrupt database")
        with patch("swe.harness.rate_limits._read_cursor_cli_token", return_value=jwt()) as cli, \
             patch("swe.plugins.cursor_auth._keychain_password", side_effect=AssertionError("real keychain")):
            self.assertIsNone(cursor_token(self.home))
        cli.assert_not_called()

    def test_protected_editor_token_never_falls_through(self):
        store = self.home / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
        store.parent.mkdir(parents=True)
        store.touch()
        with patch("swe.harness.rate_limits._read_cursor_state_token", return_value="cursor-safe-storage:v1:invalid"), \
             patch("swe.harness.rate_limits._read_cursor_cli_token", return_value=jwt()) as cli:
            self.assertIsNone(cursor_token(self.home))
        cli.assert_not_called()

    def test_plaintext_cli_file_stays_inside_the_requested_home(self):
        auth = self.home / ".config/cursor/auth.json"
        auth.parent.mkdir(parents=True)
        auth.write_text(json.dumps({"accessToken": jwt()}))
        self.assertEqual(cursor_token(self.home), jwt())

    def test_keychain_ui_is_disabled_and_restored_even_on_refusal(self):
        for status in [0, -25308]:
            security = FakeSecurity(status)
            with patch("swe.plugins.cursor_auth.sys.platform", "darwin"), \
                 patch("swe.plugins.cursor_auth.ctypes.CDLL", return_value=security):
                result = _keychain_password("fixture-service")
            self.assertEqual(security.states, [False, True])
            self.assertEqual(result, "fixture-password" if status == 0 else None)


if __name__ == "__main__":
    unittest.main()
