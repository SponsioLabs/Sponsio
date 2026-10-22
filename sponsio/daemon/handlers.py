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
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sponsio.daemon.server import DaemonServer, RpcError

if TYPE_CHECKING:
    from sponsio.daemon.guard_service import GuardService


def register_default_handlers(
    server: DaemonServer, *, guard_service: "GuardService | None" = None
) -> None:
    """Install the daemon's privileged-operation handlers.

    ``guard_service`` adds the hook-evaluation methods (``guard.check``,
    ``guard.observe``); without one the daemon serves rule writes only.
    """
    server.register("plugin.append", _handle_plugin_append)
    if guard_service is not None:
        register_guard_handlers(server, guard_service)


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
    """
    from sponsio.plugin.append_ops import AppendError, merge_staging_into_target

    target_name = params.get("target")
    staging_yaml = params.get("staging_yaml")
    dry_run = bool(params.get("dry_run", False))

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
