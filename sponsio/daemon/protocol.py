"""IPC wire format for ``sponsio.daemon`` — length-prefixed JSON.

Why JSON and not msgpack: stdlib only, easy to debug with ``nc``, and
the payload sizes (a few KB at most) make the size overhead irrelevant.

Frame layout:

    ┌─────────────────────────┬─────────────────────────────────────┐
    │  length (uint32, BE)    │  utf-8 JSON body (length bytes)     │
    └─────────────────────────┴─────────────────────────────────────┘

The big-endian uint32 prefix lets the reader know exactly how many
bytes to consume before parsing — no need to scan for delimiters or
deal with partial JSON when a packet straddles the socket buffer.

Request:  ``{"method": str, "params": dict}``
Response: ``{"ok": bool, "result": Any}`` or ``{"ok": bool, "error": str, "code": str}``

Method names are flat strings (``"ping"``, ``"plugin.append"``,
``"plugin.show"``, ``"hooks.load"``).  The dotted convention groups
related operations without forcing a hierarchical dispatch.
"""

from __future__ import annotations

import json
import socket
import struct
from dataclasses import dataclass
from typing import Any


_LEN_PREFIX = struct.Struct(">I")  # 4-byte big-endian length
MAX_FRAME_BYTES = (
    16 * 1024 * 1024
)  # 16 MiB hard cap; keeps a runaway client from eating RAM


class FrameError(Exception):
    """Raised when a frame is malformed or exceeds the size cap."""


@dataclass(frozen=True)
class Request:
    method: str
    params: dict[str, Any]

    def to_json(self) -> bytes:
        return json.dumps(
            {"method": self.method, "params": self.params},
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes) -> "Request":
        obj = json.loads(data.decode("utf-8"))
        if not isinstance(obj, dict):
            raise FrameError(f"request must be a JSON object, got {type(obj).__name__}")
        method = obj.get("method")
        if not isinstance(method, str) or not method:
            raise FrameError("request missing non-empty `method`")
        params = obj.get("params", {})
        if not isinstance(params, dict):
            raise FrameError("request `params` must be an object")
        return cls(method=method, params=params)


@dataclass(frozen=True)
class Response:
    ok: bool
    result: Any = None
    error: str | None = None
    code: str | None = None

    @classmethod
    def success(cls, result: Any = None) -> "Response":
        return cls(ok=True, result=result)

    @classmethod
    def failure(cls, error: str, code: str = "error") -> "Response":
        return cls(ok=False, error=error, code=code)

    def to_json(self) -> bytes:
        if self.ok:
            payload: dict[str, Any] = {"ok": True, "result": self.result}
        else:
            payload = {"ok": False, "error": self.error, "code": self.code}
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes) -> "Response":
        obj = json.loads(data.decode("utf-8"))
        if not isinstance(obj, dict):
            raise FrameError(
                f"response must be a JSON object, got {type(obj).__name__}"
            )
        ok = bool(obj.get("ok"))
        if ok:
            return cls(ok=True, result=obj.get("result"))
        return cls(
            ok=False,
            error=str(obj.get("error", "")),
            code=str(obj.get("code", "error")),
        )


# ---------------------------------------------------------------------------
# Frame I/O — pair with any stream socket
# ---------------------------------------------------------------------------


def write_frame(sock: socket.socket, body: bytes) -> None:
    """Write a length-prefixed JSON frame to ``sock``.

    Refuses bodies larger than :data:`MAX_FRAME_BYTES` so a malformed
    or hostile peer can't push the daemon into an OOM.
    """
    if len(body) > MAX_FRAME_BYTES:
        raise FrameError(f"frame body {len(body)} bytes exceeds cap {MAX_FRAME_BYTES}")
    sock.sendall(_LEN_PREFIX.pack(len(body)) + body)


def read_frame(sock: socket.socket) -> bytes:
    """Read a length-prefixed JSON frame, return the JSON body bytes.

    Raises :class:`FrameError` on short reads, malformed length, or
    bodies exceeding the size cap.  Raises :class:`ConnectionError`
    when the peer closes mid-frame.
    """
    header = _read_exact(sock, 4)
    (length,) = _LEN_PREFIX.unpack(header)
    if length > MAX_FRAME_BYTES:
        raise FrameError(f"incoming frame {length} bytes exceeds cap {MAX_FRAME_BYTES}")
    body = _read_exact(sock, length)
    return body


def _read_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly ``n`` bytes from ``sock`` or raise."""
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError(
                f"peer closed after {n - remaining} of {n} expected bytes"
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
