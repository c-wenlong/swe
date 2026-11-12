"""``swe mcp gateway``: the shared-server flags and the daemon's status.

The daemon itself is the standalone ``warp-gate`` package
(``starship-terminal/warp-gate``): it runs each ``"shared": true`` stdio
server once and serves it as streamable HTTP on 127.0.0.1 at
``/mcp/<name>``, installed as a LaunchAgent by ``warp-gate install``.
swe keeps the manifest side — ``share``/``unshare`` edit ``"shared"`` in
mcp.json, ``swe mcp sync`` writes the gateway URLs — and ``status`` reads
the daemon's ``/status`` endpoint, which warp-gate serves identically.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from swe.console import c, cpad
from swe.json_output import emit, take_json_flag
from swe.mcp import gateway as gw
from swe.remote import launchd

# warp-gate's LaunchAgent (``warp-gate install``); ``installed``/``pid``
# in status describe it.
WARP_GATE_LABEL = "com.starship-terminal.warp-gate"
# The labels swe's retired gateway and Quiver.app installed under. A
# machine can still carry one, so status counts it as installed rather
# than calling its still-answering daemon absent.
LEGACY_LABELS = ("com.cwenlong.swe.mcp-gateway", "com.cwenlong.quiver.mcp-gateway")


def _hub_data() -> dict:
    from swe.mcp.cli import MCP_SOURCE_FILE, load_json

    return load_json(MCP_SOURCE_FILE) or {}


def _save_hub_data(data: dict) -> None:
    from datetime import datetime

    from swe.mcp.cli import MCP_SOURCE_FILE, save_json

    data["updated"] = datetime.now().isoformat()
    save_json(MCP_SOURCE_FILE, data)


def current_settings() -> dict:
    return gw.settings_of(_hub_data())


def _save_settings(**changes) -> dict:
    data = _hub_data()
    merged = {**gw.settings_of(data), **changes}
    data[gw.GATEWAY_KEY] = merged
    _save_hub_data(data)
    return merged


def _fetch(port: int, path: str, timeout: float = 2.0):
    """GET a gateway endpoint as JSON, or None when nothing answers."""
    try:
        with urllib.request.urlopen(f"http://{gw.HOST}:{port}{path}", timeout=timeout) as resp:
            return json.loads(resp.read() or b"null")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _sync_hint(names: list[str] | None = None) -> None:
    only = f" --only={','.join(names)}" if names else ""
    print(c("dim", f"  point harnesses at it: swe mcp sync swe --all{only} --force"))


# -- share / unshare ------------------------------------------------------


def _share(args: list[str], shared: bool) -> int:
    from swe.mcp.cli import get_hub_servers, save_hub_servers

    force = "--force" in args
    names = [a for a in args if not a.startswith("-")]
    verb = "share" if shared else "unshare"
    if not names:
        print(f"Usage: {c('cyan', f'swe mcp gateway {verb} <server...>')}")
        return 1
    servers = get_hub_servers()
    unknown = [n for n in names if n not in servers]
    if unknown:
        print(c("red", f"not in the hub: {', '.join(unknown)}"), file=sys.stderr)
        return 1
    if shared:
        remote = [n for n in names if not servers[n].get("command") or servers[n].get("url")]
        if remote:
            print(c("red", f"only stdio servers can be shared; reached at a URL: "
                           f"{', '.join(remote)}"), file=sys.stderr)
            return 1
        risky = [n for n in names if gw.looks_stateful(n, servers[n])]
        if risky and not force:
            print(c("red", f"{', '.join(risky)} looks like it keeps state per session (a "
                           "browser or REPL), and one shared copy would mix sessions up. "
                           "Pass --force to share it anyway."), file=sys.stderr)
            return 1
    changed = []
    for name in names:
        entry = dict(servers[name])
        if gw.is_shared(entry) == shared:
            continue
        if shared:
            entry[gw.SHARED_KEY] = True
        else:
            entry.pop(gw.SHARED_KEY, None)
        servers[name] = entry
        changed.append(name)
    if changed:
        save_hub_servers(servers)
    print(f"{'shared' if shared else 'unshared'}: {', '.join(changed)}" if changed
          else c("dim", f"nothing to change: already {'shared' if shared else 'unshared'}"))
    settings = current_settings()
    if changed and settings["enabled"]:
        _sync_hint(changed)
    elif changed and shared:
        print(c("dim", "  the gateway daemon is a separate package now: "
                      "install it with `warp-gate install`"))
    return 0


# -- status ---------------------------------------------------------------


def gateway_status() -> dict:
    """The ``swe mcp gateway status --json`` payload."""
    from swe.mcp.cli import get_hub_servers

    settings = current_settings()
    installed = False
    agent = None
    pid = None
    if sys.platform == "darwin":
        # Any of the labels counts: a retired swe/quiver agent still runs
        # a gateway that answers /status. A loaded job wins over a
        # stopped plist: a failed install leaves the plist behind while
        # the old job keeps answering.
        for name in (WARP_GATE_LABEL, *LEGACY_LABELS):
            if launchd.plist_path(label=name).exists():
                installed = True
                agent = name
                loaded, pid = launchd.loaded_pid(label=name)
                if loaded:
                    break
    live = _fetch(settings["port"], "/status")
    running = {row["name"]: row for row in (live or {}).get("servers", [])
               if isinstance(row, dict) and "name" in row}
    rows = []
    for name, server in sorted(get_hub_servers().items()):
        if not gw.is_shared(server):
            continue
        # An older daemon has no counters: its rows read as zeros.
        row = {"calls": 0, "calls_by_tool": {}, "call_errors": 0, "last_call": None,
               **(running.get(name) or {"state": "idle" if live else "down", "pid": None,
                                        "uptime_s": None, "starts": 0, "error": None})}
        rows.append({"name": name, "url": gw.url_for(name, settings["port"]),
                     **{k: row.get(k) for k in ("state", "pid", "uptime_s", "starts", "error",
                                                "calls", "calls_by_tool", "call_errors",
                                                "last_call")}})
    return {"enabled": settings["enabled"], "port": settings["port"],
            "installed": installed, "agent": agent, "pid": pid,
            "reachable": live is not None,
            "error": (live or {}).get("error"), "servers": rows}


def _status(args: list[str]) -> int:
    args, json_view = take_json_flag(list(args))
    status = gateway_status()
    if json_view:
        return emit(status)
    port = status["port"]
    if status["reachable"]:
        head = c("green", f"running on http://{gw.HOST}:{port}")
    elif status["installed"]:
        head = c("yellow", f"installed but not answering on port {port}")
    else:
        head = c("dim", "not installed")
    print(f"\n  {c('bold', 'MCP gateway')}  {head}")
    if status["error"]:
        print(c("yellow", f"  {status['error']}"))
    if status.get("agent") and status["agent"] != WARP_GATE_LABEL:
        print(c("yellow", f"  the installed agent ({status['agent']}) is swe's retired "
                          "gateway; the daemon now runs as warp-gate: `warp-gate install`, "
                          "then bootout and delete the old plist"))
    if status["enabled"] != status["installed"] and sys.platform == "darwin":
        note = ("sync points harnesses here, but no LaunchAgent runs it: warp-gate install"
                if status["enabled"] else "installed, but sync still writes commands; "
                "run warp-gate install again")
        print(c("yellow", f"  {note}"))
    if not status["servers"]:
        print(c("dim", "  no shared servers yet: swe mcp gateway share <server...>\n"))
        return 0
    print()
    for row in status["servers"]:
        state = row["state"]
        colour = {"running": "green", "error": "red", "down": "yellow"}.get(state, "dim")
        detail = (f"pid {row['pid']}, up {row['uptime_s']}s" if state == "running"
                  else row.get("error") or "")
        if row.get("calls"):
            detail = f"{detail}, {row['calls']} calls" if detail else f"{row['calls']} calls"
        print(f"  {row['name']:<24} {cpad(colour, state, 9)} {c('dim', detail)}")
    print()
    return 0


def cmd_gateway(args: list[str]) -> int:
    from swe.mcp.cli import MCP_HELP

    sub = args[0] if args else "status"
    rest = args[1:]
    if sub in ("-h", "--help", "help") or "--help" in rest or "-h" in rest:
        print(MCP_HELP["gateway"])
        return 0
    handlers = {
        "status": _status,
        "share": lambda a: _share(a, True),
        "unshare": lambda a: _share(a, False),
    }
    if sub not in handlers:
        print(f"Unknown gateway command: {sub}")
        print(f"Available: {', '.join(handlers)}")
        return 1
    return handlers[sub](rest)
