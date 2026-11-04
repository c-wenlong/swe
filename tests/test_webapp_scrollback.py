"""The web chat's scrollback cap (#1615): pages/chat.js keeps its newest
LOG_KEEP entries in the log, folds older ones out of the DOM behind a
"Show earlier" stub, and brings them back on a click or a jump to one
message. Runs under node with the live reply tests' DOM stub."""

import shutil
import unittest

from tests.test_webapp_live_reply import ChatScript

# What the stub needs past the live reply tests' DOM: the stub's own
# placement, an element found by id once it is in the log, and node
# counts the way a browser would see them.
SETUP = r"""
const getById = document.getElementById;
const findId = (root, id) => {
  for (const c of root.children) { if (c.id === id) return c; const f = findId(c, id); if (f) return f; }
  return null;
};
document.getElementById = (id) => getById(id) || findId(log, id);
const patch = (e) => {
  if (e._scroll) return e;
  e._scroll = true;
  // A part drawn through innerHTML (a run's items box) gets these too.
  const query = e.querySelector;
  e.querySelector = (sel) => { const f = query(sel); return f && patch(f); };
  e.prepend = (c) => { c.parent = e; e.children.unshift(c); };
  // Like the DOM, a node moved under another leaves its old parent.
  const append = e.appendChild;
  e.appendChild = (c) => { if (c.parent) c.remove(); return append(c); };
  e.after = (...cs) => {
    const p = e.parent; const i = p.children.indexOf(e);
    for (const c of cs) c.parent = p;
    p.children.splice(i + 1, 0, ...cs);
  };
  Object.defineProperty(e, "nextElementSibling", { get: () => {
    const p = e.parent; if (!p) return null; return p.children[p.children.indexOf(e) + 1] || null; } });
  // Each child of the log is 10px tall: its top on screen moves with
  // what is above it and with the log's scroll.
  e.getBoundingClientRect = () => {
    const i = e.parent === log ? log.children.indexOf(e) : 0;
    return { top: i * 10 - log.scrollTop };
  };
  // A tag ahead of the classes, which the base stub doesn't read.
  const qsa = e.querySelectorAll;
  e.querySelectorAll = (sel) => {
    const m = sel.match(/^(\w+)(\..*)$/);
    return m ? qsa(m[2]).filter((x) => x.tagName === m[1]) : qsa(sel);
  };
  e.scrollIntoView = () => { scrolledTo = e; };
  e.matches = (sel) => {
    // tag.class.class[data-x] or [data-x="v"]: the forms chat.js asks.
    const m = sel.match(/^(\w+)?((?:\.[\w-]+)*)(?:\[([\w-]+)(?:="([^"]*)")?\])?$/);
    if (!m) throw new Error("matches " + sel);
    if (m[1] && e.tagName !== m[1]) return false;
    if (!m[2].split(".").filter(Boolean).every((c) => e._cls.has(c))) return false;
    if (!m[3]) return true;
    const key = m[3].replace(/^data-/, "").replace(/-(\w)/g, (_, c) => c.toUpperCase());
    return key in e.dataset && (m[4] === undefined || e.dataset[key] === m[4]);
  };
  return e;
};
let scrolledTo = null;
const create = document.createElement;
document.createElement = (t) => patch(create(t));
patch(log);
// Every node under the log: what layout and style would walk.
const nodes = (e) => e.children.reduce((n, c) => n + 1 + nodes(c), 0);
const msg = (seq, kind, text) => ({ type: 'message.appended', seq, data: { kind, text } });
// A transcript of n entries: user and assistant turns, with a tool call
// and its output every tenth seq.
const transcript = (n) => Array.from({ length: n }, (_, i) => {
  const seq = i + 1;
  if (seq % 10 === 0) return { type: 'message.appended', seq,
    data: { kind: 'tool_use', tool: { name: 'Bash', call_id: 'c' + seq, input: { command: 'ls' } } } };
  return msg(seq, seq % 2 ? 'user' : 'assistant', 'Message number ' + seq + ' with **some** text.');
});
const load = (n) => { for (const ev of transcript(n)) T.appendEvent(ev, false); T.scrollBottom(); };
const entries = () => log.children.filter((c) => c.id !== 'chat-earlier' && c !== T.chat.streamBox);
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestScrollbackCap(ChatScript):
    def run_js(self, body):
        return super().run_js(SETUP + body)

    def test_a_long_transcript_keeps_the_dom_bounded(self):
        out = self.run_js("""
            load(100);
            const small = nodes(log);
            for (const ev of transcript(2000).slice(100)) { T.appendEvent(ev, true); }
            drain(); T.scrollBottom();
            const big = { entries: entries().length, nodes: nodes(log), folded: T.chat.earlier.length,
              stub: T.chat.earlier.length && log.children[0].id };
            load(0);
            return { small, big, keep: T.LOG_KEEP };""")
        self.assertEqual(out["keep"], 200)
        self.assertEqual(out["big"]["entries"], 200)
        self.assertEqual(out["big"]["stub"], "chat-earlier")
        self.assertGreater(out["big"]["folded"], 1500)
        # 20x the transcript, about 2x the nodes of 100 entries.
        self.assertLess(out["big"]["nodes"], out["small"] * 3)

    def test_show_earlier_brings_the_history_back_in_order(self):
        out = self.run_js("""
            load(2000);
            const firstShown = entries()[0].dataset.seq;
            const label = log.children[0].querySelector('.chat-earlier-btn').textContent;
            const back = T.showEarlier();
            const after = entries().map((c) => Number(c.dataset.seq || 0));
            // Reading up while new messages land folds nothing.
            log.scrollHeight = 10000;
            T.appendEvent(msg(2001, 'assistant', 'new'), true); drain();
            const kept = entries().length;
            log.scrollHeight = 0;
            while (T.chat.earlier.length) T.showEarlier();
            const all = entries();
            const seqs = [];
            for (const c of all) {
              if (c.classList.contains('tool-run')) for (const it of c.querySelector('.tool-run-items').children) seqs.push(Number(it.dataset.seq));
              else seqs.push(Number(c.dataset.seq));
            }
            const stubLeft = !!document.getElementById('chat-earlier');
            // Back at the bottom, the history folds again.
            T.scrollBottom();
            return { firstShown, label, back: back.length, inOrder: after.every((s, i) => !i || !s || s > after[i - 1]),
              kept, total: seqs.length, ordered: seqs.every((s, i) => !i || s === seqs[i - 1] + 1),
              stubLeft, refolded: entries().length };""")
        self.assertTrue(out["label"].startswith("Show 200 earlier entries ("))
        self.assertEqual(out["back"], 200)
        self.assertTrue(out["inOrder"])
        self.assertEqual(out["kept"], 401)
        self.assertEqual(out["total"], 2001)
        self.assertTrue(out["ordered"], "every message is back, once, in seq order")
        self.assertFalse(out["stubLeft"])
        self.assertEqual(out["refolded"], 200)

    def test_a_jump_to_an_old_message_expands_first(self):
        out = self.run_js("""
            // Seqs 8 to 10 are three tools in a row: a run.
            for (const ev of transcript(2000)) {
              if (ev.seq === 8 || ev.seq === 9) ev.data = { kind: 'tool_use', tool: { name: 'Read', call_id: 'c' + ev.seq, input: {} } };
              T.appendEvent(ev, false);
            }
            T.scrollBottom();
            const missing = T.chat.earlier.length;
            const hit = T.revealSeq(15);
            const inLog = !!hit && entries().some((c) => c === hit || c.children.some((x) => x === hit
              || (x.children || []).includes(hit)));
            // A tool folded into a run is found inside it.
            const tool = T.revealSeq(10);
            const run = entries().find((c) => c.classList.contains('tool-run')
              && c.querySelector('.tool-run-items').children.includes(tool));
            return { missing, seq: hit && hit.dataset.seq, inLog, scrolled: scrolledTo === tool, opened: !!(run && run.open),
              tool: tool && tool.className, never: T.revealSeq(99999) };""")
        self.assertGreater(out["missing"], 0)
        self.assertEqual(out["seq"], "15")
        self.assertTrue(out["inLog"])
        self.assertTrue(out["scrolled"])
        self.assertTrue(out["opened"], "a jump to a tool opens its run")
        self.assertIn("tool", out["tool"])
        self.assertIsNone(out["never"])

    def test_the_oldest_drawn_entry_keeps_its_place_on_screen(self):
        out = self.run_js("""
            load(500);
            const tops = [];
            for (let k = 0; k < 2; k++) {
              const ref = entries()[0];
              const before = ref.getBoundingClientRect().top;
              T.showEarlier();
              tops.push([before, ref.getBoundingClientRect().top]);
            }
            // The second batch was the last: the stub went with it.
            return { tops, stub: !!document.getElementById('chat-earlier') };""")
        self.assertEqual([a for a, b in out["tops"]], [b for a, b in out["tops"]])
        self.assertFalse(out["stub"])

    def test_waiting_sends_and_open_approvals_never_fold_or_reorder(self):
        out = self.run_js("""
            for (const ev of transcript(50)) T.appendEvent(ev, false);
            T.appendMessage({ kind: 'approval', text: 'Run it?', request_id: 'r1', options: [] }, false, 51);
            const card = entries()[50];
            for (const ev of transcript(400)) { ev.seq += 51; T.appendEvent(ev, false); }
            T.scrollBottom();
            const stuck = { kept: log.children.includes(card), folded: T.chat.earlier.length,
              above: entries().indexOf(card) };
            // Answered, the fold goes on past it, in order.
            card.classList.add('resolved');
            T.scrollBottom();
            while (T.chat.earlier.length) T.showEarlier();
            const seqs = entries().map((c) => Number(c.dataset.seq || (c.querySelector('.tool-run-items').children[0].dataset.seq)));
            return { stuck, entries: seqs.length, ordered: seqs.every((s, i) => !i || s > seqs[i - 1]) };""")
        # Nothing past the card folds while it waits: the 50 above it do.
        self.assertEqual(out["stuck"], {"kept": True, "folded": 50, "above": 0})
        self.assertEqual(out["entries"], 451)
        self.assertTrue(out["ordered"])

    def test_a_failed_send_folds_like_any_entry(self):
        out = self.run_js("""
            for (const ev of transcript(20)) T.appendEvent(ev, false);
            const bubble = document.createElement('div'); bubble.className = 'msg user failed'; bubble.dataset.clientId = 'c9';
            log.appendChild(bubble);
            for (const ev of transcript(400)) { ev.seq += 20; T.appendEvent(ev, false); }
            T.scrollBottom();
            const folded = T.chat.earlier.includes(bubble);
            T.commandResult({ op: 'message', client_id: 'c9', cancelled: true });
            return { folded, entries: entries().length, gone: !T.chat.earlier.includes(bubble) };""")
        self.assertEqual(out, {"folded": True, "entries": 200, "gone": True})

    def test_a_removed_send_that_was_all_the_folded_history_takes_the_button(self):
        out = self.run_js("""
            const bubble = document.createElement('div'); bubble.className = 'msg user failed'; bubble.dataset.clientId = 'c9';
            log.appendChild(bubble);
            for (const ev of transcript(200)) T.appendEvent(ev, false);
            T.scrollBottom();
            const folded = T.chat.earlier.length;
            T.commandResult({ op: 'message', client_id: 'c9', cancelled: true });
            return { folded, stub: !!document.getElementById('chat-earlier') };""")
        self.assertEqual(out, {"folded": 1, "stub": False})

    def test_a_resync_keeps_a_folded_failed_send(self):
        out = self.run_js("""
            const bubble = document.createElement('div'); bubble.className = 'msg user failed'; bubble.dataset.clientId = 'c9';
            log.appendChild(bubble);
            for (const ev of transcript(300)) T.appendEvent(ev, false);
            T.scrollBottom();
            const folded = T.chat.earlier.includes(bubble);
            globalThis.api = async () => ({ status: 200, data: { messages: transcript(300) } });
            T.chat.held = [];
            await T.resync(300);
            T.chat.keep = 1e9;
            while (T.chat.earlier.length) T.showEarlier();
            return { folded, kept: log.children.includes(bubble) };""")
        self.assertEqual(out, {"folded": True, "kept": True})

    def test_an_approval_restored_into_folded_history_lands_in_order(self):
        out = self.run_js("""
            for (const ev of transcript(400)) { ev.seq *= 2; T.appendEvent(ev, false); }
            T.scrollBottom();
            T.appendMessage({ kind: 'approval', text: 'Run it?', request_id: 'r1', options: [] }, false, 101, 101);
            while (T.chat.earlier.length) T.showEarlier();
            const seqs = entries().map((c) => Number(c.dataset.seq || (c.querySelector('.tool-run-items').children[0].dataset.seq)));
            return { has: seqs.includes(101), ordered: seqs.every((s, i) => !i || s > seqs[i - 1]) };""")
        self.assertEqual(out, {"has": True, "ordered": True})

    def test_streaming_still_paints_below_a_capped_log(self):
        out = self.run_js("""
            load(600);
            delta('x', 'First para.\\n\\nSecond', 1); T.paintStreams();
            const box = T.chat.streamBox;
            const last = log.children[log.children.length - 1] === box;
            delta('x', ' more', 2); T.paintStreams(); T.scrollBottom();
            return { last, still: log.children.includes(box), entries: entries().length,
              text: box.children[0].streamText };""")
        self.assertTrue(out["last"])
        self.assertTrue(out["still"], "the streaming box never folds")
        self.assertEqual(out["entries"], 200)
        self.assertEqual(out["text"], "First para.\n\nSecond more")


if __name__ == "__main__":
    unittest.main()
