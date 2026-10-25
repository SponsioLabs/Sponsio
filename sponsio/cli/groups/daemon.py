"""``sponsio daemon`` — control-daemon group (run / ping / status)."""

from __future__ import annotations

import os
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
@click.option(
    "--state-dir",
    "state_dir",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Where the daemon keeps session traces (write-ahead log). "
        "Default ~/.sponsio/state."
    ),
)
@click.option(
    "--plugin-root",
    "plugin_root",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "The contract-library root the daemon serves from. Fixed at start; "
        "default $SPONSIO_PLUGIN_ROOT or ~/.sponsio/plugins."
    ),
)
@click.option(
    "--detach",
    is_flag=True,
    help=(
        "Start the daemon in the background and return once it answers "
        "(or another one already does). Output goes to "
        "<state-dir>/daemon.log."
    ),
)
@click.option(
    "--watch",
    is_flag=True,
    help=(
        "Apply every rule change on disk at once, weakening ones too, "
        "instead of holding those until `sponsio rules reload`. For "
        "working on Sponsio's own rules; every change is still audited."
    ),
)
def daemon_run(
    socket_path_arg: Path | None,
    socket_mode: str,
    state_dir: Path | None,
    plugin_root: Path | None,
    detach: bool,
    watch: bool,
):
    """Start the daemon.  Foreground by default; blocks until SIGINT/SIGTERM.

    Serves rule writes (``plugin.append``) and hook evaluation
    (``guard.check`` / ``guard.observe``): libraries are compiled once
    per change and session traces live in memory with a write-ahead log
    under the state dir, so a restart keeps every live conversation.

    One daemon per state dir: the daemon holds ``<state-dir>/daemon.lock``
    while it runs, and a second ``daemon run`` finds the lock taken (or
    a daemon answering on the socket) and exits 0 without touching
    anything. Hooks that find no daemon start one with ``--detach``;
    when several do so at once, one wins the lock and the rest leave.
    """
    from sponsio.daemon import default_socket_path
    from sponsio.daemon.guard_service import GuardService, default_state_dir

    path = (socket_path_arg or default_socket_path()).expanduser()
    try:
        mode = int(socket_mode, 8)
    except ValueError as e:
        raise click.ClickException(
            f"invalid --mode {socket_mode!r}: must be octal like 0600 / 0666"
        ) from e
    resolved_state = (state_dir or default_state_dir()).expanduser()

    if detach:
        sys.exit(_run_detached(path, socket_mode, resolved_state, plugin_root, watch))

    from sponsio.daemon import audit
    from sponsio.daemon.handlers import register_default_handlers
    from sponsio.daemon.server import DaemonAlreadyRunning, serve_forever

    # Not $SPONSIO_PLUGIN_ROOT: the daemon reads no environment variable
    # for a decision. daemon.yaml's plugin_root, when set, wins over this.
    service = GuardService(
        root=(plugin_root or Path.home() / ".sponsio" / "plugins").expanduser(),
        state_dir=resolved_state,
        watch=watch,
        run_dir=path.parent,
    )

    exporter = None
    cfg = service.config.current()
    if cfg.audit_otlp_endpoint:
        import socket as _socket

        from sponsio.daemon.audit_export import AuditOtlpExporter, stream_id

        host = _socket.gethostname()
        exporter = AuditOtlpExporter(
            cfg.audit_otlp_endpoint,
            headers=cfg.audit_otlp_headers,
            host=host,
            stream=stream_id(host, service.state_dir),
        )
        audit.set_exporter(service.state_dir, exporter.export)

    def _record_start(server) -> None:
        prior = server.previous_pid
        if prior is not None and prior != os.getpid() and not audit.pid_alive(prior):
            audit.append(
                service.state_dir,
                audit.DAEMON_RESTART,
                previous_pid=prior,
                socket=str(path),
            )
        else:
            audit.append(service.state_dir, audit.DAEMON_START, socket=str(path))
        click.echo(f"sponsio daemon listening at {path} (mode {socket_mode})")
        click.echo(f"  admin:     {server.admin_socket_path} (mode 0600)")
        click.echo(f"  config:    {service.state_dir / 'daemon.yaml'}")
        click.echo(f"  libraries: {service.store.root}")
        click.echo(f"  state:     {service.state_dir}")
        if watch:
            click.echo("  --watch:   every rule change applies at once (audited)")
        # Rule files, their packs and the engine modules, every 2 s.
        service.start_watch()
        cfg = service.config.current()
        if cfg.cloud_buckets or cfg.cloud_proposals:
            service.start_cloud_verify()

    try:
        serve_forever(
            path,
            handler_registry=lambda server: register_default_handlers(
                server, guard_service=service
            ),
            socket_mode=mode,
            lock_path=service.state_dir / "daemon.lock",
            on_started=_record_start,
        )
    except DaemonAlreadyRunning as e:
        click.echo(f"sponsio daemon already running: {e}")
        return
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    finally:
        service.stop_watch()
        service.stop_cloud_verify()
        service.close_gateway()
    audit.append(service.state_dir, audit.DAEMON_STOP, socket=str(path))
    if exporter is not None:
        audit.set_exporter(service.state_dir, None)
        exporter.close()
    click.echo("daemon stopped")


def _run_detached(
    path: Path,
    socket_mode: str,
    state_dir: Path,
    plugin_root: Path | None,
    watch: bool = False,
) -> int:
    """Spawn a foreground ``daemon run`` in its own session and wait for
    the socket to answer. Exit 0 when a daemon answers (ours or one
    that was already there), 1 otherwise."""
    import subprocess

    from sponsio.daemon.client import daemon_is_running, wait_for_daemon

    if daemon_is_running(path):
        click.echo(f"sponsio daemon already running at {path}")
        return 0
    state_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(state_dir, 0o700)
    except OSError:
        pass
    log_path = state_dir / "daemon.log"
    cmd = [
        sys.executable,
        "-m",
        "sponsio.cli",
        "daemon",
        "run",
        "--socket",
        str(path),
        "--mode",
        socket_mode,
        "--state-dir",
        str(state_dir),
    ]
    if plugin_root is not None:
        cmd += ["--plugin-root", str(plugin_root)]
    if watch:
        cmd.append("--watch")
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_fd,
            stderr=log_fd,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        os.close(log_fd)
    deadline = 5.0
    if wait_for_daemon(path, timeout_s=deadline):
        pid = _lock_holder(state_dir)
        who = f"pid {pid}" if pid else "another instance"
        click.echo(f"sponsio daemon running at {path} ({who}); log: {log_path}")
        return 0
    rc = proc.poll()
    click.echo(
        f"sponsio daemon did not answer at {path} within {deadline:g}s"
        + (f" (exit {rc})" if rc is not None else "")
        + f"; see {log_path}",
        err=True,
    )
    return 1


def _lock_holder(state_dir: Path) -> int | None:
    """The pid in ``<state-dir>/daemon.lock``, if any."""
    try:
        text = (state_dir / "daemon.lock").read_text(encoding="ascii").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


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

    from sponsio.daemon.guard_service import default_state_dir

    path = socket_path_arg or default_socket_path()
    running = daemon_is_running(path)
    click.echo(f"socket: {path}")
    click.echo(f"running: {'yes' if running else 'no'}")
    pid = _lock_holder(default_state_dir())
    if running and pid:
        click.echo(f"pid: {pid}")
    if not running:
        click.echo("\nStart the daemon with: sponsio daemon run --detach")
        sys.exit(1)
