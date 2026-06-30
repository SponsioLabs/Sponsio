"""``sponsio eval`` — offline trace replay with FPR/FNR scoring."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from sponsio.cli.app import cli


@cli.command(name="eval")
@click.argument(
    "trace_path",
    type=click.Path(exists=True, file_okay=True, dir_okay=True, path_type=Path),
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
@click.option(
    "--baseline",
    "baseline_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Diff against a previous JSON report (produced by `--json`).  "
        "Surfaces FPR/FNR deltas per contract and overall."
    ),
)
@click.option(
    "--max-fpr-delta",
    type=float,
    default=None,
    help=(
        "Fail (exit 1) if overall FPR rose by more than this many "
        "percentage points vs --baseline.  E.g. `0.01` = 1pp.  "
        "Use in CI to catch overblock regressions automatically."
    ),
)
@click.option(
    "--max-fnr-delta",
    type=float,
    default=None,
    help=(
        "Fail (exit 1) if overall FNR rose by more than this many "
        "percentage points vs --baseline.  Use to catch regressions "
        "where contracts started missing real incidents."
    ),
)
@click.option(
    "--write-baseline",
    "write_baseline_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        "After running, write the report JSON to this path.  Use to "
        "snapshot a green run as the new baseline for the next PR."
    ),
)
def eval_cmd(
    trace_path: Path,
    contracts,
    config_path,
    agent_id,
    as_json,
    baseline_path: Path | None,
    max_fpr_delta: float | None,
    max_fnr_delta: float | None,
    write_baseline_path: Path | None,
):
    """Replay a labelled trace corpus and report FPR/FNR per contract.

    Use this BEFORE flipping ``SPONSIO_MODE=enforce``. it answers
    "if I turn enforcement on tomorrow, how often will my contracts
    over-block legitimate traffic, and how often will they miss real
    incidents?".

    Label convention: filename prefix.\n
    \b
        safe_login.json     → expected to PASS every contract
        unsafe_drop.json    → expected to be BLOCKED by ≥1 contract
        anything_else.json  → counted but not used in FPR/FNR

    Examples:\n
        sponsio eval traces/ --config sponsio.yaml --agent bot\n
        sponsio eval traces/ "tool `transfer` at most 1 times"\n
        sponsio eval traces/ --config sponsio.yaml --json\n
        sponsio eval traces/ -c sponsio.yaml \\\n
            --baseline main-baseline.json --max-fpr-delta 0.01

    Reasonable CI gates: ``--max-fpr-delta 0.01`` (1pp overblock
    regression budget) and ``--max-fnr-delta 0.0`` (zero tolerance
    for new misses).  Adjust to your appetite.
    """
    from sponsio.eval_runner import (
        diff_reports,
        discover_cases,
        format_diff,
        format_report,
        run_eval,
    )

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
        click.echo("Usage: sponsio eval TRACE_PATH [CONTRACTS...] [--config FILE]")
        sys.exit(1)

    # Resolve contracts to a flat list of NL strings / structured entries
    contract_list: list = []
    if config_path:
        from sponsio.config import load_config

        cfg = load_config(config_path)
        if not agent_id:
            if len(cfg.agents) == 1:
                agent_id = next(iter(cfg.agents))
            else:
                click.echo(
                    click.style(
                        f"Error: multiple agents in config "
                        f"({list(cfg.agents.keys())}), use --agent",
                        fg="red",
                    )
                )
                sys.exit(1)
        for ce in cfg.agents[agent_id].contracts:
            for field_value in (ce.assumption, ce.guarantee):
                if field_value is None:
                    continue
                if isinstance(field_value, list):
                    contract_list.extend(field_value)
                else:
                    contract_list.append(field_value)
    else:
        contract_list = list(contracts)

    cases = discover_cases(trace_path)
    if not cases:
        click.echo(click.style(f"No trace files found at {trace_path}", fg="yellow"))
        sys.exit(0)

    report = run_eval(cases, contract_list)

    # Validate flag combinations BEFORE doing the eval render so a
    # typo doesn't cost the user a 30s replay.
    if (max_fpr_delta is not None or max_fnr_delta is not None) and not baseline_path:
        click.echo(
            click.style(
                "Error: --max-fpr-delta / --max-fnr-delta require --baseline",
                fg="red",
            )
        )
        sys.exit(2)

    diff = None
    if baseline_path:
        try:
            baseline_data = json.loads(baseline_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            click.echo(
                click.style(f"Error reading baseline {baseline_path}: {e}", fg="red")
            )
            sys.exit(2)
        diff = diff_reports(baseline_data, report)

    if as_json:
        # Preserve the long-standing flat shape (report fields at the
        # top) when there's no baseline. every existing script
        # depends on ``data["n_safe"]`` etc.  Only when a baseline
        # IS present do we add a sibling key for the diff, which
        # callers can look up only when they passed ``--baseline``.
        out = report.to_dict()
        if diff is not None:
            out["baseline_diff"] = diff.to_dict()
        click.echo(json.dumps(out, indent=2))
    else:
        click.echo(format_report(report))
        if diff is not None:
            click.echo(format_diff(diff))

    # Snapshot the report for the next PR's --baseline.  Done AFTER
    # the gate check so a regression-failing run doesn't auto-poison
    # main's baseline (gate failures should not silently rewrite the
    # standard you're being measured against).
    gate_failures: list[str] = []
    if diff is not None:
        gate_failures = diff.gate_violations(
            max_fpr_delta=max_fpr_delta,
            max_fnr_delta=max_fnr_delta,
        )
        if gate_failures:
            click.echo()
            for v in gate_failures:
                click.secho(f"  ✗ {v}", fg="red", bold=True)

    if write_baseline_path and not gate_failures:
        write_baseline_path.write_text(json.dumps(report.to_dict(), indent=2))
        click.secho(f"\n  ✓ baseline written to {write_baseline_path}", fg="green")
    elif write_baseline_path and gate_failures:
        click.secho(
            f"\n  · skipped writing {write_baseline_path} "
            "(gate failed. fix the regression first)",
            fg="yellow",
        )

    if gate_failures:
        sys.exit(1)
