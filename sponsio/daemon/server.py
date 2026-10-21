"""Sponsio daemon Unix socket server.

Threading model: one thread per connection, accept loop on the main
daemon thread.  Connections are short-lived (single request → response
→ close), so the thread cost is bounded and we don't need an executor.

For graceful shutdown, ``DaemonServer.stop()`` closes the listening
socket and joins outstanding worker threads with a small timeout.
"""

from __future__ import annotations

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

from sponsio.daemon.protocol import (
    FrameError,
    Request,
    Response,
    read_frame,
    write_frame,
)


_logger = logging.getLogger("sponsio.daemon")


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
        socket_path: Path to bind. A socket file already there is probed
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
    """

    def __init__(
        self,
        socket_path: Path | str,
        *,
        socket_mode: int = 0o600,
        accept_timeout_s: float = 0.5,
        lock_path: Path | str | None = None,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.lock_path = Path(lock_path) if lock_path is not None else None
        self._socket_mode = socket_mode
        self._accept_timeout_s = accept_timeout_s
        self._handlers: dict[str, RpcHandler] = {}
        self._sock: socket.socket | None = None
        self._lock_fd: int | None = None
        self.previous_pid: int | None = None
        """The pid the lock file held before this daemon took it: the
        prior instance, if there was one. ``None`` on a first start."""
        self._accept_thread: threading.Thread | None = None
        self._workers: list[threading.Thread] = []
        self._stop_event = threading.Event()
        self.register("ping", _handle_ping)

    # ---- handler registration ----

    def register(self, method: str, handler: RpcHandler) -> None:
        """Register a handler for ``method``.  Last registration wins;
        callers can override the default ``ping`` if they need to."""
        self._handlers[method] = handler

    # ---- lifecycle ----

    def start(self) -> None:
        """Bind, listen, and start the accept loop on a background thread."""
        if self._sock is not None:
            raise RuntimeError("server already started")
        self._acquire_lock()
        try:
            self._prepare_socket_path()
        except BaseException:
            self._release_lock()
            raise
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(self.socket_path))
        self._sock.settimeout(self._accept_timeout_s)
        self._sock.listen(16)
        os.chmod(self.socket_path, self._socket_mode)
        self._stop_event.clear()
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name="sponsio-daemon-accept", daemon=True
        )
        self._accept_thread.start()
        _logger.info("daemon listening at %s", self.socket_path)

    def stop(self, *, join_timeout_s: float = 2.0) -> None:
        """Close the listening socket, join workers, remove the socket file."""
        self._stop_event.set()
        bound = self._sock is not None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=join_timeout_s)
            self._accept_thread = None
        for w in list(self._workers):
            w.join(timeout=join_timeout_s)
        self._workers.clear()
        if bound:
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass
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

    def _prepare_socket_path(self) -> None:
        """Ensure ``socket_path``'s parent exists and the path itself is
        free. A socket file that a daemon answers on belongs to that
        daemon (:class:`DaemonAlreadyRunning`); one nobody answers on is
        a crashed daemon's leftover and is removed. A non-socket file
        is never removed: that is user data, not ours.
        """
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.socket_path.exists():
            return
        if not self.socket_path.is_socket():
            raise RuntimeError(
                f"socket path {self.socket_path} exists and is not a socket — "
                f"refusing to remove (looks like user data)"
            )
        if _answers(self.socket_path):
            raise DaemonAlreadyRunning(
                f"a sponsio daemon already answers at {self.socket_path}"
            )
        self.socket_path.unlink()

    def _accept_loop(self) -> None:
        while not self._stop_event.is_set():
            sock = self._sock
            if sock is None:
                return
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                # Socket closed underneath us → shutdown path.
                return
            t = threading.Thread(
                target=self._serve_connection, args=(conn,), daemon=True
            )
            self._workers.append(t)
            t.start()
            # Periodic prune of finished workers so the list doesn't grow
            # unbounded over a long-running daemon.
            self._workers[:] = [w for w in self._workers if w.is_alive()]

    def _serve_connection(self, conn: socket.socket) -> None:
        with conn:
            try:
                conn.settimeout(5.0)  # request must arrive within 5s of accept
                body = read_frame(conn)
                req = Request.from_json(body)
                response = self._dispatch(req)
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

    def _dispatch(self, req: Request) -> Response:
        handler = self._handlers.get(req.method)
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
) -> None:
    """Start a server, install handlers, block until SIGINT/SIGTERM.

    Used by ``sponsio daemon run`` (foreground) and the launchd /
    systemd unit (which runs the same code under their lifecycle).
    ``on_started`` runs once the socket is bound (the CLI records the
    start in the audit log there). :class:`DaemonAlreadyRunning`
    propagates to the caller.
    """
    import signal

    server = DaemonServer(socket_path, socket_mode=socket_mode, lock_path=lock_path)
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
