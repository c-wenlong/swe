"""T0 counter heuristics over one session's search-index rows.

:func:`compute` turns the ``messages``/``messages_fts`` rows of a single
indexed session into the ``session_metrics`` counter fields — regex and
count work only, no model (docs/insights.md §Metric catalog). The index
keeps ``role`` plus three text columns and no call/result or ``is_error``
flags, so two approximations are baked in:

- ``n_tool_calls`` counts rows that look like invocations
  (``name: argument`` or a bare tool name). Devin prefixes tool *results*
  with the name too, so its call count runs high; every harness's real
  result rows land as plain text.
- ``n_tool_errors`` and its sub-counters are text signatures over
  ``tool_text`` — a matched row is a failure, whatever emitted it.

The patterns are ports of the ``agent-audit-2026-11-07`` scripts,
validated over ~1,200 sessions: ``refine.py``'s machine-prompt list,
``followup_gates.py``'s trivial-ack list and its "an ack only counts
after an assistant message" rule, and ``tool_reliability.py``'s failure
classes retuned to the signatures the failure corpus actually shows
(guard refusals, ``<tool_use_error>``, ``Exit code N``, validation and
hook errors).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..sessions import search_decode as decode
from .store import SessionMetrics

# Trivial user acks that only unblock the agent — followup_gates.py's ACK,
# verbatim. A match counts only when an assistant row came first (the
# audit's ``last_atxt`` gate); two acks in a row count once.
_ACK = re.compile(
    r"^(yes|y|yea|yeah|yep|yup|ok|okay|k|kk|sure|go ahead|yes,? go ahead|"
    r"continue|cont|proceed|do it|please do|lgtm|sgtm|sounds good|"
    r"that's fine|thats fine|go for it|yessir|yes please|ok go|okie|"
    r"make it so|ship it|approved?|fine|do it please|ya|of course)[.! ]*$",
    re.I)

# refine.py's MACHINE_PAT: prompts a machine wrote — orchestration briefs,
# injected turns, resumed-session boilerplate — versus a human typing. The
# second group adds tags the index decoder itself flags as injected turns
# (``_INJECTED_TAGS``), so every harness's boilerplate counts, not just
# the shapes the audit happened to meet.
_MACHINE = re.compile(
    r"^(<task-notification|<lead_handoff|<agent-message|<system-reminder|"
    r"<ci-monitor-event|<task>|<handoff|<cron|<scheduled|<automated|"
    r"<webhook|<monitor|<hook|Base directory for this skill|"
    r"# AGENTS\.md instructions|"
    r"You are\b|READ-ONLY|First read the full brief|Repo: /|"
    r"\[Subagent|@conversation:|Goal check-in:|"
    r"Another \w+ session sent|Issue verified|PR #?\d+ |"
    r"The user has opened|Continue from where|Resume:|SessionStart|"
    r"\[Request interrupted|Caveat:|This session is being continued|"
    r"IMPORTANT: this context|We need modify|"
    r"You own |You are responsible|SLUG = |This deserves|"
    r"Stop hook|The coordinator sent|<local-command-stdout|"
    r"<realtime_delegation|Read and follow the brief|"
    r"A session-scoped|WORKTREE \(|fix-and-merge loop|"
    r"Build `|Read the brief|Your worktree|assigned issue|"
    r"<command-name|<bash-input|<local-command-caveat|"
    r"<external_codex_apps|\[handback-|\[handoff-|\[Image|"
    r"You are one of |Shared brief:|Response annotations:|"
    r"Your response above was cut off|# Response annotations|"
    r"\[  \{|continue the task|carried over into this session|"
    r"#\s*(Shared brief|Brief|Task\b)|one of \w+ parallel|"
    r"parallel readers|parallel workers|"
    r"<bash-stderr|<bash-stdout|<command-args|<command-message|"
    r"<cross-session-message|<fork-boilerplate|<local-command-stderr|"
    r"<mcp-polling-update|<mcp-resource-update|<teammate-message|"
    r"<user-memory-input|<user-prompt-submit-hook)",
    re.I)

# A tool row shaped like a call — ``exec: git status`` or bare
# ``mcp__server__tool`` — rather than a result's free text. The name is a
# single identifier-ish token; a result that opens with prose ("File
# created successfully at:") does not match because the colon does not
# follow the first token.
_CALL = re.compile(r"^[A-Za-z_][\w.-]*(?:: |\s*$)")

# Path/worktree guard refusals in tool output: Devin's worktree isolation
# ("This agent is isolated in the worktree … Refusing to run it —") and
# the filesystem guards ("Refusing to write through symlink"). The audit's
# ~1,700-count corpus was dominated by these two shapes.
_GUARD_REFUSAL = re.compile(
    r"\brefusing to\b|\bisolated in the worktree\b|\bworktree-isolated\b",
    re.I)

# tool_reliability.py's timeout class, verbatim. A command that only
# mentions a ``--timeout`` flag can false-positive here; that is the
# documented price of text-only detection.
_TIMEOUT = re.compile(r"timed? ?out|timeout|deadline", re.I)

# Failure signatures over tool output, from the audit's failure corpus:
# the frequent heads were worktree refusals, ``PreToolUse:* hook error``,
# ``Tool 'x' validation failed``, permission denials, ``Exit code N``,
# ``<tool_use_error>``, ``Command timed out``, ``MCP error -NNNNN`` and
# user interrupts. Deliberately anchored (``error:`` with a colon, never
# a bare ``error``) so ordinary output — source being read, test names —
# does not count.
_TOOL_ERROR = re.compile(
    r"(<tool_use_error>|exit code:? [1-9]\d*\b|\berror:|\bfailed:|"
    r"tool ['\"\w-]+ (validation )?failed|validation failed|hook error|"
    r"denied by|permission denied|the user doesn't want to proceed|"
    r"does not exist|no such (file|tool|command|directory)|not found\b|"
    r"unable to access|failed to \w+|returned an error|traceback \(most recent|"
    r"too many requests|rate.?limit\b|mcp error|limit reached|"
    r"unauthorized|forbidden|econn(refused|reset)|fetch failed|"
    r"command (failed|not found)|cancell?ed due to)", re.I)

# An errored tool row that names an MCP server — ``MCP error -32602``,
# ``mcp__notion__*`` in a ``<tool_use_error>``, ``tabs_context_mcp
# returned an error`` — counts as an MCP failure.
_MCP = re.compile(r"mcp", re.I)

# A lone ping as the session's only user text: "hi", "PONG", "test" — the
# usage probes the audit counted. Needs the session to have done nothing
# (no tool calls): a "hi" that kicked off real work is not a probe.
_PING = re.compile(
    r"^(hi+|hello+|hey+|yo|sup|hiya|howdy|hola|ping|pong|test|testing|"
    r"gm|good (morning|afternoon|evening)|are you (there|alive|up)|"
    r"you (there|up)|wake up|does this work|hello world)[.!?~ ]*$",
    re.I)


@dataclass(frozen=True)
class MessageRow:
    """One index row: ``messages`` joined to its ``messages_fts`` text."""

    role: str
    ts: str | None
    user_text: str = ""
    assistant_text: str = ""
    tool_text: str = ""


def compute(session_id: str, harness: str,
            rows: list[MessageRow]) -> SessionMetrics:
    """Counter fields of one session's index rows, in row order.

    ``first_ts`` lands as epoch seconds, the ledger's unit for the same
    name. The score half of :class:`SessionMetrics` stays ``None`` — a T2
    pass owns it.
    """
    n_tool_calls = 0
    n_tool_errors = 0
    n_acks = 0
    n_refusals = 0
    n_timeouts = 0
    n_mcp = 0
    n_user = 0
    n_machine = 0
    n_real_user = 0
    sole_real_text: str | None = None
    first_ms: float | None = None
    # The audit's last_atxt: an ack counts only once per assistant text
    # that came before it, and stays armed across intervening tool rows.
    ack_armed = False
    # Anything the agent emitted — text or a tool event — after the last
    # real prompt. A prompt with nothing after it is a dead session; a
    # live one looks dead until the next index pass recomputes it.
    answered = True
    for row in rows:
        stamp = decode.stamp_ms(row.ts)
        if stamp is not None and (first_ms is None or stamp < first_ms):
            first_ms = stamp
        if row.role == "user":
            n_user += 1
            text = row.user_text.strip()
            if _MACHINE.search(text):
                n_machine += 1
            else:
                n_real_user += 1
                sole_real_text = text if n_real_user == 1 else None
                answered = False
            if ack_armed and _ACK.match(text):
                n_acks += 1
                ack_armed = False
        elif row.role == "assistant":
            ack_armed = True
            answered = True
        else:
            answered = True
            text = row.tool_text
            refused = _GUARD_REFUSAL.search(text) is not None
            timed_out = _TIMEOUT.search(text) is not None
            if refused or timed_out or _TOOL_ERROR.search(text) is not None:
                n_refusals += refused
                n_timeouts += timed_out
                n_tool_errors += 1
                if _MCP.search(text) is not None:
                    n_mcp += 1
            elif _CALL.match(text):
                n_tool_calls += 1
    is_probe = (
        n_real_user == 1 and n_tool_calls == 0 and sole_real_text is not None
        and _PING.match(sole_real_text) is not None)
    is_dead = n_real_user > 0 and not answered
    return SessionMetrics(
        session_id=session_id, harness=harness,
        first_ts=int(first_ms // 1000) if first_ms is not None else None,
        n_messages=len(rows), n_tool_calls=n_tool_calls,
        n_tool_errors=n_tool_errors, n_ack_unblocks=n_acks,
        n_guard_refusals=n_refusals, n_timeouts=n_timeouts,
        n_mcp_errors=n_mcp, is_probe=int(is_probe), is_dead=int(is_dead),
        machine_share=(n_machine / n_user) if n_user else None)
