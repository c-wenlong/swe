"""Every plugin in every marketplace a harness has registered, installed or not.

``swe find plugins`` lists what is installed and ``swe skills hub`` what
``~/.swe/plugins`` offers. This is the third view the app's Plugins page
needs: the whole catalog of every marketplace Claude Code, Codex or Cursor
knows about, plus Devin resolved installs and OpenCode global file plugins,
each with its install and enable state per harness.

Where each harness keeps things (read from files only, never the network):

  claude  ``~/.claude/plugins/known_marketplaces.json`` (materialised) and
          ``settings.json`` ``extraKnownMarketplaces`` (declared) name the
          marketplaces; each one's manifest is
          ``<installLocation>/.claude-plugin/marketplace.json``.
          ``installed_plugins.json`` is the install record and
          ``settings.json`` ``enabledPlugins`` the enable flags.
  codex   ``~/.codex/config.toml`` ``[marketplaces.<name>]`` names them, with
          ``source_type`` ``local`` (a directory) or ``git`` (cloned to
          ``~/.codex/.tmp/marketplaces/<name>``). The manifest is
          ``.agents/plugins/marketplace.json`` in that directory, or
          ``.claude-plugin/marketplace.json`` for a Claude-format one.
          ``[plugins."<ref>"]`` is the install record, with its ``enabled``
          flag; the copy codex runs is ``plugins/cache/<market>/<plugin>/<v>``.
  cursor  keeps downloads in ``~/.cursor/plugins/cache/<market>/<plugin>/<v>``.
          Account state comes from its DashboardService API when credentials
          can be read without prompting. Cached-only state stays unknown.

A file this module cannot parse costs only what it describes: a malformed
manifest drops that marketplace's listing for that harness (its installed
plugins still show, from the install record), and an unreadable install
record drops that harness. Each is named in ``errors``. Nothing here writes,
and nothing here prints a secret: only plugin metadata, paths, and source
locations with any credentials stripped from URLs.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from swe.find.plugin_drift import _BROKEN, LITTER, _local_source, _read_json, _read_toml, _real
from swe.find.plugins import (
    PLUGIN_FALLBACK,
    _expand_root,
    _plugin_capable,
    _plugin_root,
    count_components,
)
from swe.paths import plugins_dir_for

HARNESSES = ("claude", "codex", "cursor", "devin", "opencode")
# Harnesses whose CLI swe can drive for install, uninstall, enable, disable.
ACTIONABLE = ("claude", "codex")
CONTROLLABLE = (*ACTIONABLE, "cursor", "devin", "opencode")

# Where a plugin keeps its own manifest, per harness format.
_PLUGIN_MANIFESTS = (".devin-plugin", ".claude-plugin", ".codex-plugin", ".cursor-plugin", ".plugin")


@dataclass
class Market:
    """One marketplace as one harness has registered it."""
    name: str
    source_type: str          # directory, github, git, url, npm, file, cache
    location: str             # path, owner/repo or URL (credentials stripped)
    root: Path | None         # the directory the manifest sits in
    manifest: Path | None = None
    entries: list | None = None   # the manifest's plugin entries; None if unreadable
    description: str | None = None


@dataclass
class Install:
    version: str | None
    path: Path | None         # the installed copy
    scope: str | None = None  # claude's install scope


@dataclass
class View:
    """What one harness has registered and installed."""
    harness: str
    markets: dict             # name -> Market
    installs: dict            # "plugin@marketplace" -> Install
    enabled: dict             # "plugin@marketplace" -> bool
    cache: Path | None        # <cache>/<marketplace>/<plugin>/<version>
    installs_known: bool = True
    scopes: dict = field(default_factory=dict)   # claude: ref -> every scope with a record
    plugin_notes: dict = field(default_factory=dict)
    allowed_actions: dict = field(default_factory=dict)  # ref -> supported verbs


def _err(errors: list, harness: str, detail: str, marketplace: str | None = None,
         path: Path | None = None) -> None:
    error = {"harness": harness, "marketplace": marketplace, "detail": detail}
    if path is not None:
        error["path"] = str(path)
    errors.append(error)


def _public(location: str) -> str:
    """A source location safe to print: a URL loses userinfo, query, fragment.

    ``https://user:token@host/repo.git`` is a real way to register a private
    marketplace, and the token must not reach the app or a terminal.
    """
    if "://" not in location:
        return location
    try:
        parts = urlsplit(location)
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
    except ValueError:
        return ""
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _entries(plugins: list) -> list[dict]:
    """The manifest's plugin entries that name a plugin, first one per name."""
    out, seen = [], set()
    for entry in plugins:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        out.append(entry)
    return out


def _manifest_description(data: dict) -> str | None:
    meta = data.get("metadata")
    return _text(data.get("description")) or (
        _text(meta.get("description")) if isinstance(meta, dict) else None)


def _read_manifest(market: Market, candidates: list[Path], harness: str,
                   errors: list) -> Market:
    """Fill ``market`` from the first manifest candidate that exists."""
    for path in candidates:
        if not path.is_file():
            continue
        market.manifest = path
        data = _read_json(path)
        if data is _BROKEN or not isinstance(data.get("plugins"), list):
            _err(errors, harness, f"{path} is not a readable marketplace manifest", market.name)
            return market
        market.entries = _entries(data["plugins"])
        market.description = _manifest_description(data)
        return market
    if candidates:
        market.manifest = candidates[0]
    # The folder, so a reader can tell a hub that never reached this
    # machine (#696) from a typo in one registration.
    _err(errors, harness, "no marketplace manifest on disk", market.name, market.root)
    return market


def entry_source_dir(source) -> Path | None:
    """A manifest entry's plugin directory, relative to the marketplace.

    Claude writes a local source as ``"./eng"``; codex as ``{"source":
    "local", "path": "./plugins/eng"}``. Anything else (git-subdir, github,
    url) is fetched at install time and has no folder here.
    """
    if isinstance(source, dict) and source.get("source") == "local":
        source = source.get("path")
    rel = _local_source(source)
    return Path(rel) if rel is not None else None


def _version_dirs(plugin_dir: Path) -> list[Path]:
    try:
        return sorted(p for p in plugin_dir.iterdir()
                      if p.is_dir() and not p.name.startswith(".") and p.name not in LITTER)
    except OSError:
        return []


def newest(paths: list[Path]) -> Path | None:
    """The most recently written copy, the way plugin_drift picks codex's."""
    best = None
    for p in paths:
        try:
            key = (p.stat().st_mtime, p.name)
        except OSError:
            continue
        if best is None or key > best[0]:
            best = (key, p)
    return best[1] if best else None


def cache_copies(cache: Path | None):
    """Yield (marketplace, plugin, [version dirs]) for a harness plugin cache."""
    if cache is None:
        return
    try:
        markets = sorted(p for p in cache.iterdir()
                         if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return
    for mdir in markets:
        try:
            plugins = sorted(p for p in mdir.iterdir()
                             if p.is_dir() and not p.name.startswith("."))
        except OSError:
            continue
        for pdir in plugins:
            versions = _version_dirs(pdir)
            if versions:
                yield mdir.name, pdir.name, versions


# --------------------------------------------------------------------------
# Per-harness readers


def read_claude(home: Path, errors: list) -> View | None:
    base = home / ".claude"
    if not base.is_dir():
        return None
    known = _read_json(base / "plugins" / "known_marketplaces.json")
    installed = _read_json(base / "plugins" / "installed_plugins.json")
    records = installed.get("plugins", {}) if installed is not _BROKEN else None
    if known is _BROKEN or not isinstance(records, dict):
        _err(errors, "claude", "known_marketplaces.json or installed_plugins.json "
                               "is unreadable, so claude is left out")
        return None
    settings = _read_json(base / "settings.json")
    enabled: dict = {}
    if settings is _BROKEN:
        _err(errors, "claude", "settings.json is unreadable, so enabled flags are unknown")
        settings = {}
    else:
        flags = settings.get("enabledPlugins")
        enabled = {ref: flag for ref, flag in
                   (flags if isinstance(flags, dict) else {}).items()
                   if isinstance(flag, bool)}

    # known_marketplaces.json is what claude has materialised; settings'
    # extraKnownMarketplaces is declared and materialised at startup. Either
    # one registers a marketplace; the materialised entry says more.
    declared: dict = {}
    extra = settings.get("extraKnownMarketplaces")
    declared.update(extra if isinstance(extra, dict) else {})
    declared.update(known)
    markets = {}
    for name, entry in declared.items():
        if not isinstance(name, str) or not name or not isinstance(entry, dict):
            continue
        src = entry.get("source") if isinstance(entry.get("source"), dict) else {}
        stype = src.get("source") if isinstance(src.get("source"), str) else "unknown"
        location = next((src[k] for k in ("path", "repo", "url", "package")
                         if isinstance(src.get(k), str) and src[k]), "")
        install_at = entry.get("installLocation")
        if isinstance(install_at, str) and install_at:
            root = _expand_root(install_at, home)
        elif stype == "directory" and location:
            root = _expand_root(location, home)
        else:
            root = base / "plugins" / "marketplaces" / name
        candidates = [root / ".claude-plugin" / "marketplace.json"]
        if stype == "file" and location:
            candidates.insert(0, _expand_root(location, home))
        markets[name] = _read_manifest(
            Market(name, stype, _public(location), root), candidates, "claude", errors)

    installs, scopes = {}, {}
    for ref, rows in records.items():
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(ref, str) or not isinstance(rows, list):
            continue
        # The same rule plugin_drift uses: a record counts when it names its
        # copy. There is one record per scope, and only the user scope loads
        # in every project; a project or local install is reported in
        # `scopes` but is not "installed" here, because `claude plugin
        # install` (user scope by default) would still have work to do.
        rows = [r for r in rows if isinstance(r, dict)
                and isinstance(r.get("installPath"), str) and r["installPath"]]
        scope_of = [r.get("scope") if isinstance(r.get("scope"), str) else "user" for r in rows]
        if rows:
            scopes[ref] = sorted(set(scope_of))
        row = next((r for r, scope in zip(rows, scope_of) if scope == "user"), None)
        if row is not None:
            installs[ref] = Install(str(row["version"]) if row.get("version") else None,
                                    _expand_root(row["installPath"], home), "user")
    view = View("claude", markets, installs, enabled, base / "plugins" / "cache")
    view.scopes = scopes
    return view


def read_codex(home: Path, errors: list) -> View | None:
    base = home / ".codex"
    if not base.is_dir():
        return None
    config = _read_toml(base / "config.toml")
    if config is _BROKEN:
        _err(errors, "codex", "config.toml is unreadable, so codex is left out")
        return None

    markets = {}
    table = config.get("marketplaces")
    for name, entry in (table if isinstance(table, dict) else {}).items():
        if not isinstance(entry, dict):
            continue
        raw_type = entry.get("source_type", "local")
        src = entry.get("source") if isinstance(entry.get("source"), str) else ""
        if raw_type == "local":
            stype, root = "directory", (_expand_root(src, home) if src else None)
        else:
            stype = raw_type if isinstance(raw_type, str) else "unknown"
            root = base / ".tmp" / "marketplaces" / name
        candidates = [] if root is None else [
            root / ".agents" / "plugins" / "marketplace.json",
            root / ".claude-plugin" / "marketplace.json",
        ]
        markets[name] = _read_manifest(
            Market(name, stype, _public(src), root), candidates, "codex", errors)

    cache = base / "plugins" / "cache"
    installs, enabled = {}, {}
    plugins = config.get("plugins")
    for ref, entry in (plugins if isinstance(plugins, dict) else {}).items():
        if not isinstance(entry, dict):
            continue
        # `codex plugin add` writes this table and `remove` deletes it, so
        # the table is the install record; the copy is the newest version
        # directory in the cache, "local" when the manifest has no version.
        name, _, market = ref.rpartition("@")
        copy = newest(_version_dirs(cache / market / name)) if name and market else None
        installs[ref] = Install(copy.name if copy else None, copy)
        if isinstance(entry.get("enabled"), bool):
            enabled[ref] = entry["enabled"]
    return View("codex", markets, installs, enabled, cache)


def read_cursor(home: Path, errors: list) -> View | None:
    """Cursor's cache, read-only: no manifest, no install record."""
    from swe.harness.registry import load_registry_if_present

    reg = load_registry_if_present()
    if not _plugin_capable(reg, "cursor"):
        return None
    root = _plugin_root(reg, "cursor", home) or (home / PLUGIN_FALLBACK["cursor"])
    cache = root / "cache"
    if not cache.is_dir():
        return None
    markets = {}
    for market, _plugin, _versions in cache_copies(cache):
        if market not in markets:
            markets[market] = Market(market, "cache", str(cache / market), None)
    view = View("cursor", markets, {}, {}, cache, installs_known=False)
    from swe.plugins.cursor import CursorClient, CursorPluginError
    if root.resolve() != (home / ".cursor/plugins").resolve():
        return view  # custom cache roots carry no proven account mapping
    try:
        client = CursorClient(home)
    except CursorPluginError as exc:
        _err(errors, "cursor", str(exc))
        return view  # a downloaded cache alone proves no enable state
    try:
        remote = client.plugins()
    except CursorPluginError as exc:
        _err(errors, "cursor", str(exc))
        return view
    view.installs_known = True
    cached = {f"{name}@{market}": versions for market, name, versions in cache_copies(cache)}
    for ref, item in remote.items():
        copies = cached.get(ref, [])
        folder = newest(copies)
        view.installs[ref] = Install(folder.name if folder else None, folder)
        view.enabled[ref] = item.enabled
        view.allowed_actions[ref] = list(item.actions)
        view.plugin_notes[ref] = item.detail
    return view


def read_devin(home: Path, errors: list) -> View | None:
    from swe.find.plugins import _devin_cache_dir
    from swe.harness.registry import load_registry_if_present

    reg = load_registry_if_present()
    if not _plugin_capable(reg, "devin"):
        return None
    default = home / PLUGIN_FALLBACK["devin"]
    root = _plugin_root(reg, "devin", home) or default
    if not root.exists():
        return None
    lock = _read_json(root / "lock.json")
    if lock is _BROKEN or not isinstance(lock.get("resolved"), list):
        _err(errors, "devin", "Cannot read the resolved plugin lock", path=root / "lock.json")
        return None
    rows = []
    for entry in lock["resolved"]:
        if not isinstance(entry, dict):
            continue
        spec = entry.get("spec") if isinstance(entry.get("spec"), dict) else {}
        name = entry.get("name") or spec.get("bundleId")
        identity = entry.get("identity")
        if isinstance(name, str) and name and isinstance(identity, str) and identity:
            rows.append((name, identity, entry))
    names = Counter(name for name, _identity, _entry in rows)
    view = View("devin", {}, {}, {}, None)
    for name, identity, entry in rows:
        # Identity/source labels may contain URL syntax; keep these refs opaque,
        # never turn them into a CLI argument. Native remove takes only the name.
        market = "devin-" + hashlib.sha256(identity.encode()).hexdigest()[:12]
        ref = f"{name}@{market}"
        view.installs[ref] = Install(str(entry.get("version_dir") or ""), _devin_cache_dir(root, entry))
        # A resolved lock is not a load-time policy decision. No on/off claim.
        view.allowed_actions[ref] = (["uninstall"] if names[name] == 1
                                      and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", name)
                                      and root.resolve() == default.resolve() else [])
    return view


def read_opencode(home: Path, errors: list) -> View | None:
    from swe.plugins.local import local_plugins

    rows = local_plugins(home)
    if not rows:
        return None
    view = View("opencode", {}, {}, {}, None)
    counts = Counter(p.ref for p in rows)
    entries = []
    for plugin in rows:
        name, _, market = plugin.ref.rpartition("@")
        view.installs[plugin.ref] = Install(None, plugin.path)
        # Presence describes discovery only; project configuration can override it.
        view.enabled[plugin.ref] = plugin.enabled
        view.allowed_actions[plugin.ref] = (["disable" if plugin.enabled else "enable", "uninstall"]
                                           if counts[plugin.ref] == 1 else [])
        entries.append({"name": name, "displayName": plugin.name,
                        "description": "Global OpenCode file plugin"})
    view.markets["opencode-local"] = Market("opencode-local", "file", "", None, entries=entries)
    return view


READERS = {"claude": read_claude, "codex": read_codex, "cursor": read_cursor,
           "devin": read_devin, "opencode": read_opencode}


def read_views(home: Path, errors: list, harnesses=HARNESSES) -> dict:
    views = {}
    for name in harnesses:
        view = READERS[name](home, errors)
        if view is not None:
            views[name] = view
    return views


def _hub_markets(home: Path, errors: list) -> list[Market]:
    """Each marketplace directory under ~/.swe/plugins, registered or not."""
    root = plugins_dir_for(home)
    try:
        dirs = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []
    out = []
    for d in dirs:
        manifest = d / ".claude-plugin" / "marketplace.json"
        if not manifest.is_file():
            continue
        data = _read_json(manifest)
        if data is _BROKEN or not isinstance(data.get("plugins"), list):
            _err(errors, "hub", f"{manifest} is not a readable marketplace manifest", d.name)
            continue
        name = data.get("name") if isinstance(data.get("name"), str) and data["name"] else d.name
        out.append(Market(name, "directory", str(d), d, manifest,
                          _entries(data["plugins"]), _manifest_description(data)))
    return out


# --------------------------------------------------------------------------
# Building the catalog


@dataclass
class _Plugin:
    name: str
    market: str
    meta: dict = field(default_factory=dict)
    listed_by: set = field(default_factory=set)   # harnesses (or "hub") listing it
    local: dict = field(default_factory=dict)     # origin -> its folder in that marketplace
    copies: dict = field(default_factory=dict)    # harness -> [cached copies]


@dataclass
class _Record:
    name: str
    harnesses: dict = field(default_factory=dict)   # harness -> Market
    listings: list = field(default_factory=list)    # (origin, Market) with entries
    hub: Path | None = None
    hub_origins: set = field(default_factory=set)   # listings read from the hub folder
    plugins: dict = field(default_factory=dict)     # name -> _Plugin

    def plugin(self, name: str) -> _Plugin:
        if name not in self.plugins:
            self.plugins[name] = _Plugin(name, self.name)
        return self.plugins[name]


_META_KEYS = ("displayName", "description", "version", "category", "homepage")


def _merge_meta(meta: dict, data: dict) -> None:
    """Fill the gaps in ``meta`` from a manifest entry or a plugin.json."""
    for key in _META_KEYS:
        if meta.get(key) is None:
            value = data.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and key == "version":
                value = str(value)
            meta[key] = _text(value)
    if meta.get("author") is None:
        author = data.get("author")
        if isinstance(author, dict):
            author = author.get("name")
        meta["author"] = _text(author)
    if not meta.get("tags"):
        tags = []
        for key in ("tags", "keywords"):
            value = data.get(key)
            for tag in value if isinstance(value, list) else []:
                if isinstance(tag, str) and tag.strip() and tag.strip() not in tags:
                    tags.append(tag.strip())
        meta["tags"] = tags


def _plugin_json(folder: Path) -> dict:
    for manifest in _PLUGIN_MANIFESTS:
        data = _read_json(folder / manifest / "plugin.json")
        if data is not _BROKEN and data:
            return data
    return {}


def mcp_server_details(folder: Path | None) -> list[dict]:
    """The MCP servers a plugin declares: name plus env/header key names.

    ``.mcp.json`` (``{"mcpServers": {...}}`` or a bare name map) and the
    plugin manifest's ``mcpServers`` object. Env and header dicts yield
    their keys only — the values can carry API keys and are never read.
    """
    if folder is None:
        return []
    out: dict[str, dict] = {}
    sources = []
    data = _read_json(folder / ".mcp.json")
    if data is not _BROKEN:
        sources.append(data.get("mcpServers", data))
    sources.append(_plugin_json(folder).get("mcpServers"))
    for servers in sources:
        if not isinstance(servers, dict):
            continue
        for name, cfg in servers.items():
            if not isinstance(cfg, dict):
                continue
            entry = out.setdefault(str(name), {"name": str(name),
                                               "env_keys": set(),
                                               "header_keys": set()})
            env = cfg.get("env")
            if isinstance(env, dict):
                entry["env_keys"] |= {str(k) for k in env}
            headers = cfg.get("headers")
            if isinstance(headers, dict):
                entry["header_keys"] |= {str(k) for k in headers}
    return [{**e, "env_keys": sorted(e["env_keys"]),
             "header_keys": sorted(e["header_keys"])}
            for e in sorted(out.values(), key=lambda e: e["name"])]


def mcp_server_count(folder: Path | None) -> int:
    """How many MCP servers a plugin declares, by name only."""
    return len(mcp_server_details(folder))


def _harness_state(view: View, record: _Record, plugin: _Plugin, ref: str) -> dict:
    market = record.harnesses.get(view.harness)
    available = view.harness in plugin.listed_by
    copy_paths = list(plugin.copies.get(view.harness, []))
    if not view.installs_known:
        copy = newest(copy_paths)
        return {"available": available, "installed": None, "enabled": None,
                "version": copy.name if copy else None, "scopes": [],
                "path": str(copy) if copy else None,
                "copies": [str(p) for p in copy_paths]}
    inst = view.installs.get(ref)
    path = None
    if inst is not None:
        present = inst.path is not None and (inst.path.is_dir() or (
            view.harness == "opencode" and (inst.path.exists() or inst.path.is_symlink())))
        if present and inst.path not in copy_paths:
            copy_paths.append(inst.path)
        # Claude reads a directory-marketplace plugin from the folder itself
        # (`claude plugin update` says "read from its folder"); the cache copy
        # it also leaves is never loaded.
        live = plugin.local.get(view.harness)
        if view.harness == "claude" and market is not None \
                and market.source_type == "directory" and live is not None:
            path = live
        elif present:
            path = inst.path
    return {
        **({"control_detail": view.plugin_notes.get(ref)} if view.harness == "cursor" else {}),
        "available": available,
        "installed": inst is not None,
        "enabled": view.enabled.get(ref) if inst is not None else None,
        "version": inst.version if inst is not None else None,
        "scopes": view.scopes.get(ref, []),
        "path": str(path) if path is not None else None,
        "copies": [str(p) for p in copy_paths],
        **({"allowed_actions": view.allowed_actions.get(ref, [])}
           if view.harness in ("cursor", "devin", "opencode") else {}),
    }


def _skills(states: dict, preview: Path | None, memo: dict) -> list[dict]:
    """Every skill the plugin ships, one row per name.

    Read from the copy each harness loads; ``harnesses`` says which loaded
    copies hold it, so a skill only one harness's copy has is marked. A
    plugin no harness loads is read from its folder in the marketplace (or
    a cached copy), with ``harnesses`` empty.
    """
    from swe.skills.inventory import _plugin_skills

    loaded = [(h, Path(s["path"])) for h, s in states.items() if s["path"]]
    sources = loaded or ([(None, preview)] if preview is not None else [])
    rows: dict = {}
    for harness, folder in sources:
        key = str(folder)
        if key not in memo:
            memo[key] = _plugin_skills(folder)
        for skill in memo[key]:
            row = rows.setdefault(skill["name"], {
                "name": skill["name"], "description": skill["description"],
                "path": skill["path"], "harnesses": []})
            if harness and harness not in row["harnesses"]:
                row["harnesses"].append(harness)
    return sorted(rows.values(), key=lambda r: r["name"].lower())


def _hub_drift(home: Path) -> dict:
    """realpath of each hub plugin's folder -> its ``swe skills hub`` install map."""
    from swe.skills.inventory import hub_plugins

    return {_real(p["path"]): p["install"] for p in hub_plugins(home)["plugins"]}


def build_catalog(home: Path | None = None) -> dict:
    """The ``swe plugins catalog --json`` document."""
    home = home or Path.home()
    errors: list = []
    views = read_views(home, errors)
    hub_root = _real(plugins_dir_for(home))

    def is_hub(path: Path | None) -> bool:
        return path is not None and os.path.dirname(_real(path)) == hub_root

    records: dict = {}

    def record(name: str) -> _Record:
        if name not in records:
            records[name] = _Record(name)
        return records[name]

    for harness, view in views.items():
        for name, market in view.markets.items():
            rec = record(name)
            rec.harnesses[harness] = market
            if market.entries is not None:
                rec.listings.append((harness, market))
            if market.source_type == "directory" and is_hub(market.root):
                rec.hub = market.root
                rec.hub_origins.add(harness)

    # A hub marketplace no harness has registered still belongs on the page:
    # it is what `swe skills hub` shows, with "marketplace not added".
    for market in _hub_markets(home, errors):
        real = _real(market.root)
        rec = next((r for r in records.values()
                    if r.hub is not None and _real(r.hub) == real), None)
        if rec is None:
            rec = record(market.name)
            rec.hub = market.root
        # A SWE-owned subset can coexist with the upstream marketplace.
        # Keep upstream registrations for deployment drift, prefer hub bytes.
        rec.listings.insert(0, ("hub", market))
        rec.hub_origins.add("hub")

    for rec in records.values():
        for origin, market in rec.listings:
            for entry in market.entries:
                plugin = rec.plugin(entry["name"])
                plugin.listed_by.add(origin)
                _merge_meta(plugin.meta, entry)
                rel = entry_source_dir(entry.get("source"))
                if rel is not None and market.root is not None:
                    folder = market.root / rel
                    if folder.is_dir():
                        plugin.local.setdefault(origin, folder)

    # Installed but not listed (an unreadable manifest, or a marketplace no
    # longer registered): still shown, so nothing installed goes missing.
    for view in views.values():
        for ref in [*view.installs, *view.scopes]:
            name, _, market = ref.rpartition("@")
            if name and market:
                record(market).plugin(name)

    # Cached copies. Claude's and Cursor's caches are what `swe skills`
    # scans, so a copy there with no plugin to hang it on becomes one; a
    # codex copy with no config.toml entry is a leftover codex ignores.
    for harness, view in views.items():
        for market, name, versions in cache_copies(view.cache):
            rec = records.get(market)
            if (rec is None or name not in rec.plugins) and harness == "codex":
                continue
            record(market).plugin(name).copies[harness] = versions

    hub_drift = _hub_drift(home) if any(r.hub for r in records.values()) else {}
    memo: dict = {}
    out = []
    for rec in sorted(records.values(), key=lambda r: r.name.lower()):
        plugins = []
        for plugin in sorted(rec.plugins.values(), key=lambda p: p.name.lower()):
            plugins.append(_render_plugin(rec, plugin, views, hub_drift, memo))
        description = next((m.description for _o, m in rec.listings if m.description), None)
        out.append({
            "name": rec.name,
            "hub": rec.hub is not None,
            "path": str(rec.hub) if rec.hub is not None else None,
            "description": description,
            "harnesses": {
                harness: {
                    "source_type": market.source_type,
                    "location": market.location,
                    "manifest": str(market.manifest) if market.manifest else None,
                    "readable": market.entries is not None,
                }
                for harness, market in rec.harnesses.items()
            },
            "plugins": plugins,
        })
    return {
        "harnesses": list(views),
        "actions": [h for h in CONTROLLABLE if h in views and (h != "cursor" or views[h].installs_known)],
        "marketplaces": out,
        "errors": errors,
    }


def _render_plugin(rec: _Record, plugin: _Plugin, views: dict, hub_drift: dict,
                   memo: dict) -> dict:
    ref = f"{plugin.name}@{rec.name}"
    states = {h: _harness_state(view, rec, plugin, ref) for h, view in views.items()}
    source = next((plugin.local[o] for o in ("hub", *HARNESSES) if o in plugin.local), None)
    every_copy = [p for paths in plugin.copies.values() for p in paths]
    preview = source or newest(every_copy)
    loaded = next((Path(s["path"]) for s in states.values() if s["path"]), None)
    primary = source if "hub" in plugin.local else loaded or preview
    meta = {} if "hub" in plugin.local else dict(plugin.meta)
    if primary is not None:
        _merge_meta(meta, _plugin_json(primary))
    if "hub" in plugin.local:
        _merge_meta(meta, plugin.meta)
    components = count_components(primary, source) if primary is not None else {}
    mcp = mcp_server_count(primary) or mcp_server_count(source)
    if mcp:
        components = {**components, "mcp_servers": mcp}
    # The hub cards key their drift by the plugin's folder in ~/.swe,
    # which is not `source` when another harness registered a clone of the
    # same marketplace under the same name.
    hub_source = next((plugin.local[o] for o in (*HARNESSES, "hub")
                       if o in rec.hub_origins and o in plugin.local), None)
    drift = hub_drift.get(_real(hub_source)) if hub_source is not None else None
    return {
        "name": plugin.name,
        "ref": ref,
        "marketplace": rec.name,
        "display_name": meta.get("displayName"),
        "description": meta.get("description"),
        "version": meta.get("version"),
        "category": meta.get("category"),
        "tags": meta.get("tags") or [],
        "author": meta.get("author"),
        "homepage": meta.get("homepage"),
        "listed": bool(plugin.listed_by),
        "source_path": str(source) if source is not None else None,
        "components": components,
        "skills": _skills(states, preview, memo),
        "harnesses": states,
        "drift": drift,
    }


# --------------------------------------------------------------------------
# Which plugin a path belongs to


def plugin_dirs(catalog: dict) -> list[tuple[str, str]]:
    """(realpath, ref) for every folder a catalog plugin occupies on disk:
    its source folder, each harness's loaded copy, and every cached copy."""
    out = []
    for market in catalog["marketplaces"]:
        for plugin in market["plugins"]:
            folders = {plugin["source_path"]}
            for state in plugin["harnesses"].values():
                folders.add(state["path"])
                folders.update(state["copies"])
            out += [(_real(f), plugin["ref"]) for f in folders if f]
    return out


def owners(dirs: list[tuple[str, str]], path) -> list[str]:
    """The refs owning the deepest catalog folder that contains ``path``.

    One ref means the path belongs to exactly that plugin. A plugin whose
    source is its marketplace's root folder contains its siblings, which is
    why the deepest folder wins rather than any folder.
    """
    real = _real(path)
    best, refs = -1, set()
    for folder, ref in dirs:
        if real == folder or real.startswith(folder.rstrip(os.sep) + os.sep):
            if len(folder) > best:
                best, refs = len(folder), {ref}
            elif len(folder) == best:
                refs.add(ref)
    return sorted(refs)
