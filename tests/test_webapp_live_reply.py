"""Live reply text in the web chat (#1444): pages/chat.js's port of
ReplyStreams.swift, under node with the Queued tray tests' DOM stub.

``message.delta`` frames grow a streaming bubble per reply id at the end
of the log; the next live ``message.appended`` of the same kind takes the
oldest open bubble's place when the reveal shows it. A seq gap stops
drawing a bubble, and an idle session or a reconnect drops what no final
claimed."""

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_webapp_chat_queue import CHAT_JS, PRELUDE

# The few DOM parts the bubbles need that the tray's stub leaves out.
# insertAdjacentHTML adds one node holding the markup it was given, so a
# test reads a box back as its nodes' markup in order.
DOM_EXTRA = r"""
function patchEl(e) {
  if (e._patched) return e;
  e._patched = true;
  Object.defineProperty(e, "parentNode", { get: () => e.parent });
  Object.defineProperty(e, "childNodes", { get: () => e.children });
  Object.defineProperty(e, "lastChild", { get: () => e.children[e.children.length - 1] || null });
  const query = e.querySelector;
  e.querySelector = (sel) => {
    const found = query(sel);
    // A part drawn through innerHTML reads as its owner's text.
    if (found && !found.textContent) found.textContent = e._html.replace(/<[^>]*>/g, "");
    return found && patchEl(found);
  };
  Object.defineProperty(e, "childElementCount", { get: () => e.children.length });
  Object.defineProperty(e, "previousElementSibling", { get: () => {
    const p = e.parent; if (!p) return null;
    const i = p.children.indexOf(e); return i > 0 ? p.children[i - 1] : null; } });
  e.insertBefore = (c, ref) => {
    if (c.parent) c.remove();
    c.parent = e;
    const i = e.children.indexOf(ref);
    e.children.splice(i < 0 ? e.children.length : i, 0, c);
    return c;
  };
  e.matches = () => false;
  e.insertAdjacentHTML = (_, html) => {
    e._html += html;
    const node = el("#markup");
    node._html = html;
    node.parent = e;
    e.children.push(node);
  };
  return e;
}
const baseCreate = document.createElement;
document.createElement = (t) => patchEl(baseCreate(t));
for (const id in ids) patchEl(ids[id]);
// The page's own listeners, so a test can hide and show the tab.
const docListeners = {};
document.addEventListener = (t, f) => { docListeners[t] = f; };
"""

HELPERS = r"""
const T = SessionChat._test;
T.chat.key = 'claude:s1'; T.chat.gen = 1; T.chat.rowKnown = true;
T.chat.row = { status: 'active', ownership: 'attached' };
// The first live entry shows at once, as long after the last one.
T.chat.lastRevealAt = -1e9;
const log = ids['chat-log'];
const frame = (ev) => T.handleFrame('claude:s1', 'data: ' + JSON.stringify(Object.assign({ session: 'claude:s1' }, ev)));
const delta = (id, text, seq, done = false) => frame({ type: 'message.delta', data: { id, kind: 'assistant', text, seq, done } });
const appended = (seq, data) => frame({ type: 'message.appended', seq, data });
// Shows every queued event now, without the reveal's pauses.
const drain = () => {
  while (T.chat.pending.length) { clearTimeout(T.chat.revealTimer); T.chat.revealTimer = null; T.revealNext(); }
  clearTimeout(T.chat.revealTimer); T.chat.revealTimer = null;
};
const shape = () => log.children.map((c) => c === T.chat.streamBox
  ? ['streams', c.children.map((b) => [b.dataset.streamId, b.streamText])]
  : [c.className, c.dataset.seq || '']);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
"""


class ChatScript(unittest.TestCase):
    """Runs a body against chat.js under the stub DOM; its return value
    comes back as JSON."""

    def run_js(self, body):
        source = CHAT_JS.read_text(encoding="utf-8")
        script = (PRELUDE + DOM_EXTRA + source + "\n" + HELPERS
                  + "(async () => {" + body + "})()"
                  + ".then((r) => process.stdout.write(JSON.stringify(r)))"
                  + ".catch((e) => { console.error(e); process.exit(1); });\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
        self.addCleanup(Path(f.name).unlink)
        done = subprocess.run(["node", f.name], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestLiveReply(ChatScript):
    def test_the_rules_match_the_swift_copy(self):
        out = self.run_js("""
            const d = (id, text, seq, extra = {}) => Object.assign({ id, kind: 'assistant', text, seq, done: false }, extra);
            const r = T.replyStreams();
            const steps = [r.apply(d('a', 'Hel', 1)), r.apply(d('b', 'two', 1)), r.apply(d('t', 'hmm', 1, { kind: 'thinking' })),
                           r.apply(d('a', 'lo', 2, { done: true }))];
            const first = r.shown().map((b) => [b.id, b.text, b.done]);
            // A gap stops drawing a, but a's final still takes it, not b.
            const gap = [r.apply(d('a', '!', 4)), r.shown().map((b) => b.id), r.apply(d('a', '?', 5))];
            const claims = [r.claim('tool_use'), r.claim('assistant'), r.claim(undefined), r.claim('assistant')];
            // Claimed but drawn until released; its late deltas are dropped.
            const late = [r.apply(d('b', ' more', 2)), r.shown().map((b) => b.text), r.release('b'), r.shown().length,
                          r.apply(d('b', 'again', 1))];
            // Heard mid-way: matched, never drawn.
            const s = T.replyStreams();
            const mid = [s.apply(d('m', 'tail', 3)), s.shown().length, s.claim('assistant')];
            // Idle takes only the unclaimed bubbles that were open then.
            const i = T.replyStreams();
            i.apply(d('x', 'claimed', 1)); i.apply(d('y', 'orphan', 1));
            const open = i.openIds(); i.claim('assistant'); i.apply(d('z', 'next', 1));
            const swept = [i.sweep(open), i.shown().map((b) => b.id), i.apply(d('y', 'late', 2)), i.clear(), i.shown().length,
                           i.apply(d('z', 'late', 2))];
            return { steps, first, gap, claims, late, mid, swept };""")
        self.assertEqual(out["steps"], [True, True, False, True])
        self.assertEqual(out["first"], [["a", "Hello", True], ["b", "two", False]])
        self.assertEqual(out["gap"], [True, ["b"], False])
        self.assertEqual(out["claims"], ["", "a", "b", ""])
        self.assertEqual(out["late"], [False, ["two"], True, 0, False])
        self.assertEqual(out["mid"], [False, 0, "m"])
        self.assertEqual(out["swept"], [True, ["x", "z"], False, True, 0, False])

    def test_deltas_repaint_throttled_and_the_final_takes_the_bubbles_place(self):
        out = self.run_js("""
            delta('x', 'Hel', 1); delta('x', 'lo **wor', 2);
            const throttled = [T.chat.streamPaint !== null, shape()];
            await wait(T.STREAM_MS.paint + 20);
            const painted = shape();
            const html = T.chat.streamBox.children[0].innerHTML;
            // A tool card and the final arrive together: the card is shown
            // first, and the bubble stays until the final's turn.
            appended(5, { kind: 'tool_use', tool: { name: 'Bash', call_id: 'c1' } });
            appended(6, { kind: 'assistant', text: 'Hello **world**' });
            const held = shape();
            delta('x', 'ld**', 3, true);
            T.paintStreams();
            const late = shape();
            drain();
            const final = log.children[log.children.length - 1];
            return { throttled, painted, html, held, late, after: shape(), arrive: final.classList.contains('arrive'),
                     queueEmpty: T.chat.pending.length === 0 };""")
        self.assertEqual(out["throttled"], [True, []])
        self.assertEqual(out["painted"], [["streams", [["x", "Hello **wor"]]]])
        self.assertIn('class="text"', out["html"])
        self.assertEqual(out["held"], [["tool arrive", "5"], ["streams", [["x", "Hello **wor"]]]])
        self.assertEqual(out["late"], out["held"], "a claimed id's late delta is dropped")
        self.assertEqual(out["after"], [["tool arrive", "5"], ["msg assistant", "6"]])
        self.assertFalse(out["arrive"], "the final swaps in without the arrival animation")

    def test_entries_land_above_open_bubbles(self):
        out = self.run_js("""
            delta('x', 'still writing', 1); T.paintStreams();
            T.appendMessage({ kind: 'system', text: 'note' }, true);
            return shape();""")
        self.assertEqual(out, [["msg system arrive", ""], ["streams", [["x", "still writing"]]]])

    def test_a_gap_drops_the_bubble_and_its_final_still_matches_it(self):
        out = self.run_js("""
            delta('a', 'one', 1); delta('b', 'two', 1); delta('a', 'three', 3); T.paintStreams();
            const gap = shape();
            appended(9, { kind: 'assistant', text: 'a final' }); drain();
            return { gap, after: shape() };""")
        self.assertEqual(out["gap"], [["streams", [["b", "two"]]]])
        self.assertEqual(out["after"], [["msg assistant", "9"], ["streams", [["b", "two"]]]])

    def test_idle_sweeps_an_orphan_and_clear_drops_everything(self):
        out = self.run_js("""
            T.STREAM_MS.idleGrace = 0;
            delta('x', 'cut off', 1); T.paintStreams();
            frame({ type: 'session.status', seq: 3, data: { status: 'idle' } }); drain();
            const kept = shape();
            await wait(10);
            const swept = shape();
            delta('y', 'mid', 1); T.paintStreams();
            T.clearStreams();
            // Held through a resync, a delta is dropped rather than drawn later.
            T.chat.held = [];
            delta('z', 'during resync', 1); T.paintStreams();
            const held = [shape(), T.chat.held.length];
            T.chat.held = null;
            return { kept, swept, cleared: held };""")
        self.assertEqual(out["kept"], [["streams", [["x", "cut off"]]]], "the grace runs first")
        self.assertEqual(out["swept"], [])
        self.assertEqual(out["cleared"], [[], 0])

    def test_the_sweep_takes_only_bubbles_open_when_the_status_arrived(self):
        out = self.run_js("""
            T.STREAM_MS.idleGrace = 0;
            delta('old', 'cut off', 1);
            // The reveal holds the idle status behind a tool card while the
            // next turn's reply starts streaming.
            T.chat.lastRevealAt = performance.now();
            appended(1, { kind: 'tool_use', tool: { name: 'Bash', call_id: 'c' } });
            frame({ type: 'session.status', seq: 2, data: { status: 'idle' } });
            delta('new', 'next turn', 1);
            await wait(10);
            T.paintStreams();
            const live = shape().filter((e) => e[0] === 'streams');
            drain();
            return live;""")
        self.assertEqual(out, [["streams", [["new", "next turn"]]]])

    def test_a_first_reply_replaces_the_empty_note(self):
        out = self.run_js("""
            const note = document.createElement('p');
            note.className = 'empty';
            log.appendChild(note);
            ids['chat-loading'] = note;
            delta('x', 'hi', 1); T.paintStreams();
            return shape();""")
        self.assertEqual(out, [["streams", [["x", "hi"]]]])

    def test_a_hidden_tab_keeps_every_event_and_draws_on_show(self):
        """#1554: while hidden, deltas and events still reach the chat's
        state but nothing is drawn and the cursor stays; showing the tab
        draws the bubbles and reveals every held event, in order."""
        out = self.run_js("""
            T.chat.opened = true; T.chat.stream = {};
            document.hidden = true; document.visibilityState = 'hidden';
            delta('x', 'Hel', 1); delta('x', 'lo', 2);
            appended(5, { kind: 'system', text: 'one' });
            appended(6, { kind: 'system', text: 'two' });
            await wait(T.STREAM_MS.paint + 20);
            const hidden = { shape: shape(), pending: T.chat.pending.length, seq: T.chat.seq,
                             bubble: T.chat.replies.shown().map((b) => b.text) };
            document.hidden = false; document.visibilityState = 'visible';
            docListeners.visibilitychange();
            drain();
            return { hidden, shown: shape(), seq: T.chat.seq };""")
        self.assertEqual(out["hidden"], {"shape": [], "pending": 2, "seq": 0, "bubble": ["Hello"]})
        self.assertEqual(out["shown"], [["msg system arrive", "5"], ["msg system arrive", "6"],
                                        ["streams", [["x", "Hello"]]]])
        self.assertEqual(out["seq"], 6)

    def test_a_stream_dropped_while_hidden_shows_what_it_received_first(self):
        """Reopening the stream clears what waits; everything received is
        shown before it, so a trimmed journal can't lose it, and the
        reopened stream resumes after it."""
        out = self.run_js("""
            const opened = [];
            globalThis.authHeaders = () => ({});
            globalThis.fetch = (url) => { opened.push(url); return new Promise(() => {}); };
            T.chat.opened = true; T.chat.stream = {};
            document.hidden = true; document.visibilityState = 'hidden';
            appended(5, { kind: 'system', text: 'one' });
            appended(6, { kind: 'system', text: 'two' });
            appended(7, { kind: 'system', text: 'three' });
            T.chat.stream = null;   // dropped while hidden
            document.hidden = false; document.visibilityState = 'visible';
            docListeners.visibilitychange();
            return { shown: shape().map((e) => e[1]), seq: T.chat.seq, opened };""")
        self.assertEqual(out["shown"], ["5", "6", "7"])
        self.assertEqual(out["seq"], 7)
        self.assertEqual(len(out["opened"]), 1)
        self.assertIn("last=7", out["opened"][0])

    def test_a_backlog_of_finals_draws_the_bubbles_once(self):
        out = self.run_js("""
            document.hidden = true; document.visibilityState = 'hidden';
            for (let i = 1; i <= 30; i++) delta('r' + i, 'reply ' + i, 1, true);
            for (let i = 1; i <= 30; i++) appended(100 + i, { kind: 'assistant', text: 'reply ' + i });
            document.hidden = false; document.visibilityState = 'visible';
            const shown = T.chat.replies.shown;
            let paints = 0;
            T.chat.replies.shown = () => { paints++; return shown(); };
            T.chat.opened = true; T.chat.stream = {};
            docListeners.visibilitychange();
            return { paints, entries: shape().length, streams: shape().filter((e) => e[0] === 'streams').length };""")
        self.assertEqual(out, {"paints": 1, "entries": 30, "streams": 0})

    def test_change_detection_looks_at_the_touched_bubble_only(self):
        """apply, release and sweep say whether what's drawn changed
        without comparing every bubble's text (#1555)."""
        out = self.run_js("""
            const d = (id, text, seq, extra = {}) => Object.assign({ id, kind: 'assistant', text, seq, done: false }, extra);
            const r = T.replyStreams();
            return [
              r.apply(d('a', '', 1)),             // drawn only once it has text
              r.apply(d('a', '', 2)),             // nothing new
              r.apply(d('a', 'x', 3)),            // first text
              r.apply(d('a', '', 4, { done: true })),
              r.apply(d('b', 'tail', 2)),         // heard mid-way: never drawn
              r.apply(d('b', 'more', 3)),
              r.release('b'),                     // b was never drawn
              r.apply(d('c', '', 1)),
              r.apply(d('c', 'y', 3)),            // a gap on an empty bubble
              r.sweep(['c']),                     // c was never drawn
              r.sweep(['a']),
            ];""")
        self.assertEqual(out, [False, False, True, False, False, False, False, False, False, False, True])


# Markdown a streamed reply passes through, cut anywhere: every block kind,
# blank lines inside fences and <json-render>, \r\n and \r breaks, and the
# fence and table edges #1562 ported (a fence closes only on a marker-only
# line; a table stops at a line that opens a block).
STREAM_DOCS = [
    "First paragraph with **bold\ntext** across lines.\n\nSecond one, `code`.\n\n\nThird.",
    "Intro\n\n```python\ndef f():\n\n    return 1\n```\n\nAfter **x** and https://example.com/a_b.\n",
    "~~~\nraw\n\n~~~\n\ndone",
    "| a | b |\n|---|:-:|\n| 1 | 2 |\n\nAfter the table.\n\n| x |\n|---|\n| 1 |\n> Warning: a | b\n\nend",
    "```text\n```swift build\n```\ndone\n\nnext\n\n```\nx\n````\ny\n\nz",
    "1. Run\n   ```\n   make test\n\n   more\n   ```\n2. Then\n   | a | b |\n   |---|---|\n   | 1 | 2 |\n\n- a\n- [x] b\n  continued\n\nok",
    '<json-render>{"a":\n\n1}</json-render> Saved.\n\nAfter\n\n<json-render>{}\n\n',
    "a\r\n\r\nb\r\n\r\n```\r\nc\r\n\r\n```\r\nd\r\rx\r\r\ny",
    "> q1\n> q2\n\n> q3\n\n# Title\n\n---\n\n![alt](a.png)\n\ntext ![i](b.png) here\n\n## Sub ##\n\n",
    "a\n   \nb\n\t\nc\n\n````\n\nx\n\n***\n\n_em_ and __strong__ and ~~del~~",
]


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestStreamingMarkdown(ChatScript):
    """A streaming bubble is parsed from its last settled block on, and is
    always drawn exactly as a full md() render of its text (#1555)."""

    def test_every_prefix_renders_as_md_does(self):
        out = self.run_js("""
            const docs = """ + json.dumps(STREAM_DOCS) + """;
            const bad = [];
            let settled = 0;
            for (const doc of docs) {
              for (const step of [1, 2, 5, 13]) {
                const st = {};
                let html = '';
                for (let n = step; ; n += step) {
                  const text = doc.slice(0, Math.min(n, doc.length));
                  const out = T.mdStream(st, text);
                  html = out.reset ? out.fresh : html + out.fresh;
                  const want = T.md(text);
                  if (st.html + out.tail !== want || html !== st.html) {
                    bad.push({ doc: docs.indexOf(doc), step, at: text.length, got: st.html + out.tail, want });
                    break;
                  }
                  if (n >= doc.length) break;
                }
                if (st.head) settled += 1;
              }
            }
            return { bad: bad.slice(0, 3), settled, runs: docs.length * 4 };""")
        self.assertEqual(out["bad"], [])
        # Every doc but the one-paragraph-per-line one settles something.
        self.assertGreaterEqual(out["settled"], out["runs"] - 4)

    def test_only_the_tail_is_parsed_again(self):
        out = self.run_js("""
            const para = 'A sentence with **bold**, `code` and a link https://example.com/x.\\n';
            const doc = Array.from({ length: 200 }, (_, i) => i % 5 === 4 ? '\\n' : para).join('');
            const st = {};
            let parsed = 0, whole = 0;
            for (let n = 40; n < doc.length + 40; n += 40) {
              const text = doc.slice(0, n);
              parsed += text.length - (st.head || '').length;
              whole += text.length;
              const out = T.mdStream(st, text);
              if (st.html + out.tail !== T.md(text)) return 'mismatch at ' + n;
            }
            // Text that stops growing at the end starts over, still right.
            const other = T.mdStream(st, 'new\\n\\nreply');
            return { ratio: parsed / whole, reset: other.reset, html: st.html + other.tail === T.md('new\\n\\nreply') };""")
        self.assertIsInstance(out, dict, out)
        self.assertLess(out["ratio"], 0.05)
        self.assertTrue(out["reset"])
        self.assertTrue(out["html"])

    def test_a_growing_bubble_keeps_its_settled_nodes(self):
        out = self.run_js("""
            delta('x', 'First para.\\n\\n', 1); T.paintStreams();
            const bubble = T.chat.streamBox.children[0];
            const box = bubble.querySelector('.text');
            const read = () => box.children.map((n) => n._html).join('');
            const first = box.children[0];
            delta('x', 'Second **par', 2); T.paintStreams();
            const a = read();
            delta('x', 'a**.\\n\\nThird', 3); T.paintStreams();
            const b = read();
            const nodes = box.children.slice();
            T.paintStreams();
            return {
              a, b, mdA: T.md('First para.\\n\\nSecond **par'), mdB: T.md('First para.\\n\\nSecond **para**.\\n\\nThird'),
              keptFirst: box.children[0] === first, sameBox: bubble.querySelector('.text') === box,
              settled: nodes.length === 3 && nodes[1]._html.includes('<strong>para</strong>'),
              idle: box.children.length === nodes.length && box.children.every((n, i) => n === nodes[i]),
            };""")
        self.assertEqual(out["a"], out["mdA"])
        self.assertEqual(out["b"], out["mdB"])
        self.assertTrue(out["keptFirst"], "a settled block's node is never drawn again")
        self.assertTrue(out["sameBox"], "the bubble's markup isn't replaced while it grows")
        self.assertTrue(out["settled"])
        self.assertTrue(out["idle"], "a paint with no new text touches nothing")

    def test_the_log_is_no_live_region_and_a_final_is_announced(self):
        # The shell's markup is a template in paintShell, which the page
        # does not export, so its two tags are read from the source: each
        # whole opening tag, whatever its attribute order or line breaks.
        source = CHAT_JS.read_text(encoding="utf-8")

        def tag(element_id):
            tags = re.findall(r'<[a-z]+\b[^>]*\bid="%s"[^>]*>' % element_id, source)
            self.assertEqual(len(tags), 1, element_id)
            return tags[0]

        self.assertNotIn("aria-live", tag("chat-log"))
        announce = tag("chat-announce")
        self.assertIn('role="status"', announce)
        self.assertIn('aria-live="polite"', announce)
        out = self.run_js("""
            const say = ids['chat-announce'] = el('div');
            const settle = () => new Promise((r) => setImmediate(r));
            delta('x', 'still writing', 1); T.paintStreams(); await settle();
            const streaming = say.textContent;
            T.appendMessage({ kind: 'assistant', text: 'old reply' }, false); await settle();
            const backfill = say.textContent;
            T.appendMessage({ kind: 'assistant', text: 'Done **now**' }, true); await settle();
            const final = say.textContent;
            T.appendMessage({ kind: 'error', text: 'x'.repeat(400) }, true); await settle();
            const error = say.textContent;
            // Drawn in one pass: said together, nothing overwritten.
            T.appendMessage({ kind: 'error', text: 'boom' }, true);
            T.appendMessage({ kind: 'assistant', text: 'after' }, true);
            await settle();
            const pair = say.textContent;
            // A backlog: a count, every error and prompt, then the newest.
            T.appendMessage({ kind: 'assistant', text: 'r1' }, true);
            T.appendMessage({ kind: 'error', text: 'e1' }, true);
            T.appendMessage({ kind: 'assistant', text: 'r2' }, true);
            T.appendMessage({ kind: 'assistant', text: 'r3' }, true);
            await settle();
            const backlog = say.textContent;
            // A failure written into a bubble already on screen is said too.
            T.failBubble(document.createElement('div'), 'Send failed: the hub answered 500');
            await settle();
            return { streaming, backfill, final, error, pair, backlog, failed: say.textContent };""")
        self.assertEqual(out["streaming"], "", "a streaming bubble is never read out")
        self.assertEqual(out["backfill"], "")
        self.assertEqual(out["final"], "Done now")
        self.assertTrue(out["error"].startswith("Error: "))
        self.assertTrue(out["error"].endswith("…"))
        self.assertLessEqual(len(out["error"]), len("Error: ") + 301)
        self.assertTrue(out["pair"].startswith("Error: "), out["pair"])
        self.assertTrue(out["pair"].endswith(" after"), out["pair"])
        self.assertTrue(out["backlog"].startswith("4 new entries. Error: "), out["backlog"])
        self.assertTrue(out["backlog"].endswith(" r3"), out["backlog"])
        self.assertNotIn("r1", out["backlog"])
        self.assertEqual(out["failed"], "Send failed: the hub answered 500")


if __name__ == "__main__":
    unittest.main()
