"""The legacy hook entry points, routed through the daemon.

``sponsio host guard <host>`` and ``sponsio plugin guard`` are what
hooks installed before the shim run, and what the marketplace plugin's
``hooks.json`` still names. They now do what the shim does: forward the
event to the daemon (starting one when none answers), write the
daemon's reply, and deny with exit 2 when no daemon can be reached.
``--direct`` keeps the in-process, file-backed evaluation for debugging.

The shapes and the recovery text are the shim's own, imported from
:mod:`sponsio.shim.sponsio_shim` so the two entry points cannot drift.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sponsio.daemon.client import (
    DaemonClient,
    DaemonError,
    default_socket_path,
    ensure_daemon,
)
from sponsio.shim.sponsio_shim import (
    DEFAULT_DEADLINE_S,
    START_WAIT_S,
    classify,
    deny_reply,
    recovery_reason,
)


def run_hook(
    host: str,
    hook_event: str | None,
    stdin_text: str | None = None,
    *,
    socket_path: Path | None = None,
    deadline_s: float = DEFAULT_DEADLINE_S,
) -> int:
    """Forward one hook invocation to the daemon; return the exit code.

    The mode comes from ``SPONSIO_GUARD_MODE`` when the legacy installer
    wrote ``env SPONSIO_GUARD_MODE=observe`` on the command; the plugin
    root a started daemon serves comes from ``SPONSIO_PLUGIN_ROOT`` when
    set, as the legacy entry always honoured it.
    """
    raw = stdin_text if stdin_text is not None else sys.stdin.read()
    sock = (socket_path or default_socket_path()).expanduser()
    method, event_name = classify(host, hook_event, raw)
    try:
        return _forward(host, hook_event, raw, sock, method, event_name, deadline_s)
    except Exception as e:  # noqa: BLE001 - any failure here is a deny
        return _deny(
            host, event_name, f"hook failure {e.__class__.__name__}: {e}", sock
        )


def _deny(host: str, event_name: str, cause: str, sock: Path) -> int:
    reason = recovery_reason(cause, str(sock), None, host)
    out, code = deny_reply(host, event_name, reason)
    sys.stdout.write(out + "\n")
    sys.stderr.write(reason + "\n")
    return code


def _forward(
    host: str,
    hook_event: str | None,
    raw: str,
    sock: Path,
    method: str,
    event_name: str,
    deadline_s: float,
) -> int:
    params: dict = {"host": host, "event": hook_event, "raw": raw}
    mode = os.environ.get("SPONSIO_GUARD_MODE", "").strip().lower()
    if mode in ("enforce", "observe"):
        params["mode"] = mode
    root_env = os.environ.get("SPONSIO_PLUGIN_ROOT")
    plugin_root = Path(root_env).expanduser() if root_env else None

    result = None
    cause = ""
    client = DaemonClient(socket_path=sock, timeout_s=deadline_s)
    try:
        result = client.call(method, params)
    except DaemonError as first:
        if first.code not in ("no_daemon", "transport"):
            cause = str(first)
        else:
            cause = str(first)
            if ensure_daemon(sock, wait_s=START_WAIT_S, plugin_root=plugin_root):
                try:
                    result = client.call(method, params)
                except DaemonError as second:
                    cause = f"{cause}; after restart: {second}"
            else:
                cause = f"{cause}; the daemon did not come up"

    if result is not None and not isinstance(result, dict):
        cause = "malformed daemon reply"
        result = None
    if result is None:
        return _deny(host, event_name, cause, sock)

    out = result.get("stdout")
    if isinstance(out, str) and out:
        sys.stdout.write(out)
        if not out.endswith("\n"):
            sys.stdout.write("\n")
    code = result.get("exit_code")
    return code if isinstance(code, int) else 2
