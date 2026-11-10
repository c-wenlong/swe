"""Local plugin inventory and non-destructive adoption into the SWE hub."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from swe.paths import plugins_dir_for
from swe.plugins.actions import ActionError, _config_lock, split_ref
from swe.plugins.catalog import build_catalog
from swe.plugins.models import ActionResult


def managed_plugin(market: dict, plugin: dict) -> bool:
    source, root = plugin.get("source_path"), market.get("path")
    return bool(market.get("hub") and source and root
                and Path(source).resolve().is_relative_to(Path(root).resolve()))


def local_plugin(market: dict, plugin: dict) -> bool:
    """Owned source, or an installed/cached copy; a marketplace clone alone is not usage."""
    if managed_plugin(market, plugin) and Path(plugin["source_path"]).is_dir():
        return True
    for state in plugin.get("harnesses", {}).values():
        if state.get("installed") is False:
            continue
        if any(Path(p).is_dir() for p in [state.get("path"), *state.get("copies", [])] if p):
            return True
    return False


def import_plugin(home: Path, ref: str) -> dict:
    """Copy an installed plugin into SWE. Never install or change a harness."""
    name, market_name = split_ref(ref)
    if name in (".", "..") or market_name in (".", ".."):
        raise ActionError("plugin and marketplace names cannot be . or ..")
    with _config_lock(home, "retry the plugin import"):
        catalog = build_catalog(home)
        market = next((m for m in catalog["marketplaces"] if m["name"] == market_name), None)
        plugin = next((p for p in market["plugins"] if p["ref"] == ref), None) if market else None
        if plugin is None or not local_plugin(market, plugin):
            raise ActionError("No installed plugin files to import; install/download it first")
        if managed_plugin(market, plugin):
            return _reply(ref, False, "already managed by SWE", plugin["source_path"])
        # Prefer the actual installed bytes over a marketplace's preview.
        candidates = [s.get("path") for s in plugin["harnesses"].values()
                      if s.get("installed") is not False]
        candidates += [p for s in plugin["harnesses"].values()
                       if s.get("installed") is not False for p in s.get("copies", [])]
        source = next((Path(p) for p in candidates if p and Path(p).is_dir()), None)
        if source is None:
            raise ActionError("The plugin's installed files are missing; refresh and try again")
        root = plugins_dir_for(home)
        root.mkdir(parents=True, exist_ok=True)
        target_market = Path(market["path"]) if market.get("hub") else root / market_name
        if not target_market.resolve().is_relative_to(root.resolve()):
            raise ActionError("The destination marketplace points outside SWE")
        manifest = target_market / ".claude-plugin" / "marketplace.json"
        destination = target_market / "plugins" / name
        if destination.exists() or destination.is_symlink():
            raise ActionError(f"{destination} already exists; nothing was overwritten")
        if manifest.exists():
            try:
                data = json.loads(manifest.read_text())
            except (OSError, ValueError) as exc:
                raise ActionError(f"Cannot read the SWE marketplace: {exc}") from None
            if not isinstance(data, dict) or data.get("name") != market_name or not isinstance(data.get("plugins"), list):
                raise ActionError("The SWE marketplace manifest is invalid; nothing was changed")
            if any(p.get("name") == name for p in data["plugins"] if isinstance(p, dict)):
                raise ActionError("The SWE marketplace already declares this plugin; nothing was overwritten")
        else:
            if target_market.exists():
                raise ActionError("The destination exists without a marketplace manifest; nothing was changed")
            data = {"name": market_name, "owner": {"name": "SWE"}, "plugins": []}
        # Dereference links to make an independent copy, but never copy a
        # link outside the installed plugin (credentials, sibling caches).
        resolved_source = source.resolve()
        for base, dirs, files in os.walk(source, followlinks=False):
            for part in dirs + files:
                path = Path(base) / part
                if path.is_symlink() and (not path.exists() or not path.resolve().is_relative_to(resolved_source)):
                    raise ActionError(f"Cannot import a link outside the plugin: {path}")
                if path.is_symlink() and path.is_dir():
                    raise ActionError(f"Cannot import a directory symlink; materialize it first: {path}")
        try:
            with tempfile.TemporaryDirectory(prefix=".import-", dir=root) as scratch:
                stage = Path(scratch)
                shutil.copytree(source, stage / "plugin", ignore=shutil.ignore_patterns(".git"))
                data["plugins"].append({"name": name, "source": f"./plugins/{name}"})
                (stage / "marketplace.json").write_text(json.dumps(data, indent=2) + "\n")
                if not target_market.exists():
                    (stage / "market" / ".claude-plugin").mkdir(parents=True)
                    (stage / "market" / "plugins").mkdir()
                    shutil.move(stage / "plugin", stage / "market" / "plugins" / name)
                    shutil.move(stage / "marketplace.json", stage / "market" / ".claude-plugin" / "marketplace.json")
                    os.rename(stage / "market", target_market)
                else:
                    if destination.parent.is_symlink() or not destination.parent.resolve().is_relative_to(root.resolve()):
                        raise ActionError("The destination plugins folder points outside SWE")
                    if manifest.is_symlink() or not manifest.parent.resolve().is_relative_to(root.resolve()):
                        raise ActionError("The marketplace manifest points outside SWE")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.rename(stage / "plugin", destination)
                    try:
                        os.replace(stage / "marketplace.json", manifest)
                    except OSError:
                        shutil.rmtree(destination)
                        raise
        except (OSError, shutil.Error) as exc:
            raise ActionError(f"Could not import {ref}: {exc}") from None
        return _reply(ref, True, None, str(destination))


def _reply(ref: str, changed: bool, detail: str | None, path: str) -> dict:
    return ActionResult("import", "swe", ref, changed, detail, [], path, None,
                        {"managed": True, "path": path}).to_json()
