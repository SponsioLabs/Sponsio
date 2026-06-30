"""``sponsio explain`` — show a contract's source / formula / last violation."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click

from sponsio.cli.app import cli


@cli.command()
@click.argument("query")
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="YAML config (default: ./sponsio.yaml or $SPONSIO_CONFIG).",
)
@click.option(
    "--agent",
    "agent_id",
    default=None,
    help="When the config has multiple agents, pick one.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"], case_sensitive=False),
    default="text",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--no-color",
    is_flag=True,
    default=False,
    help="Disable Rich color output (text mode only).",
)
def explain(
    query: str,
    config_path: str | None,
    agent_id: str | None,
    fmt: str,
    no_color: bool,
):
    """Explain a contract. source, compiled formula, last violation.

    \b
    Examples:
      sponsio explain C1                       # by alias from the session view
      sponsio explain "code freeze"            # by substring of the desc
      sponsio explain C1 --format json         # machine-readable

    The contract is resolved against the YAML config (default
    ``./sponsio.yaml`` or ``$SPONSIO_CONFIG``). Pass ``--agent`` if the
    config has multiple agents.

    Output covers what's structurally inferable from the contract +
    Sponsio's local session log:
      - the assume / enforce pattern + arguments as written
      - the compiled LTL form via ``formulas.nl_gen.formula_to_nl``
      - the most recent BLOCKED / OBSERVED event for this contract
        (scanning ``~/.sponsio/sessions/<agent>/*.jsonl``)
      - generic resolution hints based on pattern shape

    Richer overlays (LLM-driven contextual fix hints, cross-trace
    pattern stats) are an extension point not part of this build.
    """

    from sponsio.config import load_config, config_to_guard_kwargs
    from sponsio.models.agent import Agent
    from sponsio.models.contract import make_contracts
    from sponsio.render.explain import (
        explain_to_dict,
        find_last_violation,
        render_explain,
        resolve_contract,
    )

    # Resolve config path: --config > $SPONSIO_CONFIG > ./sponsio.yaml.
    cfg_path: Path | None = (
        Path(config_path)
        if config_path
        else (
            Path(os.environ["SPONSIO_CONFIG"])
            if os.environ.get("SPONSIO_CONFIG")
            else (Path("sponsio.yaml") if Path("sponsio.yaml").is_file() else None)
        )
    )
    if cfg_path is None:
        click.echo(
            click.style("Error: ", fg="red")
            + "no config found. Pass --config or create ./sponsio.yaml.",
            err=True,
        )
        raise SystemExit(2)

    try:
        config = load_config(str(cfg_path))
    except Exception as exc:
        click.echo(click.style(f"Error loading {cfg_path}: {exc}", fg="red"), err=True)
        raise SystemExit(2) from exc

    if agent_id is None:
        if len(config.agents) != 1:
            click.echo(
                click.style("Error: ", fg="red")
                + f"config has {len(config.agents)} agents. pass --agent to disambiguate "
                + f"(available: {', '.join(config.agents)})",
                err=True,
            )
            raise SystemExit(2)
        agent_id = next(iter(config.agents))
    elif agent_id not in config.agents:
        click.echo(
            click.style("Error: ", fg="red")
            + f"agent {agent_id!r} not in config (available: {', '.join(config.agents)})",
            err=True,
        )
        raise SystemExit(2)

    kw = config_to_guard_kwargs(config, agent_id)
    contracts = make_contracts(
        agent=Agent(id=agent_id), contracts=kw.get("contracts") or []
    )

    if not contracts:
        click.echo(
            click.style("Error: ", fg="red")
            + f"no contracts compiled for agent {agent_id!r}.",
            err=True,
        )
        raise SystemExit(2)

    contract, idx = resolve_contract(query, contracts)
    if contract is None:
        # Show the catalog as a hint.
        click.echo(
            click.style("Error: ", fg="red")
            + f"no contract matched {query!r}. Available:",
            err=True,
        )
        for i, c in enumerate(contracts):
            click.echo(f"  C{i + 1}  {getattr(c, 'desc', '') or '(unnamed)'}", err=True)
        raise SystemExit(2)

    last = find_last_violation(getattr(contract, "desc", "") or "")

    if fmt.lower() == "json":
        click.echo(
            json.dumps(
                explain_to_dict(contract, idx, last_violation=last),
                indent=2,
                default=str,
            )
        )
        return

    from rich.console import Console

    console = Console(
        file=sys.stderr,
        soft_wrap=True,
        highlight=False,
        color_system=None if no_color else "auto",
        force_terminal=False if no_color else None,
    )
    render_explain(
        console=console,
        contract=contract,
        index=idx,
        last_violation=last,
        config_path=cfg_path,
    )
