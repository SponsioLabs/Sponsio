"""``sponsio daemon`` — control-daemon group (run / ping / status)."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from sponsio.cli.app import cli


@cli.group()
def daemon():
    """Sponsio control daemon. privileged-process side of the IPC split.

    The daemon owns the host bucket / per-plugin yaml files and is the
    only entity the host agent can reach to write them.  Running as a
    separate process (and ideally a separate UID under launchd /
    systemd) makes self-modify protection an OS-level guarantee instead
    of a regex-on-tool-args guarantee.

    Subcommands:

    \b
    * ``sponsio daemon run`` . start the daemon in the foreground
      (used by launchd / systemd plists, or by hand for dev work).
    * ``sponsio daemon ping``. round-trip health check.
    * ``sponsio daemon status``. show socket path + reachability.
    """


@daemon.command(name="run")
@click.option(
    "--socket",
    "socket_path_arg",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Override the Unix socket path (default: $SPONSIO_DAEMON_SOCKET, "
        "/var/run/sponsio.sock if writable, else ~/.sponsio/sponsio.sock)."
    ),
)
@click.option(
    "--mode",
    "socket_mode",
    type=str,
    default="0600",
    help="chmod for the socket file (octal). Default 0600 keeps it owner-only.",
)
def daemon_run(socket_path_arg: Path | None, socket_mode: str):
    """Start the daemon in the foreground.  Blocks until SIGINT/SIGTERM."""
    from sponsio.daemon import default_socket_path
    from sponsio.daemon.handlers import register_default_handlers
    from sponsio.daemon.server import serve_forever

    path = socket_path_arg or default_socket_path()
    try:
        mode = int(socket_mode, 8)
    except ValueError as e:
        raise click.ClickException(
            f"invalid --mode {socket_mode!r}: must be octal like 0600 / 0666"
        ) from e
    click.echo(f"sponsio daemon listening at {path} (mode {socket_mode})")
    try:
        serve_forever(
            path,
            handler_registry=register_default_handlers,
            socket_mode=mode,
        )
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    click.echo("daemon stopped")


@daemon.command(name="ping")
@click.option(
    "--socket",
    "socket_path_arg",
    type=click.Path(path_type=Path),
    default=None,
    help="Override the daemon socket path.",
)
@click.option(
    "--echo",
    "echo_value",
    default="ping",
    help="Value to round-trip through the daemon.",
)
def daemon_ping(socket_path_arg: Path | None, echo_value: str):
    """Round-trip a ping RPC; print pid + version on success."""
    from sponsio.daemon import DaemonClient, DaemonError

    client = DaemonClient(socket_path=socket_path_arg)
    try:
        result = client.call("ping", {"echo": echo_value})
    except DaemonError as e:
        raise click.ClickException(f"{e} (code={e.code})") from e
    click.echo(
        f"✓ pong from {client.socket_path} "
        f"(pid={result['pid']}, version={result['version']}, echo={result['echo']!r})"
    )


@daemon.command(name="status")
@click.option(
    "--socket",
    "socket_path_arg",
    type=click.Path(path_type=Path),
    default=None,
    help="Override the daemon socket path.",
)
def daemon_status(socket_path_arg: Path | None):
    """Show the resolved socket path and whether the daemon answers."""
    from sponsio.daemon import default_socket_path
    from sponsio.daemon.client import daemon_is_running

    path = socket_path_arg or default_socket_path()
    running = daemon_is_running(path)
    click.echo(f"socket: {path}")
    click.echo(f"running: {'yes' if running else 'no'}")
    if not running:
        click.echo("\nStart the daemon with: sponsio daemon run")
        sys.exit(1)
