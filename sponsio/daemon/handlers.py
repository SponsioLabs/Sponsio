"""Daemon RPC handlers for Sponsio's privileged operations.

Each handler is a thin wrapper that:

* Pulls params from the request dict
* Calls into a pure-logic module (e.g. :mod:`sponsio.plugin.append_ops`)
* Translates domain-specific exceptions into :class:`RpcError` with
  the right code so the client gets a structured failure

The daemon's ``serve_forever`` calls :func:`register_default_handlers`
on the server before accepting connections, so every new connection
sees the full method table.

Method names use a dotted convention (``plugin.append``,
``hooks.load``) so groupings stay obvious in logs and ``ping`` /
unknown-method handling stays in :mod:`sponsio.daemon.server`.

Which socket serves a method is part of its contract:

* agent socket: ``ping``, ``guard.check``, ``guard.observe``,
  ``gateway.list_tools``, ``gateway.call_tool``, ``sdk.check``,
  ``sdk.observe``, ``sdk.filter_tools``, ``proposal.submit``
* admin socket: ``ping``, ``plugin.append``, ``mode.set``,
  ``rules.status``, ``rules.reload``, ``rules.verify``, ``proposal.list``,
  ``proposal.show``, ``proposal.approve``, ``proposal.reject``,
  ``audit.verify``, ``cloud.set_key``, ``cloud.key_status``
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sponsio.daemon.proposals import ProposalError, ProposalStore
from sponsio.daemon.server import DaemonServer, RpcError, current_peer

if TYPE_CHECKING:
    from sponsio.daemon.guard_service import GuardService


def register_default_handlers(
    server: DaemonServer,
    *,
    guard_service: "GuardService | None" = None,
    state_dir: Path | None = None,
) -> None:
    """Install the daemon's handlers on both sockets.

    ``guard_service`` adds the hook-evaluation methods (``guard.check``,
    ``guard.observe``) and ``mode.set``, and its state dir holds the
    audit log and the proposals; without one the daemon serves rule
    writes only, with proposals when ``state_dir`` is given.
    """
    server.register("plugin.append", _handle_plugin_append, admin=True)
    apply_append = _apply_append
    if guard_service is not None:
        register_guard_handlers(server, guard_service)
        register_gateway_handlers(server, guard_service)
        register_sdk_handlers(server, guard_service)
        state_dir = guard_service.state_dir

        def apply_append(params: dict[str, Any], dry_run: bool) -> dict[str, Any]:
            # A proposal comes from the agent side: it changes the
            # daemon's own rule store, whatever ``root`` it names. At
            # Tier 2 the daemon writes as its own user, and an approved
            # proposal must not write anywhere else.
            return _apply_append(
                {**params, "root": str(guard_service.store.root)}, dry_run
            )

    if state_dir is not None:
        if server.audit_dir is None:
            server.audit_dir = Path(state_dir)
        register_proposal_handlers(
            server, ProposalStore(Path(state_dir), {"plugin.append": apply_append})
        )
        register_audit_handlers(server, Path(state_dir))


def register_audit_handlers(server: DaemonServer, state_dir: Path) -> None:
    """``audit.verify`` on the admin socket: the chain check, with the
    last record this daemon wrote as the expected head."""
    from sponsio.daemon import audit

    def verify(_params: dict[str, Any]) -> dict[str, Any]:
        expected = audit.head(state_dir)
        report = audit.verify(state_dir, expected_head=expected)
        return {
            "ok": report.ok,
            "records": report.records,
            "chained": report.chained,
            "unchained": report.unchained,
            "head": list(report.head) if report.head else None,
            "daemon_head": list(expected) if expected else None,
            "problems": report.problems,
            "path": str(audit.audit_path(state_dir)),
        }

    server.register("audit.verify", verify, admin=True)


def register_proposal_handlers(server: DaemonServer, store: ProposalStore) -> None:
    """``proposal.submit`` on the agent socket; review on the admin socket."""

    def _wrap(fn):
        def handler(params: dict[str, Any]) -> Any:
            try:
                return fn(params)
            except ProposalError as e:
                raise RpcError(str(e), code=e.code) from e

        return handler

    def submit(params: dict[str, Any]) -> dict[str, Any]:
        kind = params.get("kind")
        inner = params.get("params")
        if not isinstance(kind, str) or not isinstance(inner, dict):
            raise RpcError(
                "`kind` (string) and `params` (object) are required", code="validation"
            )
        return store.submit(
            kind, inner, current_peer(), dry_run=bool(params.get("dry_run"))
        )

    def _id(params: dict[str, Any]) -> str:
        pid = params.get("id")
        if not isinstance(pid, str) or not pid:
            raise RpcError("`id` is required", code="validation")
        return pid

    server.register("proposal.submit", _wrap(submit))
    server.register(
        "proposal.list",
        _wrap(lambda p: {"proposals": store.list(p.get("status"))}),
        admin=True,
    )
    server.register("proposal.show", _wrap(lambda p: store.get(_id(p))), admin=True)
    server.register(
        "proposal.approve",
        _wrap(lambda p: store.decide(_id(p), True, current_peer())),
        admin=True,
    )
    server.register(
        "proposal.reject",
        _wrap(lambda p: store.decide(_id(p), False, current_peer())),
        admin=True,
    )


def register_guard_handlers(server: DaemonServer, service: "GuardService") -> None:
    """``guard.check`` (pre-event) and ``guard.observe`` (post-event).

    Params: ``host`` (str or null), ``event`` (the host's hook event
    name, for hosts that carry it outside the payload), and one of
    ``payload`` (the parsed hook JSON object) or ``raw`` (the hook's
    stdin as text). ``mode`` (``enforce`` / ``observe``, optional) is
    the mode the installer wrote on the hook command; it overrides the
    daemon's policy for this call until P0.6 moves the per-host mode
    into the daemon's own config. Result: ``allowed``, ``reason``,
    ``plugin_id``, ``library_path``, plus the rendered ``stdout`` and
    ``exit_code`` the host expects, so the client writes bytes and exits.
    """

    def _args(params: dict[str, Any]) -> dict[str, Any]:
        host = params.get("host")
        if host is not None and not isinstance(host, str):
            raise RpcError("`host` must be a string or null", code="validation")
        event = params.get("event")
        if event is not None and not isinstance(event, str):
            raise RpcError("`event` must be a string or null", code="validation")
        payload = params.get("payload")
        raw = params.get("raw")
        if payload is not None and not isinstance(payload, dict):
            raise RpcError("`payload` must be an object", code="validation")
        if raw is not None and not isinstance(raw, str):
            raise RpcError("`raw` must be a string", code="validation")
        if payload is None and raw is None:
            raise RpcError("one of `payload` or `raw` is required", code="validation")
        mode = params.get("mode")
        if mode is not None and mode not in ("enforce", "observe"):
            raise RpcError(
                '`mode` must be "enforce", "observe" or null', code="validation"
            )
        return {
            "host": host,
            "hook_event": event,
            "payload": payload,
            "raw": raw,
            "mode": mode,
        }

    server.register("guard.check", lambda params: service.check(**_args(params)))
    server.register("guard.observe", lambda params: service.observe(**_args(params)))

    def mode_set(params: dict[str, Any]) -> dict[str, Any]:
        from sponsio.daemon.gateway import HOST as GATEWAY
        from sponsio.daemon.guard_service import KNOWN_HOSTS

        host, mode = params.get("host"), params.get("mode")
        hosts = sorted(KNOWN_HOSTS | {GATEWAY, SDK_HOST})
        if host not in hosts:
            raise RpcError(f"`host` must be one of {hosts}", code="validation")
        if mode not in ("enforce", "observe"):
            raise RpcError('`mode` must be "enforce" or "observe"', code="validation")
        service.set_host_mode(host, mode)
        return {"host": host, "mode": mode}

    server.register("mode.set", mode_set, admin=True)

    def rules_reload(params: dict[str, Any]) -> dict[str, Any]:
        bucket = params.get("bucket")
        if bucket is not None and not isinstance(bucket, str):
            raise RpcError("`bucket` must be a string or null", code="validation")
        peer = current_peer()
        return service.rules_reload(bucket, peer.as_dict() if peer else None)

    def cloud_set_key(params: dict[str, Any]) -> dict[str, Any]:
        key = params.get("key")
        if (
            not isinstance(key, str)
            or not key.strip()
            or any(c.isspace() for c in key.strip())
        ):
            raise RpcError("`key` must be a non-empty API key", code="validation")
        peer = current_peer()
        return service.set_cloud_key(key.strip(), peer.as_dict() if peer else None)

    def cloud_key_status(_params: dict[str, Any]) -> dict[str, Any]:
        import hashlib

        key, source = service.cloud_api_key()
        return {
            "source": source,
            "fingerprint": hashlib.sha256(key.encode()).hexdigest()[:12]
            if key
            else None,
        }

    server.register("cloud.set_key", cloud_set_key, admin=True)
    server.register("cloud.key_status", cloud_key_status, admin=True)
    server.register("rules.status", lambda _p: service.rules_status(), admin=True)
    server.register("rules.verify", lambda _p: service.verify_cloud(), admin=True)
    server.register("rules.reload", rules_reload, admin=True)


def register_gateway_handlers(server: DaemonServer, service: "GuardService") -> None:
    """``gateway.list_tools`` and ``gateway.call_tool`` on the agent socket
    (:mod:`sponsio.daemon.gateway`).

    Params: ``session`` (the shim's id for one host session), and for a
    call ``name`` (as listed) and ``arguments``. Results carry
    ``digest``, which changes when the listed tools do. A refused or
    failed call is a result with ``isError``, as MCP reports a tool
    error, not an RPC error.
    """

    def _wrap(fn):
        def handler(params: dict[str, Any]) -> Any:
            try:
                return fn(params)
            except ValueError as e:
                raise RpcError(str(e), code="validation") from e

        return handler

    server.register(
        "gateway.list_tools",
        _wrap(lambda p: service.gateway.list_tools(p.get("session"))),
    )
    server.register(
        "gateway.call_tool",
        _wrap(
            lambda p: service.gateway.call_tool(
                p.get("session"), p.get("name"), p.get("arguments")
            )
        ),
    )


SDK_HOST = "sdk"
"""The host name of ``Sponsio(..., backend="daemon")`` calls: the session
key prefix and the ``hosts:`` entry in ``daemon.yaml`` for their mode."""
_LIBRARY = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_SESSION = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def register_sdk_handlers(server: DaemonServer, service: "GuardService") -> None:
    """``sdk.check``, ``sdk.observe`` and ``sdk.filter_tools`` on the agent
    socket: the in-process SDK with ``backend="daemon"``.

    Params: ``library`` (a library under the daemon's plugin root, named
    after the SDK's ``agent_id``), ``session`` (the SDK's session id),
    and ``tool`` with ``args`` (and optional ``context``) for a check,
    ``output`` for an observation, ``names`` for a filter. The rules and
    the session's history are the daemon's; the SDK sends events.

    A library the daemon does not have denies every call: the SDK asked
    for the daemon's rules, and finding none is not "no rules".
    """
    from sponsio.guard_stdin import conversation_key

    def _target(p: dict[str, Any]) -> tuple[str, str]:
        library, session = p.get("library"), p.get("session")
        if not isinstance(library, str) or not _LIBRARY.match(library):
            raise RpcError("`library` must be a library name", code="validation")
        if not isinstance(session, str) or not _SESSION.match(session):
            raise RpcError(
                "`session` must be 1-128 of [A-Za-z0-9._:-]", code="validation"
            )
        return library, session

    def _missing(library: str) -> str | None:
        if service.store.resolve(library) is not None:
            return None
        return (
            f"Sponsio: the daemon has no library `{library}` "
            f"({service.store.root / library / 'sponsio.yaml'}), so this call is "
            f"denied."
        )

    def _event(session: str, tool: Any, hook_event: str) -> dict[str, Any]:
        if not isinstance(tool, str) or not tool:
            raise RpcError("`tool` is required", code="validation")
        return {
            "host": SDK_HOST,
            "session_id": session,
            "hook_event_name": hook_event,
            "tool_name": tool,
        }

    def check(p: dict[str, Any]) -> dict[str, Any]:
        library, session = _target(p)
        event = _event(session, p.get("tool"), "PreToolUse")
        args, context = p.get("args") or {}, p.get("context")
        if not isinstance(args, dict):
            raise RpcError("`args` must be an object", code="validation")
        if context is not None and not isinstance(context, dict):
            raise RpcError("`context` must be an object", code="validation")
        event["tool_input"] = args
        if context:
            event["context"] = context
        missing = _missing(library)
        if missing:
            return {"allowed": False, "reason": missing}
        outcome = service.decide(event, library=library)
        return {"allowed": outcome.allowed, "reason": outcome.reason}

    def observe(p: dict[str, Any]) -> dict[str, Any]:
        library, session = _target(p)
        event = _event(session, p.get("tool"), "PostToolUse")
        event["tool_response"] = p.get("output")
        if _missing(library) is None:
            service.decide(event, library=library)
        return {"recorded": True}

    def filter_tools(p: dict[str, Any]) -> dict[str, Any]:
        library, session = _target(p)
        names = p.get("names")
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise RpcError("`names` must be a list of strings", code="validation")
        if _missing(library):
            return {"allowed": []}
        conv = conversation_key({"host": SDK_HOST, "session_id": session})
        assert conv is not None
        return {"allowed": service.allowed_tools(SDK_HOST, conv, names, library)}

    server.register("sdk.check", check)
    server.register("sdk.observe", observe)
    server.register("sdk.filter_tools", filter_tools)


def _resolve_plugin_root(params: dict[str, Any]) -> Path:
    """Resolve the per-plugin library root from RPC params or env.

    Mirrors the CLI's resolution order: explicit ``root`` param wins,
    then ``$SPONSIO_PLUGIN_ROOT``, then ``~/.sponsio/plugins``.
    """
    root_arg = params.get("root")
    if root_arg:
        return Path(str(root_arg)).expanduser()
    env = os.environ.get("SPONSIO_PLUGIN_ROOT")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".sponsio" / "plugins"


def _handle_plugin_append(params: dict[str, Any]) -> dict[str, Any]:
    """``plugin.append`` RPC: validate + atomic merge into a host bucket.

    Params:

    * ``target`` (str, required): plugin id (e.g. ``"_host_cursor"``).
    * ``staging_yaml`` (str, required): full YAML content of the
      staging document.  Sent as content (not path) so the daemon
      doesn't have to be able to read the user's filesystem — just
      the host bucket it owns.
    * ``dry_run`` (bool, optional): default false.
    * ``root`` (str, optional): override the plugin root.  Tests
      and ``--no-daemon`` mode use this to avoid touching the real
      ``~/.sponsio/plugins/``.

    Result: an :class:`AppendResult`-shaped dict the client converts
    back into the dataclass.  Errors come back as :class:`RpcError`
    with codes ``"validation"`` / ``"not_found"`` / ``"internal"``.

    Admin socket only. From the agent side the same change is a
    ``proposal.submit`` of kind ``plugin.append``.
    """
    return _apply_append(params, bool(params.get("dry_run", False)))


def _apply_append(params: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    from sponsio.plugin.append_ops import AppendError, merge_staging_into_target

    target_name = params.get("target")
    staging_yaml = params.get("staging_yaml")

    if not isinstance(target_name, str) or not target_name:
        raise RpcError("`target` is required and must be a string", code="validation")
    if not isinstance(staging_yaml, str) or not staging_yaml:
        raise RpcError(
            "`staging_yaml` is required and must be a non-empty string",
            code="validation",
        )

    root = _resolve_plugin_root(params)
    target_path = root / target_name / "sponsio.yaml"

    try:
        result = merge_staging_into_target(target_path, staging_yaml, dry_run=dry_run)
    except AppendError as e:
        raise RpcError(str(e), code=e.code) from e

    return {
        "agent_id": result.agent_id,
        "appended_count": result.appended_count,
        "descs": list(result.descs),
        "target_path": result.target_path,
        "dry_run": result.dry_run,
    }
