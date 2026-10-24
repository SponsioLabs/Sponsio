"""``sponsio run --sandbox -- <agent command>`` (Tier 3).

See :mod:`sponsio.sandbox`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from sponsio.cli.app import cli


@cli.command(name="run", context_settings={"ignore_unknown_options": True})
@click.option(
    "--sandbox", "sandbox", is_flag=True, help="Run the agent in an OS sandbox."
)
@click.option(
    "--container",
    is_flag=True,
    help="The agent's container is the sandbox (deploy/tier3): only route the "
    "network through the egress proxy.",
)
@click.option(
    "--workspace",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="The one directory the agent may write (default: the current one).",
)
@click.option(
    "--writable",
    "writable",
    multiple=True,
    type=click.Path(path_type=Path),
    help="Another path the agent may write, such as its own state directory.",
)
@click.option(
    "--keep-env",
    "keep_env",
    multiple=True,
    help="An environment variable to keep although it looks like a credential.",
)
@click.option(
    "--hide-home", is_flag=True, help="Hide the home directory, not just protect it."
)
@click.option(
    "--socket",
    "socket_path",
    type=click.Path(path_type=Path),
    default=None,
    help="The daemon's agent socket.",
)
@click.argument("command", nargs=-1, type=click.UNPROCESSED, required=True)
def run_cmd(
    sandbox: bool,
    container: bool,
    workspace: Path | None,
    writable: tuple[Path, ...],
    keep_env: tuple[str, ...],
    hide_home: bool,
    socket_path: Path | None,
    command: tuple[str, ...],
):
    """Start an agent whose only way out is Sponsio.

    \b
      sponsio run --sandbox -- claude
      sponsio run --sandbox --writable ~/.claude -- claude

    The network is the daemon's egress proxy and nothing else; files are
    read-only outside the workspace; Sponsio's own state cannot be read;
    credentials are removed from the environment. Exits with the agent's
    status.
    """
    from sponsio.sandbox import SandboxError, run

    if not (sandbox or container):
        raise click.UsageError(
            "use `sponsio run --sandbox -- <command>`, or --container inside "
            "the Tier 3 container recipe"
        )
    try:
        code = run(
            list(command),
            workspace=(workspace or Path.cwd()).expanduser(),
            socket_path=socket_path,
            writable=[p.expanduser() for p in writable],
            keep_env=frozenset(keep_env),
            hide_home=hide_home,
            container=container,
            echo=lambda line: click.echo(line, err=True),
        )
    except SandboxError as e:
        raise click.ClickException(str(e)) from e
    sys.exit(code)
