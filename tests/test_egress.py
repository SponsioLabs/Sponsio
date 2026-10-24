"""The egress proxy (P3.4): every connection a sandboxed agent makes is a
decision in the daemon, against egress.allow and the _egress library.

Upstreams are loopback servers started here; nothing leaves the machine.
"""

from __future__ import annotations

import http.server
import json
import socket
import socketserver
import tempfile
import threading
from pathlib import Path

import pytest

from sponsio.daemon import config
from sponsio.daemon.client import DaemonClient
from sponsio.daemon.egress import host_allowed, normalize_host
from sponsio.daemon.guard_service import GuardService
from sponsio.daemon.handlers import register_default_handlers
from sponsio.daemon.server import DaemonServer
from sponsio.guard_stdin import GuardPolicy


@pytest.fixture
def short_tmp():
    import shutil

    d = Path(tempfile.mkdtemp(prefix="spe-", dir="/tmp"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


class _Hello(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = f"hello {self.path}".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):
        pass


class _Echo(socketserver.BaseRequestHandler):
    def handle(self):
        data = self.request.recv(1024)
        self.request.sendall(b"echo:" + data)


@pytest.fixture
def upstreams():
    web = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Hello)
    echo = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Echo)
    for s in (web, echo):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    yield web.server_address[1], echo.server_address[1]
    for s in (web, echo):
        s.shutdown()
        s.server_close()


def _service(short_tmp: Path, allow=(), library: str | None = None) -> GuardService:
    root = short_tmp / "plugins"
    root.mkdir(exist_ok=True)
    if library is not None:
        (root / "_egress").mkdir(exist_ok=True)
        (root / "_egress" / "sponsio.yaml").write_text(library)
    state = short_tmp / "state"
    config.save(state, config.DaemonConfig(egress_allow=tuple(allow)))
    return GuardService(
        root=root,
        state_dir=state,
        run_dir=short_tmp,
        policy=GuardPolicy(mode="enforce", on_error="deny"),
    )


def _via_tcp(port: int, request: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(request)
        out = b""
        while chunk := s.recv(65536):
            out += chunk
        return out


def _get(proxy_port: int, url: str) -> bytes:
    return _via_tcp(proxy_port, f"GET {url} HTTP/1.1\r\nHost: x\r\n\r\n".encode())


def test_hosts_are_normalised_or_refused():
    assert normalize_host("API.GitHub.com.") == "api.github.com"
    assert normalize_host("127.0.0.1") == "127.0.0.1"
    assert normalize_host("user@evil.com") is None
    assert normalize_host("300.1.1.1") is None
    assert normalize_host("[::1]") is None
    for disguised in (
        "2130706433",
        "0x7f000001",
        "127.1",
        "169.254.43518",
        "010.0.0.1",
    ):
        assert normalize_host(disguised) is None, disguised
    assert host_allowed("api.github.com", 443, ("*.github.com",))
    assert not host_allowed("github.com", 443, ("*.github.com",))
    assert host_allowed("github.com", 80, ("github.com",))
    assert not host_allowed("github.com", 22, ("github.com",))
    assert host_allowed("github.com", 22, ("github.com:22",))
    assert host_allowed("github.com", 8443, ("github.com:*",))
    assert not host_allowed("github.com.evil.io", 443, ("github.com",))


def test_no_policy_refuses_every_connection(short_tmp, upstreams):
    web, _echo = upstreams
    svc = _service(short_tmp)
    proxy = svc.egress.open("run1")
    out = _get(proxy["port"], f"http://127.0.0.1:{web}/x")
    assert out.startswith(b"HTTP/1.1 403") and b"no egress policy" in out
    svc.close_gateway()


def test_an_allowed_host_is_reached_and_another_is_not(short_tmp, upstreams):
    web, _echo = upstreams
    svc = _service(short_tmp, allow=["127.0.0.1:*"])
    proxy = svc.egress.open("run1")
    out = _get(proxy["port"], f"http://127.0.0.1:{web}/ok?q=1")
    assert out.startswith(b"HTTP/1.0 200") or out.startswith(b"HTTP/1.1 200")
    assert out.endswith(b"hello /ok?q=1")
    denied = _get(proxy["port"], f"http://localhost:{web}/x")
    assert denied.startswith(b"HTTP/1.1 403") and b"not in egress.allow" in denied
    svc.close_gateway()


def test_connect_tunnels_through_the_unix_socket(short_tmp, upstreams):
    _web, echo = upstreams
    svc = _service(short_tmp, allow=["127.0.0.1:*"])
    proxy = svc.egress.open("run1")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(10)
        s.connect(proxy["unix"])
        s.sendall(f"CONNECT 127.0.0.1:{echo} HTTP/1.1\r\n\r\n".encode())
        assert s.recv(1024).startswith(b"HTTP/1.1 200")
        s.sendall(b"ping")
        assert s.recv(1024) == b"echo:ping"
    svc.close_gateway()


EGRESS_LIBRARY = """
version: "1"
agents:
  _egress:
    contracts:
      - desc: "at most two connections"
        G: {pattern: rate_limit, args: [egress, 2]}
      - desc: "no admin paths"
        G: {pattern: arg_blacklist, args: [egress, url, ["/admin"]]}
"""


def test_egress_contracts_decide_in_the_runs_session(short_tmp, upstreams):
    web, _echo = upstreams
    svc = _service(short_tmp, allow=["127.0.0.1:*"], library=EGRESS_LIBRARY)
    proxy = svc.egress.open("run1")
    url = f"http://127.0.0.1:{web}"
    assert b"no admin paths" in _get(proxy["port"], f"{url}/admin")
    assert b"hello /a" in _get(proxy["port"], f"{url}/a")
    assert b"hello /b" in _get(proxy["port"], f"{url}/b")
    third = _get(proxy["port"], f"{url}/c")
    assert third.startswith(b"HTTP/1.1 403") and b"at most two" in third
    events = svc.traces.events("_egress", "egress:run1")
    assert [e.args["url"] for e in events] == [f"{url}/a", f"{url}/b"]
    # Another run has its own count.
    other = svc.egress.open("run2")
    assert b"hello /a" in _get(other["port"], f"{url}/a")
    svc.close_gateway()


def test_a_library_alone_cannot_reach_local_addresses(short_tmp, upstreams):
    """The proxy runs on the daemon's machine: loopback (the daemon's own
    ports, other runs' proxies) and link-local (cloud metadata) need
    egress.allow to name the address."""
    web, _echo = upstreams
    svc = _service(short_tmp, library=EGRESS_LIBRARY)
    proxy = svc.egress.open("run1")
    out = _get(proxy["port"], f"http://127.0.0.1:{web}/a")
    assert out.startswith(b"HTTP/1.1 403") and b"local address" in out
    svc.close_gateway()


class _Echoes(http.server.BaseHTTPRequestHandler):
    """Answers with the path, Host header and body it saw."""

    def _answer(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.dumps(
            {
                "path": self.path,
                "host": self.headers.get("Host"),
                "body": self.rfile.read(n).decode(),
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer

    def log_message(self, *_a):
        pass


def test_plain_http_is_one_decided_request(short_tmp):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Echoes)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    svc = _service(short_tmp, allow=["127.0.0.1:*"])
    proxy = svc.egress.open("run1")
    try:
        # The Host header is the decided host, whatever the client sent;
        # a request pipelined behind the first never reaches the server.
        out = _via_tcp(
            proxy["port"],
            (
                f"POST http://127.0.0.1:{port}/ok HTTP/1.1\r\nHost: other.example\r\n"
                f"Content-Length: 3\r\n\r\nabc"
                f"GET http://127.0.0.1:{port}/second HTTP/1.1\r\nHost: x\r\n\r\n"
            ).encode(),
        )
        seen = json.loads(out.split(b"\r\n\r\n", 1)[1])
        assert seen == {"path": "/ok", "host": f"127.0.0.1:{port}", "body": "abc"}
        assert b"/second" not in out
        chunked = _via_tcp(
            proxy["port"],
            (
                f"POST http://127.0.0.1:{port}/x HTTP/1.1\r\n"
                "Transfer-Encoding: chunked\r\n\r\n"
            ).encode(),
        )
        assert chunked.startswith(b"HTTP/1.1 411")
    finally:
        srv.shutdown()
        svc.close_gateway()


def test_connect_to_port_80_and_unlisted_ports_are_refused(short_tmp, upstreams):
    _web, echo = upstreams
    svc = _service(short_tmp, allow=["127.0.0.1"])
    proxy = svc.egress.open("run1")
    tunnel80 = _via_tcp(proxy["port"], b"CONNECT example.com:80 HTTP/1.1\r\n\r\n")
    assert tunnel80.startswith(b"HTTP/1.1 403") and b"port 80" in tunnel80
    other = _via_tcp(
        proxy["port"], f"CONNECT 127.0.0.1:{echo} HTTP/1.1\r\n\r\n".encode()
    )
    assert other.startswith(b"HTTP/1.1 403") and b"not in egress.allow" in other
    svc.close_gateway()


def test_malformed_and_odd_requests_are_refused(short_tmp, upstreams):
    svc = _service(short_tmp, allow=["*"])
    proxy = svc.egress.open("run1")
    assert _via_tcp(proxy["port"], b"garbage\r\n\r\n").startswith(b"HTTP/1.1 400")
    assert _get(proxy["port"], "https://example.com/").startswith(b"HTTP/1.1 400")
    odd = _via_tcp(proxy["port"], b"CONNECT user@host:443 HTTP/1.1\r\n\r\n")
    assert odd.startswith(b"HTTP/1.1 400")
    svc.close_gateway()


def test_closing_removes_the_socket(short_tmp):
    svc = _service(short_tmp, allow=["127.0.0.1:*"])
    proxy = svc.egress.open("run1")
    assert Path(proxy["unix"]).exists()
    assert svc.egress.close("run1") == {"closed": True}
    assert not Path(proxy["unix"]).exists()


def test_the_rpc_is_on_the_agent_socket(short_tmp):
    svc = _service(short_tmp, allow=["127.0.0.1:*"])
    server = DaemonServer(short_tmp / "sock", accept_timeout_s=0.05)
    register_default_handlers(server, guard_service=svc)
    server.start()
    try:
        client = DaemonClient(socket_path=server.socket_path, timeout_s=5)
        opened = client.call("egress.open", {"session": "run9"})
        assert opened["session"] == "run9" and opened["port"] > 0
        assert client.call("egress.close", {"session": "run9"})["closed"] is True
    finally:
        server.stop(join_timeout_s=0.5)
        svc.close_gateway()
    assert json.dumps(opened)
