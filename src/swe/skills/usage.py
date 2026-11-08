"""Skill and MCP tool usage stats from the session search index.

The index is opt-in (``swe session index --on``), so every answer is either
``tracked: false`` when no index exists or a count drawn from ``tool_text``
rows. ``Skill: <name>`` calls land in ``tool_text`` through the ``skill``
input key; ``mcp__<server>__<tool>`` calls arrive whole because ``_`` is a
token character. Named tool *replies* are written ``→ <name>: <result>`` —
the arrow keeps the name out of position 0 so ``startswith`` counts calls
only (older indexes still carry ``<name>: <result>`` replies).
"""

from __future__ import annotations

from datetime import datetime


def index_rows(home=None) -> list[tuple] | None:
    """``tool_text`` index rows that could name a Skill or MCP call.

    One broad match ("skill OR mcp__") is cheaper than one query per
    question; ``tool_text`` rows are rare next to message text, and the
    callers refine the exact match in Python anyway. ``None`` when no
    usable index exists — "not tracked", not "zero calls".
    """
    from swe.sessions.search_index import available, tool_text_rows

    if not available(home):
        return None
    return tool_text_rows(home, "tool_text : (skill OR mcp__*)")


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _later_ts(a: str | None, b: str | None) -> str | None:
    """The later timestamp string. Rows can carry no ts (Kiro), and the
    verbatim strings do not sort chronologically (``…00Z`` > ``…00.500Z``
    as text), so parse both when both parse and compare raw otherwise."""
    if a is None:
        return b
    if b is None:
        return a
    pa, pb = _parse_ts(a), _parse_ts(b)
    if pa is not None and pb is not None:
        return a if pa >= pb else b
    return max(a, b)


def _summarise(rows, matched) -> dict:
    """Aggregate ``index_rows`` output.

    ``matched(tool_text)`` decides whether a row counts as a call to the
    thing being measured; the column-filtered FTS match already narrowed
    the rows, this tightens them to the exact call shape.
    """
    calls = 0
    sessions: set[int] = set()
    last_used: str | None = None
    by_harness: dict[str, int] = {}
    for session_row_id, agent, ts, tool_text in rows:
        if not matched(tool_text or ""):
            continue
        calls += 1
        sessions.add(session_row_id)
        if ts is not None:
            last_used = _later_ts(last_used, ts)
        by_harness[agent or "?"] = by_harness.get(agent or "?", 0) + 1
    return {
        "calls": calls,
        "sessions": len(sessions),
        "last_used": last_used,
        "by_harness": dict(sorted(by_harness.items())),
    }


def skill_usage(name: str, home=None, rows: list | None = None) -> dict:
    """Call stats for one skill. ``unattributed`` counts ``Skill`` calls
    indexed before the ``skill`` input key was recognised, which carry no
    skill name and cannot be credited to anyone."""
    if rows is None:
        rows = index_rows(home)
    if rows is None:
        return {"tracked": False}
    target = f"Skill: {name}"
    out = _summarise(rows, lambda text: text == target)
    out["unattributed"] = sum(1 for _, _, _, text in rows
                              if (text or "").strip() == "Skill")
    out["tracked"] = True
    return out


def mcp_usage(server: str, home=None, rows: list | None = None) -> dict:
    """Call stats for ``mcp__<server>__*`` tool calls."""
    if rows is None:
        rows = index_rows(home)
    if rows is None:
        return {"tracked": False}
    prefix = f"mcp__{server}__"
    out = _summarise(rows, lambda text: text.startswith(prefix))
    out["tracked"] = True
    return out


def plugin_usage(servers: list[str], skills: list[str], home=None,
                 rows: list | None = None) -> dict:
    """One pass over the index for a plugin's whole surface.

    ``servers`` are ``mcp__<server>__*`` prefixes and ``skills`` are
    ``Skill: <name>`` calls — a plugin counts a call when either shape
    names something it ships.
    """
    if rows is None:
        rows = index_rows(home)
    if rows is None:
        return {"tracked": False}
    prefixes = tuple(f"mcp__{s}__" for s in servers)
    targets = {f"Skill: {s}" for s in skills}
    matched = (lambda text: text in targets or text.startswith(prefixes)) \
        if prefixes else lambda text: text in targets
    out = _summarise(rows, matched)
    out["skill_calls"] = _summarise(rows, lambda text: text in targets)["calls"]
    out["mcp_calls"] = out["calls"] - out["skill_calls"]
    out["tracked"] = True
    return out
