"""``sponsio check`` — run contracts against a saved trace."""

from __future__ import annotations

import json
import sys

import click

from sponsio.cli._shared import (
    _resolve_entry,
)
from sponsio.cli.app import cli


@cli.command()
@click.option(
    "--trace",
    "-t",
    "trace_path",
    required=True,
    type=click.Path(exists=True),
    help=(
        "Trace file to check against. Accepts OTLP/JSON, OTLP JSONL, "
        "native Sponsio JSON/JSONL, and session JSONL. format is "
        "sniffed from content."
    ),
)
@click.argument("contracts", nargs=-1)
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=True),
    help="YAML config file (sponsio.yaml)",
)
@click.option("--agent", "-a", "agent_id", help="Agent ID (with --config)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def check(trace_path, contracts, config_path, agent_id, as_json):
    """Check contracts against an OTEL trace file.

    Examples:\n
        sponsio check --trace trace.json "tool `A` must precede `B`"\n
        sponsio check --trace trace.json --config sponsio.yaml --agent bot
    """
    from sponsio.formulas.evaluator import evaluate as eval_formula
    from sponsio.tracer.grounding import ground

    if config_path and contracts:
        click.echo(
            click.style(
                "Error: cannot use both --config and positional contracts", fg="red"
            )
        )
        sys.exit(1)

    if agent_id and not config_path:
        click.echo(click.style("Error: --agent requires --config", fg="red"))
        sys.exit(1)

    if not config_path and not contracts:
        click.echo("Usage: sponsio check --trace FILE [CONTRACTS...] or --config FILE")
        sys.exit(1)

    # Load trace(s) through the unified loader so this command handles
    # the same formats as `sponsio scan --trace`.  For multi-trace
    # files (native array, native JSONL), we concatenate events into
    # one logical trace since `check` is a single-trace tool.
    from sponsio.discovery.loaders import load_trace
    from sponsio.models.trace import Trace as _Trace

    try:
        loaded = load_trace(trace_path)
    except (FileNotFoundError, IsADirectoryError, ValueError) as e:
        # Symmetric error handling with `sponsio scan -t`: any user-input
        # problem surfaces as a friendly red line rather than a traceback.
        # ``click.Path(exists=True)`` already blocks the FileNotFound case
        # for direct args, but keeping it here protects future changes
        # (e.g. accepting globs) from regressing.
        click.echo(click.style(f"Error: {e}", fg="red"))
        sys.exit(1)

    if len(loaded) == 1:
        trace = loaded[0]
    else:
        # Flatten. renumber ts so ordering is preserved across files.
        merged_events: list = []
        for t in loaded:
            for ev in t.events:
                merged_events.append(ev)
        trace = _Trace(events=merged_events)
        click.echo(
            click.style(
                f"  note: merged {len(loaded)} traces into one for evaluation",
                fg="cyan",
                dim=True,
            ),
            err=True,
        )

    if not trace.events:
        click.echo(click.style("Warning: trace is empty (no spans found)", fg="yellow"))
        sys.exit(0)

    # Collect contracts (flatten ContractEntry list for this command; per-contract
    # A->E gating is still handled in the evaluation loop below).
    assumptions: list = []
    guarantees: list = []
    check_agent = agent_id or "(inline)"

    if config_path:
        from sponsio.config import load_config

        config = load_config(config_path)
        if not agent_id:
            if len(config.agents) == 1:
                agent_id = next(iter(config.agents))
            else:
                click.echo(
                    click.style(
                        f"Error: multiple agents in config ({list(config.agents.keys())}), "
                        "use --agent to specify",
                        fg="red",
                    )
                )
                sys.exit(1)
        check_agent = agent_id
        ac = config.agents[agent_id]
        for ce in ac.contracts:
            if ce.assumption is not None:
                if isinstance(ce.assumption, list):
                    assumptions.extend(ce.assumption)
                else:
                    assumptions.append(ce.assumption)
            if ce.guarantee is not None:
                if isinstance(ce.guarantee, list):
                    guarantees.extend(ce.guarantee)
                else:
                    guarantees.append(ce.guarantee)
    else:
        guarantees = list(contracts)

    if not as_json:
        click.echo()
        click.echo(click.style(f"Checking: {check_agent}", bold=True))
        click.echo(
            click.style(f"  Trace: {trace_path} ({len(trace.events)} events)", dim=True)
        )
        click.echo()

    # Ground the trace
    valuations = ground(trace)

    # Check assumptions
    results = []
    all_pass = True

    if assumptions:
        if not as_json:
            click.echo(click.style("  Assumptions:", dim=True))
        for entry in assumptions:
            nl, parsed = _resolve_entry(entry)
            if not parsed or not parsed.is_det:
                results.append(
                    {
                        "nl": nl,
                        "section": "assume",
                        "passed": False,
                        "note": "unparseable",
                    }
                )
                all_pass = False
                if not as_json:
                    icon = click.style("\u2717", fg="red")
                    click.echo(f"    {icon} {nl}  (unparseable)")
                continue

            holds = eval_formula(parsed.hard.formula, valuations)
            results.append({"nl": nl, "section": "assume", "passed": holds})
            if not holds:
                all_pass = False
            if not as_json:
                icon = (
                    click.style("\u2713", fg="green")
                    if holds
                    else click.style("\u2717", fg="red")
                )
                verdict = (
                    click.style("pass", fg="green")
                    if holds
                    else click.style("VIOLATED", fg="red")
                )
                click.echo(f"    {icon} {nl} \u2014 {verdict}")

    # Check guarantees
    if guarantees:
        if not as_json:
            click.echo(click.style("  Guarantees:", dim=True))
        for entry in guarantees:
            nl, parsed = _resolve_entry(entry)
            if not parsed or not parsed.is_det:
                results.append(
                    {
                        "nl": nl,
                        "section": "enforce",
                        "passed": False,
                        "note": "unparseable",
                    }
                )
                all_pass = False
                if not as_json:
                    icon = click.style("\u2717", fg="red")
                    click.echo(f"    {icon} {nl}  (unparseable)")
                continue

            holds = eval_formula(parsed.hard.formula, valuations)
            results.append({"nl": nl, "section": "enforce", "passed": holds})
            if not holds:
                all_pass = False
            if not as_json:
                icon = (
                    click.style("\u2713", fg="green")
                    if holds
                    else click.style("\u2717", fg="red")
                )
                verdict = (
                    click.style("pass", fg="green")
                    if holds
                    else click.style("VIOLATED", fg="red")
                )
                click.echo(f"    {icon} {nl} \u2014 {verdict}")

    # Summary
    if as_json:
        click.echo(
            json.dumps(
                {"agent": check_agent, "results": results, "all_pass": all_pass},
                indent=2,
            )
        )
    else:
        click.echo()
        total = len([r for r in results if "note" not in r])
        passed = len([r for r in results if r["passed"] and "note" not in r])
        if all_pass:
            click.echo(
                click.style(f"  \u2713 All {total} contract(s) satisfied", fg="green")
            )
        else:
            fails = total - passed
            click.echo(
                click.style(f"  \u2717 {fails}/{total} contract(s) VIOLATED", fg="red")
            )
        click.echo()
