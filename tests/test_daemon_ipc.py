"""IPC layer for the sponsio control daemon.

These tests exercise the protocol + server + client *roundtrip* at the
in-process level — start a real Unix-socket server in a thread, hit it
with a real client, assert the result.  No subprocess, no flakiness.

Each test owns its own socket path under ``tmp_path`` so they can run
in parallel without colliding.

What's covered:

* §1 — happy path round-trip (the ``ping`` default handler)
* §2 — protocol invariants (length-prefix, big payloads, error frames)
* §3 — handler errors surface to the client with the right ``code``
* §4 — unknown-method dispatch returns a structured ``not_found``
* §5 — socket-path safety (refuse to clobber non-socket files)
* §6 — ``daemon_is_running`` reflects reality
* §7 — server lifecycle (start → stop → cannot-restart-without-new)
"""

from __future__ import annotations

import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

from sponsio.daemon.client import DaemonClient, DaemonError, daemon_is_running
from sponsio.daemon.protocol import (
    FrameError,
    MAX_FRAME_BYTES,
    Request,
    Response,
    read_frame,
    write_frame,
)
from sponsio.daemon.server import DaemonServer, RpcError


# AF_UNIX path length is capped (~104 chars on macOS, 108 on Linux).
# pytest's ``tmp_path`` lives under nested fixtures dirs that routinely
# blow past the cap, so daemon-IPC tests use shallow ``/tmp``-rooted
# directories instead.  Each test gets its own dir so they can run in
# parallel without colliding on the socket name.
@pytest.fixture
def short_tmp() -> Path:
    """Short-path tempdir suitable for AF_UNIX socket binding."""
    d = Path(tempfile.mkdtemp(prefix="sp-", dir="/tmp"))
    yield d
    # Best-effort cleanup; tests don't depend on it for correctness.
    try:
        for p in d.iterdir():
            try:
                p.unlink()
            except OSError:
                pass
        d.rmdir()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def server(short_tmp: Path):
    """A started DaemonServer with the default ``ping`` handler.

    Yields the server; tears down on test exit.  Other fixtures /
    tests can ``server.register(...)`` before/after the yield as long
    as the call happens before the client hits that method.
    """
    s = DaemonServer(short_tmp / "sponsio.sock", accept_timeout_s=0.05)
    s.start()
    yield s
    s.stop(join_timeout_s=0.5)


@pytest.fixture
def client(server: DaemonServer) -> DaemonClient:
    return DaemonClient(socket_path=server.socket_path, timeout_s=2.0)


# ---------------------------------------------------------------------------
# §1 — Happy path
# ---------------------------------------------------------------------------


def test_ping_round_trip(client: DaemonClient):
    """The default ``ping`` handler echoes our value, plus daemon pid
    and Sponsio version.  This is the smallest end-to-end test that
    proves frame I/O + dispatch + handler all line up."""
    result = client.call("ping", {"echo": "hello"})
    assert result["pong"] is True
    assert result["echo"] == "hello"
    assert isinstance(result["pid"], int)
    assert isinstance(result["version"], str) and result["version"]


def test_ping_no_params(client: DaemonClient):
    """``params`` defaults to ``{}`` when omitted by the caller; the
    handler must not assume any keys are present."""
    result = client.call("ping")
    assert result["pong"] is True
    assert result["echo"] is None


def test_custom_handler_dispatches(server: DaemonServer, client: DaemonClient):
    """Caller-registered handlers run with the request params and
    their return value reaches the client."""

    def add(params):
        return params["a"] + params["b"]

    server.register("math.add", add)
    assert client.call("math.add", {"a": 2, "b": 40}) == 42


# ---------------------------------------------------------------------------
# §2 — Protocol invariants
# ---------------------------------------------------------------------------


def test_request_response_json_round_trip():
    req = Request(method="m", params={"x": 1})
    assert Request.from_json(req.to_json()) == req

    ok = Response.success({"y": 2})
    assert Response.from_json(ok.to_json()).result == {"y": 2}

    err = Response.failure("nope", code="bad")
    parsed = Response.from_json(err.to_json())
    assert not parsed.ok
    assert parsed.error == "nope"
    assert parsed.code == "bad"


def test_request_rejects_non_object_root():
    """JSON arrays / strings at the root are not valid requests; the
    parser must reject them with a ``FrameError`` rather than silently
    munging into an empty dict."""
    with pytest.raises(FrameError):
        Request.from_json(b'["not", "an", "object"]')


def test_request_rejects_missing_method():
    with pytest.raises(FrameError):
        Request.from_json(b'{"params": {}}')


def test_frame_size_cap_enforced():
    """``MAX_FRAME_BYTES`` cap defends against a hostile peer pushing
    the daemon into OOM via a giant length prefix."""
    a, b = socket.socketpair()
    try:
        oversize = b"\x00" * (MAX_FRAME_BYTES + 1)
        with pytest.raises(FrameError, match="exceeds cap"):
            write_frame(a, oversize)
    finally:
        a.close()
        b.close()


def test_frame_short_read_raises_connection_error():
    """If the peer hangs up mid-frame the reader gets a clear
    :class:`ConnectionError`, not a hang or a silent partial parse."""
    a, b = socket.socketpair()
    try:
        # Send just 2 bytes of a 4-byte length header, then close.
        a.sendall(b"\x00\x00")
        a.close()
        with pytest.raises(ConnectionError):
            read_frame(b)
    finally:
        b.close()


def test_medium_payload_round_trips(server: DaemonServer, client: DaemonClient):
    """A 256 KiB payload exercises the chunked recv path in
    ``_read_exact`` (default kernel SO_RCVBUF won't fit it in one read).
    """
    big_blob = "x" * (256 * 1024)

    def echo_big(params):
        return {"received_len": len(params["blob"])}

    server.register("blob.echo", echo_big)
    result = client.call("blob.echo", {"blob": big_blob})
    assert result["received_len"] == len(big_blob)


# ---------------------------------------------------------------------------
# §3 — Handler errors → structured ``DaemonError``
# ---------------------------------------------------------------------------


def test_handler_rpc_error_propagates_code(server: DaemonServer, client: DaemonClient):
    """A handler raising ``RpcError`` produces a non-ok response whose
    ``code`` reaches the client unchanged.  This is the contract that
    lets ``plugin append`` distinguish ``"validation"`` from
    ``"internal"`` errors when it routes through the daemon."""

    def picky(params):
        raise RpcError("nope, that's invalid", code="validation")

    server.register("picky", picky)

    with pytest.raises(DaemonError) as excinfo:
        client.call("picky")
    assert excinfo.value.code == "validation"
    assert "nope" in str(excinfo.value)


def test_handler_unexpected_exception_becomes_internal(
    server: DaemonServer, client: DaemonClient
):
    """Uncaught handler exceptions turn into ``code="internal"`` so the
    daemon never crashes a connection AND the client can tell apart
    "you sent bad data" (validation) from "we have a bug" (internal)."""

    def boom(params):
        raise RuntimeError("totally unexpected")

    server.register("boom", boom)

    with pytest.raises(DaemonError) as excinfo:
        client.call("boom")
    assert excinfo.value.code == "internal"
    assert "RuntimeError" in str(excinfo.value)


def test_unknown_method_returns_not_found(client: DaemonClient):
    with pytest.raises(DaemonError) as excinfo:
        client.call("nope.does.not.exist")
    assert excinfo.value.code == "not_found"


# ---------------------------------------------------------------------------
# §4 — Socket path safety
# ---------------------------------------------------------------------------


def test_server_refuses_to_clobber_regular_file(short_tmp: Path):
    """If the configured socket path already exists as a regular file
    (i.e. user data, not our stale socket), the server must refuse to
    delete it — losing a user file would be worse than failing to
    start.  A literal ``.sock``-named regular file is the realistic
    failure case (e.g. a backup script accidentally writing there)."""
    bogus = short_tmp / "sponsio.sock"
    bogus.write_text("USER DATA — DO NOT DELETE")
    server = DaemonServer(bogus)
    with pytest.raises(RuntimeError, match="not a socket"):
        server.start()
    assert bogus.read_text() == "USER DATA — DO NOT DELETE"


def test_server_replaces_stale_socket(short_tmp: Path):
    """A leftover socket from a crashed daemon (same path, but it IS a
    socket) is fine to remove — that's our resource."""
    sock_path = short_tmp / "sponsio.sock"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(sock_path))
    s.close()
    assert sock_path.is_socket()

    server = DaemonServer(sock_path, accept_timeout_s=0.05)
    server.start()
    try:
        assert sock_path.is_socket()
    finally:
        server.stop(join_timeout_s=0.5)


# ---------------------------------------------------------------------------
# §5 — daemon_is_running probe
# ---------------------------------------------------------------------------


def test_daemon_is_running_true_when_up(server: DaemonServer):
    assert daemon_is_running(server.socket_path) is True


def test_daemon_is_running_false_when_socket_missing(short_tmp: Path):
    assert daemon_is_running(short_tmp / "missing.sock") is False


def test_daemon_is_running_false_when_socket_dead(short_tmp: Path):
    """Stale socket file (no listener) → probe must return False, not
    raise.  This is the "daemon crashed but didn't clean up" case."""
    dead = short_tmp / "dead.sock"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(dead))
    s.close()
    # Don't listen — connect should fail.
    assert daemon_is_running(dead, timeout_s=0.2) is False


# ---------------------------------------------------------------------------
# §6 — Lifecycle
# ---------------------------------------------------------------------------


def test_double_start_raises(short_tmp: Path):
    s = DaemonServer(short_tmp / "sock")
    s.start()
    try:
        with pytest.raises(RuntimeError, match="already started"):
            s.start()
    finally:
        s.stop(join_timeout_s=0.5)


def test_stop_removes_socket_file(short_tmp: Path):
    """Clean shutdown leaves no socket file behind so the next start
    doesn't have to do a "stale or live?" probe."""
    sock_path = short_tmp / "sock"
    s = DaemonServer(sock_path, accept_timeout_s=0.05)
    s.start()
    assert sock_path.is_socket()
    s.stop(join_timeout_s=0.5)
    assert not sock_path.exists()


def test_concurrent_clients(server: DaemonServer):
    """Multiple clients in flight simultaneously — proves the
    one-thread-per-connection accept loop actually parallelises and
    that handlers don't share mutable state through the server."""
    counter = {"n": 0}
    lock = threading.Lock()

    def slow(params):
        # Simulate a non-trivial handler so threads actually overlap.
        time.sleep(0.05)
        with lock:
            counter["n"] += 1
        return counter["n"]

    server.register("slow", slow)

    clients = [DaemonClient(server.socket_path, timeout_s=2.0) for _ in range(8)]
    results: list[int] = []
    threads = [
        threading.Thread(target=lambda c=c: results.append(c.call("slow")))
        for c in clients
    ]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)
    elapsed = time.monotonic() - t0

    assert sorted(results) == list(range(1, 9))
    # If serialised, 8 × 50ms = 400ms; parallel should be way under.
    assert elapsed < 0.3, f"calls did not parallelise: {elapsed:.3f}s for 8x50ms"
