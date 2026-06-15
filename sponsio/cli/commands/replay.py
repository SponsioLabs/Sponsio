"""``sponsio replay`` — re-render a recorded session."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from sponsio.cli.app import cli


@cli.command()
@click.argument("session", required=False)
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="YAML config (for the contracts-armed table; falls back to bare table).",
)
@click.option(
    "--agent",
    "agent_id_opt",
    default=None,
    help="Override the agent id derived from the session log path.",
)
@click.option(
    "--list",
    "list_only",
    is_flag=True,
    default=False,
    help="List available sessions and exit.",
)
def replay(
    session: str | None,
    config_path: str | None,
    agent_id_opt: str | None,
    list_only: bool,
):
    """Re-render a recorded session in the v1 mockup form.

    \b
    Examples:
      sponsio replay sess_4f2a            # by short ID from the session view
      sponsio replay 20260501_120000_999  # by filename stem
      sponsio replay /path/to/log.jsonl   # by direct path
      sponsio replay --list               # browse available sessions

    Reads ``~/.sponsio/sessions/<agent>/*.jsonl`` and rebuilds the
    AgentTurnSpan tree the live monitor would have produced, then
    feeds it through the same renderer the session view uses.

    Pass ``--config`` to also render the "contracts armed" table from
    the YAML. without it, only contracts mentioned in the trace are
    surfaced.
    """

    from rich.console import Console

    from sponsio.render.replay import (
        find_session_file,
        list_sessions,
        load_replay,
    )
    from sponsio.render.session_view import render_session

    console = Console(file=sys.stderr, soft_wrap=True, highlight=False)

    if list_only:
        sessions = list_sessions()
        if not sessions:
            click.echo("No sessions found in ~/.sponsio/sessions/.", err=True)
            return
        click.echo("Available sessions (most recent first):", err=True)
        for s in sessions:
            click.echo(
                f"  {s['session_id']}   agent={s['agent_id']:<24} "
                f"{s['size_bytes']:>8} bytes   {s['stem']}",
                err=True,
            )
        return

    if not session:
        click.echo(
            click.style("Error: ", fg="red")
            + "missing SESSION arg. Try `sponsio replay --list` to browse.",
            err=True,
        )
        raise SystemExit(2)

    path, agent_id = find_session_file(session)
    if path is None:
        click.echo(
            click.style("Error: ", fg="red")
            + f"no session matched {session!r}. Try `sponsio replay --list`.",
            err=True,
        )
        raise SystemExit(2)

    turn_spans, log_agent_id = load_replay(path)
    if not turn_spans:
        click.echo(
            click.style("Note: ", fg="yellow") + f"{path} has no events.",
            err=True,
        )
        return

    contracts: list = []
    final_agent_id = agent_id_opt or agent_id or log_agent_id or "(unknown)"
    cfg_path: Path | None = (
        Path(config_path)
        if config_path
        else (
            Path(os.environ["SPONSIO_CONFIG"])
            if os.environ.get("SPONSIO_CONFIG")
            else (Path("sponsio.yaml") if Path("sponsio.yaml").is_file() else None)
        )
    )
    if cfg_path is not None:
        try:
            from sponsio.config import config_to_guard_kwargs, load_config
            from sponsio.models.agent import Agent
            from sponsio.models.contract import make_contracts

            cfg = load_config(str(cfg_path))
            cfg_agent = (
                final_agent_id
                if final_agent_id in cfg.agents
                else next(iter(cfg.agents), None)
            )
            if cfg_agent:
                kw = config_to_guard_kwargs(cfg, cfg_agent)
                contracts = make_contracts(
                    agent=Agent(id=cfg_agent),
                    contracts=kw.get("contracts") or [],
                )
        except Exception as exc:
            click.echo(
                click.style("Warning: ", fg="yellow")
                + f"could not load contracts from {cfg_path}: {exc}",
                err=True,
            )

    render_session(
        console=console,
        agent_id=final_agent_id,
        mode="replay",
        contracts=contracts,
        turn_spans=turn_spans,
        session_id=session if session.startswith("sess_") else None,
    )
