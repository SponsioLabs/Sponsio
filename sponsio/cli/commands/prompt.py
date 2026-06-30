"""``sponsio prompt`` — print an agent-facing workflow prompt."""

from __future__ import annotations


import click

from sponsio.cli.app import cli


@cli.command(name="prompt")
@click.argument(
    "flow",
    type=click.Choice(["onboard", "scan"]),
)
def cmd_prompt(flow: str):
    """Print the agent-facing prompt template for a sponsio workflow.

    Used by the ``sponsio`` skill (``W1``. initial setup, ``W2``.
    audit & refine) to drive the host agent through contract authoring
    without burning a separate LLM API call.

    Pair with the corresponding ``--emit-*`` flag:

    \b
        sponsio onboard . --emit-context     # structured input for prompt
        sponsio prompt onboard               # the prompt itself

    \b
        sponsio scan src/ --emit-context
        sponsio prompt scan

    The agent reads both, applies the prompt to the JSON in its own
    context, and writes the result via Edit/Write.  No
    ``UnifiedExtractor`` / API key needed for this path.
    """
    from importlib.resources import files

    pkg = files("sponsio.prompts")
    click.echo(pkg.joinpath(f"{flow}.md").read_text(encoding="utf-8"))
