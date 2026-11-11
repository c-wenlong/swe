"""Cursor personal plugin controls through its DashboardService Connect API.

Verified against Cursor 3.29.15's packaged protobuf/client contracts. The
SQLite installedIds value is a UI cache, never an enable-state database.
Only personal installs are writable here; team policies remain authoritative.
"""
from __future__ import annotations

import http.client
import json
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from swe import __version__

_BASE = "https://api2.cursor.sh/aiserver.v1.DashboardService/"
_USER_MODES = ("EFFECTIVE_PLUGIN_INSTALL_MODE_USER", 1)
_MAX_REPLY = 8 * 1024 * 1024


class CursorPluginError(Exception):
    pass


def _id(value) -> str | None:
    if isinstance(value, bool):
        return None
    value = str(value) if isinstance(value, (int, str)) else ""
    return value if re.fullmatch(r"[1-9][0-9]*", value) and int(value) < 2**63 else None


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", value)


@dataclass(frozen=True)
class CursorPlugin:
    id: str
    name: str
    marketplace: str
    enabled: bool
    actions: tuple[str, ...]
    detail: str | None = None

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.marketplace}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CursorClient:
    def __init__(self, home: Path, *, token: str | None = None, opener=None):
        if token is None:
            from swe.plugins.cursor_auth import cursor_token
            token = cursor_token(home)
        if not token or any(c in token for c in "\r\n"):
            raise CursorPluginError("Cursor credentials are unavailable or protected. Sign in to Cursor and allow SWE access, then refresh.")
        self.token = token
        if opener is None:
            from swe.harness.rate_limits import _verified_context
            try:
                # Includes the Windows trust store; Unix bundle files are optional.
                try:
                    context = ssl.create_default_context()
                except (OSError, ssl.SSLError, ValueError):
                    context = None
                fallback = _verified_context()
                if context is None:
                    context = fallback
                elif fallback is not None:
                    # Add verified bundle roots without dropping native/private trust.
                    roots = fallback.get_ca_certs(binary_form=True)
                    if roots:
                        context.load_verify_locations(cadata="".join(ssl.DER_cert_to_PEM_cert(c) for c in roots))
                if context is None:
                    raise CursorPluginError("Cannot initialize verified TLS for Cursor plugin requests.")
            except (OSError, ssl.SSLError, ValueError):
                raise CursorPluginError("Cannot initialize verified TLS for Cursor plugin requests.") from None
            opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=context)).open
        self.opener = opener

    def call(self, method: str, data: dict) -> dict:
        request = urllib.request.Request(_BASE + method, data=json.dumps(data).encode(), method="POST", headers={
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
            "User-Agent": f"swe/{__version__}",
        })
        try:
            with self.opener(request, timeout=10) as response:
                raw = response.read(_MAX_REPLY + 1)
            if len(raw) > _MAX_REPLY:
                raise CursorPluginError("Cursor returned an oversized plugin response.")
            result = json.loads(raw)
        except urllib.error.HTTPError as exc:
            # Never echo a response body, headers, URL parameters or token.
            if exc.code in (401, 403):
                raise CursorPluginError("Cursor plugin access was refused. Sign in to Cursor and refresh.") from None
            raise CursorPluginError(f"Cursor plugin request failed (HTTP {exc.code}).") from None
        except urllib.error.URLError:
            raise CursorPluginError("Cannot reach Cursor's plugin service. Refresh when it is available.") from None
        except (OSError, ValueError, http.client.HTTPException):
            raise CursorPluginError("Cannot read Cursor's plugin response.") from None
        if not isinstance(result, dict) or "error" in result or ("code" in result and "message" in result):
            raise CursorPluginError("Cursor rejected the plugin request.")
        return result

    def plugins(self) -> dict[str, CursorPlugin]:
        effective = self.call("GetEffectiveUserPlugins", {"useReplica": False, "excludeConfiguredVariables": True})
        personal = self.call("ListUserPluginInstalls", {"useReplica": False})
        entries, installs = effective.get("plugins", []), personal.get("installs", [])
        markets = effective.get("marketplaces", [])
        if not all(isinstance(v, list) for v in (entries, installs, markets)):
            raise CursorPluginError("Cursor returned an unsupported plugin inventory.")
        personal_by_id = {_id(p.get("pluginId")): p for p in installs if isinstance(p, dict) and _id(p.get("pluginId"))}
        self.personal_ids = set(personal_by_id)
        personal_ids = self.personal_ids
        market_by_id = {_id(m.get("id")): m for m in markets if isinstance(m, dict) and _id(m.get("id"))}
        found: dict[str, CursorPlugin] = {}
        ambiguous: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("plugin"), dict):
                continue
            plugin = entry["plugin"]
            pid = _id(plugin.get("id"))
            market = plugin.get("marketplace") or market_by_id.get(_id(plugin.get("marketplaceId")))
            name = plugin.get("name")
            if not pid or not isinstance(name, str) or not name.strip() or not isinstance(market, dict):
                continue
            market_name = market.get("name")
            if not isinstance(market_name, str) or not market_name.strip():
                continue
            enabled = entry.get("isEnabled", False)  # protobuf bool default
            if not isinstance(enabled, bool):
                raise CursorPluginError("Cursor returned an invalid plugin enable state.")
            # Never turn a team-default/required installation into a
            # personal override, or mutate a team installation.
            mode = entry.get("installMode")
            owned = pid in personal_ids and not isinstance(mode, bool) and mode in _USER_MODES and entry.get("isTeamRequired", False) is False
            detail = None if owned else "Controlled by Cursor team policy."
            keys = entry.get("configuredVariableKeys", [])  # protobuf repeated default
            team_values = entry.get("hasTeamConfiguredVariables", False)
            pins = (personal_by_id.get(pid, {}).get("pinnedGitRef"), entry.get("pinnedGitRef"))
            unpinned = all(value is None or (isinstance(value, str) and not value) for value in pins)
            safe_toggle = (isinstance(keys, list) and not keys and team_values is False
                           and unpinned and not entry.get("configuredVariables"))
            if owned:
                actions = (("disable", "uninstall") if enabled else ("enable", "uninstall")) if safe_toggle else ("uninstall",)
                if not safe_toggle:
                    detail = "On/off controls for configured or pinned plugins remain in Cursor; removal is available."
            else:
                actions = ()
            item = CursorPlugin(pid, _slug(name.strip()), _slug(market_name), enabled, actions, detail)
            if item.ref in found and found[item.ref].id != pid:
                ambiguous.add(item.ref)
            found[item.ref] = item
        # A sanitized cache name is not enough to choose between IDs.
        if ambiguous:
            raise CursorPluginError("Cursor plugin names collide after cache-name normalization; controls are unavailable.")
        return found

    def change(self, action: str, ref: str) -> dict:
        before = self.plugins().get(ref)
        if before is None:
            raise CursorPluginError("Cursor could not uniquely match this plugin to an account installation.")
        if action not in ("enable", "disable", "uninstall"):
            raise CursorPluginError("Cursor supports enabling, disabling and removing existing personal plugins here.")
        want = action == "enable"
        if action in ("enable", "disable") and before.enabled == want and any(a in before.actions for a in ("enable", "disable")):
            return self._reply(action, ref, False, f"already {action}d", True, before.enabled)
        if action not in before.actions:
            raise CursorPluginError(before.detail or "Change this plugin in Cursor.")
        if action == "uninstall":
            reply = self.call("UninstallUserPlugin", {"pluginId": before.id})
            if reply.get("success") is not True:
                raise CursorPluginError("Cursor did not confirm removal. Refresh before trying again.")
        else:
            # Request a merge and omit variables and pinned refs; only
            # the enable flag is sent. Never request or resend secrets.
            self.call("UpdateUserPluginInstall", {"pluginId": before.id, "isEnabled": want, "mergeWithStored": True})
        after = self.plugins().get(ref)
        verified = before.id not in self.personal_ids if action == "uninstall" else after is not None and after.id == before.id and after.enabled == want
        if not verified:
            raise CursorPluginError("Cursor has not confirmed the new plugin state. Refresh before trying again.")
        detail = "Refocus Cursor or start a new CLI session to apply the change."
        if action == "uninstall" and after is not None:
            detail = "Personal installation removed; Cursor's team policy still provides this plugin. " + detail
        return self._reply(action, ref, True, detail, after is not None, after.enabled if after else None)

    @staticmethod
    def _reply(action, ref, changed, detail, installed, enabled) -> dict:
        from swe.plugins.models import ActionResult
        return ActionResult(action, "cursor", ref, changed, detail, [], None, None,
                            {"installed": installed, "enabled": enabled, "version": None}).to_json()
