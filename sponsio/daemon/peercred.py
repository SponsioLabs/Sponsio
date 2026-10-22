"""Who is on the other end of a Unix socket, as the kernel says.

The daemon tags every request with its peer's uid, gid and pid, read
from the kernel at accept time, never from anything the client sends:
``SO_PEERCRED`` on Linux, ``LOCAL_PEERCRED`` (a ``struct xucred``) and
``LOCAL_PEERPID`` on macOS. The admin socket's policy decides on that
identity, and the audit log records it.
"""

from __future__ import annotations

import socket
import struct
import sys
from dataclasses import asdict, dataclass

_SOL_LOCAL = 0
"""macOS ``SOL_LOCAL``; Python does not export it."""
_LOCAL_PEERPID = 0x002
_XUCRED = struct.Struct("=IIhxx16I")
"""``struct xucred``: version, uid, ngroups, groups[16] (groups[0] is
the effective gid)."""
_UCRED = struct.Struct("=iII")
"""Linux ``struct ucred``: pid, uid, gid."""


@dataclass(frozen=True)
class Peer:
    uid: int
    gid: int
    pid: int | None
    """``None`` where the platform cannot say."""

    def as_dict(self) -> dict:
        return asdict(self)


class PeerCredError(OSError):
    """The kernel would not say who the peer is."""


def get_peer(sock: socket.socket) -> Peer:
    """The identity of the process connected to ``sock`` (a connected
    ``AF_UNIX`` stream socket)."""
    if sys.platform.startswith("linux"):
        so_peercred = getattr(socket, "SO_PEERCRED", 17)
        raw = sock.getsockopt(socket.SOL_SOCKET, so_peercred, _UCRED.size)
        pid, uid, gid = _UCRED.unpack(raw)
        return Peer(uid=uid, gid=gid, pid=pid)
    if sys.platform == "darwin":
        local_peercred = getattr(socket, "LOCAL_PEERCRED", 0x001)
        raw = sock.getsockopt(_SOL_LOCAL, local_peercred, _XUCRED.size)
        fields = _XUCRED.unpack(raw)
        uid, ngroups, groups = fields[1], fields[2], fields[3:]
        gid = groups[0] if ngroups > 0 else -1
        try:
            raw_pid = sock.getsockopt(_SOL_LOCAL, _LOCAL_PEERPID, 4)
            pid: int | None = struct.unpack("=i", raw_pid)[0]
        except OSError:
            pid = None
        return Peer(uid=uid, gid=gid, pid=pid)
    raise PeerCredError(f"peer credentials are not supported on {sys.platform}")
