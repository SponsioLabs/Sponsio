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
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

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


def default_admin_socket_path() -> Path:
    """The admin socket beside :func:`default_socket_path`."""
    from sponsio.daemon.server import admin_socket_for

    return admin_socket_for(default_socket_path())


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


def wait_for_daemon(
    socket_path: Path, *, timeout_s: float, poll_s: float = 0.05
) -> bool:
    """Block until a daemon answers at ``socket_path`` or ``timeout_s``
    passes. Whoever started it: a hook that raced another hook to start
    the daemon waits for the winner's socket all the same."""
    deadline = time.monotonic() + timeout_s
    while True:
        if daemon_is_running(socket_path, timeout_s=0.2):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_s)


def daemon_start_command(
    socket_path: Path,
    *,
    sponsio: Sequence[str] | None = None,
    plugin_root: Path | None = None,
    state_dir: Path | None = None,
) -> list[str]:
    """``sponsio daemon run --detach ...`` for ``socket_path``.

    ``sponsio`` is the command prefix that runs the CLI (default: this
    interpreter with ``-m sponsio.cli``). Roots given explicitly are
    fixed on the command line so the daemon that comes up does not
    depend on the starter's environment.
    """
    cmd = list(sponsio) if sponsio else [sys.executable, "-m", "sponsio.cli"]
    cmd += ["daemon", "run", "--detach", "--socket", str(socket_path)]
    if plugin_root is not None:
        cmd += ["--plugin-root", str(plugin_root)]
    if state_dir is not None:
        cmd += ["--state-dir", str(state_dir)]
    return cmd


_DAEMON_ENV_PASSTHROUGH = (
    "HOME",
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "USER",
    "LOGNAME",
    "SHELL",
    "TERM",
    "TZ",
    "OPENCLAW_HOME",
)


def daemon_environment() -> dict[str, str]:
    """The environment a daemon started on a hook's behalf gets.

    Only the locale and path basics pass through. No ``SPONSIO_*``
    variable does: the hook runs in the host's environment, which the
    agent under guard can edit (a ``settings.json`` ``env`` block), and
    a daemon that inherited it would read its mode, plugin root, and
    error policy from the agent.
    """
    return {k: v for k, v in os.environ.items() if k in _DAEMON_ENV_PASSTHROUGH}


def ensure_daemon(
    socket_path: Path,
    *,
    wait_s: float = 1.5,
    sponsio: Sequence[str] | None = None,
    plugin_root: Path | None = None,
    state_dir: Path | None = None,
) -> bool:
    """Start a daemon for ``socket_path`` if none answers, and wait up to
    ``wait_s`` for one to. Returns whether a daemon answers at the end.

    Several callers can run this at once: every one of them spawns a
    ``daemon run --detach``, the daemons race for the state dir lock,
    the losers exit, and every caller finds the one socket.
    """
    if daemon_is_running(socket_path, timeout_s=0.2):
        return True
    cmd = daemon_start_command(
        socket_path, sponsio=sponsio, plugin_root=plugin_root, state_dir=state_dir
    )
    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=daemon_environment(),
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        return False
    return wait_for_daemon(socket_path, timeout_s=wait_s)


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
