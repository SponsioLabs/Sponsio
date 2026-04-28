"""Introspect a running MCP server's tool inventory.

The MCP protocol exposes the runtime tool list via JSON-RPC
``tools/list``. Static manifests (``.mcp.json`` / ``mcp.json``) only
describe how to *spawn* the server; the actual tool inventory
(including parameter schemas) is only available once the server is
running and has completed its ``initialize`` handshake.

This module spawns an MCP server, performs the standard handshake,
issues ``tools/list``, and returns the parsed tool inventory. Used by
``sponsio plugin scan --introspect`` to remove the manual
``--tools t1,t2,t3`` step — the operator (or the host agent driving
the skill) just points at the spawn command.

Transport: stdio with newline-delimited JSON (the most common form
for MCP stdio servers; see modelcontextprotocol/specification §
"Transports").  Servers using LSP-style Content-Length framing aren't
supported yet — those are rare for stdio in practice.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any


PROTOCOL_VERSION = "2024-11-05"


class IntrospectError(RuntimeError):
    """Raised when MCP introspection fails for any reason."""


@dataclass
class ToolInfo:
    """One tool entry returned by ``tools/list``.

    Mirrors the JSON-RPC reply shape but typed for downstream
    consumers (heuristic generator, LLM extractor).  ``inputSchema``
    is kept as a free-form dict because MCP servers are inconsistent
    about which JSON Schema features they use.
    """

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


def introspect_mcp_server(
    spawn_cmd: list[str],
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: float = 10.0,
) -> list[ToolInfo]:
    """Spawn an MCP server, do the handshake, and return its tools.

    Args:
        spawn_cmd: Argv list to spawn the server (e.g. ``["python3",
            "server.py"]``).  Caller is responsible for any shell
            tokenisation upstream — we do not pass through a shell.
        env: Extra environment variables.  Merged into ``os.environ``
            (we don't strip the parent env because most MCP servers
            need PATH / HOME / etc. to function).
        cwd: Working directory for the spawned server.
        timeout: Per-request timeout in seconds. The whole handshake
            doesn't get to exceed ~3x this in practice.

    Returns:
        List of :class:`ToolInfo` in the order the server returned them.

    Raises:
        IntrospectError: spawn failed, server died, JSON-RPC error
            response, malformed JSON, or timeout.
    """
    if not spawn_cmd:
        raise IntrospectError("spawn_cmd is empty")

    full_env = {**os.environ, **(env or {})}

    try:
        proc = subprocess.Popen(
            spawn_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=full_env,
            cwd=cwd,
            text=True,
            bufsize=1,  # line-buffered
        )
    except FileNotFoundError as e:
        raise IntrospectError(f"spawn failed — {spawn_cmd[0]!r} not found") from e
    except PermissionError as e:
        raise IntrospectError(f"spawn failed — {e}") from e

    try:
        # 1. initialize
        _send(proc, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "sponsio-plugin-scan",
                    "version": "0.1.0",
                },
            },
        })
        init_resp = _read_response(proc, expected_id=1, timeout=timeout)
        if "error" in init_resp:
            raise IntrospectError(
                f"initialize failed: {init_resp['error'].get('message', init_resp['error'])}"
            )

        # 2. notifications/initialized — fire-and-forget, no reply
        _send(proc, {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })

        # 3. tools/list
        _send(proc, {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
        })
        list_resp = _read_response(proc, expected_id=2, timeout=timeout)
        if "error" in list_resp:
            raise IntrospectError(
                f"tools/list failed: {list_resp['error'].get('message', list_resp['error'])}"
            )

        result = list_resp.get("result") or {}
        raw_tools = result.get("tools") or []
        if not isinstance(raw_tools, list):
            raise IntrospectError(f"tools/list returned non-list: {type(raw_tools).__name__}")

        tools: list[ToolInfo] = []
        for t in raw_tools:
            if not isinstance(t, dict):
                continue
            name = t.get("name")
            if not isinstance(name, str) or not name:
                continue
            tools.append(ToolInfo(
                name=name,
                description=t.get("description") or "",
                input_schema=t.get("inputSchema") or {},
            ))
        return tools
    finally:
        # Always clean up: terminate, then kill if it doesn't exit
        # within a short grace period.  Some MCP servers wait for a
        # ``shutdown`` notification — we don't bother because we're
        # done with them and they're stateless processes.
        try:
            proc.terminate()
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:  # pragma: no cover — best-effort cleanup
            pass


def _send(proc: subprocess.Popen, msg: dict) -> None:
    """Write one NDJSON message to the server's stdin."""
    if proc.stdin is None:
        raise IntrospectError("subprocess has no stdin")
    line = json.dumps(msg) + "\n"
    try:
        proc.stdin.write(line)
        proc.stdin.flush()
    except BrokenPipeError as e:
        # Most likely server died; surface its stderr if we can grab it.
        stderr_tail = _read_stderr_tail(proc)
        suffix = f"\n--- server stderr ---\n{stderr_tail}" if stderr_tail else ""
        raise IntrospectError(f"server closed stdin while sending {msg.get('method')!r}{suffix}") from e


def _read_response(
    proc: subprocess.Popen,
    expected_id: int,
    timeout: float,
) -> dict:
    """Read NDJSON messages from the server until one matches ``expected_id``.

    Skips any unrelated traffic (notifications, log messages, replies
    to other ids — though we don't currently issue concurrent
    requests).  Per-line read uses a thread with a join-timeout so we
    don't hang forever on a stuck server.
    """
    if proc.stdout is None:
        raise IntrospectError("subprocess has no stdout")

    # ``readline`` doesn't accept a timeout natively; do it on a
    # worker thread with a join-timeout.
    while True:
        line_holder: list[str] = []

        def _do_read() -> None:
            try:
                line_holder.append(proc.stdout.readline())  # type: ignore[union-attr]
            except Exception as e:  # pragma: no cover
                line_holder.append(f"__error__:{e}")

        t = threading.Thread(target=_do_read, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            stderr_tail = _read_stderr_tail(proc)
            suffix = f"\n--- server stderr ---\n{stderr_tail}" if stderr_tail else ""
            raise IntrospectError(
                f"timed out after {timeout}s waiting for response id={expected_id}{suffix}"
            )

        line = (line_holder[0] if line_holder else "").strip()
        if not line:
            # EOF — server exited.
            stderr_tail = _read_stderr_tail(proc)
            suffix = f"\n--- server stderr ---\n{stderr_tail}" if stderr_tail else ""
            raise IntrospectError(
                f"server exited before responding to id={expected_id}{suffix}"
            )

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            # Some servers leak progress / log lines onto stdout; skip.
            continue

        if not isinstance(msg, dict):
            continue
        if msg.get("id") == expected_id:
            return msg
        # Unrelated message — keep reading.


def _read_stderr_tail(proc: subprocess.Popen, max_chars: int = 800) -> str:
    """Best-effort grab of any stderr the server emitted.

    Doesn't block — if stderr has nothing buffered we return empty.
    Useful surfacing when the server crashes during handshake.
    """
    if proc.stderr is None:
        return ""
    try:
        # Don't actually wait — read whatever's already in the pipe.
        # subprocess.Popen.stderr is non-buffered text mode here.
        proc.stderr.flush()
        # On Python 3.9+ stdin/stdout/stderr are TextIOWrapper; .peek
        # isn't available, but we can do a non-blocking read via os.
        import fcntl  # noqa: PLC0415 — POSIX-only, demo limitation
        flags = fcntl.fcntl(proc.stderr.fileno(), fcntl.F_GETFL)
        fcntl.fcntl(proc.stderr.fileno(), fcntl.F_SETFL, flags | os.O_NONBLOCK)
        try:
            data = proc.stderr.read() or ""
        except (BlockingIOError, IOError):
            data = ""
        return data[-max_chars:] if data else ""
    except Exception:  # pragma: no cover — Windows / weird envs
        return ""
