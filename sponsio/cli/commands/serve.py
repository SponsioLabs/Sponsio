"""``sponsio serve`` — dashboard server stub."""

from __future__ import annotations


import click

from sponsio.cli.app import cli
from sponsio.constants import DASHBOARD_DEFAULT_PORT


@cli.command()
@click.option("--host", default="127.0.0.1", help="Bind address")
@click.option("--port", "-p", default=DASHBOARD_DEFAULT_PORT, type=int)
@click.option("--dev", is_flag=True)
def serve(host: str, port: int, dev: bool):
    """Start the Sponsio dashboard server.

    This build ships the contract runtime + CLI; the long-lived HTTP
    backend that serves the web dashboard is not part of this
    distribution. To inspect contract activity locally, use:

    \b
        sponsio host trace --follow      # live coloured stream
        sponsio report --since 1h        # session log summary
        sponsio replay <session>         # re-render a recorded session
        sponsio export-sessions --to ... # ship audit to your collector
    """
    click.echo(
        click.style("sponsio serve", bold=True)
        + ": the dashboard server is not part of this distribution "
        "(the engine ships CLI + runtime only).\n"
        "  sponsio host trace --follow  # live alternative\n"
        "  sponsio replay <session>     # re-render a recorded session view\n"
        "  sponsio report --since 1h    # session-log summary\n",
        err=True,
    )
    raise SystemExit(2)
