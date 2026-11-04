"""Sessions that live inside a database rather than a file of their own.

Devin CLI keeps every turn in ``sessions.db`` (``message_nodes``) and
OpenCode in ``opencode.db``. The index reads each session under a
synthetic ``files.path`` of ``"<db path>#<session id>"`` whenever its stat
moves. An OpenCode session is read whole; a Devin session reads only the
nodes past the ``rowid`` watermark its :class:`DevinMark` records, and
whole when the mark no longer holds. This module is the Python side of
``SearchDevin.swift`` and ``SearchOpenCode.swift``; keep them in step,
since the stat, the mark and the fields they compute are what let a
session one writer read count as current to the other.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from urllib.parse import quote

from swe.sessions import search_decode as decode


def synthetic_path(db_path: str, session_id: str) -> str:
    return f"{db_path}#{session_id}"


def open_read_only(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True, timeout=5.0)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _ms(value) -> int:
    """A Devin stamp in epoch milliseconds: seconds today, milliseconds
    past ``1e12`` (``SearchStamp``'s rule)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return 0
    return int(value) if value > 1_000_000_000_000 else int(value) * 1000


@dataclass
class DevinSession:
    id: str
    title: str
    directory: str | None
    created_ms: int
    activity_ms: int
    head: int | None
    # Every node the session holds, streamed copies included: a streamed
    # reply is stored again as it grows, so the count moves with it.
    nodes: int
    # The newest ``rowid`` among them, read in the same statement as
    # ``nodes``: the watermark a read records. 0 for an empty session.
    max_row: int = 0

    @property
    def stat_mtime_ms(self) -> float:
        return float(self.activity_ms or self.created_ms)


class SchemaError(sqlite3.Error):
    """The database lacks the tables this reader knows."""


def devin_sessions(conn: sqlite3.Connection) -> list[DevinSession]:
    """Every visible session, newest activity first."""
    session_columns = _columns(conn, "sessions")
    if not ({"id", "created_at", "last_activity_at"} <= session_columns
            and {"session_id", "node_id", "parent_node_id", "chat_message"}
            <= _columns(conn, "message_nodes")):
        raise SchemaError("lacks the sessions/message_nodes schema this build reads")

    def column(name: str) -> str:
        return f"s.{name}" if name in session_columns else "NULL"

    hidden = " WHERE s.hidden = 0" if "hidden" in session_columns else ""
    rows = conn.execute(
        f"SELECT s.id, {column('title')}, {column('working_directory')},"
        f" s.created_at, s.last_activity_at, {column('main_chain_id')},"
        " (SELECT COUNT(*) FROM message_nodes n WHERE n.session_id = s.id),"
        " (SELECT MAX(n.rowid) FROM message_nodes n WHERE n.session_id = s.id)"
        f" FROM sessions s{hidden}"
        " ORDER BY s.last_activity_at DESC, s.id"
    ).fetchall()
    out = []
    for sid, title, directory, created, activity, head, nodes, max_row in rows:
        if not isinstance(sid, str) or not sid:
            continue
        out.append(DevinSession(
            id=sid, title=title if isinstance(title, str) else "",
            directory=directory if isinstance(directory, str) else None,
            created_ms=_ms(created), activity_ms=_ms(activity),
            head=head if isinstance(head, int) else None, nodes=int(nodes or 0),
            max_row=_int(max_row)))
    return out


MARK_PREFIX = "devin_chain:"


def mark_key(path: str) -> str:
    """The ``meta`` key holding a Devin session's :class:`DevinMark`."""
    return MARK_PREFIX + path


@dataclass(frozen=True)
class DevinMark:
    """Where the last read of one Devin session stopped, kept in the
    index's ``meta`` table beside the ``files`` row it was committed with
    (``SearchDevin.Mark``).

    Devin writes nodes with ``INSERT OR REPLACE`` into an ``AUTOINCREMENT``
    table, so a new or rewritten node always takes a ``rowid`` above
    ``row``, and a rewrite also removes a row at or below it. A mark holds
    while the session still has exactly ``nodes`` rows at or below
    ``row`` and its chain, walked down from the current head, first meets
    such a row at ``head``: everything past it is new. ``ino`` is the
    database file's, so a replaced database never continues an old mark.
    ``session`` is the index session the rows went to; ``title`` the
    first-prompt title the chain offered, if any.
    """

    ino: int
    row: int
    head: int
    nodes: int
    session: int = 0
    title: str | None = None

    def value(self, session_row_id: int) -> str:
        """The stored form, the same bytes Swift writes."""
        return (f"{self.ino} {self.row} {self.head} {self.nodes} {session_row_id}"
                f"\n{self.title or ''}")

    @staticmethod
    def parse(value) -> "DevinMark | None":
        if not isinstance(value, str):
            return None
        numbers, _, title = value.partition("\n")
        parts = numbers.split(" ")
        if len(parts) != 5 or not all(re.fullmatch(r"-?[0-9]+", p) for p in parts):
            return None
        ino, row, head, nodes, session = (int(p) for p in parts)
        return DevinMark(ino, row, head, nodes, session, title or None)


@dataclass
class DevinRead:
    """One session's read: every message along the main chain, or with
    ``appended`` only those past the mark's head. ``mark`` is what the
    commit records for the next read (None for an empty chain)."""

    messages: list
    prompt_title: str | None
    appended: bool
    mark: DevinMark | None


def _node(conn: sqlite3.Connection, session_id: str, node_id: int):
    """``(rowid, parent, chat_message bytes)`` of one node, through the
    ``UNIQUE(session_id, node_id)`` index. Loading every node's parent to
    walk a 50,000-node forest took 1 to 3 s for a 260-node chain (#1613)."""
    # As bytes, decoded the way Swift's `text()` does (a bad byte becomes
    # U+FFFD) rather than failing the whole session.
    row = conn.execute(
        "SELECT rowid, parent_node_id, CAST(chat_message AS BLOB) FROM message_nodes"
        " WHERE session_id = ? AND node_id = ?", (session_id, node_id)).fetchone()
    if row is None:
        return None
    rowid, parent, raw = row
    return (_int(rowid), parent if isinstance(parent, int) else None,
            raw.decode("utf-8", "replace").encode("utf-8") if isinstance(raw, bytes) else None)


def _head(conn: sqlite3.Connection, session: DevinSession) -> int | None:
    """``main_chain_id`` when that node exists, else the newest node."""
    if session.head is not None and conn.execute(
            "SELECT 1 FROM message_nodes WHERE session_id = ? AND node_id = ?",
            (session.id, session.head)).fetchone():
        return session.head
    newest = conn.execute("SELECT MAX(node_id) FROM message_nodes WHERE session_id = ?",
                          (session.id,)).fetchone()[0]
    return newest if isinstance(newest, int) else None


def _walk(conn: sqlite3.Connection, session: DevinSession, start: int | None,
          floor: int | None = None):
    """The chain from ``start`` down, newest first, as ``(node_id, rowid,
    raw)``. Streamed copies are siblings in a forest; walking parents from
    the head gives one node per message. With ``floor`` the walk stops at
    the first node at or below that ``rowid`` and returns it apart:
    ``(nodes, stop)``."""
    nodes = []
    seen: set[int] = set()
    cursor = start
    while cursor is not None and cursor not in seen:
        found = _node(conn, session.id, cursor)
        if found is None:
            break
        rowid, parent, raw = found
        if floor is not None and rowid <= floor:
            return nodes, (cursor, raw, parent)
        seen.add(cursor)
        nodes.append((cursor, rowid, raw))
        cursor = parent
    return nodes, None


def _call_names(raw: bytes | None) -> dict:
    """The ``tool_call_id -> name`` pairs one record adds to the decoder."""
    if raw is None:
        return {}
    scratch = decode.Decoder("devin")
    scratch.decode_line(raw)
    return scratch._devin.call_names


def _tool_call_id(raw: bytes | None) -> str | None:
    """The call a ``role: "tool"`` record answers."""
    if raw is None:
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    if not isinstance(obj, dict) or obj.get("role") != "tool":
        return None
    return decode.json_string(obj.get("tool_call_id"))


def _prime_calls(conn: sqlite3.Connection, session: DevinSession, span: list,
                 stop) -> dict:
    """The call names the appended ``span`` looks up but never assigns,
    from the indexed chain below it: the nearest assignment walking down,
    which is the one a whole read's decoder would hold. Usually the node
    just below the span."""
    assigned: set[str] = set()
    needed: set[str] = set()
    for raw in span:
        call_id = _tool_call_id(raw)
        if call_id is not None and call_id not in assigned:
            needed.add(call_id)
        assigned.update(_call_names(raw))
    resolved: dict = {}
    if not needed:
        return resolved
    node, raw, parent = stop
    seen = {node}
    while True:
        for call_id, name in _call_names(raw).items():
            if call_id in needed and call_id not in resolved:
                resolved[call_id] = name
        if len(resolved) == len(needed) or parent is None or parent in seen:
            return resolved
        found = _node(conn, session.id, parent)
        if found is None:
            return resolved
        seen.add(parent)
        _, next_parent, raw = found
        parent = next_parent


def _decode(raws: list, decoder: decode.Decoder) -> tuple[list, str | None]:
    messages: list = []
    title = None
    for raw in raws:
        if raw is None:
            continue
        found, hints = decoder.decode_line(raw)
        messages += found
        if title is None and hints.title is not None:
            title = hints.title
    return messages, title


def devin_read(conn: sqlite3.Connection, session: DevinSession,
               mark: DevinMark | None = None, ino: int = 0) -> DevinRead:
    """``SearchDevin.read``: the session's messages through the Devin
    decoder. With a ``mark`` the last commit recorded, only the chain past
    its head is read, unless the session lost a row at or below its
    watermark, the database is another file, or the chain no longer runs
    through its head (``main_chain_id`` moved to a branch, a streamed copy
    replaced the head): then the whole chain is read again."""
    start = _head(conn, session)
    if (mark is not None and mark.ino == ino and session.max_row >= mark.row
            and conn.execute(
                "SELECT COUNT(*) FROM message_nodes WHERE session_id = ? AND rowid <= ?",
                (session.id, mark.row)).fetchone()[0] == mark.nodes):
        span, stop = _walk(conn, session, start, floor=mark.row)
        if stop is not None and stop[0] == mark.head:
            raws = [raw for _, _, raw in reversed(span)]
            decoder = decode.Decoder("devin")
            decoder._devin.call_names.update(_prime_calls(conn, session, raws, stop))
            # A title the chain already offered stays; only a chain that
            # had none takes the span's first prompt.
            decoder.prompt_title_sent = mark.title is not None
            messages, title = _decode(raws, decoder)
            title = mark.title if mark.title is not None else title
            return DevinRead(messages, title, True, DevinMark(
                ino, session.max_row, start, session.nodes, title=title))
    chain, _ = _walk(conn, session, start)
    messages, title = _decode([raw for _, _, raw in reversed(chain)], decode.Decoder("devin"))
    next_mark = (DevinMark(ino, session.max_row, start, session.nodes, title=title)
                 if chain else None)
    return DevinRead(messages, title, False, next_mark)


def devin_messages(conn: sqlite3.Connection, session: DevinSession) -> tuple[list, str | None]:
    """The session's whole chain through the Devin decoder, plus the
    first-prompt title candidate the decoder offered."""
    read = devin_read(conn, session)
    return read.messages, read.prompt_title


def devin_title(session: DevinSession, prompt_title: str | None) -> str:
    """``sessions.title`` unless Devin left its placeholder, else the first
    prompt."""
    title = decode.json_string(session.title)
    if title is not None and title != "Untitled":
        return title
    return prompt_title or ""


# -- OpenCode (SearchOpenCode.swift) -------------------------------------


@dataclass
class OpenCodeSession:
    id: str
    title: str
    directory: str | None
    created_ms: int
    updated_ms: int
    # `message` rows plus 2.x `session_message` rows not copied from them:
    # with the activity time, the whole change detector.
    count: int

    @property
    def activity_ms(self) -> int:
        return self.updated_ms if self.updated_ms > 0 else self.created_ms


def _int(value) -> int:
    """``sqlite3_column_int64``: NULL is 0, a REAL truncates."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def opencode_sessions(conn: sqlite3.Connection) -> list[OpenCodeSession]:
    """Top-level, unarchived sessions, newest activity first: the legacy
    ``session`` table plus 2.x ``session_v2`` (whose row wins for a session
    in both)."""
    session_columns = _columns(conn, "session")
    if not ({"id", "time_created", "time_updated"} <= session_columns
            and {"id", "session_id", "time_created", "data"} <= _columns(conn, "message")
            and {"message_id", "time_created", "data"} <= _columns(conn, "part")):
        raise SchemaError("lacks the session/message/part schema this build reads")

    def column(name: str) -> str:
        return f"s.{name}" if name in session_columns else "NULL"

    filters = ((" AND s.parent_id IS NULL" if "parent_id" in session_columns else "")
               + (" AND s.time_archived IS NULL" if "time_archived" in session_columns else ""))
    v2_columns = _columns(conn, "session_v2")
    has_v2 = {"id", "time_created", "time_updated"} <= v2_columns
    if {"id", "session_id"} <= _columns(conn, "session_message"):
        def v2_count(ref: str) -> str:
            return (f"(SELECT COUNT(*) FROM session_message sm WHERE sm.session_id = {ref}"
                    " AND NOT EXISTS (SELECT 1 FROM message m WHERE m.id = sm.id))")
    else:
        def v2_count(ref: str) -> str:
            return "0"
    legacy = f"""
        SELECT s.id AS id, s.time_created AS time_created, s.time_updated AS time_updated,
               {column("title")} AS title, {column("directory")} AS directory,
               (SELECT COUNT(*) FROM message m WHERE m.session_id = s.id) + {v2_count("s.id")} AS n
        FROM session s
        WHERE 1=1{filters}"""
    if has_v2:
        def v2_column(name: str) -> str:
            return f"v.{name}" if name in v2_columns else "NULL"
        v2_filters = ((" AND v.parent_id IS NULL" if "parent_id" in v2_columns else "")
                      + (" AND v.time_archived IS NULL" if "time_archived" in v2_columns else ""))
        rows = f"""
        SELECT v.id AS id, v.time_created AS time_created,
               MAX(v.time_updated, COALESCE(s.time_updated, 0)) AS time_updated,
               COALESCE(NULLIF({v2_column("title")}, ''), {column("title")}) AS title,
               COALESCE(NULLIF({v2_column("directory")}, ''), {column("directory")}) AS directory,
               (SELECT COUNT(*) FROM message m WHERE m.session_id = v.id) + {v2_count("v.id")} AS n
        FROM session_v2 v LEFT JOIN session s ON s.id = v.id
        WHERE 1=1{v2_filters}
        UNION ALL
        {legacy} AND NOT EXISTS (SELECT 1 FROM session_v2 v WHERE v.id = s.id)"""
    else:
        rows = legacy
    out = []
    for sid, created, updated, title, directory, n in conn.execute(
        f"SELECT id, time_created, time_updated, title, directory, n FROM ({rows})"
        " ORDER BY CASE WHEN time_updated > 0 THEN time_updated ELSE time_created END DESC"
    ):
        if not isinstance(sid, str):
            continue
        out.append(OpenCodeSession(
            id=sid, title=title if isinstance(title, str) else "",
            directory=directory if isinstance(directory, str) else None,
            created_ms=_int(created), updated_ms=_int(updated), count=_int(n)))
    return out


def _json_object(raw) -> dict | None:
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _tool_outcome(state: dict | None):
    """``state.error`` when the call failed or was cancelled, else
    ``state.output``."""
    if state is None:
        return None
    error = state.get("error")
    if isinstance(error, str) and error.strip():
        return error
    output = state.get("output")
    return output if isinstance(output, str) else None


def _tool_text(part: dict) -> str | None:
    """One ``tool`` part: what ran, then what came back, worded the way
    every other harness's tool rows are."""
    state = part.get("state") if isinstance(part.get("state"), dict) else None
    tool_input = state.get("input") if state is not None else None
    # OpenCode spells the file argument `filePath`.
    if (isinstance(tool_input, dict) and isinstance(tool_input.get("filePath"), str)
            and not isinstance(tool_input.get("file_path"), str)):
        tool_input = {**tool_input, "file_path": tool_input["filePath"]}
    pieces = [piece for piece in (decode.tool_call_text(part.get("tool"), tool_input),
                                  _tool_outcome(state)) if piece is not None]
    return decode.bounded("\n".join(pieces))


def _iso_ms(ms: int) -> str:
    return decode.iso_seconds(ms / 1000)


def opencode_messages(conn: sqlite3.Connection, session: OpenCodeSession) -> list:
    """One session's turns, legacy rows first: a turn's ``text`` and
    ``reasoning`` parts join into its one message, and each ``tool`` part
    follows as its own."""
    messages: list = []
    open_id = None
    open_role = None
    open_ts = None
    words: list[str] = []
    tools: list = []

    def flush():
        if open_role is not None:
            text = decode.bounded("\n".join(words))
            if text is not None:
                messages.append(decode.Message(open_role, text, open_ts))
        messages.extend(tools)

    for message_id, role, kind, data, created in conn.execute(
        """SELECT m.id, json_extract(m.data, '$.role'),
                  json_extract(p.data, '$.type'), p.data, m.time_created
           FROM message m JOIN part p ON p.message_id = m.id
           WHERE m.session_id = ?
             AND json_extract(m.data, '$.role') IN ('user','assistant')
             AND json_extract(p.data, '$.type') IN ('text','reasoning','tool')
           ORDER BY m.time_created, m.id, p.time_created, p.rowid""",
        (session.id,),
    ):
        message_id = message_id if isinstance(message_id, str) else ""
        if message_id != open_id:
            flush()
            words, tools = [], []
            open_id = message_id
            open_role = role if role in (decode.USER, decode.ASSISTANT, decode.TOOL) else None
            open_ts = _iso_ms(_int(created))
        if not isinstance(data, str):
            continue
        part = _json_object(data)
        if kind == "tool":
            text = _tool_text(part) if part is not None else None
            if text is not None:
                tools.append(decode.Message(decode.TOOL, text, open_ts))
        elif part is not None and isinstance(part.get("text"), str):
            words.append(part["text"])
    flush()
    return messages + _opencode_v2_messages(conn, session)


def _opencode_v2_messages(conn: sqlite3.Connection, session: OpenCodeSession) -> list:
    """OpenCode 2.x turns: one ``session_message`` row per turn, rows the
    migration copied from ``message`` left to the legacy read."""
    if not ({"id", "session_id", "type", "seq", "time_created", "data"}
            <= _columns(conn, "session_message")):
        return []
    messages: list = []
    for kind, raw, created in conn.execute(
        """SELECT sm.type, sm.data, sm.time_created FROM session_message sm
           WHERE sm.session_id = ? AND sm.type IN ('user','synthetic','assistant')
             AND NOT EXISTS (SELECT 1 FROM message m WHERE m.id = sm.id)
           ORDER BY sm.seq""",
        (session.id,),
    ):
        data = _json_object(raw)
        if data is None:
            continue
        ts = _iso_ms(_int(created))
        if kind != "assistant":
            text = decode.bounded(data["text"]) if isinstance(data.get("text"), str) else None
            if text is not None:
                messages.append(decode.Message(decode.USER, text, ts))
            continue
        words: list[str] = []
        tools: list = []
        content = data.get("content")
        # Swift's `as? [[String: Any]]`: one non-object block voids the list.
        blocks = content if isinstance(content, list) and all(
            isinstance(block, dict) for block in content) else []
        for block in blocks:
            kind_of = block.get("type")
            if kind_of in ("text", "reasoning"):
                if isinstance(block.get("text"), str):
                    words.append(block["text"])
            elif kind_of == "tool":
                state = dict(block["state"]) if isinstance(block.get("state"), dict) else {}
                inner = state.get("content")
                if "output" not in state and isinstance(inner, list) and all(
                        isinstance(item, dict) for item in inner):
                    state["output"] = "\n".join(
                        item["text"] for item in inner if isinstance(item.get("text"), str))
                text = _tool_text({"tool": block.get("name"), "state": state})
                if text is not None:
                    tools.append(decode.Message(decode.TOOL, text, ts))
        text = decode.bounded("\n".join(words))
        if text is not None:
            messages.append(decode.Message(decode.ASSISTANT, text, ts))
        messages.extend(tools)
    return messages


def opencode_title(session: OpenCodeSession, messages: list) -> str:
    """The session row's title, else the first-user-prompt fallback."""
    if session.title:
        return session.title
    first = next((m for m in messages if m.role == decode.USER), None)
    return (decode.title_candidate(first.text) or "") if first else ""
