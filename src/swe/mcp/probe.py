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
import subprocess
import time
from pathlib import Path

PROTOCOL_VERSION = "2025-06-18"  # mirrors gateway_server.DEFAULT_PROTOCOL

# Separate deadlines: a cold server (uvx resolving, npx fetching) gets the
# gateway's patience for initialize; a live one answers tools/list fast.
INIT_TIMEOUT_S = 30.0
TOOLS_TIMEOUT_S = 10.0
# stderr keeps at most this much text for the error message; a chatty
# server's log tail is more useful than its start.
_STDERR_TAIL = 400


class ProbeError(RuntimeError):
    """Why the handshake failed, in words a user can act on."""


def _exited_error(proc: subprocess.Popen) -> "ProbeError":
    tail = _stderr_tail(proc)
    detail = f"exited (status {proc.returncode})"
    return ProbeError(f"{detail}: {tail}" if tail else detail)


def _wait_reply(proc: subprocess.Popen, buf: bytearray, mid: int,
                deadline: float) -> dict:
    """Read stdout until the response for ``mid`` arrives.

    Notifications and log lines that happen to parse as JSON are skipped;
    a line that does not parse is dropped outright (some servers print
    plain-text banners). Returns the whole message so the caller sees the
    ``error`` member too.
    """
    fd = proc.stdout.fileno()
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
        if proc.poll() is not None:
            # EOF or not, nothing more will arrive.
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                raise _exited_error(proc)
            buf.extend(chunk)
            continue
        if not select.select([fd], [], [], min(left, 0.25))[0]:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            chunk = b""
        if not chunk:
            if proc.poll() is not None:
                raise _exited_error(proc)
            continue
        buf.extend(chunk)


def _send(proc: subprocess.Popen, message: dict) -> None:
    try:
        proc.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
        proc.stdin.flush()
    except (OSError, BrokenPipeError) as exc:
        raise ProbeError(f"cannot write to the server: {exc}") from exc


def _stderr_tail(proc: subprocess.Popen) -> str:
    """Whatever the server logged, for folding into the error message."""
    try:
        if proc.stderr is None:
            return ""
        fd = proc.stderr.fileno()
        os.set_blocking(fd, False)
        try:
            data = os.read(fd, _STDERR_TAIL * 4)
        finally:
            os.set_blocking(fd, True)
        text = (data or b"").decode("utf-8", "replace").strip()
        return text[-_STDERR_TAIL:]
    except OSError:
        return ""


def _stop(proc: subprocess.Popen) -> None:
    try:
        proc.stdin.close()
    except OSError:
        pass
    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=1.5)
        except (subprocess.TimeoutExpired, OSError):
            try:
                proc.kill()
                proc.wait(timeout=1.5)
            except (subprocess.TimeoutExpired, OSError):
                pass


def _result_or_raise(reply: dict, what: str, proc: subprocess.Popen) -> dict:
    if isinstance(reply.get("result"), dict):
        return reply["result"]
    error = reply.get("error")
    detail = error.get("message") if isinstance(error, dict) else error
    tail = _stderr_tail(proc)
    if tail:
        detail = f"{detail or 'no result'} — stderr: {tail}"
    raise ProbeError(f"{what} failed: {detail or 'no result'}")


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
        )
    except OSError as exc:
        raise ProbeError(f"cannot start: {exc}") from exc

    buf = bytearray()
    try:
        init_s = INIT_TIMEOUT_S if timeout_s is None else timeout_s
        try:
            _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": PROTOCOL_VERSION,
                                    "capabilities": {},
                                    "clientInfo": {"name": "swe-mcp-probe",
                                                   "version": "1"}}})
            reply = _wait_reply(proc, buf, 1,
                                time.monotonic() + init_s)
        except TimeoutError:
            raise ProbeError(f"no answer to initialize in {init_s:.0f}s")
        result = _result_or_raise(reply, "initialize", proc)

        _send(proc, {"jsonrpc": "2.0",
                     "method": "notifications/initialized"})
        tools_s = TOOLS_TIMEOUT_S if timeout_s is None else timeout_s
        try:
            _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                         "params": {}})
            reply = _wait_reply(proc, buf, 2,
                                time.monotonic() + tools_s)
        except TimeoutError:
            raise ProbeError(f"no answer to tools/list in {tools_s:.0f}s")
        tools_result = _result_or_raise(reply, "tools/list", proc)
        tools = tools_result.get("tools")
        out = []
        for tool in tools if isinstance(tools, list) else []:
            if not isinstance(tool, dict):
                continue
            out.append({"name": str(tool.get("name") or ""),
                        "description": str(tool.get("description") or "")})
        server_info = result.get("serverInfo")
        return {
            "server": server_info if isinstance(server_info, dict) else None,
            "protocol_version": (result.get("protocolVersion")
                                 if isinstance(result.get("protocolVersion"), str)
                                 else None),
            "tools": out,
        }
    finally:
        _stop(proc)
