"""Sponsio MCP gateway shim: the one MCP server an agent's host is given.

Standard library only, run as ``python3 -I -S sponsio_mcp.py``. It
speaks MCP over stdio (newline-delimited JSON-RPC) to the host and
forwards ``tools/list`` and ``tools/call`` to the daemon's gateway
(:mod:`sponsio.daemon.gateway`), which holds the upstream servers and
their credentials. The shim holds neither, and decides nothing: talking
to the daemon's socket directly gets an agent no more than this does.

Usage::

    sponsio_mcp.py --socket PATH [--timeout SECONDS]
                   [--plugin-root DIR] [--state-dir DIR] [--sponsio CMD]

One shim process is one session: its history in the daemon is keyed by
an id the shim draws at start. When a call changes which tools the
session may use, the shim sends ``notifications/tools/list_changed`` and
the host lists again. With no daemon, the shim starts one as the hook
shim does; if none answers, ``tools/list`` fails and every call is an
error result, so nothing runs.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid

try:
    from . import sponsio_shim as transport
except ImportError:  # run as a script: the hook shim sits beside this file
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import sponsio_shim as transport  # type: ignore[no-redef]

DEFAULT_TIMEOUT_S = 300.0
"""Budget for one forwarded request. A first ``tools/list`` starts the
upstream servers, and a call waits for the upstream tool."""
SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "sponsio-gateway"


def parse_args(argv: list[str]) -> tuple[transport.Options, float]:
    opts = transport.parse_args(argv)
    timeout = DEFAULT_TIMEOUT_S
    for i, arg in enumerate(argv[:-1]):
        if arg == "--timeout":
            try:
                timeout = float(argv[i + 1])
            except ValueError:
                pass
    return opts, timeout


class Bridge:
    """One host session: reads requests, writes replies and notifications."""

    def __init__(self, opts: transport.Options, timeout: float, out) -> None:
        self.opts = opts
        self.timeout = timeout
        self.out = out
        self.session = uuid.uuid4().hex
        self.digest: str | None = None
        self._write_lock = threading.Lock()
        self._digest_lock = threading.Lock()

    # ---- daemon ----

    def rpc(self, method: str, params: dict) -> dict:
        """One gateway RPC; raises ``RuntimeError`` with the reason the
        host should see when no daemon answers or it refuses."""
        params = {"session": self.session, **params}
        deadline = time.monotonic() + self.timeout
        try:
            return transport.call(self.opts.socket, method, params, deadline)
        except transport.Unreachable as first:
            cause = str(first)
        if self.opts.no_start:
            raise RuntimeError(transport.system_recovery_reason(cause))
        failure = transport.start_daemon(self.opts)
        if failure is None:
            until = min(deadline, time.monotonic() + transport.START_WAIT_S)
            if transport.wait_for_socket(self.opts.socket, until):
                try:
                    return transport.call(self.opts.socket, method, params, deadline)
                except transport.Unreachable as second:
                    cause = f"{cause}; after restart: {second}"
            else:
                cause = f"{cause}; the daemon did not come up"
        else:
            cause = f"{cause}; {failure}"
        raise RuntimeError(recovery_reason(cause, self.opts))

    def _saw(self, digest: object) -> bool:
        """Record the listed tools' digest; whether it changed since the
        host last listed."""
        if not isinstance(digest, str):
            return False
        with self._digest_lock:
            changed = self.digest is not None and digest != self.digest
            self.digest = digest
            return changed

    # ---- MCP ----

    def write(self, msg: dict) -> None:
        line = json.dumps(msg, separators=(",", ":")) + "\n"
        with self._write_lock:
            self.out.write(line)
            self.out.flush()

    def handle(self, msg: dict) -> dict | None:
        """The reply to ``msg``, or ``None`` for a notification."""
        method = msg.get("method")
        rid = msg.get("id")
        if rid is None:
            return None  # notifications: initialized, cancelled, ...
        if not isinstance(method, str):
            # A reply to a request we never sent, or no method at all.
            if "result" in msg or "error" in msg:
                return None
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32600, "message": "invalid request"},
            }
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        try:
            result = self._dispatch(method, params)
        except _RpcFailure as e:
            return {"jsonrpc": "2.0", "id": rid, "error": e.error}
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def _dispatch(self, method: str, params: dict) -> dict:
        if method == "initialize":
            asked = params.get("protocolVersion")
            version = asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[1]
            return {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": SERVER_NAME, "version": "1"},
                "instructions": (
                    "Tools from the servers Sponsio mediates. The list follows "
                    "the session's state: a tool appears once its contracts "
                    "allow it."
                ),
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            try:
                reply = self.rpc("gateway.list_tools", {})
            except RuntimeError as e:
                raise _RpcFailure(-32000, str(e)) from e
            self._saw(reply.get("digest"))
            tools = reply.get("tools")
            return {"tools": tools if isinstance(tools, list) else []}
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments")
            try:
                reply = self.rpc(
                    "gateway.call_tool", {"name": name, "arguments": arguments}
                )
            except RuntimeError as e:
                return _error_result(str(e))
            if self._saw(reply.get("digest")):
                self.write(
                    {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
                )
            result = reply.get("result")
            return (
                result
                if isinstance(result, dict)
                else _error_result("Sponsio gateway: malformed reply from the daemon.")
            )
        raise _RpcFailure(-32601, f"method not found: {method}")

    def serve(self, stdin) -> None:
        workers: list[threading.Thread] = []
        for line in stdin:
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self.write(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "parse error"},
                    }
                )
                continue
            if not isinstance(msg, dict):
                self.write(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32600, "message": "invalid request"},
                    }
                )
                continue
            if msg.get("method") == "tools/call":
                # A slow tool must not hold up the host's other requests.
                t = threading.Thread(target=self._reply, args=(msg,), daemon=True)
                t.start()
                workers.append(t)
                workers[:] = [w for w in workers if w.is_alive()]
            else:
                self._reply(msg)
        for t in workers:
            t.join(self.timeout)

    def _reply(self, msg: dict) -> None:
        try:
            reply = self.handle(msg)
        except Exception as e:  # noqa: BLE001 - a shim failure is an error reply
            reply = {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "error": {"code": -32603, "message": f"sponsio shim: {e}"},
            }
        if reply is not None:
            self.write(reply)


def recovery_reason(cause: str, opts: transport.Options) -> str:
    log = os.path.join(opts.state_dir or "~/.sponsio/state", "daemon.log")
    return (
        f"Sponsio: the enforcement daemon is not reachable ({cause}). No tool "
        f"behind the Sponsio gateway can run until it is. From your own "
        f"terminal: run `sponsio daemon run --detach --socket {opts.socket}`, "
        f"then `sponsio doctor`. Log: {log}"
    )


class _RpcFailure(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.error = {"code": code, "message": message}


def _error_result(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def main(argv: list[str] | None = None) -> int:
    opts, timeout = parse_args(sys.argv[1:] if argv is None else argv)
    bridge = Bridge(opts, timeout, sys.stdout)
    try:
        bridge.serve(sys.stdin)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
