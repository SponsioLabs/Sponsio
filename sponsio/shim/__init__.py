"""The hook shim: the only Sponsio code a host runs per tool call.

:mod:`sponsio.shim.sponsio_shim` is a stdlib-only script. The installers
write a hook command that runs it under the installing interpreter with
``-I -S`` (no site, no environment-driven path), and it forwards the
hook's stdin to the daemon and the daemon's reply to the host. When the
daemon cannot be reached it denies. See :func:`shim_path`.
"""

from __future__ import annotations

from pathlib import Path


def shim_path() -> Path:
    """Absolute path of the shim script in this install."""
    return (Path(__file__).parent / "sponsio_shim.py").resolve()


def mcp_shim_path() -> Path:
    """Absolute path of the MCP gateway shim (:mod:`.sponsio_mcp`)."""
    return (Path(__file__).parent / "sponsio_mcp.py").resolve()


__all__ = ["mcp_shim_path", "shim_path"]
