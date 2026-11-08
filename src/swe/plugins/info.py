"""``swe plugins info <ref>`` — one plugin's full detail sheet.

Rolls the catalog row up with what the table view cannot show: the MCP
servers the plugin declares (names and env/header key names, never
values), where those servers stand in the swe hub, when the plugin's
files last changed, and how often its skills and servers get called in
the session search index.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from swe.plugins.catalog import build_catalog, mcp_server_details, newest


def _latest_mtime(folder: Path | None, cap: int = 5000) -> float | None:
    """Newest mtime under ``folder``, reading at most ``cap`` entries."""
    if folder is None or not folder.is_dir():
        return None
    newest: float | None = None
    seen = 0
    for root, dirs, files in os.walk(folder):
        for name in (*dirs, *files):
            seen += 1
            if seen > cap:
                return newest
            try:
                mtime = (Path(root) / name).stat().st_mtime
            except OSError:
                continue
            if newest is None or mtime > newest:
                newest = mtime
    return newest


def _iso(mtime: float | None) -> str | None:
    if mtime is None:
        return None
    return datetime.fromtimestamp(mtime, timezone.utc).isoformat()


def _inventory_rows() -> list[dict]:
    """The ``swe mcp list`` rows, keyed for the info join.

    Kept as a function so a partial inventory — or none, when the MCP
    subsystem cannot read this machine — degrades to "declared only"
    instead of failing the sheet. Tests patch it to inject rows.
    """
    try:
        from swe.mcp.cli import (_display_tools, get_mcp_tools,
                                 get_tool_servers, load_registry)
        from swe.mcp.inventory import server_inventory

        registry = load_registry()
        tool_data = {t: get_tool_servers(t)
                     for t in _display_tools(get_mcp_tools(registry))}
        return [row for row, _raw
                in server_inventory(tool_data, include_hub=True)]
    except Exception:
        return []


def _find_plugin(catalog: dict, ref: str) -> dict | None:
    """The catalog row for ``name@market``, or ``name`` when unambiguous."""
    matches = []
    for market in catalog["marketplaces"]:
        for plugin in market["plugins"]:
            if plugin["ref"] == ref or (
                    "@" not in ref and plugin["name"] == ref):
                matches.append(plugin)
    return matches[0] if len(matches) == 1 else None


def plugin_info(ref: str, home: Path | None = None) -> dict | None:
    """The info-sheet payload, or ``None`` when the ref resolves to nothing."""
    home = home or Path.home()
    catalog = build_catalog(home)
    plugin = _find_plugin(catalog, ref)
    if plugin is None:
        return None

    # Declared servers come from the folder the catalog calls primary:
    # the loaded copy when one is on, else the source folder, else the
    # newest cached copy (a plugin only in a harness cache still has one).
    folder = next((Path(s["path"]) for s in plugin["harnesses"].values()
                   if s.get("path")), None)
    if folder is None and plugin.get("source_path"):
        folder = Path(plugin["source_path"])
    if folder is None:
        folder = newest([Path(c) for s in plugin["harnesses"].values()
                         for c in s.get("copies") or []])
    declared = mcp_server_details(folder)

    # Join declared servers with the swe-side inventory for state and
    # secret names. Inventory keys are names only, values stay sealed.
    installed = {row["name"]: row for row in _inventory_rows()}

    from swe.skills.usage import index_rows, mcp_usage, plugin_usage, skill_usage

    rows = index_rows(home)
    mcp_servers = []
    for decl in declared:
        inv = installed.get(decl["name"], {})
        mcp_servers.append({
            **decl,
            "env_keys": sorted(set(decl["env_keys"]) | set(inv.get("env_keys") or [])),
            "header_keys": sorted(set(decl["header_keys"]) | set(inv.get("header_keys") or [])),
            "harnesses": inv.get("harnesses") or [],
            "disabled_in": inv.get("disabled_in") or [],
            "in_hub": inv.get("in_hub", False),
            "secrets_missing": inv.get("secrets_missing") or [],
            "usage": mcp_usage(decl["name"], rows=rows),
        })
    skills = [dict(s, usage=skill_usage(s["name"], rows=rows))
              for s in plugin["skills"]]
    server_names = [s["name"] for s in mcp_servers]
    skill_names = [s["name"] for s in skills]
    usage = plugin_usage(server_names, skill_names, rows=rows) \
        if rows is not None else {"tracked": False}
    return {
        "action": "info",
        "ref": plugin["ref"],
        "plugin": plugin,
        "mcp_servers": mcp_servers,
        "skills": skills,
        "updated": _iso(_latest_mtime(folder)),
        "usage": usage,
    }
