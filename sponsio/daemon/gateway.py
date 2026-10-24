"""The MCP gateway: the agent's only route to its MCP tools (Tier 3).

At Tier 1 and 2 an agent's MCP servers are the host's: the host spawns
them with the credentials in its own config, and Sponsio sees a call
only through the host's hook. Through the gateway, the daemon spawns the
upstream servers instead, with credentials from its own
``<state_dir>/gateway.yaml``, and the agent's MCP config lists one
server, the gateway shim (:mod:`sponsio.shim.sponsio_mcp`), which
forwards to the daemon. The agent holds no credential for an upstream
and has no other route to one.

The gateway answers two calls on the agent socket:

* ``gateway.list_tools``: the upstream tools a call to which the
  session's current state admits (``filter_tools`` per library), named
  ``<server>__<tool>``. A tool whose contracts would block it now is not
  listed.
* ``gateway.call_tool``: refuses a tool that is not listed, decides the
  call with its arguments (``guard.check``), forwards an admitted call,
  and records the output. A refused call never reaches the upstream.

Each reply carries a ``digest`` of the listed names, so the shim can
tell the host ``notifications/tools/list_changed`` when a call changed
the list. Contracts are written against ``mcp__<server>__<tool>``, the
name Claude Code gives an MCP tool, and live in the server's library
(``<plugin_root>/<server>/sponsio.yaml``), as on the hook path; the
``_gateway`` library's ``defaults`` cover every server.

The file::

    servers:
      github:
        command: [npx, -y, "@modelcontextprotocol/server-github"]
        env: {GITHUB_PERSONAL_ACCESS_TOKEN: "..."}
        cwd: null            # optional
        timeout_s: 60        # start-up and per-call budget

Only stdio servers with newline-delimited JSON are supported. A server
is started on first use and again after it exits; a change to the file
applies on the next call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shlex
import subprocess
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sponsio.guard_stdin import GATEWAY_SERVER, conversation_key

if TYPE_CHECKING:
    from sponsio.daemon.guard_service import GuardService

GATEWAY_FILE = "gateway.yaml"
HOST = "gateway"
"""The host name gateway events carry: the session key prefix, the
``hosts:`` entry in ``daemon.yaml`` for the gateway's mode, and the
``_gateway`` library."""
PROTOCOL_VERSION = "2025-06-18"
DEFAULT_TIMEOUT_S = 60.0
SEPARATOR = "__"

_NAME = re.compile(r"^[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*$")
"""A server name: no ``__`` and no leading or trailing ``_``, so
``mcp__<server>__<tool>`` always splits back to the server."""
_SESSION = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_TOOL_FIELDS = ("title", "description", "inputSchema", "outputSchema", "annotations")

_logger = logging.getLogger("sponsio.daemon.gateway")


class GatewayError(Exception):
    """An upstream server could not be started or did not answer."""


@dataclass(frozen=True)
class ServerSpec:
    name: str
    command: tuple[str, ...]
    env: tuple[tuple[str, str], ...] = ()
    cwd: str | None = None
    timeout_s: float = DEFAULT_TIMEOUT_S


def config_path(state_dir: Path) -> Path:
    return Path(state_dir) / GATEWAY_FILE


def parse_servers(raw: Any) -> dict[str, ServerSpec]:
    """Valid server entries of a parsed ``gateway.yaml``; an invalid one
    is left out with a warning, so one typo does not take down the rest."""
    servers = raw.get("servers") if isinstance(raw, dict) else None
    if not isinstance(servers, dict):
        return {}
    out: dict[str, ServerSpec] = {}
    for name, entry in servers.items():
        name = str(name)
        if not _NAME.match(name) or name.casefold() == GATEWAY_SERVER:
            # ``sponsio`` is the gateway's own name in the host's config.
            _logger.warning("gateway: server name %r is not valid; skipped", name)
            continue
        if not isinstance(entry, dict):
            _logger.warning("gateway: server %r is not a mapping; skipped", name)
            continue
        command = entry.get("command")
        if isinstance(command, str):
            command = shlex.split(command)
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(c, str) and c for c in command)
        ):
            _logger.warning("gateway: server %r has no valid command; skipped", name)
            continue
        env = entry.get("env") or {}
        if not isinstance(env, dict):
            _logger.warning("gateway: server %r env is not a mapping; skipped", name)
            continue
        timeout = entry.get("timeout_s", DEFAULT_TIMEOUT_S)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
            timeout = DEFAULT_TIMEOUT_S
        cwd = entry.get("cwd")
        out[name] = ServerSpec(
            name=name,
            command=tuple(command),
            env=tuple(sorted((str(k), str(v)) for k, v in env.items())),
            cwd=str(Path(cwd).expanduser()) if isinstance(cwd, str) and cwd else None,
            timeout_s=max(1.0, float(timeout)),
        )
    return out


def load_servers(state_dir: Path) -> dict[str, ServerSpec]:
    import yaml

    try:
        text = config_path(state_dir).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as e:
        _logger.warning("gateway: cannot read %s: %s", GATEWAY_FILE, e)
        return {}
    try:
        return parse_servers(yaml.safe_load(text))
    except yaml.YAMLError as e:
        _logger.warning("gateway: %s does not parse: %s", GATEWAY_FILE, e)
        return {}


# ---------------------------------------------------------------------------
# One upstream server
# ---------------------------------------------------------------------------


@dataclass
class _Pending:
    done: threading.Event = field(default_factory=threading.Event)
    reply: dict | None = None


class Upstream:
    """A stdio MCP server the daemon runs, with its credentials.

    Started on first use; a server that exited is started again on the
    next one. Requests from several threads share the process, matched
    to replies by id.
    """

    def __init__(self, spec: ServerSpec) -> None:
        self.spec = spec
        self._lock = threading.Lock()
        """Guards ``_pending`` and ``_next_id``."""
        self._start_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._pending: dict[int, _Pending] = {}
        self._next_id = 0
        self._tools: list[dict] | None = None
        self._stderr: deque[str] = deque(maxlen=20)

    # ---- process ----

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _ensure(self) -> None:
        with self._start_lock:
            if self._alive():
                return
            self._start()

    def _start(self) -> None:
        self._tools = None
        env = {**os.environ, **dict(self.spec.env)}
        try:
            proc = subprocess.Popen(
                list(self.spec.command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=self.spec.cwd,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as e:
            raise GatewayError(f"cannot start `{self.spec.name}`: {e}") from e
        self._proc = proc
        threading.Thread(target=self._read, args=(proc,), daemon=True).start()
        threading.Thread(target=self._drain, args=(proc,), daemon=True).start()
        try:
            self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "sponsio-gateway", "version": "1"},
                },
            )
            self._notify("notifications/initialized")
        except GatewayError:
            self._kill(proc)
            raise

    def _read(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict):
                self._on_message(msg)
        # The server exited: nothing pending will be answered.
        with self._lock:
            pending, self._pending = self._pending, {}
        for p in pending.values():
            p.done.set()

    def _drain(self, proc: subprocess.Popen) -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            text = line.decode("utf-8", "replace").rstrip()
            self._stderr.append(text)
            _logger.debug("gateway %s: %s", self.spec.name, text)

    def _on_message(self, msg: dict) -> None:
        method = msg.get("method")
        if method is None:
            with self._lock:
                p = self._pending.pop(msg.get("id"), None)  # type: ignore[arg-type]
            if p is not None:
                p.reply = msg
                p.done.set()
            return
        if method == "notifications/tools/list_changed":
            self._tools = None
            return
        if "id" in msg:
            # A request from the server (ping, roots, sampling). Only
            # ping is answered; the gateway offers the server nothing.
            if method == "ping":
                self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
            else:
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "error": {"code": -32601, "message": "not supported"},
                    }
                )

    def _send(self, msg: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise GatewayError(f"`{self.spec.name}` is not running")
        data = (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            with self._write_lock:
                proc.stdin.write(data)
                proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as e:
            raise GatewayError(f"`{self.spec.name}` closed its input") from e

    def _notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def _request(self, method: str, params: dict | None = None) -> dict:
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            pending = self._pending[rid] = _Pending()
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        try:
            self._send(msg)
        except GatewayError:
            with self._lock:
                self._pending.pop(rid, None)
            raise
        if not pending.done.wait(self.spec.timeout_s):
            with self._lock:
                self._pending.pop(rid, None)
            raise GatewayError(
                f"`{self.spec.name}` did not answer {method} "
                f"within {self.spec.timeout_s:g}s"
            )
        reply = pending.reply
        if reply is None:
            # Its stderr goes to the daemon log, not into the reply: the
            # agent can read replies, and a server may print a secret.
            _logger.warning(
                "gateway %s exited: %s", self.spec.name, "; ".join(self._stderr)
            )
            raise GatewayError(f"`{self.spec.name}` exited")
        if "error" in reply:
            err = reply["error"]
            message = err.get("message") if isinstance(err, dict) else err
            raise GatewayError(f"`{self.spec.name}` {method} failed: {message}")
        result = reply.get("result")
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        except OSError:
            pass

    def close(self) -> None:
        with self._start_lock:
            proc, self._proc = self._proc, None
        if proc is not None:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            self._kill(proc)

    # ---- MCP ----

    def tools(self) -> list[dict]:
        """The server's tools, cached until it says the list changed or
        it is restarted."""
        self._ensure()
        cached = self._tools
        if cached is not None:
            return cached
        tools: list[dict] = []
        cursor: str | None = None
        for _page in range(100):
            result = self._request("tools/list", {"cursor": cursor} if cursor else {})
            for t in result.get("tools") or []:
                if isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"]:
                    tools.append(t)
            cursor = result.get("nextCursor")
            if not isinstance(cursor, str) or not cursor:
                break
        self._tools = tools
        return tools

    def call(self, tool: str, arguments: dict) -> dict:
        self._ensure()
        return self._request("tools/call", {"name": tool, "arguments": arguments})


# ---------------------------------------------------------------------------
# The gateway
# ---------------------------------------------------------------------------


def _digest(names: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(names)).encode("utf-8")).hexdigest()[:16]


def _error_result(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


class Gateway:
    """The upstream servers of ``gateway.yaml``, served per session
    through ``service``'s rules and traces."""

    def __init__(self, service: "GuardService") -> None:
        self.service = service
        self.state_dir = Path(service.state_dir)
        self._lock = threading.Lock()
        self._sig: tuple | None = None
        self._upstreams: dict[str, Upstream] = {}

    def _signature(self) -> tuple | None:
        try:
            st = config_path(self.state_dir).stat()
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def upstreams(self) -> dict[str, Upstream]:
        """The configured servers, re-read when the file changes. A server
        whose entry changed or went away is stopped."""
        sig = self._signature()
        stale: list[Upstream] = []
        with self._lock:
            if sig != self._sig:
                specs = load_servers(self.state_dir) if sig else {}
                current: dict[str, Upstream] = {}
                for name, spec in specs.items():
                    up = self._upstreams.get(name)
                    if up is not None and up.spec == spec:
                        current[name] = up
                    else:
                        current[name] = Upstream(spec)
                stale = [
                    u for n, u in self._upstreams.items() if current.get(n) is not u
                ]
                self._upstreams, self._sig = current, sig
            ups = dict(self._upstreams)
        for u in stale:
            u.close()
        return ups

    def inventory(self) -> tuple[dict[str, tuple[str, dict]], dict[str, str]]:
        """``({contract name: (server, tool)}, {server: error})``. A server
        that cannot be started or listed contributes no tools."""
        tools: dict[str, tuple[str, dict]] = {}
        errors: dict[str, str] = {}
        for name, up in sorted(self.upstreams().items()):
            try:
                listed = up.tools()
            except GatewayError as e:
                errors[name] = str(e)
                _logger.warning("gateway: %s", e)
                continue
            for t in listed:
                tools[f"mcp__{name}{SEPARATOR}{t['name']}"] = (name, t)
        return tools, errors

    @staticmethod
    def session_key(session: Any) -> str:
        if not isinstance(session, str) or not _SESSION.match(session):
            raise ValueError("`session` must be 1-128 of [A-Za-z0-9._:-]")
        key = conversation_key({"host": HOST, "session_id": session})
        assert key is not None
        return key

    @staticmethod
    def listed_name(contract_name: str) -> str:
        return contract_name[len("mcp__") :]

    def _offered(self, conv: str, names: list[str]) -> list[str]:
        return self.service.allowed_tools(HOST, conv, names)

    def list_tools(self, session: str) -> dict[str, Any]:
        conv = self.session_key(session)
        tools, errors = self.inventory()
        offered = self._offered(conv, list(tools))
        listed = []
        for name in offered:
            _server, t = tools[name]
            entry = {k: t[k] for k in _TOOL_FIELDS if k in t}
            entry["name"] = self.listed_name(name)
            entry.setdefault("inputSchema", {"type": "object"})
            listed.append(entry)
        out: dict[str, Any] = {"tools": listed, "digest": _digest(offered)}
        if errors:
            out["errors"] = errors
        return out

    def call_tool(self, session: str, name: Any, arguments: Any) -> dict[str, Any]:
        conv = self.session_key(session)
        if not isinstance(name, str) or not name:
            raise ValueError("`name` is required")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ValueError("`arguments` must be an object")
        tools, _errors = self.inventory()
        contract_name = f"mcp__{name}"
        found = tools.get(contract_name)

        def done(result: dict) -> dict[str, Any]:
            return {
                "result": result,
                "digest": _digest(self._offered(conv, list(tools))),
            }

        if found is None:
            return done(_error_result(f"Sponsio gateway: no tool `{name}`."))
        server, tool = found
        event = {
            "host": HOST,
            "session_id": session,
            "hook_event_name": "PreToolUse",
            "tool_name": contract_name,
            "tool_input": arguments,
        }
        outcome = self.service.decide(event, offered_only=True)
        if not outcome.allowed:
            return done(_error_result(outcome.reason or "blocked by Sponsio"))
        try:
            result = self.upstreams()[server].call(tool["name"], arguments)
        except (GatewayError, KeyError) as e:
            return done(_error_result(f"Sponsio gateway: {e}"))
        self.service.decide(
            {
                "host": HOST,
                "session_id": session,
                "hook_event_name": "PostToolUse",
                "tool_name": contract_name,
                "tool_input": arguments,
                "tool_response": result.get("structuredContent")
                or result.get("content"),
            }
        )
        return done(result)

    def close(self) -> None:
        with self._lock:
            ups, self._upstreams, self._sig = self._upstreams, {}, None
        for u in ups.values():
            u.close()
