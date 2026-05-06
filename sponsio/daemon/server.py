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


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class DaemonServer:
    """Unix-socket JSON-RPC server.

    Args:
        socket_path: Path to bind. Will be unlinked first if it exists
            and is a stale socket; refuses to clobber a non-socket file.
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
    ) -> None:
        self.socket_path = Path(socket_path)
        self._socket_mode = socket_mode
        self._accept_timeout_s = accept_timeout_s
        self._handlers: dict[str, RpcHandler] = {}
        self._sock: socket.socket | None = None
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
        self._prepare_socket_path()
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
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass

    # ---- internals ----

    def _prepare_socket_path(self) -> None:
        """Ensure ``socket_path``'s parent exists and the path itself is
        free of a prior daemon's stale socket.  Refuses to delete a
        non-socket file at that path — that's user data, not ours.
        """
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            if self.socket_path.is_socket():
                self.socket_path.unlink()
            else:
                raise RuntimeError(
                    f"socket path {self.socket_path} exists and is not a socket — "
                    f"refusing to remove (looks like user data)"
                )

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
) -> None:
    """Start a server, install handlers, block until SIGINT/SIGTERM.

    Used by ``sponsio daemon run`` (foreground) and the launchd /
    systemd unit (which runs the same code under their lifecycle).
    """
    import signal

    server = DaemonServer(socket_path, socket_mode=socket_mode)
    if handler_registry is not None:
        handler_registry(server)
    server.start()

    stop = threading.Event()

    def _sig(_signo: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    try:
        stop.wait()
    finally:
        server.stop()
