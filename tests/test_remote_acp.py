"""The ACP channel (``remote/acp.py``, ``inject.AcpInjector``) against a
fake agent.

The fake is a small Python script speaking newline-delimited JSON-RPC
on stdio, like the fake codex in test_codex_usage_daily.py. Its
behaviour comes from ``FAKE_ACP_MODE``; every message it receives is
appended to ``FAKE_ACP_LOG`` so a test can check what the client sent.
No network, no real HOME, no real agent.
"""

import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.remote import acp, inject, supervise
from swe.sessions.models import Session

from tests._tempdir import temp_dir

FAKE_AGENT = r'''
import json, os, sys

mode = os.environ.get("FAKE_ACP_MODE", "ok")
log = os.environ.get("FAKE_ACP_LOG")
count_file = os.environ.get("FAKE_ACP_COUNT")
starts = 0
if count_file:
    try:
        starts = int(open(count_file).read() or 0)
    except OSError:
        starts = 0
    starts += 1
    open(count_file, "w").write(str(starts))


def out(msg):
    sys.stdout.write((msg if isinstance(msg, str) else json.dumps(msg)) + "\n")
    sys.stdout.flush()


def update(sid, body):
    out({"jsonrpc": "2.0", "method": "session/update",
         "params": {"sessionId": sid, "update": body}})


def chunk(sid, what, text, mid=None):
    body = {"sessionUpdate": what, "content": {"type": "text", "text": text}}
    if mid:
        body["messageId"] = mid
    update(sid, body)


sid = "fresh-1"
prompt_id = None
for raw in sys.stdin:
    msg = json.loads(raw)
    if log:
        with open(log, "a") as fh:
            fh.write(json.dumps(msg) + "\n")
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        if mode == "init-error":
            out({"jsonrpc": "2.0", "id": mid,
                 "error": {"code": -32000, "message": "auth required"}})
            continue
        out({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": 1,
            "agentCapabilities": {"loadSession": mode != "no-load"},
            "agentInfo": {"name": "fake-agent", "version": "0"},
            "authMethods": []}})
    elif method == "session/load":
        sid = msg["params"]["sessionId"]
        # History replays as updates before the answer.
        chunk(sid, "user_message_chunk", "old question")
        chunk(sid, "agent_message_chunk", "OLD ANSWER")
        if mode == "load-error":
            out({"jsonrpc": "2.0", "id": mid,
                 "error": {"code": -32002, "message": "no such session"}})
        else:
            out({"jsonrpc": "2.0", "id": mid, "result": {}})
    elif method == "session/new":
        out({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": sid}})
    elif method == "session/prompt":
        prompt_id = mid
        if mode == "crash" or (mode == "crash-once" and starts == 1):
            sys.exit(3)
        if mode == "refuse":
            out({"jsonrpc": "2.0", "id": mid,
                 "error": {"code": -32000, "message": "quota exceeded"}})
            continue
        if mode == "malformed":
            out("not json at all")
            out("[1, 2, 3]")
            out({"jsonrpc": "2.0", "method": "session/update",
                 "params": [1, 2]})
            out({"jsonrpc": "2.0", "id": [1], "result": {}})
            update(sid, {"sessionUpdate": "agent_message_chunk",
                         "content": "not a block"})
            out({"jsonrpc": "2.0", "id": 900, "method": "fs/read_text_file",
                 "params": {"sessionId": sid, "path": "/etc/hosts"}})
            continue  # answer the prompt once the fs error comes back
        if mode == "permission":
            out({"jsonrpc": "2.0", "id": "perm-1",
                 "method": "session/request_permission", "params": {
                     "sessionId": sid,
                     "toolCall": {"toolCallId": "t1", "title": "Run ls",
                                  "rawInput": {"command": "ls -la"}},
                     "options": [
                         {"optionId": "yes", "name": "Allow", "kind": "allow_once"},
                         {"optionId": "yes-all", "name": "Always allow",
                          "kind": "allow_always"},
                         {"optionId": "no", "name": "Reject", "kind": "reject_once"},
                     ]}})
            continue
        if mode == "slow":
            chunk(sid, "agent_message_chunk", "working on it ")
            continue  # waits for session/cancel
        chunk(sid, "agent_thought_chunk", "pondering")
        chunk(sid, "agent_message_chunk", "hel", "m1")
        chunk(sid, "agent_message_chunk", "lo world ", "m1")
        update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t9",
                     "title": "Read README.md", "kind": "read",
                     "status": "pending"})
        update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t9",
                     "status": "completed"})
        chunk(sid, "agent_message_chunk", "done ", "m2")
        out({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
    elif method == "session/cancel":
        if prompt_id is not None:
            out({"jsonrpc": "2.0", "id": prompt_id,
                 "result": {"stopReason": "cancelled"}})
            prompt_id = None
    elif mid == "perm-1":
        out({"jsonrpc": "2.0", "id": prompt_id,
             "result": {"stopReason": "end_turn"}})
        prompt_id = None
    elif mid == 900:
        out({"jsonrpc": "2.0", "id": prompt_id,
             "result": {"stopReason": "end_turn"}})
        prompt_id = None
'''


class _Recorder:
    """A DeltaStream and an emit sink in one, waitable."""

    def __init__(self):
        self.cond = threading.Condition()
        self.pushes = []
        self.finished = []
        self.finish_alls = 0
        self.events = []

    def push(self, key, machine, msg_id, kind, text):
        with self.cond:
            self.pushes.append((msg_id, kind, text))
            self.cond.notify_all()

    def finish(self, key, msg_id):
        with self.cond:
            self.finished.append(msg_id)
            self.cond.notify_all()

    def finish_all(self, key):
        with self.cond:
            self.finish_alls += 1
            self.cond.notify_all()

    def emit(self, key, event):
        with self.cond:
            self.events.append(event)
            self.cond.notify_all()

    def wait(self, pred, timeout=10.0):
        with self.cond:
            ok = self.cond.wait_for(pred, timeout=timeout)
        assert ok, "timed out waiting for the fake agent"

    def of(self, etype):
        return [e for e in self.events if e.get("type") == etype]


def _session(path, sid="d1", tool="devin"):
    return Session(timestamp=0, agent="Devin", path=str(path),
                   session_id=sid, tool_name=tool)


class _AcpCase(unittest.TestCase):
    mode = "ok"

    def setUp(self):
        self.home = temp_dir(self)
        self.proj = self.home / "proj"
        self.proj.mkdir()
        script = self.home / "fake_acp.py"
        script.write_text(FAKE_AGENT)
        self.log = self.home / "agent.log"
        self.argv = [sys.executable, str(script)]
        self.enterContext(patch.dict(os.environ, {
            "FAKE_ACP_MODE": self.mode, "FAKE_ACP_LOG": str(self.log),
            "FAKE_ACP_COUNT": str(self.home / "starts")}))
        self.enterContext(patch.object(
            acp, "command_for", lambda name, reg=None: list(self.argv)))
        self.key = "devin:d1"
        self.session = _session(self.proj)
        self.rec = _Recorder()
        self.addCleanup(inject.AcpInjector.close, self.key)

    def _mark(self, **extra):
        supervise.mark(self.home, self.key, {
            "mode": "acp", "tool_name": "devin", "session_id": "d1",
            "cwd": str(self.proj), **extra})

    def _injector(self):
        return inject.injector_for(
            self.home, self.key, supervise.meta_for(self.home, self.key),
            emit=self.rec.emit, machine="m1", deltas=self.rec)

    def _sent(self):
        return [json.loads(line) for line in
                self.log.read_text().splitlines()] if self.log.exists() \
            else []

    def _starts(self):
        return int((self.home / "starts").read_text())


class TestPromptStreaming(_AcpCase):
    def test_registry_picks_acp(self):
        self._mark()
        self.assertIsInstance(self._injector(), inject.AcpInjector)

    def test_prompt_streams_text_thought_and_tool(self):
        self._mark()
        inj = self._injector()
        res = inj.send(self.session, {"text": "reply with ok"}, "done")
        self.assertTrue(res.ok, res.note)
        conn = inject.AcpInjector._conns[self.key]
        self.assertTrue(conn.idle.wait(10))
        self.rec.wait(lambda: self.rec.finish_alls >= 1)
        kinds = [(k, t) for _id, k, t in self.rec.pushes]
        self.assertIn(("thinking", "pondering"), kinds)
        self.assertIn(("tool_use", "Read README.md"), kinds)
        texts = "".join(t for _id, k, t in self.rec.pushes
                        if k == "assistant")
        self.assertEqual(texts, "hello world done ")
        # The replayed history of session/load never streams.
        self.assertNotIn("OLD ANSWER", texts)
        # m1 and m2 are two messages, split by the tool call.
        ids = {i for i, k, _t in self.rec.pushes if k == "assistant"}
        self.assertEqual(len(ids), 2)
        self.assertIn("acp:tool:t9", self.rec.finished)
        sent = self._sent()
        init = next(m for m in sent if m.get("method") == "initialize")
        self.assertEqual(init["params"]["protocolVersion"], 1)
        self.assertFalse(init["params"]["clientCapabilities"]["terminal"])
        load = next(m for m in sent if m.get("method") == "session/load")
        self.assertEqual(load["params"]["sessionId"], "d1")
        self.assertTrue(os.path.isabs(load["params"]["cwd"]))
        prompt = next(m for m in sent if m.get("method") == "session/prompt")
        self.assertEqual(prompt["params"]["prompt"],
                         [{"type": "text", "text": "reply with ok"}])

    def test_real_delta_stream_carries_each_kind(self):
        from swe.remote import stream

        events = []
        deltas = stream.DeltaStream(lambda key, ev: events.append(ev),
                                    interval=0.0)
        self._mark()
        inj = inject.injector_for(
            self.home, self.key, supervise.meta_for(self.home, self.key),
            emit=self.rec.emit, machine="m1", deltas=deltas)
        self.assertTrue(inj.send(self.session, {"text": "x"}, "done").ok)
        self.assertTrue(inject.AcpInjector._conns[self.key].idle.wait(10))
        kinds = {e["data"]["kind"] for e in events
                 if e["type"] == "message.delta"}
        self.assertEqual(kinds, {"assistant", "thinking", "tool_use"})
        self.assertTrue(any(e["data"]["id"] == "acp:tool:t9"
                            and e["data"].get("done") for e in events))

    def test_second_send_reuses_the_agent(self):
        self._mark()
        inj = self._injector()
        self.assertTrue(inj.send(self.session, {"text": "a"}, "done").ok)
        inject.AcpInjector._conns[self.key].idle.wait(10)
        self.assertTrue(self._injector().send(
            self.session, {"text": "b"}, "done").ok)
        self.assertEqual(self._starts(), 1)
        loads = [m for m in self._sent() if m.get("method") == "session/load"]
        self.assertEqual(len(loads), 1)

    def test_empty_and_detached_sends_refused(self):
        inj = inject.AcpInjector(self.home, self.key)
        self.assertFalse(inj.send(self.session, {"text": " "}, "done").ok)
        res = inj.send(self.session, {"text": "hi"}, "done")
        self.assertFalse(res.ok)
        self.assertIn("detached", res.note)

    def test_model_control_unsupported(self):
        self._mark()
        res = self._injector().control(self.session, "model", "x")
        self.assertFalse(res.ok)
        self.assertTrue(res.note.startswith("unsupported_control"))

    def test_detach_closes_the_agent(self):
        self._mark()
        inj = self._injector()
        self.assertTrue(inj.send(self.session, {"text": "a"}, "done").ok)
        conn = inject.AcpInjector._conns[self.key]
        res = inj.control(self.session, "detach", "")
        self.assertTrue(res.ok)
        self.assertNotIn(self.key, inject.AcpInjector._conns)
        self.assertIsNone(supervise.meta_for(self.home, self.key))
        self.assertIsNotNone(conn.proc.poll())


class TestNoLoad(_AcpCase):
    mode = "no-load"

    def test_new_session_recorded_and_said(self):
        self._mark()
        res = self._injector().send(self.session, {"text": "hi"}, "done")
        self.assertTrue(res.ok, res.note)
        self.assertIn("new session", res.note)
        meta = supervise.meta_for(self.home, self.key)
        self.assertEqual(meta["acp_session_id"], "fresh-1")
        self.assertFalse(any(m.get("method") == "session/load"
                             for m in self._sent()))


class TestPermission(_AcpCase):
    mode = "permission"

    def _ask(self):
        self._mark()
        inj = self._injector()
        res = inj.send(self.session, {"text": "list files"}, "done")
        self.assertTrue(res.ok, res.note)
        self.rec.wait(lambda: self.rec.of("approval.requested"))
        return inj, self.rec.of("approval.requested")[0]["data"]

    def test_request_becomes_approval_with_menu_rows(self):
        _inj, data = self._ask()
        self.assertTrue(data["request_id"].startswith("acp-"))
        self.assertIn("Run ls", data["prompt"])
        self.assertIn("ls -la", data["prompt"])
        self.assertEqual(data["options"], [
            {"n": "1", "label": "Allow"},
            {"n": "2", "label": "Always allow"},
            {"n": "3", "label": "Reject"},
        ])

    def test_semantic_decision_round_trip(self):
        inj, data = self._ask()
        res = inj.approval(self.session, data["request_id"], "always")
        self.assertTrue(res.ok, res.note)
        conn = inject.AcpInjector._conns[self.key]
        self.assertTrue(conn.idle.wait(10))
        reply = next(m for m in self._sent() if m.get("id") == "perm-1")
        self.assertEqual(reply["result"]["outcome"],
                         {"outcome": "selected", "optionId": "yes-all"})
        # A retried answer can't reach the agent twice.
        again = inj.approval(self.session, data["request_id"], "allow")
        self.assertFalse(again.ok)

    def test_menu_row_and_unknown_row(self):
        inj, data = self._ask()
        self.assertFalse(inj.approval(
            self.session, data["request_id"], "7").ok)
        self.assertFalse(inj.approval(self.session, "acp-nope", "1").ok)
        self.assertFalse(inj.approval(
            self.session, data["request_id"], "maybe").ok)
        res = inj.approval(self.session, data["request_id"], "3")
        self.assertTrue(res.ok, res.note)
        inject.AcpInjector._conns[self.key].idle.wait(10)
        reply = next(m for m in self._sent() if m.get("id") == "perm-1")
        self.assertEqual(reply["result"]["outcome"]["optionId"], "no")

    def test_interrupt_cancels_and_retires_the_request(self):
        inj, data = self._ask()
        res = inj.control(self.session, "interrupt", "")
        self.assertTrue(res.ok, res.note)
        self.rec.wait(lambda: self.rec.of("approval.resolved"))
        resolved = self.rec.of("approval.resolved")[0]["data"]
        self.assertEqual(resolved, {"request_id": data["request_id"],
                                    "source": "cancelled"})
        # Closing waits for the agent to exit, so it has logged all it read.
        inject.AcpInjector.close(self.key)
        sent = self._sent()
        self.assertTrue(any(m.get("method") == "session/cancel"
                            for m in sent))
        reply = next(m for m in sent if m.get("id") == "perm-1")
        self.assertEqual(reply["result"]["outcome"],
                         {"outcome": "cancelled"})


class TestCancel(_AcpCase):
    mode = "slow"

    def test_interrupt_ends_the_turn(self):
        self._mark()
        inj = self._injector()
        self.assertEqual(inj.send(self.session, {"text": "go"}, "done").note,
                         "turn started")
        conn = inject.AcpInjector._conns[self.key]
        self.assertFalse(conn.idle.is_set())
        # queue refuses mid-turn
        res = inj.send(self.session, {"text": "more", "mode": "queue"},
                       "done")
        self.assertFalse(res.ok)
        self.assertTrue(inj.control(self.session, "interrupt", "").ok)
        self.assertTrue(conn.idle.wait(10))
        self.assertEqual(conn.last_stop, "cancelled")
        self.rec.wait(lambda: self.rec.finish_alls >= 1)
        # Nothing running: a second interrupt says so.
        self.assertFalse(inj.control(self.session, "interrupt", "").ok)

    def test_steer_waits_for_the_turn_then_sends(self):
        self._mark()
        inj = self._injector()
        self.assertTrue(inj.send(self.session, {"text": "go"}, "done").ok)
        conn = inject.AcpInjector._conns[self.key]
        real_wait = conn.idle.wait
        waiting = threading.Event()

        def spy(timeout=None):
            waiting.set()
            return real_wait(timeout)

        conn.idle.wait = spy
        out = {}
        t = threading.Thread(target=lambda: out.setdefault(
            "r", inj.send(self.session, {"text": "next", "mode": "steer"},
                          "active")))
        t.start()
        self.assertTrue(waiting.wait(10))
        inj.control(self.session, "interrupt", "")
        t.join(15)
        self.assertTrue(out["r"].ok, out["r"].note)
        self.assertTrue(out["r"].degraded)
        inject.AcpInjector.close(self.key)
        prompts = [m for m in self._sent()
                   if m.get("method") == "session/prompt"]
        self.assertEqual(len(prompts), 2)


class TestMalformed(_AcpCase):
    mode = "malformed"

    def test_garbage_is_skipped_and_fs_request_refused(self):
        self._mark()
        inj = self._injector()
        self.assertTrue(inj.send(self.session, {"text": "x"}, "done").ok)
        conn = inject.AcpInjector._conns[self.key]
        self.assertTrue(conn.idle.wait(10))
        self.assertTrue(conn.alive())
        self.assertEqual(conn.last_stop, "end_turn")
        err = next(m for m in self._sent() if m.get("id") == 900)
        self.assertEqual(err["error"]["code"], -32601)
        self.assertEqual(self.rec.pushes, [])


class TestRefusedPrompt(_AcpCase):
    mode = "refuse"

    def setUp(self):
        super().setUp()
        # Generous: the refusal must land inside send's early window.
        self.enterContext(patch.object(inject, "_ACP_EARLY_S", 10.0))

    def test_early_error_reported(self):
        self._mark()
        res = self._injector().send(self.session, {"text": "x"}, "done")
        self.assertFalse(res.ok)
        self.assertIn("quota exceeded", res.note)


class TestCrash(_AcpCase):
    mode = "crash-once"

    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(inject, "_ACP_EARLY_S", 10.0))

    def test_crash_mid_turn_then_restart_loads_the_session(self):
        self._mark()
        inj = self._injector()
        res = inj.send(self.session, {"text": "x"}, "done")
        self.assertFalse(res.ok)
        self.assertIn("exited", res.note)
        conn = inject.AcpInjector._conns[self.key]
        self.assertTrue(conn.idle.wait(10))
        self.rec.wait(lambda: self.rec.finish_alls >= 1)
        # Next send restarts the agent and loads the same session.
        res = self._injector().send(self.session, {"text": "y"}, "done")
        self.assertTrue(res.ok, res.note)
        self.assertIn("restarted", res.note)
        self.assertEqual(self._starts(), 2)
        loads = [m["params"]["sessionId"] for m in self._sent()
                 if m.get("method") == "session/load"]
        self.assertEqual(loads, ["d1", "d1"])


class TestCrashWithApproval(_AcpCase):
    mode = "permission"

    def test_agent_death_retires_open_requests(self):
        self._mark()
        inj = self._injector()
        inj.send(self.session, {"text": "x"}, "done")
        self.rec.wait(lambda: self.rec.of("approval.requested"))
        conn = inject.AcpInjector._conns[self.key]
        conn.proc.kill()
        self.rec.wait(lambda: self.rec.of("approval.resolved"))
        self.assertEqual(self.rec.of("approval.resolved")[0]["data"]["source"],
                         "channel_closed")
        res = inj.approval(
            self.session,
            self.rec.of("approval.requested")[0]["data"]["request_id"],
            "allow")
        self.assertFalse(res.ok)


class TestAttach(_AcpCase):
    def _attach(self, mode="auto", **kw):
        kw.setdefault("last_append_at", None)
        kw.setdefault("status", "done")
        with patch.object(supervise, "_local_writer_pids", return_value=[]), \
             patch.object(acp, "auto_eligible", return_value=True):
            return supervise.attach(self.home, self.key, self.session,
                                    mode=mode, **kw)

    def test_auto_attaches_over_acp_and_keeps_the_agent(self):
        ok, note = self._attach()
        self.assertTrue(ok, note)
        self.assertIn("acp", note)
        meta = supervise.meta_for(self.home, self.key)
        self.assertEqual(meta["mode"], "acp")
        self.assertNotIn("acp_session_id", meta)
        conn = inject.AcpInjector._conns[self.key]
        self.assertTrue(conn.alive())
        # The first send reuses the agent attach started.
        self.assertTrue(self._injector().send(
            self.session, {"text": "hi"}, "done").ok)
        self.assertEqual(self._starts(), 1)

    def test_model_pick_keeps_auto_off_acp(self):
        with patch.object(acp, "auto_eligible", return_value=True):
            self.assertEqual(supervise.resolve_mode(
                self.session, "auto", model="x"), "tmux")
            self.assertEqual(supervise.resolve_mode(
                self.session, "auto"), "acp")

    def test_explicit_acp_refuses_a_model(self):
        ok, note = self._attach(mode="acp", model="gpt")
        self.assertFalse(ok)
        self.assertIn("/model", note)


class TestAttachNoLoad(_AcpCase):
    mode = "no-load"

    def test_new_session_recorded_and_noted(self):
        with patch.object(supervise, "_local_writer_pids", return_value=[]):
            ok, note = supervise.attach(
                self.home, self.key, self.session, mode="acp",
                last_append_at=None, status="done")
        self.assertTrue(ok, note)
        self.assertIn("new session", note)
        self.assertEqual(
            supervise.meta_for(self.home, self.key)["acp_session_id"],
            "fresh-1")


class TestAttachFallback(_AcpCase):
    mode = "init-error"

    def test_auto_falls_back_to_tmux(self):
        with patch.object(supervise, "_local_writer_pids", return_value=[]), \
             patch.object(acp, "auto_eligible", return_value=True), \
             patch.object(supervise, "_attach_tmux",
                          return_value=(True, "attached under tmux swe-x")) \
                as tmux:
            ok, note = supervise.attach(
                self.home, self.key, self.session, mode="auto",
                last_append_at=None, status="done")
        self.assertTrue(ok)
        self.assertIn("fell back to tmux", note)
        self.assertIn("auth required", note)
        self.assertEqual(tmux.call_args[0][4]["mode"], "tmux")
        self.assertNotIn(self.key, inject.AcpInjector._conns)

    def test_explicit_acp_reports_the_failure(self):
        with patch.object(supervise, "_local_writer_pids", return_value=[]):
            ok, note = supervise.attach(
                self.home, self.key, self.session, mode="acp",
                last_append_at=None, status="done")
        self.assertFalse(ok)
        self.assertIn("handshake failed", note)
        self.assertIsNone(supervise.meta_for(self.home, self.key))


class TestAttachLoadError(_AcpCase):
    mode = "load-error"

    def test_failed_load_falls_back(self):
        with patch.object(supervise, "_local_writer_pids", return_value=[]), \
             patch.object(acp, "auto_eligible", return_value=True), \
             patch.object(supervise, "_attach_tmux",
                          return_value=(True, "attached under tmux swe-x")):
            ok, note = supervise.attach(
                self.home, self.key, self.session, mode="auto",
                last_append_at=None, status="done")
        self.assertTrue(ok)
        self.assertIn("no such session", note)


class TestHubRoute(unittest.TestCase):
    def test_acp_mode_without_an_acp_cli_is_422(self):
        from tests.test_remote_hub import _Server, _session as hub_session

        srv = _Server(sessions=[hub_session(tool="devin", sid="d7")],
                      statuses=lambda ss: ["done"])
        self.addCleanup(srv.stop)
        cred = srv.pair()
        with patch.object(acp, "command_for", return_value=None):
            status, data = srv.request(
                "POST", "/v1/sessions/devin:d7/control",
                body={"op": "attach", "mode": "acp"},
                headers={"Authorization": f"Bearer {cred['credential']}"})
        self.assertEqual(status, 422)
        body = json.loads(data)["error"]
        self.assertEqual(body["code"], "bad_mode")
        self.assertIn("Agent Client Protocol", body["message"])


class TestDetection(unittest.TestCase):
    """``command_for``: harness.json first, then the --help probe."""

    def setUp(self):
        self.bin = temp_dir(self)
        self.enterContext(patch.dict(
            os.environ, {"PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin"}))
        acp._PROBES.clear()
        self.addCleanup(acp._PROBES.clear)

    def _tool(self, name, help_text):
        path = self.bin / name
        path.write_text(
            f"#!/bin/sh\necho x >> {self.bin}/{name}.runs\n"
            f"cat <<'EOF'\n{help_text}\nEOF\n")
        path.chmod(0o755)
        return path

    def _runs(self, name):
        p = self.bin / f"{name}.runs"
        return len(p.read_text().splitlines()) if p.exists() else 0

    def test_subcommand_found_and_cached(self):
        self._tool("devin", "Commands:\n  acp   Run as an ACP server\n")
        self.assertEqual(acp.command_for("devin", reg={}), ["devin", "acp"])
        self.assertEqual(acp.command_for("devin", reg={}), ["devin", "acp"])
        self.assertEqual(self._runs("devin"), 1)
        self.assertTrue(acp.auto_eligible("devin", reg={}))

    def test_cli_without_acp(self):
        self._tool("devin", "Commands:\n  acp-ish  nope\n  --acpx\n")
        self.assertIsNone(acp.command_for("devin", reg={}))
        self.assertIsNone(acp.command_for("devin", reg={}))

    def test_flag_candidates_in_order(self):
        self._tool("gemini", "Options:\n  --experimental-acp  start ACP\n")
        self.assertEqual(acp.command_for("gemini", reg={}),
                         ["gemini", "--experimental-acp"])

    def test_not_installed_and_native_channels(self):
        self.assertIsNone(acp.command_for("devin", reg={}))
        self._tool("codex", "Commands:\n  acp\n")
        self.assertIsNone(acp.command_for("codex", reg={}))

    def test_claude_adapter_is_opt_in(self):
        self.assertIsNone(acp.command_for("claude", reg={}))
        adapter = self._tool("claude-agent-acp", "")
        self.assertEqual(acp.command_for("claude", reg={}),
                         ["claude-agent-acp"])
        # An ACP server is never started with --help.
        self.assertEqual(self._runs("claude-agent-acp"), 0)
        self.assertFalse(acp.auto_eligible("claude", reg={}))
        self.assertTrue(adapter.exists())

    def test_registry_declaration_wins(self):
        self._tool("devin", "Commands:\n  acp\n")
        reg = {"devin": {"capabilities": {"acp": {"supported": False}}}}
        self.assertIsNone(acp.command_for("devin", reg=reg))
        self._tool("mytool", "")
        reg = {"mytool": {"capabilities": {"acp": {
            "argv": ["mytool", "serve-acp"]}}}}
        self.assertEqual(acp.command_for("mytool", reg=reg),
                         ["mytool", "serve-acp"])
        reg = {"mytool": {"capabilities": {"acp": {"argv": ["gone"]}}}}
        self.assertIsNone(acp.command_for("mytool", reg=reg))
        # A malformed declaration falls through to the fallback table.
        reg = {"devin": {"capabilities": {"acp": {"argv": [3]}}}}
        self.assertEqual(acp.command_for("devin", reg=reg), ["devin", "acp"])


if __name__ == "__main__":
    unittest.main()
