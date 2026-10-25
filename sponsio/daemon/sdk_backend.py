"""The SDK's ``backend="daemon"``: rules and history live in the daemon.

``Sponsio(agent_id="support-bot", backend="daemon")`` keeps no rules of
its own. Each check is sent to the daemon (``sdk.check``), which decides
it against ``<plugin_root>/support-bot/sponsio.yaml`` with the session's
history, in the daemon's mode for the ``sdk`` host. The process running
the agent cannot change the rules, the mode or the history; it can only
send events.

Without a daemon every check is blocked. The SDK does not start one: the
daemon's lifecycle belongs to whoever runs the service.
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

from sponsio.daemon.client import DaemonClient, DaemonError, default_socket_path


class DaemonBackend:
    """One SDK session's link to the daemon."""

    def __init__(
        self,
        library: str,
        *,
        socket_path: str | Path | None = None,
        session: str | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        self.library = library
        self.client = DaemonClient(
            socket_path=Path(socket_path).expanduser()
            if socket_path
            else default_socket_path(),
            timeout_s=timeout_s,
        )
        self.session = session or uuid.uuid4().hex
        self._context: dict[str, str] = {}
        self._lock = threading.Lock()

    def _params(self, **extra: Any) -> dict[str, Any]:
        return {"library": self.library, "session": self.session, **extra}

    def _unreachable(self, e: DaemonError) -> str:
        return (
            f"Sponsio: the daemon did not decide this call ({e}), so it is "
            f"blocked. Start it with `sponsio daemon run --detach "
            f"--socket {self.client.socket_path}`."
        )

    def check(self, tool: str, args: dict | None) -> tuple[bool, str]:
        """``(allowed, reason)``. Any failure to get a verdict blocks."""
        with self._lock:
            context = dict(self._context)
        params = self._params(tool=tool, args=args or {})
        if context:
            params["context"] = context
        try:
            reply = self.client.call("sdk.check", params)
        except DaemonError as e:
            return False, self._unreachable(e)
        if not isinstance(reply, dict) or not isinstance(reply.get("allowed"), bool):
            return False, "Sponsio: malformed reply from the daemon; blocked."
        return reply["allowed"], str(reply.get("reason") or "")

    def observe(self, tool: str, output: Any) -> None:
        """Record a tool's output. Best effort: it never gates."""
        if not isinstance(output, (str, int, float, bool, list, dict, type(None))):
            output = str(output)
        try:
            self.client.call("sdk.observe", self._params(tool=tool, output=output))
        except DaemonError:
            pass

    def filter_tools(self, names: list[str]) -> list[str]:
        """The subset of ``names`` the session may call now; none when the
        daemon does not answer."""
        try:
            reply = self.client.call("sdk.filter_tools", self._params(names=names))
        except DaemonError:
            return []
        allowed = reply.get("allowed") if isinstance(reply, dict) else None
        if not isinstance(allowed, list):
            return []
        keep = set(allowed)
        return [n for n in names if n in keep]

    def add_context(self, facts: dict[str, str]) -> None:
        with self._lock:
            self._context.update({str(k): str(v) for k, v in facts.items()})

    def rotate(self) -> None:
        """Start a new session: an empty history in the daemon."""
        with self._lock:
            self.session = uuid.uuid4().hex
            self._context.clear()
