"""Sponsio control daemon — privileged-process side of the IPC split.

The daemon owns the host bucket / per-plugin yaml files and is the only
entity the host agent can reach to write them. All write paths go
through ``DaemonServer`` running as a separate UID (when system-installed)
or under the user's UID with file mode 600 (dev mode); either way the
agent's tool surface (``Edit`` / ``Write`` / ``Bash``) cannot reach the
underlying inode without going through the IPC client.

This module is the substrate fix for the "regex is open-class but the
threat is closed-class" gap in ``sponsio:capability/self-modify``.
The regex pack stays as best-effort detection; ``DaemonServer`` is the
hard enforcement.

Two surfaces:

* :class:`DaemonServer` — Unix socket server, dispatches JSON-RPC
  requests to method handlers.  See :mod:`sponsio.daemon.server`.
* :class:`DaemonClient` — thin client used by ``sponsio plugin append``,
  ``sponsio plugin show``, and the host hook subprocess to talk to the
  daemon.  See :mod:`sponsio.daemon.client`.

The wire format is length-prefixed JSON over Unix domain sockets — no
extra deps, easy to debug, and a clean error model (every reply has
``ok: bool`` and either ``result`` or ``error``).
"""

from sponsio.daemon.client import DaemonClient, DaemonError, default_socket_path
from sponsio.daemon.protocol import Request, Response
from sponsio.daemon.server import DaemonServer, RpcHandler

__all__ = [
    "DaemonClient",
    "DaemonError",
    "DaemonServer",
    "Request",
    "Response",
    "RpcHandler",
    "default_socket_path",
]
