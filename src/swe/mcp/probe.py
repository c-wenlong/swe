"""One-shot MCP handshake: ``initialize`` + ``tools/list`` over stdio.

`swe mcp tools` spawns the configured command itself, asks for its tool
list, and kills it. The gateway's child machinery is threaded and
long-lived; this is the synchronous single-probe version. Same wire
format: newline-delimited JSON-RPC (the stdio transport in the spec).
"""

from __future__ import annotations

import json
import os
import select
import shutil
import signal
import subprocess
import time

from swe.console import sanitize

PROTOCOL_VERSION = "2025-06-18"  # mirrors gateway_server.DEFAULT_PROTOCOL

# Separate deadlines: a cold server (uvx resolving, npx fetching) gets the
# gateway's patience for initialize; a live one answers tools/list fast.
INIT_TIMEOUT_S = 30.0
TOOLS_TIMEOUT_S = 10.0
# stderr keeps at most this much text for the error message; a chatty
# server's log tail is more useful than its start.
_STDERR_TAIL = 400
# tools/list can paginate through nextCursor; the cap stops a server that
# answers every page with another cursor.
_MAX_TOOL_PAGES = 8


class ProbeError(RuntimeError):
    """Why the handshake failed, in words a user can act on."""


class _StderrTail:
    """Everything the child wrote to stderr, trimmed to the useful tail.

    Drained alongside stdout — a server logging hard enough to fill its
    stderr pipe would otherwise block mid-answer and read as a timeout.
    """

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes) -> None:
        self.buf.extend(data)
        if len(self.buf) > _STDERR_TAIL * 8:
            del self.buf[:-_STDERR_TAIL * 4]

    def text(self) -> str:
        text = bytes(self.buf).decode("utf-8", "replace").strip()
        return sanitize(text[-_STDERR_TAIL:])


def _exited_error(proc: subprocess.Popen, errbuf: _StderrTail) -> "ProbeError":
    tail = errbuf.text()
    detail = f"exited (status {proc.returncode})"
    return ProbeError(f"{detail}: {tail}" if tail else detail)


def _wait_reply(proc: subprocess.Popen, buf: bytearray,
                errbuf: _StderrTail, mid: int, deadline: float) -> dict:
    """Read stdout until the response for ``mid`` arrives.

    stderr drains in the same loop so a logging server cannot block on a
    full pipe. Notifications and log lines that happen to parse as JSON
    are skipped; a line that does not parse is dropped outright (some
    servers print plain-text banners). Returns the whole message so the
    caller sees the ``error`` member too.
    """
    out_fd = proc.stdout.fileno()
    err_fd = proc.stderr.fileno() if proc.stderr is not None else None
    while True:
        # Parse what is buffered first: the reply can already be here when
        # the process exits or its last chunk held more than one line.
        while b"\n" in buf:
            line, _, rest = bytes(buf).partition(b"\n")
            buf[:] = rest
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if message.get("id") != mid:
                continue
            return message
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError
        fds = [out_fd] + ([err_fd] if err_fd is not None else [])
        readable = select.select(fds, [], [], min(left, 0.25))[0]
        if err_fd in readable:
            try:
                errbuf.feed(os.read(err_fd, 65536))
            except OSError:
                pass
        if out_fd not in readable:
            if proc.poll() is not None and not readable:
                raise _exited_error(proc, errbuf)
            continue
        try:
            chunk = os.read(out_fd, 65536)
        except OSError:
            chunk = b""
        if not chunk:
            if proc.poll() is not None:
                raise _exited_error(proc, errbuf)
            continue
        buf.extend(chunk)


def _send(proc: subprocess.Popen, message: dict) -> None:
    try:
        proc.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
        proc.stdin.flush()
    except (OSError, BrokenPipeError) as exc:
        raise ProbeError(f"cannot write to the server: {exc}") from exc


def _stop(proc: subprocess.Popen) -> None:
    """Stop the child's whole process group.

    start_new_session made the spawned command a group leader, so the
    usual npx/uvx/sh -c wrappers' server children come down with it —
    signalling only the direct pid would leave those running.
    """
    try:
        proc.stdin.close()
    except OSError:
        pass

    def _signal_group(sig) -> None:
        try:
            os.killpg(proc.pid, sig)
        except (OSError, ProcessLookupError, AttributeError):
            try:
                proc.send_signal(sig)
            except OSError:
                pass

    if proc.poll() is None:
        _signal_group(signal.SIGTERM)
        try:
            proc.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            _signal_group(signal.SIGKILL)
            try:
                proc.wait(timeout=1.5)
            except (subprocess.TimeoutExpired, OSError):
                pass
        except OSError:
            pass


def _result_or_raise(reply: dict, what: str,
                     errbuf: _StderrTail) -> dict:
    if isinstance(reply.get("result"), dict):
        return reply["result"]
    error = reply.get("error")
    detail = error.get("message") if isinstance(error, dict) else error
    tail = errbuf.text()
    if tail:
        detail = f"{detail or 'no result'} — stderr: {tail}"
    raise ProbeError(f"{what} failed: {detail or 'no result'}")


def _list_tools(proc: subprocess.Popen, buf: bytearray,
                errbuf: _StderrTail, tools_s: float) -> list:
    """``tools/list``, following ``nextCursor`` until the last page."""
    tools: list = []
    cursor = None
    for page in range(_MAX_TOOL_PAGES):
        mid = 2 + page
        params = {"cursor": cursor} if cursor else {}
        _send(proc, {"jsonrpc": "2.0", "id": mid, "method": "tools/list",
                     "params": params})
        try:
            reply = _wait_reply(proc, buf, errbuf, mid,
                                time.monotonic() + tools_s)
        except TimeoutError:
            if page:
                raise ProbeError(
                    f"no answer to a tools/list page in {tools_s:.0f}s")
            raise ProbeError(f"no answer to tools/list in {tools_s:.0f}s")
        result = _result_or_raise(reply, "tools/list", errbuf)
        page_tools = result.get("tools")
        for tool in page_tools if isinstance(page_tools, list) else []:
            if not isinstance(tool, dict):
                continue
            tools.append({
                "name": sanitize(str(tool.get("name") or "")),
                "description": sanitize(str(tool.get("description") or "")),
            })
        cursor = result.get("nextCursor")
        if not isinstance(cursor, str) or not cursor:
            return tools
    return tools


def inspect_stdio(command: str, args: list, env: dict | None,
                  timeout_s: float | None = None) -> dict:
    """Handshake one stdio server and list its tools.

    ``command``/``args``/``env`` come from the resolved config (secrets
    already substituted by the caller — this function never sees a
    ``${NAME}`` it should expand). Returns ``{"server": serverInfo,
    "protocol_version": str, "tools": [{"name", "description"}]}``.
    Raises ``ProbeError`` for every failure mode a user can act on.
    """
    if not command:
        raise ProbeError("no command configured")
    resolved = (os.path.expanduser(command)
                if command.startswith(("/", "~")) else None)
    if resolved is not None:
        if not (os.path.isfile(resolved) and os.access(resolved, os.X_OK)):
            raise ProbeError(f"binary not found: {command}")
        command = resolved
    elif shutil.which(command) is None:
        raise ProbeError(f"'{command}' not in PATH")

    child_env = dict(os.environ)
    for key, value in (env or {}).items():
        child_env[str(key)] = str(value)

    try:
        proc = subprocess.Popen(
            [command, *[str(a) for a in (args or [])]],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=child_env,
            start_new_session=True,
        )
    except OSError as exc:
        raise ProbeError(f"cannot start: {exc}") from exc

    buf = bytearray()
    errbuf = _StderrTail()
    try:
        init_s = INIT_TIMEOUT_S if timeout_s is None else timeout_s
        try:
            _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": PROTOCOL_VERSION,
                                    "capabilities": {},
                                    "clientInfo": {"name": "swe-mcp-probe",
                                                   "version": "1"}}})
            reply = _wait_reply(proc, buf, errbuf, 1,
                                time.monotonic() + init_s)
        except TimeoutError:
            raise ProbeError(f"no answer to initialize in {init_s:.0f}s")
        result = _result_or_raise(reply, "initialize", errbuf)

        _send(proc, {"jsonrpc": "2.0",
                     "method": "notifications/initialized"})
        tools_s = TOOLS_TIMEOUT_S if timeout_s is None else timeout_s
        tools = _list_tools(proc, buf, errbuf, tools_s)
        server_info = result.get("serverInfo")
        if isinstance(server_info, dict):
            server_info = {k: sanitize(str(v)) for k, v in server_info.items()}
        else:
            server_info = None
        return {
            "server": server_info,
            "protocol_version": (result.get("protocolVersion")
                                 if isinstance(result.get("protocolVersion"), str)
                                 else None),
            "tools": tools,
        }
    finally:
        _stop(proc)
