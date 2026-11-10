"""Global OpenCode file plugins. Disabled copies live outside discovery, in SWE.

No source is executed. Package configuration and project plugins are deliberately
outside this adapter; moving a discovered file is supported by both OpenCode
generations without guessing a plugin's runtime ID.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from swe.find.plugins import _plugin_capable, _plugin_root


@dataclass
class LocalPlugin:
    name: str
    path: Path
    enabled: bool
    root: Path
    parked: Path

    @property
    def ref(self) -> str:
        slug = re.sub(r"[^A-Za-z0-9_.+-]", "-", self.name).strip(".-") or "plugin"
        slug = slug[:180]
        identity = str(self.root.absolute()) + "\0" + self.name
        digest = hashlib.sha256(identity.encode()).hexdigest()[:12]
        return f"{slug}-{digest}@opencode-local"


def local_plugins(home: Path) -> list[LocalPlugin]:
    from swe.harness.registry import load_registry_if_present

    reg = load_registry_if_present()
    if not _plugin_capable(reg, "opencode"):
        return []
    root = _plugin_root(reg, "opencode", home) or home / ".config/opencode/plugins"
    key = hashlib.sha256(str(root.absolute()).encode()).hexdigest()
    parked = home / ".swe/data/plugin-disabled/opencode" / key
    found = []
    for folder, enabled in ((root, True), (parked, False)):
        try:
            paths = sorted(folder.iterdir())
        except OSError:
            continue
        for path in paths:
            if path.name.startswith(".") or path.name in ("node_modules", "__pycache__"):
                continue
            if enabled and path.is_dir():
                try:
                    if not any(path.iterdir()):
                        continue
                except OSError:
                    continue
            if (not enabled and path.is_symlink()) or path.is_dir() or (path.is_file() and path.suffix in (".js", ".ts")):
                found.append(LocalPlugin(path.name, path, enabled, root, parked))
    return found


def local_action(home: Path, action: str, ref: str) -> dict:
    from swe.plugins.actions import ActionError, _config_lock
    from swe.plugins.models import ActionResult

    if action not in ("enable", "disable", "uninstall"):
        raise ActionError("OpenCode local plugins support enable, disable and uninstall")
    with _config_lock(home, "retry the OpenCode plugin action"):
        matches = [p for p in local_plugins(home) if p.ref == ref]
        if len(matches) != 1:
            raise ActionError("OpenCode plugin is missing or has conflicting active/disabled copies; refresh")
        plugin = matches[0]
        storage = home / ".swe/data/plugin-disabled/opencode"
        if not plugin.parked.resolve().is_relative_to(storage.resolve()):
            raise ActionError("The disabled plugin folder points outside SWE storage")
        desired = action == "enable"
        changed = action == "uninstall" or plugin.enabled != desired
        destination = plugin.path
        try:
            if action == "uninstall":
                if plugin.path.is_symlink() or plugin.path.is_file():
                    plugin.path.unlink()
                else:
                    shutil.rmtree(plugin.path)
            elif changed:
                destination = (plugin.root if desired else plugin.parked) / plugin.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists() or destination.is_symlink():
                    raise ActionError("The destination already exists; nothing was overwritten")
                # Never follows a plugin symlink or copies across filesystems.
                os.rename(plugin.path, destination)
        except OSError as exc:
            raise ActionError(f"Could not {action} the OpenCode plugin: {exc.strerror}") from None
        return ActionResult(
            action=action, harness="opencode", ref=ref, changed=changed,
            detail=("Applies to global file discovery; restart existing OpenCode sessions"
                    if changed else f"already {action}d"),
            ran=[], edited=str(destination) if changed else None, output=None,
            state={"installed": action != "uninstall",
                   "enabled": desired if action != "uninstall" else None,
                   "version": None},
        ).to_json()
