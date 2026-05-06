"""Client side of the Sponsio daemon IPC.

Used by:

* ``sponsio plugin append`` / ``sponsio plugin show`` / ``sponsio plugin load``
  to perform privileged writes/reads through the daemon when one is
  running, instead of touching the file directly.
* The host hook subprocess (``sponsio cursor guard`` etc.) to fetch
  the active rule library at evaluation time.

When no daemon is running the CLI falls back to direct file access —
that's the dev-mode path; production install lays down a launchd /
systemd service so the daemon is always up.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any

from sponsio.daemon.protocol import (
    FrameError,
    Request,
    Response,
    read_frame,
    write_frame,
)


class DaemonError(Exception):
    """Raised when the daemon refuses or cannot complete a request.

    ``code`` mirrors ``Response.code`` so callers can branch on
    ``"validation"`` / ``"not_found"`` / ``"internal"`` etc.
    """

    def __init__(self, message: str, *, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Socket path resolution
# ---------------------------------------------------------------------------


def default_socket_path() -> Path:
    """Where the daemon's Unix socket lives by default.

    Resolution order:

    1. ``$SPONSIO_DAEMON_SOCKET`` env var (override for tests / custom installs)
    2. System-installed socket at ``/var/run/sponsio.sock`` (when daemon
       is registered as a launchd / systemd service)
    3. Per-user dev-mode socket at ``~/.sponsio/sponsio.sock``

    The dev-mode default keeps ``sponsio daemon run`` zero-config — the
    user can ``sponsio daemon run`` in one terminal and ``sponsio plugin
    append`` from any other shell as the same UID without configuration.
    """
    env = os.environ.get("SPONSIO_DAEMON_SOCKET")
    if env:
        return Path(env).expanduser()
    system_path = Path("/var/run/sponsio.sock")
    if system_path.exists():
        return system_path
    return Path.home() / ".sponsio" / "sponsio.sock"


def daemon_is_running(
    socket_path: Path | None = None, *, timeout_s: float = 0.5
) -> bool:
    """Return ``True`` iff a daemon is reachable at ``socket_path``.

    Doesn't raise; returns ``False`` for any reason the connect fails.
    Useful for "should I use the daemon or fall back?" branches.
    """
    path = socket_path or default_socket_path()
    if not path.exists() or not path.is_socket():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout_s)
            s.connect(str(path))
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class DaemonClient:
    """One-shot RPC client.

    Usage::

        client = DaemonClient()
        resp = client.call("ping", {"echo": "hi"})
        # resp == {"pong": True, "echo": "hi", "pid": 1234, "version": "..."}

    The client opens a fresh connection per call — daemon connections
    are cheap (Unix socket, same host) and per-call open/close keeps
    the failure surface tiny: a half-broken connection can't poison
    the next request.
    """

    def __init__(
        self,
        socket_path: Path | None = None,
        *,
        timeout_s: float = 10.0,
    ) -> None:
        self.socket_path = socket_path or default_socket_path()
        self.timeout_s = timeout_s

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send one RPC, return the result.  Raise :class:`DaemonError`
        on non-success responses or transport failures."""
        request = Request(method=method, params=params or {})
        if not self.socket_path.exists():
            raise DaemonError(
                f"daemon socket not found at {self.socket_path} — "
                f"is the daemon running? Try `sponsio daemon run`.",
                code="no_daemon",
            )
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout_s)
                s.connect(str(self.socket_path))
                write_frame(s, request.to_json())
                body = read_frame(s)
        except FileNotFoundError as e:
            raise DaemonError(
                f"daemon socket not found at {self.socket_path}: {e}",
                code="no_daemon",
            ) from e
        except (OSError, FrameError) as e:
            raise DaemonError(f"transport error: {e}", code="transport") from e

        try:
            response = Response.from_json(body)
        except FrameError as e:
            raise DaemonError(f"protocol error: {e}", code="protocol") from e

        if not response.ok:
            raise DaemonError(
                response.error or "(no error message)",
                code=response.code or "error",
            )
        return response.result
