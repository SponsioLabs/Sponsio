"""Sponsio daemon Unix socket server.

Two sockets, two method tables. The agent socket serves what a hook or
an agent-side client may call (``ping``, ``guard.check``,
``guard.observe``, ``proposal.submit``); the admin socket serves
everything that changes the daemon's state (``plugin.append``,
``mode.set``, ``proposal.approve``, ...). A method called on the wrong
socket is ``not_found``.

Every connection is tagged with its peer's uid, gid and pid, read from
the kernel (:mod:`sponsio.daemon.peercred`); handlers read it with
:func:`current_peer`. An admin call is checked against the admin policy
(Tier 1: the daemon's own uid or root) and written to the audit log with
the peer, whether it was allowed or not.

Threading model: one thread per connection, one accept thread per
socket. Connections are short-lived (single request → response →
close), so the thread cost is bounded and we don't need an executor.

For graceful shutdown, ``DaemonServer.stop()`` closes the listening
sockets and joins outstanding worker threads with a small timeout.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import threading
import traceback
from pathlib import Path
from typing import Any, Callable, Protocol

try:  # POSIX only; the daemon is not supported on Windows
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]

from sponsio.daemon.peercred import Peer, get_peer
from sponsio.daemon.protocol import (
    FrameError,
    Request,
    Response,
    read_frame,
    write_frame,
)


_logger = logging.getLogger("sponsio.daemon")

AGENT = "agent"
ADMIN = "admin"

AUDIT_ADMIN_CALL = "admin_call"
"""Audit event: a call on the admin socket, allowed or refused."""

_conn = threading.local()


def current_peer() -> Peer | None:
    """The peer of the connection this handler is serving, or ``None``
    outside a request or where the kernel would not say."""
    return getattr(_conn, "peer", None)


def admin_socket_for(socket_path: Path | str) -> Path:
    """The admin socket that pairs with an agent socket:
    ``sponsio.sock`` → ``sponsio.admin.sock``, ``sock`` → ``sock.admin``."""
    p = Path(socket_path)
    return p.with_name(f"{p.stem}.admin{p.suffix}")


def same_user_or_root(peer: Peer | None) -> bool:
    """Tier 1 admin policy: the daemon's own uid, or root. (Tier 2 runs
    the daemon as its own user, so this then means root alone.)"""
    return peer is not None and peer.uid in (os.getuid(), 0)


# ---------------------------------------------------------------------------
# Handler protocol — caller registers one callable per method name
# ---------------------------------------------------------------------------


class RpcHandler(Protocol):
    """Callable that handles one RPC method.

    Receives the request params as a dict, returns the result value
    (anything JSON-serialisable).  Raises :class:`RpcError` for
    structured failures; any other exception is converted to an
    ``error: "internal"`` response with the exception message.
    """

    def __call__(self, params: dict[str, Any]) -> Any: ...


class RpcError(Exception):
    """Structured handler-side failure.  ``code`` lets the client
    distinguish e.g. ``"validation"`` vs ``"not_found"`` vs ``"internal"``.
    """

    def __init__(self, message: str, *, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


class DaemonAlreadyRunning(RuntimeError):
    """Another daemon holds the lock, or answers on the socket.

    Starting is not an error for the caller: the daemon it wanted is
    up. ``sponsio daemon run`` exits 0 on it, and the shims that raced
    to start one all find the winner's socket.
    """


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class DaemonServer:
    """Unix-socket JSON-RPC server.

    Args:
        socket_path: The agent socket. A socket file already there is probed
            first: if a daemon answers, :class:`DaemonAlreadyRunning` is
            raised and the file is left alone; only a socket nobody
            answers on (a crashed daemon's) is unlinked. A non-socket
            file is never removed.
        lock_path: A file to hold an exclusive ``flock`` on for the
            daemon's lifetime, so two daemons cannot serve the same
            state dir (each would keep its own trace, and the second
            to bind would have removed the first one's socket). The
            holder's pid is written into it. ``None`` skips the lock
            (in-process test servers on private sockets).
        socket_mode: chmod applied after bind. Default 0o600 — only
            the daemon's own UID can connect.  When the daemon runs as
            a separate system user serving multiple users, the install
            machinery overrides to 0o666 (the kernel's getsockopt+
            SO_PEERCRED is what authoritatively identifies the caller).
        accept_timeout_s: Poll interval on the accept loop. Bigger means
            ``stop()`` takes longer to take effect; smaller wastes CPU.
            Default 0.5s is the sweet spot for tests + production.
        admin_socket_path: The admin socket; default
            :func:`admin_socket_for` ``socket_path``. Always mode 0600.
        admin_policy: Decides from the peer whether an admin call may
            run. Default :func:`same_user_or_root`.
        audit_dir: Where admin calls are audited (the daemon's state
            dir). ``None`` until set; :func:`register_default_handlers`
            sets it from the guard service.
    """

    def __init__(
        self,
        socket_path: Path | str,
        *,
        socket_mode: int = 0o600,
        accept_timeout_s: float = 0.5,
        lock_path: Path | str | None = None,
        admin_socket_path: Path | str | None = None,
        admin_policy: Callable[[Peer | None], bool] = same_user_or_root,
        audit_dir: Path | str | None = None,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.admin_socket_path = (
            Path(admin_socket_path)
            if admin_socket_path is not None
            else admin_socket_for(self.socket_path)
        )
        self.lock_path = Path(lock_path) if lock_path is not None else None
        self.admin_policy = admin_policy
        self.audit_dir = Path(audit_dir) if audit_dir is not None else None
        self._socket_mode = socket_mode
        self._accept_timeout_s = accept_timeout_s
        self._tables: dict[str, dict[str, RpcHandler]] = {AGENT: {}, ADMIN: {}}
        self._socks: dict[str, socket.socket] = {}
        self._lock_fd: int | None = None
        self.previous_pid: int | None = None
        """The pid the lock file held before this daemon took it: the
        prior instance, if there was one. ``None`` on a first start."""
        self._accept_threads: list[threading.Thread] = []
        self._workers: list[threading.Thread] = []
        self._stop_event = threading.Event()
        self.register("ping", _handle_ping)
        self.register("ping", _handle_ping, admin=True)

    # ---- handler registration ----

    def register(
        self, method: str, handler: RpcHandler, *, admin: bool = False
    ) -> None:
        """Register a handler for ``method`` on the agent socket, or on
        the admin socket with ``admin=True``. Last registration wins;
        callers can override the default ``ping`` if they need to."""
        self._tables[ADMIN if admin else AGENT][method] = handler

    def methods(self, *, admin: bool = False) -> list[str]:
        return sorted(self._tables[ADMIN if admin else AGENT])

    # ---- lifecycle ----

    def start(self) -> None:
        """Bind both sockets, listen, and start one accept loop each on
        background threads."""
        if self._socks:
            raise RuntimeError("server already started")
        self._acquire_lock()
        paths = {AGENT: self.socket_path, ADMIN: self.admin_socket_path}
        try:
            for path in paths.values():
                self._prepare_socket_path(path)
            for role, path in paths.items():
                self._socks[role] = self._bind(
                    path, self._socket_mode if role == AGENT else 0o600
                )
        except BaseException:
            self._close_sockets()
            self._release_lock()
            raise
        self._stop_event.clear()
        for role, sock in self._socks.items():
            t = threading.Thread(
                target=self._accept_loop,
                args=(role, sock),
                name=f"sponsio-daemon-accept-{role}",
                daemon=True,
            )
            self._accept_threads.append(t)
            t.start()
        _logger.info(
            "daemon listening at %s (admin %s)",
            self.socket_path,
            self.admin_socket_path,
        )

    def _bind(self, path: Path, mode: int) -> socket.socket:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(str(path))
            sock.settimeout(self._accept_timeout_s)
            # The full backlog: on macOS a connect to a Unix socket whose
            # backlog is full is refused at once, and a refused hook denies.
            sock.listen(socket.SOMAXCONN)
            os.chmod(path, mode)
        except BaseException:
            sock.close()
            raise
        return sock

    def _close_sockets(self) -> None:
        """Close and unlink every socket this server bound."""
        paths = {AGENT: self.socket_path, ADMIN: self.admin_socket_path}
        for role, sock in list(self._socks.items()):
            try:
                sock.close()
            except OSError:
                pass
            try:
                paths[role].unlink()
            except FileNotFoundError:
                pass
        self._socks.clear()

    def stop(self, *, join_timeout_s: float = 2.0) -> None:
        """Close the listening sockets, join workers, remove the socket files."""
        self._stop_event.set()
        self._close_sockets()
        for t in self._accept_threads:
            t.join(timeout=join_timeout_s)
        self._accept_threads.clear()
        for w in list(self._workers):
            w.join(timeout=join_timeout_s)
        self._workers.clear()
        self._release_lock()

    # ---- internals ----

    def _acquire_lock(self) -> None:
        """Take the exclusive lock, or raise :class:`DaemonAlreadyRunning`.

        The lock file is created with mode 0600 and holds the pid of
        the daemon that owns it; the pid that was there before is kept
        on :attr:`previous_pid` so the caller can tell a restart after
        a crash from a first start.
        """
        if self.lock_path is None or fcntl is None:
            return
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise DaemonAlreadyRunning(
                f"another sponsio daemon holds {self.lock_path}"
            ) from None
        try:
            prior = os.read(fd, 64).decode("ascii", "replace").strip()
            self.previous_pid = int(prior) if prior.isdigit() else None
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        except OSError:
            self.previous_pid = None
        self._lock_fd = fd

    def _release_lock(self) -> None:
        fd = self._lock_fd
        self._lock_fd = None
        if fd is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    @staticmethod
    def _prepare_socket_path(path: Path) -> None:
        """Ensure ``path``'s parent exists and the path itself is free.
        A socket file that a daemon answers on belongs to that daemon
        (:class:`DaemonAlreadyRunning`); one nobody answers on is a
        crashed daemon's leftover and is removed. A non-socket file is
        never removed: that is user data, not ours.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            return
        if not path.is_socket():
            raise RuntimeError(
                f"socket path {path} exists and is not a socket — "
                f"refusing to remove (looks like user data)"
            )
        if _answers(path):
            raise DaemonAlreadyRunning(f"a sponsio daemon already answers at {path}")
        path.unlink()

    def _accept_loop(self, role: str, sock: socket.socket) -> None:
        while not self._stop_event.is_set():
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                # Socket closed underneath us → shutdown path.
                return
            t = threading.Thread(
                target=self._serve_connection, args=(conn, role), daemon=True
            )
            self._workers.append(t)
            t.start()
            # Periodic prune of finished workers so the list doesn't grow
            # unbounded over a long-running daemon.
            self._workers[:] = [w for w in self._workers if w.is_alive()]

    def _serve_connection(self, conn: socket.socket, role: str = AGENT) -> None:
        try:
            peer: Peer | None = get_peer(conn)
        except OSError:
            peer = None
        _conn.peer = peer
        with conn:
            try:
                conn.settimeout(5.0)  # request must arrive within 5s of accept
                body = read_frame(conn)
                req = Request.from_json(body)
                if role == ADMIN:
                    response = self._dispatch_admin(req, peer)
                else:
                    response = self._dispatch(req, AGENT)
            except FrameError as e:
                response = Response.failure(f"frame error: {e}", code="frame")
            except ConnectionError:
                # Peer hung up; nothing to send.
                return
            except Exception as e:  # noqa: BLE001 — log+respond, don't crash daemon
                _logger.exception("unhandled error in connection")
                response = Response.failure(
                    f"internal error: {e.__class__.__name__}: {e}",
                    code="internal",
                )
            try:
                write_frame(conn, response.to_json())
            except (OSError, FrameError):
                # Peer gone or response too big; we already logged above
                # if the body was the cause.
                pass

    def _dispatch_admin(self, req: Request, peer: Peer | None) -> Response:
        """Admin calls run only for a peer the policy accepts, and every
        one is audited with the peer, allowed or not."""
        if not self.admin_policy(peer):
            response = Response.failure(
                "admin calls are not allowed from this user", code="permission"
            )
        else:
            response = self._dispatch(req, ADMIN)
        self._audit_admin(req, peer, response)
        return response

    def _audit_admin(self, req: Request, peer: Peer | None, response: Response) -> None:
        if self.audit_dir is None or req.method == "ping":
            return
        from sponsio.daemon import audit

        try:
            canonical = json.dumps(req.params, sort_keys=True, default=str)
        except (TypeError, ValueError):
            canonical = repr(req.params)
        audit.append(
            self.audit_dir,
            AUDIT_ADMIN_CALL,
            method=req.method,
            peer=peer.as_dict() if peer is not None else None,
            ok=response.ok,
            code=None if response.ok else response.code,
            params_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
        )

    def _dispatch(self, req: Request, role: str = AGENT) -> Response:
        handler = self._tables[role].get(req.method)
        if handler is None:
            return Response.failure(f"unknown method: {req.method!r}", code="not_found")
        try:
            result = handler(req.params)
            return Response.success(result)
        except RpcError as e:
            return Response.failure(str(e), code=e.code)
        except Exception as e:  # noqa: BLE001
            _logger.exception("handler %s raised", req.method)
            tb = traceback.format_exc(limit=3)
            return Response.failure(
                f"{e.__class__.__name__}: {e}\n{tb}", code="internal"
            )


def _answers(path: Path, timeout_s: float = 0.5) -> bool:
    """Does something accept connections on the socket at ``path``?"""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout_s)
            s.connect(str(path))
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Default handlers
# ---------------------------------------------------------------------------


def _handle_ping(params: dict[str, Any]) -> dict[str, Any]:
    """Round-trip health check.  Echoes ``params["echo"]`` if present
    and reports daemon pid + version so a client can sanity-check the
    binary on the other end.
    """
    from sponsio import __version__ as sponsio_version

    return {
        "pong": True,
        "echo": params.get("echo"),
        "pid": os.getpid(),
        "version": sponsio_version,
    }


# ---------------------------------------------------------------------------
# Convenience wrapper for "run the daemon foreground" CLI use
# ---------------------------------------------------------------------------


def serve_forever(
    socket_path: Path | str,
    *,
    handler_registry: Callable[[DaemonServer], None] | None = None,
    socket_mode: int = 0o600,
    lock_path: Path | str | None = None,
    on_started: Callable[[DaemonServer], None] | None = None,
    admin_socket_path: Path | str | None = None,
) -> None:
    """Start a server, install handlers, block until SIGINT/SIGTERM.

    Used by ``sponsio daemon run`` (foreground) and the launchd /
    systemd unit (which runs the same code under their lifecycle).
    ``on_started`` runs once the socket is bound (the CLI records the
    start in the audit log there). :class:`DaemonAlreadyRunning`
    propagates to the caller.
    """
    import signal

    server = DaemonServer(
        socket_path,
        socket_mode=socket_mode,
        lock_path=lock_path,
        admin_socket_path=admin_socket_path,
    )
    if handler_registry is not None:
        handler_registry(server)
    server.start()
    if on_started is not None:
        on_started(server)

    stop = threading.Event()

    def _sig(_signo: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    try:
        stop.wait()
    finally:
        server.stop()
