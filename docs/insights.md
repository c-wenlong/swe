# Insights

Design for the `swe insights` feature: a programmatic audit of how the
agentic workflow is actually going, derived from the session stores `swe`
already indexes. This doc is the spec; rows marked "not built" describe
the target, not shipped code. Update the phasing section as parts land.

## What it answers

The session search index records what happened. Insights turns that into
three questions a user (or an agent) can act on:

- Is the workflow getting better or worse? (outcome trend, first-pass rate)
- What is it costing me? (guard refusals, timeouts, trivial-ack unblocks)
- What is broken right now? (dead MCP entries, unwired hooks, stale copies)

## Compute model

Three tiers, split by cost. The rule that shapes all of them: **never parse
transcripts at view time**, and the cheap tiers must be useful without the
expensive ones ever having run.

| tier | runs when | does what |
|---|---|---|
| T0 passive | after each index pass | fills `session_metrics` counters for every indexed session not yet measured, reading `messages`/`messages_fts` in the shared search index — regex/count work only, no model |
| T1 on-view | `swe insights` run / app tab opened | aggregates `session_metrics`, plus live doctor-style checks (dead MCP entries, unwired hooks, drifted `~/.agents` skill copies, plaintext-secret presence — count and filename only, never values) |
| T2 trigger-driven | backlog drain | scores sessions whose `outcome` is NULL: one cheap model call per session, batched |

T0 is a **derived store**: `insights.sqlite` reads the search index's own
`messages`/`messages_fts` rows instead of hooking the transcript decoders
(D10). That keeps it out of the two-writer fencing the search index shares
with the app, and means a session's metrics can be recomputed at any time.

### T2 triggers (no scheduler required)

`session_metrics` rows land with `outcome=NULL`. A scoring pass drains
that backlog whenever any of these fires — none is required:

1. an index-write hook once the backlog crosses a threshold,
2. piggybacking when `swe insights` or `swe report` runs and finds a stale backlog,
3. `swe insights --score` by hand,
4. optional launchd/cron — last feature to build, off by default.

Verified 2026-11-08: `swe report` has no scheduler on a stock install, so
nothing may depend on it running daily. The same trigger list applies to
the report's own agent pass.

## Data model — `~/.swe/data/insights.sqlite` (not built)

Device-local runtime store under `data/` (already gitignored): rebuilt
from the index, never synced (D1). Rows keep forever — `session_metrics`
is ~200 B/session, a year is ~3 MB, and longitudinal regression is the
point (D2). Findings transition `open → filed → ignored` instead of
deleting; resolved findings leave the default view after ~30 days but
stay queryable.

```sql
meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)   -- schema_version

session_metrics(            -- one row per indexed session
  session_id TEXT PRIMARY KEY, harness TEXT, first_ts INTEGER,
  n_messages INT, n_tool_calls INT, n_tool_errors INT,
  n_ack_unblocks INT, n_guard_refusals INT, n_timeouts INT, n_mcp_errors INT,
  is_probe INT, is_dead INT, machine_share REAL,
  outcome INT,            -- NULL until a T2 pass scores it
  first_pass INT, causes TEXT, score_confidence TEXT)

daily_rollup(day TEXT, harness TEXT, metric TEXT, value REAL,
             PRIMARY KEY(day, harness, metric))

findings(id TEXT PRIMARY KEY, kind TEXT, severity TEXT, title TEXT,
         evidence_json TEXT, status TEXT,   -- open | filed | ignored
         followup_id TEXT, first_seen TEXT, last_seen TEXT)
```

`findings.evidence_json` carries session ids, `file:line` refs and counts
— enough for an agent to act without re-deriving. No raw secrets, ever:
a secrets finding records the filename and line, not the value.

## Surfaces (not built)

| surface | what it shows |
|---|---|
| `swe insights` | the dashboard in the terminal: trend, cost, health checks, findings |
| `swe insights --json` | the machine contract — same payload agents consume to file follow-ups |
| `swe insights --score` | drains the T2 backlog now |
| SWE.app Insights tab | Usage group, after Overview; same `insights.sqlite`, no transcript parsing |

### App tab conventions

- Filter bar mirrors the Sessions page's `FilterBar`: window, harness,
  **project**, model, search. A project selection dims non-matching rows
  rather than removing them, so global context stays visible (D12).
- The cost-of-friction section defaults to repo × metric, because a global
  total hides the fixable shape (audit: ~1,700 refusals were 820 kopifund
  path issues + 520 quiver HOME-setting commands — two different fixes).
- Every section (KPI strip, health checks, charts, findings) is hideable
  and reorderable through a settings popover, persisted per machine with
  the same mechanism the menu bar uses: a `ToggleList` in `UserDefaults`,
  the `PopupTabs` pattern in SWECore (D11).
- "File finding" hands off to the existing `swe report followup` flow —
  no second tracker (D3).

## Metric catalog (v0 targets)

Counters T0 can compute without a model: `n_tool_errors`, `n_timeouts`,
`n_guard_refusals`, `n_ack_unblocks` (trivial "yes"/"continue" prompts that
unblock the agent), `n_mcp_errors`, `is_probe` (single-token usage pings),
`is_dead` (prompt with no assistant reply), `machine_share` (harness/template
prompts vs. human prompts).

T2 adds per session: `outcome` (1–5), `first_pass`, `causes`, plus rollups
into `daily_rollup`. Baselines come from the 30-day audit
(`agent-audit-2026-11-07`, `metrics_summary.json`): 91% score 4–5, 68%
first-pass, ~1,700 guard refusals, 453 ack-unblocks in 171 sessions.

## Decisions

- D1 device-local store — findings are machine-bound; shared authored
  state re-derives on every machine anyway.
- D2 keep `session_metrics`/`daily_rollup` forever; findings transition
  states, never delete.
- D3 findings file through `swe report followup`, not a parallel tracker.
- D8 T2 scoring is trigger-driven backlog drain, never cron-dependent.
- D9 programmatic-first: the tab is fully useful with the agent pass
  never having run. Model scoring only covers what regex can't.
- D10 `insights.sqlite` reads the shared search index — it never joins
  the decoder pipeline or the index's two-writer fencing.
- D11 section visibility/order persists via `ToggleList` + `UserDefaults`,
  the `PopupTabs` pattern.
- D12 project filter dims non-matching rows instead of removing them.

## Phasing

- v0 (this doc + store): `insights.sqlite` DDL, T0 metrics pass, `swe insights`.
  No model calls anywhere.
- v1: T2 trigger hooks, `outcome`/`first_pass`, app Insights tab,
  orchestration rollups (dispatched → finished → failed per batch).
- v2: optional launchd scoring, benchmarks/evals on the metric layer.

## Out of scope

Raw transcript storage (the index already holds text), cross-machine
rollups, anything that requires `swe report` to run on a schedule.
