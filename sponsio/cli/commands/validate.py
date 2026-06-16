"""``sponsio validate`` — parse-check contract strings / a config."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click

from sponsio.cli._shared import (
    _looks_like_sponsio_config,
)
from sponsio.cli.app import cli


@cli.command()
@click.argument("contracts", nargs=-1)
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=True),
    help="YAML config file (sponsio.yaml)",
)
@click.option("--agent", "-a", "agent_id", help="Agent ID to validate (with --config)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option(
    "--traces",
    "trace_paths",
    multiple=True,
    type=click.Path(exists=True),
    help=(
        "Replay each parsed contract against the trace file(s) or "
        "directory.  Adds a per-contract pass/fail/error count so you "
        "can see whether a rule would have hit your historical traffic "
        "before flipping it to enforce mode.  Repeat for multiple paths."
    ),
)
def validate(contracts, config_path, agent_id, as_json, trace_paths):
    """Validate that contract strings parse into formal patterns.

    If you pass a single existing ``.yaml`` / ``.yml`` path that looks like
    a Sponsio project file (``agents:`` or ``version:`` + ``extractor:``),
    it is treated as ``--config`` automatically so ``sponsio validate
    ./sponsio.yaml`` does the right thing.

    With ``--traces``, each successfully-parsed deterministic contract is
    replayed against the supplied trace files / directories and a
    pass / fail / error count is reported alongside the parse result.
    Counts only. for per-failure attribution and repair suggestions
    see the proprietary ``sponsio-pro`` validation pipeline.

    Examples:\n
        sponsio validate "tool `A` must precede `B`"\n
        sponsio validate --config sponsio.yaml\n
        sponsio validate --config sponsio.yaml --agent customer_bot\n
        sponsio validate --config sponsio.yaml --traces traces/\n
        sponsio validate ./sponsio.yaml   # same as --config when file looks like a project config
    """
    from sponsio.generation.dsl_to_contract import (
        ContractSyntaxError,
        parse_nl_unified,
    )

    if config_path and contracts:
        click.echo(
            click.style(
                "Error: cannot use both --config and positional contracts", fg="red"
            )
        )
        sys.exit(1)

    # ``sponsio validate ./sponsio.yaml`` (forgot --config) used to try to
    # parse the *path string* as a contract. When the path exists and the
    # head of the file looks like a project config, treat it as --config.
    if not config_path and len(contracts) == 1:
        raw = contracts[0]
        p = Path(os.path.expanduser(str(raw)))
        if not p.is_absolute():
            p = Path.cwd() / p
        try:
            p = p.resolve()
        except OSError:
            p = Path(raw)
        if p.is_file() and p.suffix.lower() in (".yaml", ".yml"):
            if _looks_like_sponsio_config(p):
                if not as_json:
                    click.echo(
                        click.style("  note: ", fg="cyan", dim=True)
                        + (
                            f"treating {p} as a Sponsio config (equivalent to "
                            f"`--config {p.name}`). "
                            f"If you meant a one-line contract that looks like a path, "
                            f"quote it or use `sponsio validate --config` explicitly."
                        ),
                        err=True,
                    )
                config_path = str(p)
                contracts = ()

    if agent_id and not config_path:
        click.echo(click.style("Error: --agent requires --config", fg="red"))
        sys.exit(1)

    if not config_path and not contracts:
        click.echo("Usage: sponsio validate [CONTRACTS...] or --config FILE")
        sys.exit(1)

    # ---- trace replay setup -------------------------------------------
    # Loaded once so a 1000-contract config doesn't re-parse the trace
    # bundle 1000 times.  ``trace_paths`` is empty in the common case.
    traces_loaded: list = []
    if trace_paths:
        from sponsio.discovery.loaders import load_traces

        try:
            traces_loaded = load_traces(list(trace_paths))
        except Exception as e:  # noqa: BLE001
            click.echo(
                click.style("Error: ", fg="red")
                + f"failed to load traces from {list(trace_paths)}: {e}",
                err=True,
            )
            sys.exit(1)
        if not as_json and not traces_loaded:
            click.echo(
                click.style("  warn: ", fg="yellow")
                + "no traces loaded. replay counts will all be 0",
                err=True,
            )

    # Collect contracts to validate (flatten contract entries into
    # per-section lists for display).
    def _flatten(ac) -> dict:
        assumptions: list = []
        guarantees: list = []
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
        return {"assumptions": assumptions, "guarantees": guarantees}

    agent_contracts: dict[str, dict] = {}

    if config_path:
        from sponsio.config import load_config

        config = load_config(config_path)
        agents_to_check = (
            {agent_id: config.agents[agent_id]} if agent_id else config.agents
        )
        for aid, ac in agents_to_check.items():
            agent_contracts[aid] = _flatten(ac)
    else:
        agent_contracts["(inline)"] = {
            "assumptions": [],
            "guarantees": list(contracts),
        }

    # Validate each contract
    all_results = []
    all_ok = True

    for aid, ag in agent_contracts.items():
        if not as_json:
            click.echo(click.style(f"\nAgent: {aid}", bold=True))

        for section, label in [
            ("assumptions", "Assumptions"),
            ("guarantees", "Guarantees"),
        ]:
            items = ag[section]
            if not items:
                continue
            if not as_json:
                click.echo(click.style(f"  {label}:", dim=True))

            for entry in items:
                # Handle both ConstraintEntry (from config) and plain strings
                from sponsio.config import ConstraintEntry, _compile_structured

                # Track the compiled formula (or DetFormula wrapper) so
                # the replay path below has a single source of truth
                # regardless of which branch produced it.
                formula_for_replay = None
                # ``result`` is only set in the NL branches; init here
                # so the replay-eligibility check below doesn't trip
                # UnboundLocalError on structured / ltl entries.
                result = None

                if isinstance(entry, ConstraintEntry):
                    if entry.is_structured:
                        try:
                            compiled = _compile_structured(entry)
                            ok = True
                            pattern = entry.pattern
                            formula = (
                                repr(compiled.formula)
                                if hasattr(compiled, "formula")
                                else ""
                            )
                            # OSS only ships deterministic patterns;
                            # ``_compile_structured`` raises on unknown
                            # names rather than falling through to sto.
                            kind = "DET"
                            nl = f"{entry.pattern}({', '.join(str(a) for a in entry.args)})"
                            formula_for_replay = compiled
                        except Exception as e:
                            ok = False
                            pattern = entry.pattern or ""
                            formula = ""
                            kind = "ERROR"
                            nl = str(e)
                    elif entry.is_ltl:
                        from sponsio.config import _compile_ltl

                        try:
                            compiled = _compile_ltl(entry)
                            ok = True
                            pattern = "ltl"
                            formula = repr(compiled.formula)
                            kind = "DET"
                            nl = entry.ltl or ""
                            formula_for_replay = compiled
                        except Exception as e:
                            ok = False
                            pattern = "ltl"
                            formula = ""
                            kind = "ERROR"
                            nl = str(e)
                    else:
                        nl = entry.nl
                        try:
                            result = parse_nl_unified(nl)
                        except ContractSyntaxError as e:
                            ok = False
                            pattern = ""
                            formula = ""
                            kind = "SYNTAX-ERROR"
                            nl = f"{entry.nl}  ({e.hint or 'no pattern matched'})"
                            result = None
                        if result is None:
                            pass  # already populated above
                        elif result.is_det:
                            ok = True
                            pattern = getattr(result.hard, "pattern_name", "")
                            formula = (
                                repr(result.hard.formula)
                                if hasattr(result.hard, "formula")
                                else ""
                            )
                            kind = "DET"
                            formula_for_replay = result.hard
                else:
                    nl = str(entry)
                    try:
                        result = parse_nl_unified(nl)
                    except ContractSyntaxError as e:
                        ok = False
                        pattern = ""
                        formula = ""
                        kind = "SYNTAX-ERROR"
                        nl = f"{str(entry)}  ({e.hint or 'no pattern matched'})"
                        result = None

                    if result is None:
                        pass  # already populated above
                    elif result.is_det:
                        ok = True
                        pattern = getattr(result.hard, "pattern_name", "")
                        formula = (
                            repr(result.hard.formula)
                            if hasattr(result.hard, "formula")
                            else ""
                        )
                        kind = "DET"
                        formula_for_replay = result.hard
                    else:
                        pattern = ""
                        formula = ""
                        kind = "UNKNOWN"
                        all_ok = False

                # Replay against historical traces \u2014 only meaningful for
                # successfully-parsed DET contracts (sto contracts need
                # an LLM judge, which sponsio-pro covers).
                replay_summary: dict | None = None
                if (
                    traces_loaded
                    and ok
                    and kind == "DET"
                    and formula_for_replay is not None
                ):
                    from sponsio.discovery.trace_replay import replay_formula

                    rep = replay_formula(formula_for_replay, traces_loaded)
                    replay_summary = {
                        "pass": rep.pass_count,
                        "fail": rep.fail_count,
                        "error": rep.error_count,
                        "pass_rate": rep.pass_rate,
                        "errors": list(rep.errors),
                    }

                entry = {
                    "nl": nl,
                    "ok": ok,
                    "type": kind.lower(),
                    "pattern": pattern,
                    "formula": formula,
                    "agent": aid,
                    "section": section,
                }
                if replay_summary is not None:
                    entry["replay"] = replay_summary
                all_results.append(entry)
                if not ok:
                    all_ok = False

                if not as_json:
                    icon = (
                        click.style("\u2713", fg="green")
                        if ok
                        else click.style("\u2717", fg="red")
                    )
                    kind_color = "cyan" if kind == "DET" else "magenta"
                    click.echo(f"    {icon} {click.style(kind, fg=kind_color)}: {nl}")
                    if pattern:
                        click.echo(click.style(f"      Pattern : {pattern}", dim=True))
                    if formula:
                        click.echo(click.style(f"      Formula : {formula}", dim=True))
                    if replay_summary is not None:
                        rate = replay_summary["pass_rate"]
                        rate_str = "n/a" if rate is None else f"{rate:.0%}"
                        replay_line = (
                            f"      Replay  : "
                            f"{replay_summary['pass']} pass / "
                            f"{replay_summary['fail']} fail"
                        )
                        if replay_summary["error"]:
                            replay_line += f" / {replay_summary['error']} error"
                        replay_line += f"  ({rate_str})"
                        # Color: green if no fails+errors, yellow if any
                        # fails / errors (the contract would block, or
                        # a trace was malformed).
                        color = (
                            "green"
                            if replay_summary["fail"] == 0
                            and replay_summary["error"] == 0
                            else "yellow"
                        )
                        click.echo(click.style(replay_line, fg=color, dim=True))

    if as_json:
        click.echo(json.dumps({"contracts": all_results, "ok": all_ok}, indent=2))
    else:
        click.echo()
        if all_ok:
            click.echo(
                click.style(
                    f"  \u2713 All {len(all_results)} contract(s) validated", fg="green"
                )
            )
        else:
            fails = sum(1 for r in all_results if not r["ok"])
            click.echo(
                click.style(f"  \u2717 {fails} contract(s) failed to parse", fg="red")
            )
        click.echo()

    # Non-zero exit on any failure so CI / pre-commit hooks catch
    # unparseable contracts instead of silently shipping them.
    if not all_ok:
        sys.exit(1)
