/* Worktrees: every git worktree the hub knows, grouped by repo, with the
 * state that says whether it is safe to delete. Create (New worktree),
 * read (list and detail), update (lock, unlock, move) and delete (remove,
 * Clean up merged, Slim). The list is #/worktrees; one worktree is
 * #/worktrees?path=<path>.
 * Data: GET /v1/app/worktrees[?worktree=<path>], POST
 * /v1/app/worktrees/add|remove|lock|unlock|move|prune|slim
 * (pages/worktrees.py, which calls swe.worktrees). Nothing destructive is
 * sent unless the person ticked it: `force` only behind its own checkbox,
 * `yes` only from a confirm button after a dry run. The bundle runs each
 * page script in its own scope, so helpers are wt-prefixed. */
"use strict";

const wtPage = {
  data: null, seq: 0, token: 0,
  state: "all", repo: "", q: "",
  notice: null,     // { text, to } after a write that worked
  error: null,      // a write that failed outside a sheet
  detail: null,     // the worktree #/worktrees?path= shows
};

/* Wire states: label, quiver arrow (AGENTS.md "State arrows"), order. */
const WT_STATES = {
  "merged-clean": { label: "Merged and clean", arrow: "green" },
  "merged-dirty": { label: "Merged, uncommitted changes", arrow: "yellow" },
  "open-pr": { label: "Open PR", arrow: "yellow" },
  "no-pr": { label: "No PR", arrow: "grey" },
  "orphaned": { label: "Orphaned", arrow: "yellow" },
  "unknown": { label: "Unknown", arrow: "grey" },
  "protected": { label: "Protected", arrow: "grey" },
  "main": { label: "Main checkout", arrow: "grey" },
};
const WT_ORDER = Object.keys(WT_STATES);
const WT_CHIP_LABEL = {
  "merged-clean": "Merged", "merged-dirty": "Merged, dirty", "open-pr": "Open PR", "no-pr": "No PR",
  orphaned: "Orphaned", unknown: "Unknown", protected: "Protected", main: "Main",
};
/* What a force delete would throw away, by state. merged-clean needs no force. */
const WT_RISK = {
  "merged-dirty": "It is merged, but it has uncommitted changes that exist nowhere else.",
  "open-pr": "Its pull request is still open, so commits made here may not be pushed anywhere.",
  "no-pr": "It has no pull request, so its commits may exist only in this folder.",
  unknown: "swe couldn't tell whether it is merged, so it can't say what would be lost.",
};
/* The Mac app's harness labels: always an icon and the name. */
const WT_HARNESS = { claude: "Claude Code", codex: "Codex", devin: "Devin", "swe-pr": "swe PR checkout", other: "Other" };
const WT_HARNESS_ICON = { "swe-pr": "worktrees", other: "harnesses" };

const WT_PLUS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';
const WT_LOCK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>';

const wtOnRoute = () => /^#\/worktrees(\?|$)/.test(location.hash);
const wtQuery = () => new URLSearchParams(location.hash.split("?")[1] || "");
/* An in-app route, escaped for an attribute (never a server URL). */
const wtRoute = (hash) => esc(hash);
const wtDetailLink = (path) => `#/worktrees?path=${encodeURIComponent(path)}`;

/* ---------------------------------------------------------------- format */

/* 1024-based, as `du` reports: "840 MB", "1.2 GB". */
function wtBytes(n) {
  if (n == null || !isFinite(n)) return "Not measured";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = Math.max(0, n), i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  const text = i === 0 || v >= 10 ? String(Math.round(v)) : v.toFixed(1);
  return `${text} ${units[i]}`;
}

const wtEpoch = (iso) => {
  const t = Date.parse(iso || "");
  return isFinite(t) ? t / 1000 : null;
};
const wtAgo = (iso) => {
  const e = wtEpoch(iso);
  return e == null ? "no commits" : Fmt.relative(e);
};
const wtTilde = (path) => Fmt.tilde(path, wtPage.data && wtPage.data.home);
const wtShort = (sha) => String(sha || "").slice(0, 8);
const wtBase = (path) => String(path || "").replace(/\/+$/, "").split("/").pop() || path;
const wtCount = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;
const wtBranchName = (w) => w.branch || `detached at ${wtShort(w.head)}`;
/* swe never deletes a folder git no longer lists, so an orphan has no Delete. */
const wtCanDelete = (w) => !["main", "protected", "orphaned"].includes(w.state);
const wtPruneCommand = (w) => `git -C ${Fmt.shellWord(w.main_checkout || ".")} worktree prune`;

function wtStateBadge(state) {
  const s = WT_STATES[state] || { label: state || "Unknown", arrow: "grey" };
  return `<span class="badge wt-state"><img class="badge-arrow" src="state-${s.arrow}.svg" alt="" aria-hidden="true">${esc(s.label)}</span>`;
}

function wtLockBadge(w) {
  if (!w.locked) return "";
  const why = w.lock_reason ? `Locked: ${w.lock_reason}` : "Locked";
  return `<span class="badge wt-locked" title="${esc(why)}">${WT_LOCK}Locked</span>`;
}

function wtPrLink(w) {
  const pr = w.pr;
  if (!pr || !pr.number) return "";
  const state = String(pr.state || "").toLowerCase();
  const text = `PR #${pr.number}${state ? ` ${state}` : ""}`;
  const href = safeHref(pr.url);
  return href
    ? `<a class="badge wt-pr wt-pr-${esc(state)}" href="${href}" target="_blank" rel="noopener noreferrer" title="Open the pull request on GitHub">${esc(text)}</a>`
    : `<span class="badge wt-pr">${esc(text)}</span>`;
}

function wtHarness(h) {
  const label = WT_HARNESS[h] || Fmt.harnessName(h) || "Other";
  const mark = WT_HARNESS_ICON[h] ? `<span class="wt-glyph">${icon(WT_HARNESS_ICON[h])}</span>`
    : Fmt.logo(h, "logo");
  return `<span class="wt-mark" title="${esc(label)}">${mark}<span>${esc(label)}</span></span>`;
}

function wtSizeText(w) {
  if (w.size_bytes == null) return "Not measured";
  const re = w.rebuildable_bytes;
  return re ? `${wtBytes(w.size_bytes)}, ${wtBytes(re)} rebuildable` : wtBytes(w.size_bytes);
}

/* ----------------------------------------------------------------- model */

const wtList = () => (wtPage.data && wtPage.data.worktrees) || [];
const wtFind = (path) => wtList().find((w) => w.path === path)
  || (wtPage.detail && wtPage.detail.path === path ? wtPage.detail : null);
const wtRepoKey = (w) => w.main_checkout || w.repo || w.path;
const wtRepoTitle = (w) => w.repo || wtBase(w.main_checkout || w.path);

/* The repos a New worktree can go in: one per main checkout. */
function wtRepos() {
  const seen = new Map();
  for (const w of wtList()) {
    const key = w.main_checkout;
    if (key && !seen.has(key)) seen.set(key, { path: key, label: wtRepoTitle(w) });
  }
  return [...seen.values()].sort((a, b) => a.label.localeCompare(b.label));
}

function wtMatches(w, p) {
  if (p.state === "locked" ? !w.locked : p.state !== "all" && w.state !== p.state) return false;
  if (p.repo && wtRepoKey(w) !== p.repo) return false;
  const q = p.q.trim().toLowerCase();
  if (!q) return true;
  const hay = [w.branch, w.path, w.repo, w.head, w.harness, w.lock_reason,
    (WT_STATES[w.state] || {}).label, w.pr && w.pr.number ? `#${w.pr.number} pr${w.pr.number}` : ""]
    .filter(Boolean).join(" ").toLowerCase();
  return q.split(/\s+/).every((word) => hay.includes(word));
}

/* Groups by repo, alphabetical; the main checkout first in its group, the
 * rest newest commit first. */
function wtGroups(list) {
  const by = new Map();
  for (const w of list) {
    const key = wtRepoKey(w);
    if (!by.has(key)) by.set(key, { key, title: wtRepoTitle(w), rows: [] });
    by.get(key).rows.push(w);
  }
  const groups = [...by.values()].sort((a, b) => a.title.localeCompare(b.title));
  for (const g of groups) {
    g.rows.sort((a, b) => (b.state === "main") - (a.state === "main")
      || (wtEpoch(b.last_commit_at) || 0) - (wtEpoch(a.last_commit_at) || 0));
  }
  return groups;
}

function wtSum(list, key) {
  return list.reduce((n, w) => n + (Number(w[key]) || 0), 0);
}
/* A sum as text: "Not measured" when no row has the number. */
const wtSumText = (list, key) => (list.some((w) => w[key] != null) ? wtBytes(wtSum(list, key)) : "Not measured");

/* ----------------------------------------------------------------- pages */

function renderWorktrees() {
  const p = wtPage;
  if (p.notice && p.notice.shown) p.notice = null;
  if (p.error && p.error.shown) p.error = null;
  const path = wtQuery().get("path");
  return path ? renderWtDetail(path) : renderWtList();
}

function wtHeader(busy) {
  return pageHeader("Worktrees",
    "Every git worktree on this machine, grouped by repo, with the state that says whether it is safe to delete.",
    `<button class="btn hairline wt-button primary" id="wt-new" type="button" title="Check out a branch or pull request into a new worktree">${WT_PLUS}New worktree</button>
     <button class="icon-button${busy ? " spinning" : ""}" id="page-refresh" aria-label="Refresh" title="Refresh">${icon("rescan")}</button>`);
}

function bindWtHeader() {
  const add = document.getElementById("wt-new");
  if (add) add.onclick = () => wtOpenNew();
  bindRefresh(renderWorktrees, () => { wtPage.notice = null; wtPage.error = null; });
}

async function renderWtList() {
  const p = wtPage, gen = routeGen, mine = ++p.seq;
  if (!p.data) {
    document.getElementById("panel").innerHTML = `<div class="page">${wtHeader(true)}${wtSkeleton()}</div>`;
    bindWtHeader();
  } else {
    paintWt();
  }
  const { status, data } = await api("/v1/app/worktrees?scope=all");
  if (gen !== routeGen || mine !== p.seq) return;
  if (status === 401) return renderGate();
  if (status !== 200 || !data) return pageError(wtHeader(false), status, data, renderWorktrees);
  p.data = data;
  paintWt();
}

function wtSkeleton() {
  const row = (i) => `<div class="wt-row"><span class="wt-main">
    <span class="wt-title">${Sk.bar(130 - (i % 3) * 22, 13)}${Sk.pill(86)}</span>
    ${Sk.bar(`${58 - (i % 4) * 8}%`, 11)}${Sk.bar(`${36 - (i % 3) * 5}%`, 11)}</span></div>`;
  const group = (n, off) => `<div class="card wt-group"><div class="wt-group-head">${Sk.bar(120 + off * 20, 14)}</div>
    <div class="wt-rows">${Array.from({ length: n }, (_, i) => row(i + off)).join("")}</div></div>`;
  return `<div role="status" aria-label="Loading" class="stack">${group(3, 0)}${group(2, 3)}</div>`;
}

function wtNoticeHtml() {
  const p = wtPage;
  // Shown once: the next route (renderWorktrees) drops what was already on screen.
  if (p.notice) p.notice.shown = true;
  if (p.error) p.error.shown = true;
  return `${p.error ? `<div class="card wt-banner"><p class="error" role="alert">${esc(p.error.text)}</p></div>` : ""}
    ${p.notice ? `<div class="card wt-banner wt-ok" role="status"><span>${esc(p.notice.text)}</span>${
      p.notice.to ? `<a class="wt-notice-link" href="${wtRoute(p.notice.to)}">Open</a>` : ""}</div>` : ""}`;
}
function wtSummaryHtml(list) {
  const clean = list.filter((w) => w.state === "merged-clean");
  const dirty = list.filter((w) => w.state === "merged-dirty");
  const stat = (label, value, sub) => `<div class="wt-stat"><div class="caption">${esc(label)}</div>
    <div class="wt-stat-value num">${esc(value)}</div>${sub ? `<div class="caption">${esc(sub)}</div>` : ""}</div>`;
  const repos = new Set(list.map(wtRepoKey)).size;
  return `<div class="card wt-summary">
    <div class="wt-stats">
      ${stat("Worktrees", String(list.length), wtCount(repos, "repo"))}
      ${stat("Total size", wtSumText(list, "size_bytes"), "by du, an estimate")}
      ${stat("Merged and clean", String(clean.length), clean.length ? `${wtSumText(clean, "size_bytes")} to reclaim` : "nothing to clean")}
      ${stat("Rebuildable", wtSumText(list, "rebuildable_bytes"), "node_modules, caches and similar")}
    </div>
    <div class="wt-bulk">
      <button class="btn hairline wt-button" type="button" data-wt-bulk="prune"${clean.length || dirty.length ? "" : " disabled"}
        title="Preview, then delete every merged worktree">Clean up merged…</button>
      <button class="btn hairline wt-button" type="button" data-wt-bulk="slim"${wtSum(list, "rebuildable_bytes") ? "" : " disabled"}
        title="Preview, then delete the folders a build can recreate">Slim rebuildable…</button>
    </div></div>`;
}

function wtChipsHtml(list) {
  const p = wtPage;
  const counts = {};
  for (const w of list) counts[w.state] = (counts[w.state] || 0) + 1;
  const chips = [["all", "All", list.length]];
  for (const s of WT_ORDER) if (counts[s]) chips.push([s, WT_CHIP_LABEL[s], counts[s]]);
  const locked = list.filter((w) => w.locked).length;
  if (locked) chips.push(["locked", "Locked", locked]);
  return chips.map(([key, label, n]) => `<button type="button" class="wt-chip" data-wt-chip="${esc(key)}"
    aria-pressed="${p.state === key}">${esc(label)}<span class="num">${n}</span></button>`).join("");
}

/* What to do about an orphan: git has to forget it, and swe won't delete folders itself. */
function wtOrphanNote(w) {
  return `<span class="caption wt-orphan">Run <code class="wt-path">git worktree prune</code> in the main checkout.
    <button type="button" class="icon-button wt-copy" data-wt-copy="${esc(wtPruneCommand(w))}" aria-label="Copy the git worktree prune command" title="Copy the command">${Fmt.copyIcon()}</button></span>`;
}

function wtRowHtml(w) {
  const actions = [];
  actions.push(`<button type="button" class="icon-button wt-copy" data-wt-copy="${esc(w.path)}" aria-label="Copy path" title="Copy path">${Fmt.copyIcon()}</button>`);
  if (w.rebuildable_bytes) actions.push(`<button type="button" class="btn hairline secondary" data-wt="slim" data-path="${esc(w.path)}" title="Delete what a build can recreate">Slim</button>`);
  if (wtCanDelete(w)) actions.push(`<button type="button" class="btn hairline secondary danger-text" data-wt="delete" data-path="${esc(w.path)}">Delete…</button>`);
  const notes = [];
  if (w.state === "orphaned") notes.push(wtOrphanNote(w));
  if (w.hold) notes.push(`<span class="caption wt-hold">Clean up keeps it: ${esc(w.hold)}</span>`);
  return `<div class="wt-row"><div class="wt-body">
    <a class="wt-main" href="${wtRoute(wtDetailLink(w.path))}" title="Open ${esc(wtBranchName(w))}">
      <span class="wt-title"><span class="name">${esc(wtBranchName(w))}</span>
        ${wtStateBadge(w.state)}${wtLockBadge(w)}${w.dirty && w.state !== "merged-dirty" ? '<span class="badge">Uncommitted changes</span>' : ""}</span>
      <code class="wt-path" title="${esc(w.path)}">${esc(wtTilde(w.path))}</code>
      <span class="wt-meta caption"><span>Last commit ${esc(wtAgo(w.last_commit_at))}</span><span class="num">${esc(wtSizeText(w))}</span>${wtHarness(w.harness)}</span>
    </a>
    ${notes.length ? `<div class="wt-notes">${notes.join("")}</div>` : ""}</div>
    <span class="wt-side">${wtPrLink(w)}<span class="wt-actions">${actions.join("")}</span></span>
  </div>`;
}

function wtGroupsHtml(list) {
  const p = wtPage;
  const shown = list.filter((w) => wtMatches(w, p));
  if (!shown.length) {
    return `<div class="card wt-empty"><p class="empty">No worktree matches these filters.</p>
      <div><button class="btn hairline wt-button" type="button" data-wt-clear>Clear filters</button></div></div>`;
  }
  return wtGroups(shown).map((g) => `<div class="card wt-group">
    <div class="wt-group-head"><span class="card-title">${esc(g.title)}</span>
      <span class="caption">${esc(wtCount(g.rows.length, "worktree"))}, ${esc(wtSumText(g.rows, "size_bytes"))}</span>
      <code class="wt-path wt-group-path" title="${esc(g.key)}">${esc(wtTilde(g.key))}</code></div>
    <div class="wt-rows">${g.rows.map(wtRowHtml).join("")}</div></div>`).join("");
}

function paintWt() {
  const p = wtPage, list = wtList();
  if (!list.length) {
    document.getElementById("panel").innerHTML = `<div class="page">${wtHeader(false)}<div class="stack">${wtNoticeHtml()}
      <div class="card wt-empty"><p class="empty">No worktrees yet. A worktree checks a branch out into its own folder, so an agent can work on it without touching your main checkout.</p>
      <div><button class="btn hairline wt-button primary" id="wt-new-empty" type="button">New worktree</button></div></div></div></div>`;
    bindWtHeader();
    const add = document.getElementById("wt-new-empty");
    if (add) add.onclick = () => wtOpenNew();
    return;
  }
  const repos = wtRepos();
  if (p.repo && !repos.some((r) => r.path === p.repo)) p.repo = "";
  const repoSelect = repos.length > 1 ? `<select class="field" id="wt-repo-filter" aria-label="Repo">
      <option value="">All repos</option>${repos.map((r) => `<option value="${esc(r.path)}"${r.path === p.repo ? " selected" : ""}>${esc(r.label)}</option>`).join("")}</select>` : "";
  document.getElementById("panel").innerHTML = `<div class="page">${wtHeader(false)}
    <div class="stack">
      <div id="wt-notice" class="stack">${wtNoticeHtml()}</div>
      <div id="wt-summary">${wtSummaryHtml(list)}</div>
      <div class="filters wt-filters">
        <input class="field search" id="wt-search" type="search" placeholder="Search branch, path, PR or harness" aria-label="Search worktrees"
          value="${esc(p.q)}" autocomplete="off" spellcheck="false">${repoSelect}</div>
      <div class="wt-chips" id="wt-chips" role="group" aria-label="Filter by state">${wtChipsHtml(list)}</div>
      <div class="stack" id="wt-groups">${wtGroupsHtml(list)}</div>
    </div></div>`;
  bindWtHeader();
  const search = document.getElementById("wt-search");
  bindSearch(search, (v) => { p.q = v; }, paintWtResults);
  const repoSel = document.getElementById("wt-repo-filter");
  if (repoSel) repoSel.onchange = () => { p.repo = repoSel.value; paintWtResults(); };
  bindWtDelegates();
}

/* A filter change repaints the chips and the groups, not the search box
 * the person is typing in. */
function paintWtResults() {
  const list = wtList();
  const chips = document.getElementById("wt-chips"), groups = document.getElementById("wt-groups");
  if (!chips || !groups) return;
  chips.innerHTML = wtChipsHtml(list);
  groups.innerHTML = wtGroupsHtml(list);
  wtWireCopy(groups);
}

function wtWireCopy(root) {
  for (const btn of root.querySelectorAll("[data-wt-copy]")) Fmt.copyTick(btn, btn.dataset.wtCopy);
}

/* One click handler for the page's own buttons, so a repaint of the
 * groups needs no rebinding. */
function bindWtDelegates() {
  const panel = document.getElementById("panel");
  wtWireCopy(panel);
  panel.onclick = (event) => {
    const t = event.target;
    if (!t || !t.closest) return;
    const chip = t.closest("[data-wt-chip]");
    if (chip) { wtPage.state = chip.dataset.wtChip; paintWtResults(); return; }
    if (t.closest("[data-wt-clear]")) {
      Object.assign(wtPage, { state: "all", repo: "", q: "" });
      paintWt();
      return;
    }
    const bulk = t.closest("[data-wt-bulk]");
    if (bulk) { return bulk.dataset.wtBulk === "prune" ? wtOpenPrune() : wtOpenSlim(null); }
    const btn = t.closest("[data-wt]");
    if (!btn) return;
    const w = wtFind(btn.dataset.path);
    if (!w) return;
    const act = btn.dataset.wt;
    if (act === "delete") wtOpenDelete(w);
    else if (act === "slim") wtOpenSlim(w);
    else if (act === "lock") wtOpenLock(w);
    else if (act === "unlock") wtUnlock(w);
    else if (act === "move") wtOpenMove(w);
  };
}

/* ---------------------------------------------------------------- detail */

async function renderWtDetail(path) {
  const p = wtPage, gen = routeGen, mine = ++p.seq;
  const back = `<a class="wt-back" href="#/worktrees">‹ Worktrees</a>`;
  document.getElementById("panel").innerHTML = `<div class="page">${wtHeader(false)}${back}
    <div class="stack" role="status" aria-label="Loading"><div class="card">${Sk.bar("40%", 18)}${Sk.bar("60%", 11)}${Sk.bar("50%", 11)}</div></div></div>`;
  bindWtHeader();
  const { status, data } = await api(`/v1/app/worktrees?worktree=${encodeURIComponent(path)}`);
  if (gen !== routeGen || mine !== p.seq) return;
  if (status === 401) return renderGate();
  if (status !== 200 || !data || !data.worktree) {
    const msg = failText(status, data, "then refresh");
    document.getElementById("panel").innerHTML = `<div class="page">${wtHeader(false)}${back}
      <div class="card"><p class="error">${esc(msg)}</p></div></div>`;
    bindWtHeader();
    return;
  }
  p.detail = data.worktree;
  // The list behind it, if it has not loaded yet, supplies the repo choices.
  paintWtDetail(data.worktree);
}

function wtKv(label, valueHtml) {
  return `<div class="wt-kv"><span class="caption">${esc(label)}</span><span class="wt-kv-value">${valueHtml}</span></div>`;
}

function paintWtDetail(w) {
  const back = `<a class="wt-back" href="#/worktrees">‹ Worktrees</a>`;
  const canDelete = wtCanDelete(w);
  const orphan = w.state === "orphaned";
  const buttons = orphan ? `<button class="btn hairline wt-button" type="button" data-wt-copy="${esc(w.path)}">${Fmt.copyIcon()}Copy path</button>` : [
    w.locked
      ? `<button class="btn hairline wt-button" type="button" data-wt="unlock" data-path="${esc(w.path)}">Unlock</button>`
      : `<button class="btn hairline wt-button" type="button" data-wt="lock" data-path="${esc(w.path)}">Lock…</button>`,
    w.state !== "main" ? `<button class="btn hairline wt-button" type="button" data-wt="move" data-path="${esc(w.path)}">Move…</button>` : "",
    `<button class="btn hairline wt-button" type="button" data-wt="slim" data-path="${esc(w.path)}"${w.rebuildable_bytes ? "" : " disabled"}>Slim…</button>`,
    `<button class="btn hairline wt-button" type="button" data-wt-copy="${esc(w.path)}">${Fmt.copyIcon()}Copy path</button>`,
    canDelete ? `<button class="btn hairline wt-button danger-text" type="button" data-wt="delete" data-path="${esc(w.path)}">Delete…</button>` : "",
  ].join("");
  const protectedNote = orphan ? `<div class="wt-notes">${wtOrphanNote(w)}</div>` : !canDelete ? `<p class="caption">${w.state === "main"
    ? "This is the repo's main checkout, so swe won't delete it."
    : "This worktree is protected, so swe won't delete it."}</p>` : "";
  const dirty = w.dirty || w.untracked
    ? `${w.dirty ? "Uncommitted changes" : "No uncommitted changes"}${w.untracked ? `, ${wtCount(w.untracked, "untracked file")}` : ""}`
    : "Clean";
  const when = wtEpoch(w.last_commit_at);
  document.getElementById("panel").innerHTML = `<div class="page">${wtHeader(false)}${back}
    <div class="stack">
      <div id="wt-notice" class="stack">${wtNoticeHtml()}</div>
      <div class="card wt-head">
        <div class="wt-title"><span class="name">${esc(wtBranchName(w))}</span>${wtStateBadge(w.state)}${wtLockBadge(w)}${wtPrLink(w)}</div>
        <div class="wt-actions wt-detail-actions">${buttons}</div>
        ${protectedNote}
        ${w.hold ? `<p class="caption wt-hold">Clean up keeps it: ${esc(w.hold)}</p>` : ""}
        ${w.locked && w.lock_reason ? `<p class="caption">Locked: ${esc(w.lock_reason)}</p>` : ""}
      </div>
      <div class="card wt-details">
        ${w.reason ? wtKv("Why", esc(w.reason)) : ""}
        ${wtKv("Path", `<code class="wt-path">${esc(w.path)}</code>`)}
        ${wtKv("Repo", esc(w.repo || "No GitHub remote"))}
        ${wtKv("Main checkout", `<code class="wt-path">${esc(w.main_checkout || "")}</code>`)}
        ${wtKv("Branch", esc(w.branch || "Detached"))}
        ${wtKv("Head", `<code class="wt-path">${esc(wtShort(w.head))}</code>`)}
        ${wtKv("Last commit", `${esc(wtAgo(w.last_commit_at))}${when ? ` <span class="caption">(${esc(new Date(when * 1000).toLocaleString())})</span>` : ""}`)}
        ${wtKv("Size", `<span class="num">${esc(wtBytes(w.size_bytes))}</span>${w.size_bytes != null && w.rebuildable_bytes ? ` <span class="caption">(${esc(wtBytes(w.rebuildable_bytes))} rebuildable)</span>` : ""}`)}
        ${wtKv("Working tree", esc(dirty))}
        ${wtKv("Harness", wtHarness(w.harness))}
      </div>
    </div></div>`;
  bindWtHeader();
  bindWtDelegates();
}

/* ---------------------------------------------------------------- writes */

const wtPost = (action, body) => apiPost(`/v1/app/worktrees/${action}`, body);

/* A write worked: say so on the page and read it again. */
function wtDone(text, to) {
  wtPage.notice = { text, to: to || null };
  wtPage.error = null;
  wtPage.seq++;
  if (!wtOnRoute()) return;
  const here = wtQuery().get("path");
  if (!here) return renderWorktrees();
  // On a worktree's page: stay when it is still there, follow it when it
  // moved, and go back to the list when it is gone.
  if (to && to === wtDetailLink(here)) return renderWorktrees();
  location.hash = to && to.startsWith("#/worktrees?path=") ? to : "#/worktrees";
}

async function wtUnlock(w) {
  wtPage.notice = null;
  wtPage.error = null;
  const { status, data } = await wtPost("unlock", { path: w.path });
  if (status === 401) { if (wtOnRoute()) renderGate(); return; }
  if (status === 200 && data) return wtDone(`Unlocked ${wtBranchName(w)}.`, wtHereLink(w));
  wtPage.error = { text: failText(status, data) };
  if (wtOnRoute()) renderWorktrees();
}
const wtHereLink = (w) => (wtQuery().get("path") ? wtDetailLink(w.path) : null);

/* ---------------------------------------------------------------- sheets */

/* One dialog for every flow, on document.body so a repaint of the page
 * behind it can't close it. A reply for a sheet closed (or replaced)
 * since the request began compares its token and is dropped. */
function wtShow(inner, cls = "") {
  const sheet = bodySheet("wt-sheet", "sheet wt-sheet", "Worktrees");
  sheet.dataset.token = String(++wtPage.token);
  sheet.className = `sheet wt-sheet${cls ? ` ${cls}` : ""}`;
  sheet.innerHTML = `<div class="sheet-body">${inner}</div>`;
  if (!sheet.open) sheet.showModal();
  return { sheet, token: sheet.dataset.token };
}
const wtLive = (h) => h.sheet.open && h.sheet.dataset.token === h.token;
const wtById = (id) => document.getElementById(id);

function wtSheetError(msg) {
  const el = wtById("wt-sheet-error");
  if (!el) return;
  el.textContent = msg || "";
  el.hidden = !msg;
}

/* A failed write inside a sheet: the 401 closes it for the gate. */
function wtSheetFail(h, status, data) {
  if (!wtLive(h)) return;
  if (status === 401) {
    h.sheet.close();
    if (wtOnRoute()) renderGate();
    return;
  }
  wtSheetError(failText(status, data));
}

/* ---- New worktree */

function wtOpenNew() {
  const p = wtPage;
  const repos = wtRepos();
  const picked = p.repo && repos.some((r) => r.path === p.repo) ? p.repo : (repos[0] || {}).path || "";
  const repoField = repos.length
    ? `<select class="field" id="wt-n-repo">${repos.map((r) => `<option value="${esc(r.path)}"${r.path === picked ? " selected" : ""}>${esc(r.label)}</option>`).join("")}
         <option value="">Another path…</option></select>
       <input class="field wt-mono" id="wt-n-repo-path" type="text" placeholder="/absolute/path/to/repo" autocomplete="off" spellcheck="false" hidden>`
    : `<input class="field wt-mono" id="wt-n-repo-path" type="text" placeholder="/absolute/path/to/repo" autocomplete="off" spellcheck="false">`;
  const seg = (value, label, checked) => `<label class="wt-seg"><input type="radio" name="wt-n-mode" value="${value}"${checked ? " checked" : ""}><span>${label}</span></label>`;
  const h = wtShow(`<form id="wt-n-form" novalidate>
    <div class="sheet-title">New worktree</div>
    <label class="wt-field"><span class="wt-label">Repository</span>${repoField}</label>
    <div class="wt-field"><span class="wt-label" id="wt-n-mode-label">Check out</span>
      <div class="wt-segs" role="radiogroup" aria-labelledby="wt-n-mode-label">${seg("existing", "Existing branch", true)}${seg("new", "New branch", false)}${seg("pr", "Pull request", false)}</div></div>
    <label class="wt-field" id="wt-n-branch-row"><span class="wt-label" id="wt-n-branch-label">Branch</span>
      <input class="field" id="wt-n-branch" type="text" autocomplete="off" spellcheck="false" placeholder="feature/login"></label>
    <label class="wt-field" id="wt-n-base-row" hidden><span class="wt-label">Base</span>
      <input class="field" id="wt-n-base" type="text" autocomplete="off" spellcheck="false" placeholder="The repo's default branch"></label>
    <label class="wt-field" id="wt-n-pr-row" hidden><span class="wt-label">Pull request number</span>
      <input class="field" id="wt-n-pr" type="text" inputmode="numeric" autocomplete="off" placeholder="1234"></label>
    <label class="wt-field"><span class="wt-label">Folder <span class="caption">(optional)</span></span>
      <input class="field wt-mono" id="wt-n-path" type="text" autocomplete="off" spellcheck="false" placeholder="swe picks one next to the repo"></label>
    <p class="caption" id="wt-n-hint"></p>
    <p class="error" id="wt-sheet-error" role="alert" hidden></p>
    <div class="sheet-actions">
      <button class="btn hairline wt-button" type="button" id="wt-cancel">Cancel</button>
      <button class="btn hairline wt-button primary" type="submit" id="wt-n-create" disabled>Create worktree</button>
    </div></form>`);

  const mode = () => (document.querySelector('input[name="wt-n-mode"]:checked') || {}).value || "existing";
  const repoValue = () => {
    const sel = wtById("wt-n-repo");
    return (sel && sel.value) || wtById("wt-n-repo-path").value.trim();
  };
  const problem = () => {
    const m = mode();
    if (!repoValue()) return "Choose a repository, or enter its path.";
    if (!repoValue().startsWith("/")) return "The repository path must be absolute.";
    if (m === "pr") return /^[1-9]\d*$/.test(wtById("wt-n-pr").value.trim()) ? null : "Enter the pull request's number.";
    if (!wtById("wt-n-branch").value.trim()) return m === "new" ? "Name the new branch." : "Name the branch to check out.";
    const path = wtById("wt-n-path").value.trim();
    if (path && !path.startsWith("/")) return "The folder must be an absolute path, or empty.";
    return null;
  };
  const sync = () => {
    const m = mode(), sel = wtById("wt-n-repo");
    wtById("wt-n-repo-path").hidden = !!(sel && sel.value);
    wtById("wt-n-branch-row").hidden = m === "pr";
    wtById("wt-n-base-row").hidden = m !== "new";
    wtById("wt-n-pr-row").hidden = m !== "pr";
    wtById("wt-n-branch-label").textContent = m === "new" ? "New branch name" : "Branch";
    const bad = problem();
    wtById("wt-n-hint").textContent = bad || "";
    wtById("wt-n-create").disabled = !!bad || h.sheet.dataset.working === "1";
    wtSheetError("");
  };
  const form = wtById("wt-n-form");
  form.oninput = sync;
  form.onchange = sync;
  wtById("wt-cancel").onclick = () => h.sheet.close();
  form.onsubmit = async (e) => {
    e.preventDefault();
    if (problem() || h.sheet.dataset.working === "1") return;
    const m = mode();
    const body = { repo: repoValue() };
    if (m === "pr") body.pr = Number(wtById("wt-n-pr").value.trim());
    else {
      body.branch = wtById("wt-n-branch").value.trim();
      if (m === "new") {
        body.new = true;
        const base = wtById("wt-n-base").value.trim();
        if (base) body.base = base;
      }
    }
    const path = wtById("wt-n-path").value.trim();
    if (path) body.path = path;
    h.sheet.dataset.working = "1";
    wtById("wt-n-create").disabled = true;
    wtById("wt-n-create").textContent = "Creating…";
    const { status, data } = await wtPost("add", body);
    if (!wtLive(h)) return;
    h.sheet.dataset.working = "";
    if (status === 200 && data && data.worktree) {
      h.sheet.close();
      wtDone(`Created ${wtBranchName(data.worktree)} at ${wtTilde(data.worktree.path)}.`, wtDetailLink(data.worktree.path));
      return;
    }
    wtById("wt-n-create").textContent = "Create worktree";
    sync();
    wtSheetFail(h, status, data);
  };
  sync();
  const first = wtById("wt-n-branch");
  if (first) first.focus();
}

/* ---- Delete */

function wtOpenDelete(w) {
  if (!wtCanDelete(w)) return;
  const needsForce = w.state !== "merged-clean";
  const risk = WT_RISK[w.state] || "swe can't say this worktree is merged and clean.";
  const lost = [];
  if (w.dirty) lost.push("uncommitted changes");
  if (w.untracked) lost.push(wtCount(w.untracked, "untracked file"));
  const h = wtShow(`<form id="wt-d-form" novalidate>
    <div class="sheet-title">Delete this worktree?</div>
    <p class="caption">This deletes:</p>
    <ul class="wt-delete-list">
      <li>The folder <code class="wt-path">${esc(w.path)}</code> and everything in it ${w.size_bytes != null ? ` (${esc(wtBytes(w.size_bytes))})` : ""}.</li>
      <li id="wt-d-branch-line" hidden>The local branch <strong>${esc(w.branch || "")}</strong>.</li>
    </ul>
    ${lost.length ? `<p class="note">It holds ${esc(lost.join(" and "))}.</p>` : ""}
    ${w.branch ? `<label class="wt-check"><input type="checkbox" id="wt-d-branch"><span>Also delete the branch <strong>${esc(w.branch)}</strong></span></label>` : ""}
    ${w.locked ? `<p class="note">This worktree is locked${w.lock_reason ? ` (${esc(w.lock_reason)})` : ""}. Unlock it first.</p>` : ""}
    ${needsForce ? `<div class="wt-risk"><p class="note">${esc(WT_STATES[w.state] ? `State: ${WT_STATES[w.state].label}. ` : "")}${esc(risk)}</p>
      <label class="wt-check"><input type="checkbox" id="wt-d-force"><span>I understand. Delete it anyway, even though it is not merged and clean.</span></label></div>` : ""}
    <p class="error" id="wt-sheet-error" role="alert" hidden></p>
    <div class="sheet-actions">
      <button class="btn hairline wt-button" type="button" id="wt-cancel">Cancel</button>
      <button class="btn hairline wt-button danger" type="submit" id="wt-d-go">Delete worktree</button>
    </div></form>`);
  const force = wtById("wt-d-force"), branch = wtById("wt-d-branch"), go = wtById("wt-d-go");
  const sync = () => {
    go.disabled = h.sheet.dataset.working === "1" || w.locked || (needsForce && !force.checked);
    wtById("wt-d-branch-line").hidden = !(branch && branch.checked);
    wtSheetError("");
  };
  if (force) force.onchange = sync;
  if (branch) branch.onchange = sync;
  wtById("wt-cancel").onclick = () => h.sheet.close();
  wtById("wt-d-form").onsubmit = async (e) => {
    e.preventDefault();
    sync();
    if (go.disabled) return;
    // `force` is in the body only when its own box is ticked.
    const body = { path: w.path };
    if (needsForce && force.checked) body.force = true;
    if (branch && branch.checked) body.delete_branch = true;
    h.sheet.dataset.working = "1";
    go.disabled = true;
    go.textContent = "Deleting…";
    const { status, data } = await wtPost("remove", body);
    if (!wtLive(h)) return;
    h.sheet.dataset.working = "";
    if (status === 200 && data) {
      h.sheet.close();
      const freed = data.freed_bytes != null ? `, freed ${wtBytes(data.freed_bytes)}` : "";
      wtDone(`Deleted ${wtTilde(data.removed || w.path)}${freed}.`, null);
      return;
    }
    go.textContent = "Delete worktree";
    sync();
    wtSheetFail(h, status, data);
  };
  sync();
  wtById("wt-cancel").focus();
}

/* ---- Lock and move */

function wtOpenLock(w) {
  const h = wtShow(`<form id="wt-l-form" novalidate>
    <div class="sheet-title">Lock this worktree</div>
    <p class="caption">A locked worktree can't be pruned or removed until it is unlocked.</p>
    <label class="wt-field"><span class="wt-label">Reason <span class="caption">(optional)</span></span>
      <input class="field" id="wt-l-reason" type="text" autocomplete="off" placeholder="An agent is working here"></label>
    <p class="error" id="wt-sheet-error" role="alert" hidden></p>
    <div class="sheet-actions">
      <button class="btn hairline wt-button" type="button" id="wt-cancel">Cancel</button>
      <button class="btn hairline wt-button primary" type="submit" id="wt-l-go">Lock</button>
    </div></form>`);
  wtById("wt-cancel").onclick = () => h.sheet.close();
  wtById("wt-l-form").onsubmit = async (e) => {
    e.preventDefault();
    if (h.sheet.dataset.working === "1") return;
    h.sheet.dataset.working = "1";
    const body = { path: w.path };
    const reason = wtById("wt-l-reason").value.trim();
    if (reason) body.reason = reason;
    wtById("wt-l-go").disabled = true;
    const { status, data } = await wtPost("lock", body);
    if (!wtLive(h)) return;
    h.sheet.dataset.working = "";
    if (status === 200 && data) {
      h.sheet.close();
      wtDone(`Locked ${wtBranchName(w)}.`, wtHereLink(w));
      return;
    }
    wtById("wt-l-go").disabled = false;
    wtSheetFail(h, status, data);
  };
  wtById("wt-l-reason").focus();
}

function wtOpenMove(w) {
  const h = wtShow(`<form id="wt-m-form" novalidate>
    <div class="sheet-title">Move this worktree</div>
    <p class="caption">From <code class="wt-path">${esc(w.path)}</code></p>
    <label class="wt-field"><span class="wt-label">New folder</span>
      <input class="field wt-mono" id="wt-m-path" type="text" autocomplete="off" spellcheck="false" value="${esc(w.path)}"></label>
    <p class="caption" id="wt-m-hint"></p>
    <p class="error" id="wt-sheet-error" role="alert" hidden></p>
    <div class="sheet-actions">
      <button class="btn hairline wt-button" type="button" id="wt-cancel">Cancel</button>
      <button class="btn hairline wt-button primary" type="submit" id="wt-m-go" disabled>Move</button>
    </div></form>`);
  const sync = () => {
    const v = wtById("wt-m-path").value.trim();
    const bad = !v.startsWith("/") ? "The new folder must be an absolute path." : v === w.path ? "That is where it already is." : "";
    wtById("wt-m-hint").textContent = bad;
    wtById("wt-m-go").disabled = !!bad || h.sheet.dataset.working === "1";
    wtSheetError("");
  };
  wtById("wt-m-path").oninput = sync;
  wtById("wt-cancel").onclick = () => h.sheet.close();
  wtById("wt-m-form").onsubmit = async (e) => {
    e.preventDefault();
    sync();
    if (wtById("wt-m-go").disabled) return;
    h.sheet.dataset.working = "1";
    wtById("wt-m-go").disabled = true;
    const { status, data } = await wtPost("move", { path: w.path, new_path: wtById("wt-m-path").value.trim() });
    if (!wtLive(h)) return;
    h.sheet.dataset.working = "";
    if (status === 200 && data && data.worktree) {
      h.sheet.close();
      wtDone(`Moved ${wtBranchName(w)} to ${wtTilde(data.worktree.path)}.`, wtDetailLink(data.worktree.path));
      return;
    }
    sync();
    wtSheetFail(h, status, data);
  };
  sync();
  const input = wtById("wt-m-path");
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
}

/* The scope a bulk action covers: the repo filter's repo, else every repo. */
const wtScope = () => (wtPage.repo ? { repo: wtPage.repo } : { all: true });

/* ---- Slim: preview what a build can recreate, then confirm. */

async function wtOpenSlim(w) {
  const title = w ? `Slim ${wtBranchName(w)}` : wtPage.repo ? "Slim this repo's worktrees" : "Slim every worktree";
  const body = w ? { path: w.path } : wtScope();
  const h = wtShow(`<div class="sheet-title">${esc(title)}</div>
    <div class="wt-loading" role="status"><span class="spinner" aria-hidden="true"></span><span>Measuring what a build can recreate…</span></div>
    <div class="sheet-actions"><button class="btn hairline wt-button" type="button" id="wt-cancel">Cancel</button></div>`);
  wtById("wt-cancel").onclick = () => h.sheet.close();
  const { status, data } = await wtPost("slim", body);
  if (!wtLive(h)) return;
  if (status !== 200 || !data) {
    const box = h.sheet.querySelector(".wt-loading");
    if (status === 401) return wtSheetFail(h, status, data);
    box.outerHTML = `<p class="error" role="alert">${esc(failText(status, data))}</p>`;
    return;
  }
  const dirs = data.dirs || [];
  if (!dirs.length) {
    h.sheet.querySelector(".wt-loading").outerHTML = `<p class="empty">Nothing to slim. No folder a build can recreate was found.</p>`;
    return;
  }
  const total = data.freed_bytes != null ? data.freed_bytes : wtSum(dirs, "bytes");
  h.sheet.querySelector(".sheet-body").innerHTML = `<form id="wt-s-form">
    <div class="sheet-title">${esc(title)}</div>
    <p class="caption">These ${wtCount(dirs.length, "folder")} can be rebuilt by installing or building again. Deleting them frees ${esc(wtBytes(total))}. Your source files stay.${data.days ? ` Folders changed in the last ${esc(wtCount(data.days, "day"))} are kept.` : ""}</p>
    <ul class="wt-list">${dirs.map((d) => `<li><code class="wt-path">${esc(wtTilde(d.path))}</code><span class="num caption">${esc(wtBytes(d.bytes))}</span></li>`).join("")}</ul>
    <p class="error" id="wt-sheet-error" role="alert" hidden></p>
    <div class="sheet-actions">
      <button class="btn hairline wt-button" type="button" id="wt-cancel">Cancel</button>
      <button class="btn hairline wt-button danger" type="submit" id="wt-s-go">Delete ${esc(wtCount(dirs.length, "folder"))}</button>
    </div></form>`;
  wtById("wt-cancel").onclick = () => h.sheet.close();
  wtById("wt-s-form").onsubmit = async (e) => {
    e.preventDefault();
    if (h.sheet.dataset.working === "1") return;
    h.sheet.dataset.working = "1";
    const go = wtById("wt-s-go");
    go.disabled = true;
    go.textContent = "Deleting…";
    const reply = await wtPost("slim", { ...body, yes: true });
    if (!wtLive(h)) return;
    h.sheet.dataset.working = "";
    if (reply.status === 200 && reply.data) {
      h.sheet.close();
      wtDone(`Slimmed ${w ? wtBranchName(w) : "every worktree"}, freed ${wtBytes(reply.data.freed_bytes)}.`, w ? wtHereLink(w) : null);
      return;
    }
    go.disabled = false;
    go.textContent = `Delete ${wtCount(dirs.length, "folder")}`;
    wtSheetFail(h, reply.status, reply.data);
  };
  wtById("wt-cancel").focus();
}

/* ---- Clean up merged: dry-run prune, then confirm with yes. */

function wtOpenPrune() {
  const h = wtShow(`<div class="sheet-title">Clean up merged worktrees</div>
    <div class="wt-loading" role="status"><span class="spinner" aria-hidden="true"></span><span>Looking for merged worktrees…</span></div>`, "wt-wide");
  wtPrunePreview(h, false);
}

async function wtPrunePreview(h, includeDirty, notice) {
  const body = wtScope();
  if (includeDirty) body.include = "merged-dirty";
  const { status, data } = await wtPost("prune", body);
  if (!wtLive(h)) return;
  const frame = (inner) => { h.sheet.querySelector(".sheet-body").innerHTML = inner; };
  if (status !== 200 || !data) {
    if (status === 401) return wtSheetFail(h, status, data);
    frame(`<div class="sheet-title">Clean up merged worktrees</div><p class="error" role="alert">${esc(failText(status, data))}</p>
      <div class="sheet-actions"><button class="btn hairline wt-button" type="button" id="wt-cancel">Close</button></div>`);
    wtById("wt-cancel").onclick = () => h.sheet.close();
    return;
  }
  const cands = data.candidates || [], skipped = data.skipped || [];
  const hasDirty = wtList().some((w) => w.state === "merged-dirty") || cands.some((c) => c.state === "merged-dirty");
  const dirtyCount = cands.filter((c) => c.state === "merged-dirty" || c.dirty).length;
  const total = wtSum(cands, "size_bytes");
  frame(`<form id="wt-p-form">
    <div class="sheet-title">Clean up merged worktrees</div>
    ${notice ? `<p class="note" role="status">${esc(notice)}</p>` : ""}
    ${cands.length
      ? `<p class="caption">These ${wtCount(cands.length, "worktree")} are merged. Deleting them frees about ${esc(wtBytes(data.freed_bytes != null && data.freed_bytes > 0 ? data.freed_bytes : total))}. Branches stay.</p>
         <ul class="wt-list">${cands.map((c) => `<li><span class="wt-list-main"><span class="name">${esc(wtBranchName(c))}</span>
           ${c.state === "merged-dirty" ? '<span class="badge">Uncommitted changes</span>' : ""}
           <code class="wt-path">${esc(wtTilde(c.path))}</code></span><span class="num caption">${esc(wtBytes(c.size_bytes))}</span></li>`).join("")}</ul>`
      : `<p class="empty">Nothing to clean up. No worktree is merged and safe to delete.</p>`}
    ${hasDirty ? `<label class="wt-check"><input type="checkbox" id="wt-p-dirty"${includeDirty ? " checked" : ""}>
      <span>Also include merged worktrees that have uncommitted changes</span></label>` : ""}
    ${includeDirty && dirtyCount ? `<p class="note">${esc(wtCount(dirtyCount, "worktree"))} here ${dirtyCount === 1 ? "has" : "have"} uncommitted changes. Those changes are lost for good.</p>` : ""}
    ${skipped.length ? `<details class="wt-skipped"><summary>${esc(wtCount(skipped.length, "worktree"))} skipped</summary>
      <ul class="wt-list">${skipped.map((s) => `<li><code class="wt-path">${esc(wtTilde(s.path))}</code><span class="caption">${esc(s.reason || "")}</span></li>`).join("")}</ul></details>` : ""}
    <p class="error" id="wt-sheet-error" role="alert" hidden></p>
    <div class="sheet-actions">
      <button class="btn hairline wt-button" type="button" id="wt-cancel">Cancel</button>
      <button class="btn hairline wt-button danger" type="submit" id="wt-p-go"${cands.length ? "" : " disabled"}>Delete ${esc(wtCount(cands.length, "worktree"))}</button>
    </div></form>`);
  wtById("wt-cancel").onclick = () => h.sheet.close();
  const dirty = wtById("wt-p-dirty");
  if (dirty) dirty.onchange = () => {
    h.sheet.querySelector(".sheet-body").innerHTML = `<div class="sheet-title">Clean up merged worktrees</div>
      <div class="wt-loading" role="status"><span class="spinner" aria-hidden="true"></span><span>Looking again…</span></div>`;
    wtPrunePreview(h, dirty.checked);
  };
  wtById("wt-p-form").onsubmit = async (e) => {
    e.preventDefault();
    if (h.sheet.dataset.working === "1" || !cands.length) return;
    h.sheet.dataset.working = "1";
    const go = wtById("wt-p-go");
    go.disabled = true;
    go.textContent = "Deleting…";
    // `yes` comes only from this confirm, with the paths the preview listed.
    const reply = await wtPost("prune", { ...body, yes: true, paths: cands.map((c) => c.path) });
    if (!wtLive(h)) return;
    h.sheet.dataset.working = "";
    if (reply.status === 200 && reply.data) {
      h.sheet.close();
      const n = (reply.data.removed || []).length, left = (reply.data.skipped || []).length;
      wtDone(`Deleted ${wtCount(n, "worktree")}, freed ${wtBytes(reply.data.freed_bytes)}${left ? `. ${wtCount(left, "worktree")} skipped` : ""}.`, null);
      return;
    }
    // More would go than the preview showed: look again instead of deleting.
    if (reply.status === 409 && reply.data && reply.data.error && reply.data.error.code === "changed") {
      return wtPrunePreview(h, includeDirty, reply.data.error.message);
    }
    go.disabled = false;
    go.textContent = `Delete ${wtCount(cands.length, "worktree")}`;
    wtSheetFail(h, reply.status, reply.data);
  };
  wtById("wt-cancel").focus();
}

registerPage("worktrees", renderWorktrees);
