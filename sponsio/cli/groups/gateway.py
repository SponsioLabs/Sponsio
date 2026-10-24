"""``sponsio gateway``: the MCP gateway (Tier 3).

The daemon runs the upstream MCP servers listed in
``<state-dir>/gateway.yaml``, with their credentials, and the agent's
host is given one MCP server, the gateway shim. See
:mod:`sponsio.daemon.gateway`.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import click

from sponsio.cli.app import cli


@cli.group()
def gateway():
    """MCP gateway: the agent reaches its MCP tools only through Sponsio."""


@gateway.command(name="command")
@click.option(
    "--socket",
    "socket_path",
    type=click.Path(path_type=Path),
    default=None,
    help="The daemon's agent socket (default ~/.sponsio/sponsio.sock).",
)
@click.option(
    "--state-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="The daemon's state dir, where gateway.yaml lives.",
)
@click.option(
    "--plugin-root",
    type=click.Path(path_type=Path),
    default=None,
    help="The contract-library root a daemon the shim starts serves.",
)
def gateway_command(
    socket_path: Path | None, state_dir: Path | None, plugin_root: Path | None
):
    """Print the MCP server entry for the host's MCP config.

    Put it under ``mcpServers`` in ``.mcp.json`` (Claude Code) or
    ``~/.cursor/mcp.json`` (Cursor), named ``sponsio``, and remove the
    servers it replaces: their credentials belong in gateway.yaml.
    """
    from sponsio.guard_stdin import GATEWAY_SERVER
    from sponsio.integrations.hosts import gateway_server_entry
    from sponsio.system_install import installed_layout

    lay = installed_layout()
    if lay is not None and not (socket_path or plugin_root or state_dir):
        # A system install: the root-owned shim, the engine's interpreter,
        # and a shim that never starts a daemon of its own.
        entry = gateway_server_entry(
            socket_path=lay.socket,
            plugin_root=lay.plugins,
            state_dir=lay.state,
            python=str(lay.python),
            shim=lay.mcp_shim,
            no_start=True,
        )
    else:
        entry = gateway_server_entry(
            socket_path=socket_path.expanduser() if socket_path else None,
            plugin_root=plugin_root.expanduser() if plugin_root else None,
            state_dir=state_dir.expanduser() if state_dir else None,
        )
    click.echo(json.dumps({"mcpServers": {GATEWAY_SERVER: entry}}, indent=2))


@gateway.command(name="tools")
@click.option(
    "--socket",
    "socket_path",
    type=click.Path(path_type=Path),
    default=None,
    help="The daemon's agent socket (default ~/.sponsio/sponsio.sock).",
)
def gateway_tools(socket_path: Path | None):
    """The tools a new session is offered, and servers that failed."""
    from sponsio.daemon.client import DaemonClient, DaemonError, default_socket_path

    client = DaemonClient(
        socket_path=(socket_path or default_socket_path()).expanduser(),
        timeout_s=120.0,
    )
    try:
        reply = client.call(
            "gateway.list_tools", {"session": f"cli-{uuid.uuid4().hex[:12]}"}
        )
    except DaemonError as e:
        raise click.ClickException(f"{e} (code={e.code})") from e
    tools = reply.get("tools") or []
    for t in tools:
        click.echo(t.get("name", "?"))
    if not tools:
        click.echo("no tools offered (is gateway.yaml in the daemon's state dir?)")
    for server, err in sorted((reply.get("errors") or {}).items()):
        click.secho(f"{server}: {err}", fg="red", err=True)
