"""Read Cursor's current account credentials without allowing keychain UI.

The editor account wins over CLI credentials. In particular, a protected
editor login never falls through to a different account's CLI token.
"""
from __future__ import annotations

import ctypes
import hashlib
import sys
import threading
from pathlib import Path

_LOCK = threading.Lock()
_PATHS = ("Library/Application Support/Cursor/User/globalStorage/state.vscdb",
          ".config/Cursor/User/globalStorage/state.vscdb",
          "AppData/Roaming/Cursor/User/globalStorage/state.vscdb")


def _keychain_password(service: str, account: str | None = None) -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        get_ui = security.SecKeychainGetUserInteractionAllowed
        get_ui.argtypes = [ctypes.POINTER(ctypes.c_uint8)]
        get_ui.restype = ctypes.c_int32
        set_ui = security.SecKeychainSetUserInteractionAllowed
        set_ui.argtypes = [ctypes.c_uint8]
        set_ui.restype = ctypes.c_int32
        find = security.SecKeychainFindGenericPassword
        find.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
                         ctypes.c_uint32, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32),
                         ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        find.restype = ctypes.c_int32
        free = security.SecKeychainItemFreeContent
        free.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        free.restype = ctypes.c_int32
    except (OSError, AttributeError):
        return None
    with _LOCK:
        previous = ctypes.c_uint8()
        if get_ui(ctypes.byref(previous)) != 0 or set_ui(0) != 0:
            return None
        password = ctypes.c_void_p()
        length = ctypes.c_uint32()
        try:
            service_bytes = service.encode()
            account_bytes = account.encode() if account is not None else None
            status = find(None, len(service_bytes), service_bytes,
                          len(account_bytes) if account_bytes else 0, account_bytes,
                          ctypes.byref(length), ctypes.byref(password), None)
            if status != 0 or not password.value or length.value > 1024 * 1024:
                return None
            return ctypes.string_at(password, length.value).decode("utf-8")
        except (UnicodeError, ValueError):
            return None
        finally:
            if password.value:
                free(None, password)
            set_ui(previous.value)


def cursor_token(home: Path) -> str | None:
    from swe.harness.rate_limits import (_read_cursor_state_token, _read_cursor_cli_token,
        _cursor_token_claims, _cursor_safe_storage_ciphertext, _aes_128_cbc_decrypt)
    editor_store = None
    for relative in _PATHS:
        candidate = home / relative
        try:
            candidate.lstat()
            editor_store = candidate
            break
        except FileNotFoundError:
            continue
        except OSError:
            return None
    value = _read_cursor_state_token((str(editor_store),)) if editor_store is not None else None
    # A throwaway/pinned home never consults the system account keychain.
    system_home = Path.home()
    import os
    try:
        import pwd
        system_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError):
        pass
    allow_keychain = home.resolve() == system_home.resolve()
    if value:
        if _cursor_token_claims(value) is not None:
            return value
        ciphertext = _cursor_safe_storage_ciphertext(value)
        if ciphertext is None:
            return None
        password = _keychain_password("Cursor Safe Storage") if allow_keychain else None
        if password is None:
            return None
        key = hashlib.pbkdf2_hmac("sha1", password.encode(), b"saltysalt", 1003, 16)
        plain = _aes_128_cbc_decrypt(key, b" " * 16, ciphertext)
        try:
            token = plain.decode("utf-8").strip() if plain else None
        except UnicodeError:
            return None
        return token if token and _cursor_token_claims(token) is not None else None
    if editor_store is not None:
        return None  # existing missing/corrupt/locked store must not select another account
    token = _read_cursor_cli_token(str(home / ".config/cursor/auth.json"))
    if not token and sys.platform == "darwin" and allow_keychain:
        token = _keychain_password("cursor-access-token", "cursor-user")
    return token if token and _cursor_token_claims(token) is not None else None
