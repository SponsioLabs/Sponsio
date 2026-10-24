"""The egress proxy (Tier 3): the sandboxed agent's only route out.

``sponsio run --sandbox`` asks the daemon for a proxy (``egress.open``)
and starts the agent where nothing else is reachable. Every connection
the agent makes is one decision, in the daemon, before any byte leaves:

* a ``CONNECT host:port`` (HTTPS and anything tunnelled) is decided on
  the destination;
* a plain-HTTP request is decided on the destination, the method and
  the URL.

The decision is a call to the tool ``egress`` with arguments ``host``,
``port``, ``method`` and ``url``, made in the run's session (history
under ``egress:<session>``), against two things:

* ``egress.allow`` in ``daemon.yaml``: host patterns (``github.com``,
  ``*.pypi.org``); a host that matches none is refused;
* the ``_egress`` library, if there is one: contracts on ``egress``,
  such as ``arg_blacklist(egress, host, [...])`` or a ``rate_limit``.

With neither configured every connection is refused. With the library,
an admitted connection is recorded in the session's trace, so contracts
can count and order connections. TLS is not intercepted: contracts see
where a connection goes, never what it carries.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import secrets
import select
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from sponsio.daemon.guard_service import GuardService

HOST = "egress"
"""The host name egress events carry, and the tool they call."""
TOOL = "egress"
MAX_HEADER = 64 * 1024
CONNECT_TIMEOUT_S = 15.0
IDLE_TIMEOUT_S = 300.0

_HOSTNAME = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$"
)
_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_SESSION = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

_logger = logging.getLogger("sponsio.daemon.egress")


def normalize_host(raw: str) -> str | None:
    """The host a connection names, lower-case, or ``None`` when it is not
    a plain hostname or a canonical dotted-quad IPv4 address.

    Userinfo, IPv6 and other spellings of an address are refused rather
    than guessed at: ``2130706433``, ``0x7f000001`` and ``127.1`` all
    resolve to 127.0.0.1, and would slip past a contract naming it.
    """
    host = raw.strip().lower().rstrip(".")
    if _IPV4.match(host):
        parts = host.split(".")
        canonical = all(p == str(int(p)) and int(p) <= 255 for p in parts)
        return host if canonical else None
    if not _HOSTNAME.match(host):
        return None
    labels = host.split(".")
    # A numeric or hex last label is an address in disguise; no TLD is.
    if labels[-1].isdigit() or any(lab.startswith("0x") for lab in labels):
        return None
    return host


DEFAULT_PORTS = (80, 443)


def host_allowed(host: str, port: int, patterns: tuple[str, ...]) -> bool:
    """``github.com`` matches itself on ports 80 and 443; ``*.github.com``
    its subdomains; ``github.com:22`` that host on port 22;
    ``github.com:*`` any port."""
    for raw in patterns:
        pat, sep, port_s = raw.lower().rpartition(":")
        if sep and port_s == "*":
            if fnmatch.fnmatchcase(host, pat):
                return True
            continue
        if not sep or not port_s.isdigit():
            pat, ports = raw.lower(), DEFAULT_PORTS
        else:
            ports = (int(port_s),)
        if port in ports and fnmatch.fnmatchcase(host, pat):
            return True
    return False


def _is_local(ip: str) -> bool:
    import ipaddress

    addr = ipaddress.ip_address(ip)
    return (
        addr.is_loopback
        or addr.is_link_local
        or addr.is_unspecified
        or addr.is_multicast
        or addr.is_reserved
    )


def resolve(host: str, port: int, allow: tuple[str, ...]) -> tuple[str | None, str]:
    """``(address, reason)``: the address to connect to, decided once so a
    DNS answer cannot change between the check and the connection.

    Loopback, link-local (cloud metadata), unspecified, multicast and
    reserved addresses are refused unless ``egress.allow`` names that
    exact address: through the proxy they would reach the daemon's own
    machine.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as e:
        return None, f"Sponsio egress: cannot resolve {host}: {e}"
    for family, _t, _p, _c, sockaddr in infos:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        ip = sockaddr[0]
        permits = (ip, f"{ip}:{port}", f"{ip}:*")
        if _is_local(ip) and not any(a.lower() in permits for a in allow):
            return None, (
                f"Sponsio egress: {host} resolves to a local address ({ip}); "
                f"name it in egress.allow to permit it"
            )
        return ip, ""
    return None, f"Sponsio egress: no address for {host}"


def _send_error(conn: socket.socket, code: int, reason: str) -> None:
    body = reason.encode("utf-8", "replace") + b"\n"
    head = (
        f"HTTP/1.1 {code} {'Forbidden' if code == 403 else 'Bad Gateway'}\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"X-Sponsio-Reason: {reason[:200].encode('ascii', 'replace').decode()}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode("latin-1", "replace")
    try:
        conn.sendall(head + body)
    except OSError:
        pass


def _pipe(a: socket.socket, b: socket.socket) -> None:
    """Copy both ways until either side closes or goes idle."""
    socks = [a, b]
    try:
        while True:
            ready, _w, _x = select.select(socks, [], [], IDLE_TIMEOUT_S)
            if not ready:
                return
            for s in ready:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except OSError:
        return
    finally:
        for s in socks:
            try:
                s.close()
            except OSError:
                pass


def _relay(src: socket.socket, dst: socket.socket) -> None:
    """Copy ``src`` to ``dst`` until ``src`` closes, then close both."""
    try:
        src.settimeout(IDLE_TIMEOUT_S)
        while True:
            data = src.recv(65536)
            if not data:
                return
            dst.sendall(data)
    except OSError:
        return
    finally:
        for s in (src, dst):
            try:
                s.close()
            except OSError:
                pass


@dataclass
class _Request:
    method: str
    target: str
    version: str
    headers: list[tuple[str, str]]
    rest: bytes
    """Bytes read past the header block (the start of a body)."""


def _read_request(conn: socket.socket) -> _Request | None:
    buf = b""
    while b"\r\n\r\n" not in buf:
        if len(buf) > MAX_HEADER:
            return None
        chunk = conn.recv(8192)
        if not chunk:
            return None
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3:
        return None
    headers = []
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep:
            headers.append((name.strip(), value.strip()))
    return _Request(parts[0].upper(), parts[1], parts[2], headers, rest)


class SessionProxy:
    """One run's proxy: a Unix socket and a loopback TCP port, both
    deciding in the run's session."""

    def __init__(self, egress: "Egress", session: str, socket_dir: Path) -> None:
        self.egress = egress
        self.session = session
        self.unix_path = socket_dir / f"egress-{secrets.token_hex(6)}.sock"
        self._stop = threading.Event()
        self._unix = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._unix.bind(str(self.unix_path))
        os.chmod(self.unix_path, 0o666)
        self._unix.listen(64)
        self._tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._tcp.bind(("127.0.0.1", 0))
        self._tcp.listen(64)
        self.port = self._tcp.getsockname()[1]
        for s in (self._unix, self._tcp):
            s.settimeout(0.2)
            threading.Thread(target=self._accept, args=(s,), daemon=True).start()

    def _accept(self, listener: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        conn.settimeout(CONNECT_TIMEOUT_S)
        try:
            req = _read_request(conn)
        except OSError:
            req = None
        if req is None:
            _send_error(conn, 400, "Sponsio egress: malformed request")
            conn.close()
            return
        if req.method == "CONNECT":
            host, _, port_s = req.target.rpartition(":")
            url = None
        else:
            parts = urlsplit(req.target)
            if parts.scheme != "http" or not parts.hostname:
                _send_error(
                    conn, 400, "Sponsio egress: a proxy request needs an http:// URL"
                )
                conn.close()
                return
            host = parts.hostname
            port_s = str(parts.port or 80)
            url = req.target
        norm = normalize_host(host)
        try:
            port = int(port_s)
        except ValueError:
            port = -1
        if norm is None or not 0 < port < 65536:
            _send_error(
                conn, 400, f"Sponsio egress: not a host and port: {req.target!r}"
            )
            conn.close()
            return
        if req.method == "CONNECT" and port == 80:
            # A tunnel to port 80 would carry plain HTTP past the method
            # and URL contracts; send it as a proxy request instead.
            _send_error(
                conn, 403, "Sponsio egress: use plain proxying for port 80, not CONNECT"
            )
            conn.close()
            return
        length = 0
        if req.method != "CONNECT":
            headers = {n.lower(): v for n, v in req.headers}
            if "transfer-encoding" in headers:
                _send_error(
                    conn, 411, "Sponsio egress: send a Content-Length, not chunked"
                )
                conn.close()
                return
            try:
                length = int(headers.get("content-length", "0"))
            except ValueError:
                length = -1
            if length < 0:
                _send_error(conn, 400, "Sponsio egress: bad Content-Length")
                conn.close()
                return
        allowed, reason = self.egress.decide(
            self.session, host=norm, port=port, method=req.method, url=url
        )
        if not allowed:
            _send_error(conn, 403, reason)
            conn.close()
            return
        address, reason = resolve(
            norm, port, self.egress.service.config.current().egress_allow
        )
        if address is None:
            _send_error(conn, 403, reason)
            conn.close()
            return
        try:
            upstream = socket.create_connection(
                (address, port), timeout=CONNECT_TIMEOUT_S
            )
        except OSError as e:
            _send_error(conn, 502, f"Sponsio egress: cannot reach {norm}:{port}: {e}")
            conn.close()
            return
        conn.settimeout(None)
        upstream.settimeout(None)
        try:
            if req.method == "CONNECT":
                conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            else:
                parts = urlsplit(req.target)
                path = parts.path or "/"
                if parts.query:
                    path += "?" + parts.query
                lines = [f"{req.method} {path} {req.version}"]
                for name, value in req.headers:
                    if name.lower().startswith("proxy-") or name.lower() in (
                        "connection",
                        "host",
                        "keep-alive",
                    ):
                        continue
                    lines.append(f"{name}: {value}")
                # The host decided on, not one the client chose: a shared
                # server would otherwise serve any of its virtual hosts.
                lines.append(f"Host: {norm}" + ("" if port == 80 else f":{port}"))
                lines.append("Connection: close")
                upstream.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
                # Exactly the declared body, and then nothing more from the
                # client: a request pipelined behind it was never decided.
                body = req.rest[:length]
                remaining = length - len(body)
                if body:
                    upstream.sendall(body)
                while remaining > 0:
                    chunk = conn.recv(min(65536, remaining))
                    if not chunk:
                        break
                    upstream.sendall(chunk)
                    remaining -= len(chunk)
                _relay(upstream, conn)
                return
            if req.rest:
                upstream.sendall(req.rest)
        except OSError:
            conn.close()
            upstream.close()
            return
        _pipe(conn, upstream)

    def close(self) -> None:
        self._stop.set()
        for s in (self._unix, self._tcp):
            try:
                s.close()
            except OSError:
                pass
        try:
            self.unix_path.unlink()
        except OSError:
            pass


class Egress:
    """Every run's proxy, deciding through ``service``."""

    def __init__(self, service: "GuardService", socket_dir: Path) -> None:
        self.service = service
        self.socket_dir = Path(socket_dir)
        self._lock = threading.Lock()
        self._proxies: dict[str, SessionProxy] = {}

    @staticmethod
    def check_session(session: Any) -> str:
        if not isinstance(session, str) or not _SESSION.match(session):
            raise ValueError("`session` must be 1-128 of [A-Za-z0-9._:-]")
        return session

    def open(self, session: str) -> dict[str, Any]:
        session = self.check_session(session)
        with self._lock:
            proxy = self._proxies.get(session)
            if proxy is None:
                self.socket_dir.mkdir(parents=True, exist_ok=True)
                proxy = self._proxies[session] = SessionProxy(
                    self, session, self.socket_dir
                )
        return {"unix": str(proxy.unix_path), "port": proxy.port, "session": session}

    def close(self, session: str) -> dict[str, Any]:
        session = self.check_session(session)
        with self._lock:
            proxy = self._proxies.pop(session, None)
        if proxy is not None:
            proxy.close()
        return {"closed": proxy is not None}

    def close_all(self) -> None:
        with self._lock:
            proxies, self._proxies = list(self._proxies.values()), {}
        for p in proxies:
            p.close()

    def decide(
        self, session: str, *, host: str, port: int, method: str, url: str | None
    ) -> tuple[bool, str]:
        """``(allowed, reason)`` for one connection."""
        allow = self.service.config.current().egress_allow
        has_library = self.service.store.resolve("_egress") is not None
        if not allow and not has_library:
            return False, (
                "Sponsio egress: no egress policy, so every connection is refused. "
                "Add hosts to `egress.allow` in daemon.yaml, or contracts on "
                "`egress` in the _egress library."
            )
        if allow and not host_allowed(host, port, allow):
            return False, f"Sponsio egress: {host}:{port} is not in egress.allow"
        args: dict[str, Any] = {"host": host, "port": port, "method": method}
        if url is not None:
            args["url"] = url
        if not has_library:
            # Allowed by the list. With no contract on `egress` there is
            # nothing that would read a history of connections.
            return True, ""
        outcome = self.service.decide(self._event(session, args), library="_egress")
        return outcome.allowed, outcome.reason or ""

    @staticmethod
    def _event(session: str, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "host": HOST,
            "session_id": session,
            "hook_event_name": "PreToolUse",
            "tool_name": TOOL,
            "tool_input": args,
        }
