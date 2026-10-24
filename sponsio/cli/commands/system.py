"""``sponsio install --system`` and ``sponsio uninstall --system`` (Tier 2).

See :mod:`sponsio.system_install`. Run them with ``sudo`` from your own
terminal; ``--dry-run`` prints every step without doing any.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import click

from sponsio.cli.app import cli


def _require_root(dry_run: bool) -> None:
    if not dry_run and os.geteuid() != 0:
        raise click.ClickException(
            "a system install changes root-owned paths: run it with sudo "
            "(or add --dry-run to see what it would do)"
        )


def _invoking_home() -> Path | None:
    user = os.environ.get("SUDO_USER")
    if not user:
        return Path.home()
    import pwd

    try:
        return Path(pwd.getpwnam(user).pw_dir)
    except KeyError:
        return None


@cli.command(name="install")
@click.option(
    "--system", "system", is_flag=True, help="Install for the whole machine (Tier 2)."
)
@click.option(
    "--host",
    "hosts",
    multiple=True,
    type=click.Choice(["claude-code", "cursor"]),
    help="Host to wire through its managed settings (repeatable; default claude-code).",
)
@click.option(
    "--python", "python", default=None, help="A root-owned Python 3.10+ for the engine."
)
@click.option(
    "--package",
    default=None,
    help="What to pip-install into the engine (a version spec, a wheel or a "
    "source directory). Default: this version of sponsio, with signing.",
)
@click.option(
    "--allow-user-hooks",
    is_flag=True,
    help="Let user and project hooks keep loading. Doctor then reports Tier 2 (degraded).",
)
@click.option("--no-migrate", is_flag=True, help="Do not import ~/.sponsio/plugins.")
@click.option("--dry-run", is_flag=True, help="Print every step; change nothing.")
def install_cmd(
    system: bool,
    hosts: tuple[str, ...],
    python: str | None,
    package: str | None,
    allow_user_hooks: bool,
    no_migrate: bool,
    dry_run: bool,
):
    """Install Sponsio for the whole machine, out of the agent's reach.

    Creates the _sponsio user, a root-owned engine and shims, the daemon's
    private state and its service, imports your contract libraries, and
    writes the host's managed settings. For one host's hooks under your
    own user, use `sponsio host install <host>`.
    """
    from sponsio.system_install import (
        Executor,
        InstallError,
        InstallOptions,
        detect_layout,
        install,
    )

    if not system:
        raise click.UsageError(
            "use --system for a machine-wide install, or `sponsio host install "
            "<host>` for your own user"
        )
    _require_root(dry_run)
    try:
        layout = detect_layout()
        ex = Executor(layout, dry_run=dry_run, echo=click.echo)
        manifest = install(
            ex,
            InstallOptions(
                hosts=hosts or ("claude-code",),
                python=python,
                package=package,
                allow_user_hooks=allow_user_hooks,
                migrate_from=None if no_migrate else _invoking_home(),
            ),
        )
    except InstallError as e:
        raise click.ClickException(str(e)) from e
    if dry_run:
        click.secho("dry run: nothing was changed", fg="yellow")
        return
    from sponsio.daemon.client import daemon_is_running

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not daemon_is_running(layout.socket):
        time.sleep(0.25)
    if daemon_is_running(layout.socket):
        click.secho(f"daemon answering at {layout.socket}", fg="green")
    else:
        click.secho(
            f"the daemon is not answering at {layout.socket} yet; see "
            f"{layout.logs / 'daemon.log'}",
            fg="red",
        )
    click.echo(f"hosts: {', '.join(manifest['hosts'])}")
    click.echo("next, as your own user: `sponsio doctor --tier`")


@cli.command(name="uninstall")
@click.option(
    "--system", "system", is_flag=True, help="Remove the machine-wide install."
)
@click.option(
    "--keep-state", is_flag=True, help="Keep the rules, audit log and history."
)
@click.option("--dry-run", is_flag=True, help="Print every step; change nothing.")
def uninstall_cmd(system: bool, keep_state: bool, dry_run: bool):
    """Remove the system install: service, engine, shims, settings, user."""
    from sponsio.system_install import Executor, InstallError, detect_layout, uninstall

    if not system:
        raise click.UsageError(
            "use --system to remove the machine-wide install, or `sponsio host "
            "uninstall <host>` for your own user"
        )
    _require_root(dry_run)
    try:
        ex = Executor(detect_layout(), dry_run=dry_run, echo=click.echo)
        notes = uninstall(ex, keep_state=keep_state)
    except InstallError as e:
        raise click.ClickException(str(e)) from e
    for note in notes:
        click.echo(note)
    if dry_run:
        click.secho("dry run: nothing was changed", fg="yellow")
