"""The root Sponsio CLI group.

Lives in its own module so every command and group module can register
itself on the shared group via ``from sponsio.cli.app import cli`` /
``@cli.command()`` without importing the whole :mod:`sponsio.cli`
package (which would be circular).
"""

from __future__ import annotations

import click

from sponsio import __version__


@click.group()
@click.version_option(version=__version__, prog_name="sponsio")
def cli():
    """Sponsio. the contract layer for LLM agent systems."""
