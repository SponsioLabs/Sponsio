"""Sponsio CLI entry point."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import click

from sponsio.constants import DASHBOARD_DEFAULT_PORT


@click.group()
@click.version_option(version="0.1.0a0", prog_name="sponsio")
def cli():
    """Sponsio — the contract layer for LLM agent systems."""


# ---------------------------------------------------------------------------
# demo
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--scenario",
    default="cleanup",
    type=click.Choice(["cleanup", "trial", "loan"], case_sensitive=False),
    help="Demo scenario: cleanup (default), trial, loan",
)
@click.option(
    "--mode",
    default="mock",
    type=click.Choice(["mock", "integration"], case_sensitive=False),
    show_default=True,
    help="mock uses no optional SDKs; integration runs repo example scripts.",
)
@click.option("--no-guard", is_flag=True, help="Replay the unsafe trajectory.")
@click.option("--fast", is_flag=True, help="Skip typing delays.")
def demo(scenario: str, mode: str, no_guard: bool, fast: bool):
    """Run a Sponsio demo in your terminal.

    Three trajectory replays showing unsafe agent behavior and the
    contracts that block it. The default mock mode works from a plain
    PyPI install with no API key and no optional framework SDKs.

    \b
      cleanup  — Claude Code cleanup agent deletes `.env` & `.git/`
      trial    — Clinical Trial Recruiter falsifies patient records
      loan     — Loan officer edits applications to pass AML — 19/24 models

    Examples:\n
        sponsio demo\n
        sponsio demo --scenario trial --fast\n
        sponsio demo --scenario loan --no-guard\n
        sponsio demo --mode integration --scenario cleanup
    """
    scenario_map = {
        "cleanup": ("demo_coding_cleanup.py", "Coding Agent \u2014 Cleanup gone rogue"),
        "trial": (
            "demo_trial_recruiter.py",
            "Clinical Trial Recruiter \u2014 Record falsification",
        ),
        "loan": (
            "demo_loan_fraud.py",
            "Loan Agent \u2014 AML-check circumvention",
        ),
    }

    script_name, label = scenario_map[scenario]

    click.echo()
    click.echo(click.style("Sponsio Demo", bold=True))
    click.echo(click.style(f"  {label}", fg="cyan"))
    click.echo()

    if mode == "mock":
        from sponsio.demos.replay import run_demo

        run_demo(scenario, no_guard=no_guard, fast=fast)
        return

    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "examples" / "demo" / script_name

    if not script_path.exists():
        click.echo(
            click.style(
                "Error: integration demo scripts are only available from a "
                "source checkout. Use the default mock mode from PyPI: "
                f"{click.style('sponsio demo', bold=True)}",
                fg="red",
            )
        )
        sys.exit(1)

    try:
        cmd = [sys.executable, str(script_path)]
        if no_guard:
            cmd.append("--no-guard")
        if fast:
            cmd.append("--fast")
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)
    except KeyboardInterrupt:
        click.echo("\nInterrupted.")


# ---------------------------------------------------------------------------
# patterns
# ---------------------------------------------------------------------------


@cli.command()
def patterns():
    """List all available contract patterns with examples."""

    def _section(title, items, color):
        click.echo(click.style(title, bold=True))
        click.echo()
        for name, example, meaning in items:
            click.echo(click.style(f"  {name}", fg=color, bold=True))
            click.echo(f"    Example : {example}")
            click.echo(click.style(f"    Meaning : {meaning}", dim=True))
            click.echo()

    # --- Core temporal (14) ---
    click.echo()
    _section(
        "Core Temporal Patterns (14 det)",
        [
            ("must_precede", "tool `A` must precede `B`", "A must happen before B"),
            (
                "always_followed_by",
                "tool `A` must always be followed by `B`",
                "whenever A, eventually B",
            ),
            ("no_reversal", "cannot `B` after `A`", "A commits; B forbidden after"),
            (
                "requires_permission",
                "tool `X` requires permission `perm`",
                "tool needs authorization",
            ),
            ("no_data_leak", "no data leak from `src` to `ext`", "data containment"),
            (
                "mutual_exclusion",
                "`A` and `B` are mutually exclusive",
                "at most one per session",
            ),
            ("rate_limit", "tool `X` at most N times", "frequency cap"),
            ("idempotent", "tool `X` must execute at most once", "single execution"),
            (
                "deadline",
                "`action` within N steps of `trigger`",
                "time-bounded obligation",
            ),
            ("must_confirm", "tool `X` requires confirmation", "human-in-the-loop"),
            ("cooldown", "N steps between consecutive `X`", "minimum interval"),
            (
                "segregation_of_duty",
                "review and approve by different agents",
                "separation of concerns",
            ),
            ("bounded_retry", "tool `X` limited to N retries", "retry cap"),
            (
                "loop_detection",
                "tool `X` at most N consecutive calls",
                "runaway loop prevention",
            ),
        ],
        "cyan",
    )

    # --- Argument / path / length (4) ---
    _section(
        "Argument & Path Constraints (4 det)",
        [
            (
                "arg_blacklist",
                "tool `bash` arg `command` must not match `rm -rf`",
                "forbid patterns in args",
            ),
            (
                "scope_limit",
                "tool `file_write` restricted to `/app/data`",
                "restrict tool to allowed paths",
            ),
            (
                "arg_length_limit",
                "tool `bash` arg `command` max 500 chars",
                "block code-injection via long args",
            ),
            (
                "data_intact",
                "`grep` must use only original data files",
                "tool must use unmodified data",
            ),
        ],
        "cyan",
    )

    # --- OWASP Agentic Top 10 (8) ---
    _section(
        "OWASP Agentic Security Patterns (8 det)",
        [
            (
                "destructive_action_gate",
                "`delete_db` requires approval from `approver`",
                "human approval + role for destructive ops",
            ),
            (
                "untrusted_source_gate",
                "after `web_fetch`, `send_email` requires re-confirmation",
                "re-confirm after untrusted input (A,E pair)",
            ),
            (
                "required_steps_completion",
                "every `start_task` must be followed by all of [`log`, `notify`]",
                "all steps must follow trigger",
            ),
            (
                "tool_allowlist",
                "only [`read_file`, `write_file`] may be called",
                "first-line defense against injected tools",
            ),
            (
                "dangerous_bash_commands",
                "ban `rm -rf`, `sudo`, `chmod` in bash",
                "preset: dangerous shell commands",
            ),
            (
                "dangerous_sql_verbs",
                "ban `DROP`, `TRUNCATE` in `execute_sql`",
                "preset: dangerous SQL verbs",
            ),
            (
                "irreversible_once",
                "`deploy_production` at most once per session",
                "irreversible action protection",
            ),
            (
                "confirm_after_source",
                "after `fetch_url`, `file_write` requires confirmation",
                "narrow source→action gate (A,E pair)",
            ),
        ],
        "cyan",
    )

    # --- Atom extensions (3) ---
    _section(
        "Resource & Delegation Constraints (3 det)",
        [
            (
                "token_budget",
                "session total tokens must not exceed 100000",
                "limit token consumption",
            ),
            (
                "arg_value_range",
                "tool `set_price` field `amount` in [0, 1000]",
                "constrain numeric arguments",
            ),
            (
                "delegation_depth_limit",
                "delegation chain max depth 3",
                "limit agent-to-agent delegation",
            ),
        ],
        "cyan",
    )

    # --- Soft evaluators (7) ---
    _section(
        "Soft Evaluators (7 sto)",
        [
            ("pii", "response must not contain PII", "regex-based, no LLM"),
            ("length", "response must be under 200 words", "word/char count, no LLM"),
            ("format", "output must be in JSON format", "structure validation, no LLM"),
            (
                "content_prohibition",
                "response must not mention competitors",
                "substring/regex check, no LLM",
            ),
            ("tone", "response must be empathetic", "LLM-scored evaluation"),
            (
                "relevance",
                "response must be relevant to topic",
                "LLM-scored evaluation",
            ),
            (
                "llm_judge",
                "response must follow company policy",
                "generic LLM judge fallback",
            ),
        ],
        "magenta",
    )


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def _looks_like_sponsio_config(path: Path) -> bool:
    """Return True if ``path`` is probably a :file:`sponsio.yaml` (not
    an arbitrary string the user wanted to parse as a contract).

    Kept intentionally narrow so ``sponsio validate interesting.yaml`` only
    auto-routes when the file *looks* like a Sponsio config, not every YAML
    on disk.
    """
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:32768]
    except OSError:
        return False
    # Project configs list agents; ``init`` output uses version+extractor.
    if re.search(r"(?m)^\s*agents:\s*", head):
        return True
    return bool(
        re.search(r"(?m)^\s*version:\s*\d", head)
        and re.search(r"(?m)^\s*extractor:\s*", head)
    )


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
def validate(contracts, config_path, agent_id, as_json):
    """Validate that contract strings parse into formal patterns.

    If you pass a single existing ``.yaml`` / ``.yml`` path that looks like
    a Sponsio project file (``agents:`` or ``version:`` + ``extractor:``),
    it is treated as ``--config`` automatically so ``sponsio validate
    ./sponsio.yaml`` does the right thing.

    Examples:\n
        sponsio validate "tool `A` must precede `B`"\n
        sponsio validate --config sponsio.yaml\n
        sponsio validate --config sponsio.yaml --agent customer_bot\n
        sponsio validate ./sponsio.yaml   # same as --config when file looks like a project config
    """
    from sponsio.generation.nl_to_contract import (
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

    # Collect contracts to validate (flatten contract entries into
    # per-section lists for display).
    def _flatten(ac) -> dict:
        assumptions: list = []
        enforcements: list = []
        for ce in ac.contracts:
            if ce.assumption is not None:
                if isinstance(ce.assumption, list):
                    assumptions.extend(ce.assumption)
                else:
                    assumptions.append(ce.assumption)
            if ce.enforcement is not None:
                if isinstance(ce.enforcement, list):
                    enforcements.extend(ce.enforcement)
                else:
                    enforcements.append(ce.enforcement)
        return {"assumptions": assumptions, "guarantees": enforcements}

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
                            kind = "DET"
                            nl = f"{entry.pattern}({', '.join(str(a) for a in entry.args)})"
                        except Exception as e:
                            ok = False
                            pattern = entry.pattern or ""
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
                        elif result.is_sto:
                            ok = True
                            pattern = getattr(result.sto, "desc", "")
                            formula = ""
                            kind = "STO"
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
                    elif result.is_sto:
                        pattern = getattr(result.sto, "desc", "")
                        formula = ""
                        kind = "STO"
                    else:
                        pattern = ""
                        formula = ""
                        kind = "UNKNOWN"
                        all_ok = False

                entry = {
                    "nl": nl,
                    "ok": ok,
                    "type": kind.lower(),
                    "pattern": pattern,
                    "formula": formula,
                    "agent": aid,
                    "section": section,
                }
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


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def _resolve_entry(entry):
    """Resolve a constraint entry (string or ConstraintEntry) to (nl_text, parsed_result).

    For structured entries (pattern + args), compiles directly.
    For NL strings, runs through parse_nl_unified.
    """
    from sponsio.config import ConstraintEntry, _compile_structured
    from sponsio.generation.nl_to_contract import (
        ContractSyntaxError,
        UnifiedParseResult,
        parse_nl_unified,
    )

    if isinstance(entry, ConstraintEntry):
        if entry.is_structured:
            try:
                compiled = _compile_structured(entry)
                nl = f"{entry.pattern}({', '.join(str(a) for a in entry.args)})"
                return nl, UnifiedParseResult(original_nl=nl, hard=compiled)
            except Exception:
                return str(entry.pattern), None
        else:
            nl = entry.nl
    else:
        nl = str(entry)
    try:
        return nl, parse_nl_unified(nl)
    except ContractSyntaxError:
        # Unparseable — `sponsio check` signals this by returning
        # a None result, same shape as a structured-compile error.
        return nl, None


@cli.command()
@click.option(
    "--trace",
    "-t",
    "trace_path",
    required=True,
    type=click.Path(exists=True),
    help=(
        "Trace file to check against. Accepts OTLP/JSON, OTLP JSONL, "
        "native Sponsio JSON/JSONL, and session JSONL — format is "
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
        # Flatten — renumber ts so ordering is preserved across files.
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
            if ce.enforcement is not None:
                if isinstance(ce.enforcement, list):
                    guarantees.extend(ce.enforcement)
                else:
                    guarantees.append(ce.enforcement)
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
                        "passed": True,
                        "note": "sto (skipped)",
                    }
                )
                if not as_json:
                    dash = click.style("\u2013", dim=True)
                    skip = click.style("(sto, skip)", dim=True)
                    click.echo(f"    {dash} {nl}  {skip}")
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
                        "passed": True,
                        "note": "sto (skipped)",
                    }
                )
                if not as_json:
                    dash = click.style("\u2013", dim=True)
                    skip = click.style("(sto, skip)", dim=True)
                    click.echo(f"    {dash} {nl}  {skip}")
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


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--since",
    default="7d",
    show_default=True,
    help="Time window: 'all', '30m', '24h', '7d'.",
)
@click.option(
    "--agent",
    default=None,
    help="Filter to one agent_id. Default: every agent under ~/.sponsio/sessions.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["markdown", "md", "html", "json"], case_sensitive=False),
    default="markdown",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--out",
    "-o",
    "out_path",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Write report to this file. Default: stdout.",
)
@click.option(
    "--live",
    is_flag=True,
    default=False,
    help="Watch mode: re-render every --interval seconds. Ctrl+C to exit.",
)
@click.option(
    "--interval",
    default=2.0,
    show_default=True,
    type=float,
    help="Seconds between refreshes in --live mode.",
)
@click.option(
    "--base-dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Override the session log directory (default: ~/.sponsio/sessions).",
)
def report(
    since: str,
    agent: str | None,
    fmt: str,
    out_path: str | None,
    live: bool,
    interval: float,
    base_dir: str | None,
):
    """Summarize shadow-mode session logs into a shareable report.

    \b
    Examples:
      sponsio report                                    # markdown, last 7 days, all agents
      sponsio report --agent support_bot --since 24h    # one agent, last day
      sponsio report --format html -o report.html       # HTML to file
      sponsio report --format json --since all          # machine-readable dump
      sponsio report --live                             # watch mode, refreshes every 2s

    Reads JSONL files written by ``mode='observe'`` (shadow mode) from
    ``~/.sponsio/sessions/<agent_id>/*.jsonl``.  Nothing is modified.
    """
    # Lazy imports so `sponsio --help` stays fast.
    from pathlib import Path

    from sponsio.reporting import aggregate, load_events, render
    from sponsio.reporting.reader import parse_since

    # Validate --since up front so we fail fast with a readable error.
    try:
        parse_since(since)
    except ValueError as e:
        click.echo(click.style(f"Error: {e}", fg="red"))
        raise SystemExit(2)

    bd = Path(base_dir) if base_dir else None

    def _one_pass() -> str:
        events = load_events(since=since, agent=agent, base_dir=bd)
        rep = aggregate(events)
        return render(rep, fmt=fmt)

    if live:
        if out_path is not None:
            click.echo(
                click.style("Error: ", fg="red")
                + "--live cannot be combined with --out."
            )
            raise SystemExit(2)
        import time as _time

        try:
            while True:
                # ANSI clear-screen + home cursor; harmless on non-TTY.
                click.echo("\x1b[2J\x1b[H", nl=False)
                click.echo(_one_pass())
                _time.sleep(max(0.25, interval))
        except KeyboardInterrupt:
            click.echo("\n(live mode stopped)")
            return

    out = _one_pass()
    if out_path is None:
        click.echo(out, nl=False)
    else:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(out)
        click.echo(
            click.style("Wrote ", fg="green")
            + out_path
            + f" ({len(out)} bytes, format={fmt})"
        )


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
@click.option(
    "--port",
    "-p",
    default=DASHBOARD_DEFAULT_PORT,
    type=int,
    help=f"Port (default: {DASHBOARD_DEFAULT_PORT}, same as Sponsio(dashboard=True) / Vite proxy)",
)
@click.option(
    "--dev", is_flag=True, help="Start frontend dev server too (requires npm)"
)
def serve(host: str, port: int, dev: bool):
    """Start the Sponsio dashboard server.

    \b
    Examples:
      sponsio serve                  # API on http://127.0.0.1:8000
      sponsio serve -p 9000          # custom port
      sponsio serve --dev            # API + frontend dev server
      sponsio serve --host 0.0.0.0   # expose to network
    """
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        click.echo(
            click.style("Error: ", fg="red")
            + "uvicorn is required. Install with: pip install 'sponsio[web]'"
        )
        raise SystemExit(1)

    # Ensure the user's CWD is on sys.path so `api/` (which is not an installed
    # package) can be imported. Console-script entrypoints don't add "" to
    # sys.path the way `python -c` / `python -m` do. Also propagate via
    # PYTHONPATH so uvicorn reloader subprocesses inherit it under --dev.
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    existing_pp = os.environ.get("PYTHONPATH", "")
    if cwd not in existing_pp.split(os.pathsep):
        os.environ["PYTHONPATH"] = (
            f"{cwd}{os.pathsep}{existing_pp}" if existing_pp else cwd
        )

    # Check that api.main exists
    try:
        from api.main import app  # noqa: F401
    except ImportError as e:
        click.echo(
            click.style("Error: ", fg="red")
            + f"Dashboard API not found ({e}).\n"
            + "  Make sure you're running from the Sponsio project directory."
        )
        raise SystemExit(1)

    url = f"http://{host}:{port}"
    click.echo(f"\n  {click.style('Sponsio dashboard', fg='cyan', bold=True)}: {url}")
    click.echo(f"  {click.style('API docs', fg='cyan')}: {url}/docs")

    if dev:
        # Start frontend dev server in background
        web_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
        if os.path.isdir(web_dir):
            click.echo(
                f"  {click.style('Frontend', fg='cyan')}: http://localhost:3000\n"
            )
            subprocess.Popen(
                ["npm", "run", "dev"],
                cwd=web_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            click.echo(
                click.style("  Warning: ", fg="yellow")
                + f"web/ directory not found at {web_dir}, skipping frontend.\n"
            )
    else:
        click.echo()

    import uvicorn

    uvicorn.run("api.main:app", host=host, port=port, reload=dev)


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("paths", nargs=-1, required=True)
@click.option("--agent", "-a", default="agent", help="Agent ID for generated config")
@click.option(
    "--llm", is_flag=True, help="Enable LLM inference (auto-detects provider from env)"
)
@click.option("--model", "-m", default=None, help="LLM model (default: auto-detect)")
@click.option(
    "--provider",
    default=None,
    type=click.Choice(["openai", "anthropic", "gemini"]),
    help=(
        "LLM provider (default: auto-detect from env). "
        "Anthropic uses ANTHROPIC_API_KEY; Gemini uses GOOGLE_API_KEY "
        "or GEMINI_API_KEY (1500 req/day free tier)."
    ),
)
@click.option(
    "--base-url",
    default=None,
    help=(
        "OpenAI-compatible HTTP endpoint. Covers Ollama (local), "
        "OpenRouter, DeepSeek, Together, Groq, vLLM, Azure OpenAI. "
        "Reads OPENAI_BASE_URL env if not given."
    ),
)
@click.option(
    "--out",
    "-o",
    type=click.Path(),
    default=None,
    help=(
        "Write YAML to this path. Defaults to `./sponsio.yaml`. "
        "Use `-o -` to print to stdout for piping."
    ),
)
@click.option(
    "--append", is_flag=True, help="Append to existing file instead of overwriting"
)
@click.option(
    "--policy",
    "-p",
    multiple=True,
    type=click.Path(exists=True),
    help="Policy document (.md/.txt) to extract constraints from",
)
@click.option(
    "--trace",
    "-t",
    "traces",
    multiple=True,
    type=str,
    help=(
        "Execution trace file, directory, or glob to mine contracts "
        "from. Accepts OTLP/JSON, OTLP JSONL, native Sponsio "
        "JSON/JSONL, and session-log JSONL "
        "(~/.sponsio/sessions/<agent>/*.jsonl). `~` is expanded. Can "
        "be repeated: `-t 'traces/*.jsonl' -t extra.json`. No LLM required."
    ),
)
@click.option(
    "--trace-min-support",
    type=int,
    default=1,
    show_default=True,
    help=(
        "Minimum number of traces a pattern must appear in before "
        "trace-mining proposes it. Default `1` is loose — bump up "
        "(e.g. `5`) when feeding a large production audit log."
    ),
)
@click.option(
    "--trace-confidence-threshold",
    type=float,
    default=0.95,
    show_default=True,
    help=(
        "Confidence floor for ordering / sequence mining (0–1). "
        "Higher = stricter. Default 0.95."
    ),
)
@click.option(
    "--push/--no-push",
    default=False,
    help=(
        "Push the YAML to the local dashboard at --push-url "
        "(default: off). The dashboard is an optional observability "
        "companion; opt in explicitly so `sponsio scan` is a pure, "
        "offline code-gen step by default."
    ),
)
@click.option(
    "--push-url",
    default="http://127.0.0.1:8000",
    help="Dashboard URL to push to (default: http://127.0.0.1:8000)",
)
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help=(
        "Read provider/model/api_key from sponsio.yaml's `extractor:` "
        "section.  Implies --llm.  Explicit --provider/--model/--base-url "
        "still win over YAML values."
    ),
)
def scan(
    paths: tuple[str, ...],
    agent: str,
    llm: bool,
    model: str | None,
    provider: str | None,
    base_url: str | None,
    out: str | None,
    append: bool,
    policy: tuple[str, ...],
    traces: tuple[str, ...],
    trace_min_support: int,
    trace_confidence_threshold: float,
    push: bool,
    push_url: str,
    config_path: str | None,
):
    """Scan source code, policy docs, and traces to propose contracts.

    Analyzes tool definitions, decorators, and call patterns to infer
    safety constraints. Optionally extracts constraints from policy
    documents (.md/.txt) using the discovered tool inventory as context,
    and mines ordering / exclusion / rate-limit patterns from execution
    traces (OTLP/JSON, OTLP JSONL, or native Sponsio).

    \b
    Examples:
      sponsio scan src/                                # writes ./sponsio.yaml (rule-based)
      sponsio scan src/ --llm                          # + LLM inference
      sponsio scan src/ --policy security.md --llm     # code + policy
      sponsio scan src/ -t 'traces/*.jsonl'            # code + trace mining
      sponsio scan src/ -t traces/ --trace-min-support 5
      sponsio scan src/ -o custom.yaml                 # write to custom path
      sponsio scan src/ -o sponsio.yaml --append       # merge into existing
      sponsio scan src/ -o -                           # print to stdout (pipe)
      sponsio scan src/ --push                         # also push to dashboard
    """
    from sponsio.discovery.extractors.code_analysis import CodeAnalyzer

    # Route progress messages to stderr with light styling so the YAML
    # body on stdout is still pipeable to a file or another command.
    def _scan_progress(msg: str) -> None:
        click.echo(click.style("· ", fg="cyan", dim=True) + msg, err=True)

    # Pull provider/model/key/base_url from the YAML's ``extractor:``
    # section if --config was given.  CLI flags retain the highest
    # precedence — they're how you override on a one-off basis.
    api_key: str | None = None
    if config_path:
        from sponsio.config import load_config

        cfg = load_config(config_path)
        ext = cfg.extractor
        if not (ext.provider or ext.model or ext.api_key or ext.base_url):
            click.echo(
                click.style("  warn: ", fg="yellow")
                + f"{config_path} has no `extractor:` section — "
                "nothing to inherit.",
                err=True,
            )
        else:
            _scan_progress(
                f"using extractor config from {config_path} "
                f"(provider={ext.provider or '<auto>'}, "
                f"model={ext.model or '<default>'})"
            )
        provider = provider or ext.provider
        model = model or ext.model
        base_url = base_url or ext.base_url
        api_key = ext.api_key
        # --config implies --llm: configuring an extractor and then NOT
        # using it would be confusing.
        if not llm:
            llm = True
            _scan_progress("--config implies --llm; enabling LLM inference")

    analyzer = CodeAnalyzer(
        use_llm=llm,
        llm_model=model,
        api_key=api_key,
        provider=provider,
        base_url=base_url,
        progress=_scan_progress,
    )
    source_paths = list(paths)

    # Extract tool inventory for policy document context
    tool_inventory = analyzer.get_tool_inventory(source_paths) if policy else None

    yaml_content = analyzer.generate_yaml(
        source_paths,
        agent_id=agent,
        policy_paths=list(policy),
        tool_inventory=tool_inventory,
        trace_paths=list(traces) if traces else None,
        trace_min_support=trace_min_support,
        trace_confidence_threshold=trace_confidence_threshold,
    )

    # --- Auto-validate & drop unparseable contracts ---------------------
    # Goal: the file we hand the user is *directly usable*. Any contract
    # that the parser can't compile is dropped here (and listed on
    # stderr) instead of being left as a landmine in the YAML.
    yaml_content, dropped_contracts = _filter_invalid_contracts(yaml_content)

    # --- Post-scan summary (stderr) -------------------------------------
    # Helps users notice the "0 contracts" case immediately and points
    # them at --llm if they only ran the AST pass.
    n_tools, n_contracts, n_review = _scan_summary_counts(yaml_content)
    summary_color = "green" if n_contracts > 0 else "yellow"
    summary = f"Scan summary: {n_tools} tool(s), {n_contracts} contract(s) kept"
    if n_review:
        summary += f" ({n_review} flagged for review)"
    if dropped_contracts:
        summary += f", {len(dropped_contracts)} dropped (failed to parse)"
    click.echo(click.style("• " + summary, fg=summary_color), err=True)
    for d in dropped_contracts:
        click.echo(
            click.style("  dropped: ", fg="yellow")
            + f"[{d['agent']}] "
            + click.style(d["nl"][:120], dim=True)
            + (f"  ({d['error']})" if d.get("error") else ""),
            err=True,
        )
    if n_tools == 0:
        click.echo(
            click.style("  note: ", fg="cyan")
            + "0 tools usually means nothing in the scanned path matched "
            "Sponsio's discovery rules (``@tool``, ``Agent(tools=[...])``, "
            "``TOOLS = [fn, ...]``, etc.), or the tree was effectively empty "
            "(dependency dirs like ``.venv`` / ``node_modules`` are skipped). "
            "Point at the directory that contains your agent's tool modules.",
            err=True,
        )
    if n_contracts == 0 and not llm:
        click.echo(
            click.style("  hint: ", fg="cyan")
            + "no contracts inferred from AST. Re-run with "
            + click.style("--llm", bold=True)
            + " (and optionally --policy <doc>) for richer inference.",
            err=True,
        )
    if policy and not llm:
        click.echo(
            click.style("  warn: ", fg="yellow")
            + "--policy was given but --llm was not — "
            + f"{len(policy)} policy file(s) were ignored.",
            err=True,
        )

    # Default output: write to ``./sponsio.yaml`` so the common
    # interactive case never leaves the user wondering where the YAML
    # went.  Two opt-outs:
    #   * ``-o -``      → print to stdout (pipeline use)
    #   * ``-o <path>`` → write to a specific path
    if out == "-":
        click.echo(yaml_content)
        click.echo(
            click.style("• ", fg="cyan")
            + "YAML written to stdout (use `-o <path>` to save to a file).",
            err=True,
        )
    else:
        target = out or "sponsio.yaml"
        existed = os.path.exists(target)
        if append and existed:
            with open(target) as f:
                existing = f.read()
            yaml_content = _merge_yaml(existing, yaml_content)
        with open(target, "w") as f:
            f.write(yaml_content)
        abs_out = os.path.abspath(target)
        verb = (
            "Updated" if append and existed else ("Overwrote" if existed else "Wrote")
        )
        click.echo(
            click.style("✓ ", fg="green") + f"{verb} {click.style(abs_out, bold=True)}",
            err=True,
        )
        if existed and not append:
            click.echo(
                click.style("  note: ", fg="yellow")
                + "existing file was overwritten. "
                + "Use --append to merge new contracts into it instead.",
                err=True,
            )
        click.echo(
            click.style("  tip: ", fg="cyan", dim=True)
            + f"re-run `sponsio validate --config {abs_out}` after manual edits.",
            err=True,
        )

    if push:
        _push_scan_to_dashboard(
            yaml_content=yaml_content,
            filename=(os.path.basename(out) if out and out != "-" else "sponsio.yaml"),
            dashboard_url=push_url,
            source_paths=source_paths,
        )


def _filter_invalid_contracts(yaml_content: str) -> tuple[str, list[dict]]:
    """Drop contracts that fail to compile so the saved YAML is usable as-is.

    Walks every ``agents.<id>.contracts[*]`` entry, runs the same parser
    that ``sponsio validate`` uses, and rewrites the YAML with only the
    entries that parse cleanly. Bad ones are returned for stderr display.

    Conservative on errors: if PyYAML / the parser modules aren't
    importable, returns the input unchanged (and an empty drop list) so
    a minimal install still gets a working scan, just without the
    auto-validate net.

    Returns:
        (cleaned_yaml, dropped) where ``dropped`` is a list of
        ``{"agent": str, "nl": str, "error": str}``.
    """
    try:
        import yaml as _yaml
    except ImportError:
        return yaml_content, []

    try:
        from sponsio.config import _compile_structured, _parse_constraint_entry
        from sponsio.generation.nl_to_contract import (
            ContractSyntaxError,
            parse_nl_unified,
        )
    except ImportError:
        return yaml_content, []

    try:
        data = _yaml.safe_load(yaml_content)
    except _yaml.YAMLError:
        return yaml_content, []

    if not isinstance(data, dict):
        return yaml_content, []

    agents_raw = data.get("agents", {})
    if not isinstance(agents_raw, dict):
        return yaml_content, []

    def _validate_one(item) -> tuple[bool, str, str]:
        try:
            entry = _parse_constraint_entry(item)
        except Exception as e:  # noqa: BLE001
            return False, str(item)[:120], f"parse: {e}"
        if entry.is_structured:
            try:
                _compile_structured(entry)
            except Exception as e:  # noqa: BLE001
                args = ", ".join(str(a) for a in (entry.args or []))
                return False, f"{entry.pattern}({args})", str(e)
            return True, "", ""
        else:
            nl = entry.nl or ""
            try:
                parse_nl_unified(nl)
            except ContractSyntaxError as e:
                return False, nl, e.hint or "no pattern matched"
            except Exception as e:  # noqa: BLE001
                return False, nl, str(e)
            return True, "", ""

    bad_per_agent: dict[str, set[int]] = {}
    dropped: list[dict] = []

    for agent_id, ag in agents_raw.items():
        # An agent block is normally a dict with `contracts:`; bare lists
        # are tolerated by the loader but rare from generate_yaml. Handle
        # both for safety.
        if isinstance(ag, dict):
            contracts = ag.get("contracts", [])
        elif isinstance(ag, list):
            contracts = ag
        else:
            continue
        if not isinstance(contracts, list):
            continue

        bad: set[int] = set()
        for idx, ce in enumerate(contracts):
            # An entry can be either a bare string (E only, NL form), or
            # a dict with A/E keys whose values are themselves NL strings
            # or structured ``{pattern, args}`` dicts.
            sub_items: list = []
            if isinstance(ce, str):
                sub_items.append(ce)
            elif isinstance(ce, dict):
                for key in ("A", "E"):
                    if key not in ce:
                        continue
                    val = ce[key]
                    sub_items.extend(val if isinstance(val, list) else [val])
            else:
                continue

            entry_dropped = False
            for it in sub_items:
                if it is None:
                    continue
                ok, nl_repr, err = _validate_one(it)
                if not ok:
                    dropped.append(
                        {"agent": str(agent_id), "nl": nl_repr, "error": err}
                    )
                    entry_dropped = True
                    break
            if entry_dropped:
                bad.add(idx)

        if bad:
            bad_per_agent[str(agent_id)] = bad

    if not bad_per_agent:
        return yaml_content, dropped  # nothing to rewrite

    cleaned = _drop_contract_indices(yaml_content, bad_per_agent)
    return cleaned, dropped


def _drop_contract_indices(
    yaml_content: str, bad_per_agent: dict[str, set[int]]
) -> str:
    """Remove specific contract entries (by 0-based index) per agent.

    Preserves comments, confidence tags and the surrounding YAML
    structure that ``generate_yaml`` produces.  If an agent's
    ``contracts:`` list ends up empty we replace it with ``contracts: []``
    so the resulting file still parses.
    """
    out: list[str] = []
    lines = yaml_content.split("\n")

    in_agents = False
    current_agent: str | None = None
    in_contracts = False
    current_idx = -1
    skipping = False
    contracts_line_idx: int | None = None
    kept_in_current_contracts = 0

    def _finalize_contracts_block() -> None:
        # If the contracts: list ended up empty, swap the header line for
        # ``contracts: []`` so the YAML stays valid.
        nonlocal contracts_line_idx, kept_in_current_contracts
        if contracts_line_idx is not None and kept_in_current_contracts == 0:
            header = out[contracts_line_idx]
            stripped = header.lstrip()
            indent = header[: len(header) - len(stripped)]
            if stripped.rstrip().endswith(":"):
                out[contracts_line_idx] = f"{indent}contracts: []"
        contracts_line_idx = None
        kept_in_current_contracts = 0

    for line in lines:
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        # Blank / comment lines: keep unless we're inside a dropped entry.
        if not stripped or stripped.startswith("#"):
            if skipping:
                continue
            out.append(line)
            continue

        # Top-level key (col 0) → reset everything.
        if indent == 0:
            _finalize_contracts_block()
            in_agents = stripped.startswith("agents:")
            current_agent = None
            in_contracts = False
            current_idx = -1
            skipping = False
            out.append(line)
            continue

        # Inside agents: each agent header sits at indent 2.
        if in_agents and indent == 2 and stripped.rstrip().endswith(":"):
            _finalize_contracts_block()
            current_agent = stripped.rstrip()[:-1].strip()
            in_contracts = False
            current_idx = -1
            skipping = False
            out.append(line)
            continue

        # Properties of the current agent live at indent 4.
        if current_agent is not None and indent == 4:
            _finalize_contracts_block()
            in_contracts = stripped.startswith("contracts:")
            skipping = False
            current_idx = -1
            if in_contracts:
                contracts_line_idx = len(out)
                kept_in_current_contracts = 0
            out.append(line)
            continue

        # Inside a contracts: list, entries start at indent 6 with "- ".
        if in_contracts and indent >= 6:
            if stripped.startswith("- "):
                current_idx += 1
                bad_set = bad_per_agent.get(current_agent or "", set())
                skipping = current_idx in bad_set
                if not skipping:
                    kept_in_current_contracts += 1
                    out.append(line)
                continue
            # Continuation line of the current entry.
            if not skipping:
                out.append(line)
            continue

        # Anything else: outside our tracked regions.
        skipping = False
        out.append(line)

    _finalize_contracts_block()
    return "\n".join(out)


def _scan_summary_counts(yaml_content: str) -> tuple[int, int, int]:
    """Count tools, contracts and review-flagged contracts in scan YAML.

    Tolerant to formatting; we just look for stable line shapes that the
    YAML emitter produces.  Returns ``(tools, contracts, review_flagged)``.
    """
    n_tools = 0
    n_contracts = 0
    n_review = 0
    in_tools = False
    for raw in yaml_content.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if line.startswith("tools:"):
            in_tools = True
            continue
        if in_tools:
            if line.startswith("  - name:"):
                n_tools += 1
                continue
            if line and not line.startswith(" "):
                in_tools = False
        if stripped.startswith("- E:") or stripped.startswith("- A:"):
            n_contracts += 1
            if "review recommended" in stripped:
                n_review += 1
    return n_tools, n_contracts, n_review


def _push_scan_to_dashboard(
    yaml_content: str,
    filename: str,
    dashboard_url: str,
    source_paths: list[str],
) -> None:
    """POST the scan YAML to the running dashboard.

    Silently skips if the dashboard isn't reachable; this is additive UX,
    not a required step.
    """
    base = dashboard_url.rstrip("/")
    try:
        import httpx
    except ImportError:
        click.echo(
            click.style("  note: ", fg="yellow")
            + "httpx not installed, skipping dashboard push."
        )
        return

    # 1. Check that the dashboard is actually running before uploading.
    try:
        r = httpx.get(f"{base}/api/health", timeout=1.5)
        if r.status_code != 200:
            raise RuntimeError(f"/api/health returned {r.status_code}")
    except Exception:
        click.echo(
            click.style("  tip: ", fg="cyan")
            + f"dashboard not running at {base} — start it with "
            + click.style("sponsio serve", bold=True)
            + " to see scan results in the UI."
        )
        return

    # 2. POST the YAML as a file upload, tagged with source=cli so the
    #    dashboard's CLI tab can distinguish it from browser uploads.
    try:
        files = {"file": (filename, yaml_content.encode("utf-8"), "text/yaml")}
        r = httpx.post(
            f"{base}/api/scan/upload",
            files=files,
            params={"source": "cli"},
            timeout=10.0,
        )
        if r.status_code != 200:
            click.echo(
                click.style("  push failed: ", fg="yellow")
                + f"HTTP {r.status_code} {r.text[:200]}"
            )
            return
        result = r.json()
        summary = (
            f"{result.get('agent_name', '?')}: "
            f"{result.get('score', 0)}/100 "
            f"({result.get('grade', '?')})"
        )
        click.echo(click.style("✓ ", fg="green") + f"Pushed to dashboard — {summary}")
        click.echo(
            f"  View at {click.style(base.replace(':8000', ':3000') + '/scan', bold=True)}"
        )
    except Exception as e:
        click.echo(click.style("  push failed: ", fg="yellow") + str(e))


def _merge_yaml(existing: str, new: str) -> str:
    """Merge new scan results into an existing YAML file.

    Appends new contract entries (``- E:`` / ``- A: ... E:``) from
    *new* after the last contract in *existing*, avoiding duplicates.

    Works with the current ``contracts: [{A, E}]`` YAML schema.
    """
    existing_lines = existing.rstrip().split("\n")

    # --- Extract contract entries from new content ---
    # A contract entry starts with a line matching `- E:` or `- A:` at
    # the expected indent (6 spaces inside `contracts:`). Continuation
    # lines are indented deeper.
    new_lines = new.split("\n")
    new_entries: list[list[str]] = []
    in_contracts = False
    current_entry: list[str] = []

    for line in new_lines:
        stripped = line.strip()
        if "contracts:" in line and stripped != "contracts: []":
            in_contracts = True
            continue
        if not in_contracts:
            continue
        # A new entry starts with `- E:` or `- A:` (possibly with trailing comment)
        if stripped.startswith("- E:") or stripped.startswith("- A:"):
            if current_entry:
                new_entries.append(current_entry)
            current_entry = [line]
        elif current_entry and (
            stripped.startswith("pattern:")
            or stripped.startswith("args:")
            or stripped.startswith("source:")
            or stripped.startswith("E:")
            or stripped.startswith("desc:")
        ):
            # Continuation of the current entry
            current_entry.append(line)
        elif current_entry and not stripped and not stripped.startswith("#"):
            # Blank line or end of section
            pass
        elif current_entry and stripped.startswith("#"):
            # Comment inside an entry — keep it
            current_entry.append(line)
        elif not stripped:
            continue
        else:
            # Non-entry, non-continuation line — we've left the contracts block
            break
    if current_entry:
        new_entries.append(current_entry)

    if not new_entries:
        return existing

    # --- Fingerprint existing entries to deduplicate ---
    # Normalize each entry to a single key string for comparison.
    def _fingerprint(lines: list[str]) -> str:
        return " ".join(ln.strip() for ln in lines)

    existing_fingerprints: set[str] = set()
    temp_entry: list[str] = []
    in_existing_contracts = False
    for line in existing_lines:
        stripped = line.strip()
        if "contracts:" in line:
            in_existing_contracts = True
            continue
        if not in_existing_contracts:
            continue
        if stripped.startswith("- E:") or stripped.startswith("- A:"):
            if temp_entry:
                existing_fingerprints.add(_fingerprint(temp_entry))
            temp_entry = [line]
        elif temp_entry and stripped and not stripped.startswith("#"):
            temp_entry.append(line)
        elif not stripped:
            continue
    if temp_entry:
        existing_fingerprints.add(_fingerprint(temp_entry))

    # Filter out duplicates
    to_add = [
        entry
        for entry in new_entries
        if _fingerprint(entry) not in existing_fingerprints
    ]

    if not to_add:
        return existing

    # --- Append after last content line ---
    result = existing.rstrip() + "\n"
    result += "      # --- appended by sponsio scan ---\n"
    for entry in to_add:
        result += "\n".join(entry) + "\n"
    return result


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@cli.command(name="export")
@click.argument(
    "source",
    type=click.Path(exists=True, file_okay=True, dir_okay=True, path_type=Path),
)
@click.option(
    "--to",
    "target_dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    required=True,
    help="Output directory for OTLP-JSON trace files.",
)
@click.option(
    "--label",
    type=click.Choice(["safe", "unsafe", "none"]),
    default="safe",
    show_default=True,
    help=(
        "Filename prefix applied to each output trace.  ``safe`` / "
        "``unsafe`` make the file ready for `sponsio eval`; ``none`` "
        "preserves the input basename untouched (useful when you've "
        "already pre-labelled Sponsio-native dumps)."
    ),
)
@click.option(
    "--agent",
    "agent_id",
    default=None,
    help=(
        "Override the ``service.name`` stamped on the OTLP output.  "
        "Defaults to the ``metadata.agent_id`` in the source JSON, "
        "then to the first event's ``agent``, then to ``'agent'``."
    ),
)
@click.option(
    "--glob",
    "glob_pattern",
    default="*.json",
    show_default=True,
    help="Only convert files matching this glob (directory mode only).",
)
def export_cmd(
    source: Path,
    target_dir: Path,
    label: str,
    agent_id: str | None,
    glob_pattern: str,
):
    """Convert Sponsio-native trace dumps to OTLP JSON for ``sponsio eval``.

    The canonical flow from prod to eval corpus:

    \b
        # 1. In your agent (observe mode — never blocks):
        guard = BaseGuard(agent_id="bot", contracts=[...], mode="observe")
        # ...runs happen, violations logged but not enforced...

        # 2. Dump the accumulated trace to disk at session end:
        guard.trace.export("/var/log/sponsio/run.json")

        # 3. Later, convert a directory of these dumps into an eval corpus:
        sponsio export /var/log/sponsio/ --to traces/ --label safe

        # 4. Re-label incident traces and re-run eval:
        mv traces/safe_run_123.json traces/unsafe_run_123.json
        sponsio eval traces/ --config sponsio.yaml

    SOURCE may be a single ``.json`` file or a directory of them.
    Output filenames are ``{label}_{source-basename}.json`` — the
    prefix is what ``sponsio eval`` reads to know which traces are
    expected to pass vs be blocked, so picking the right ``--label``
    at export time saves a rename pass later.
    """
    from sponsio.models.trace import Trace
    from sponsio.tracer.otel_writer import trace_to_otlp

    # Collect source files
    if source.is_file():
        sources = [source]
    else:
        sources = sorted(source.glob(glob_pattern))
        if not sources:
            click.echo(
                click.style(
                    f"No files matched {glob_pattern} under {source}", fg="yellow"
                ),
                err=True,
            )
            sys.exit(0)

    target_dir.mkdir(parents=True, exist_ok=True)

    converted = 0
    skipped: list[tuple[Path, str]] = []

    for src in sources:
        try:
            raw = json.loads(src.read_text())
        except (json.JSONDecodeError, OSError) as e:
            skipped.append((src, f"read: {e}"))
            continue

        # Accept either the bare Trace dict shape ({"events": [...], "metadata": {...}})
        # OR the richer ``export_trace()`` envelope (same shape, extra metadata).
        # Reject OTLP input — that's already in the target shape and would
        # silently duplicate rather than convert.
        if "resourceSpans" in raw:
            skipped.append((src, "already OTLP JSON — refusing to re-wrap"))
            continue
        if "events" not in raw:
            skipped.append((src, "no 'events' key — not a Sponsio trace dump"))
            continue

        try:
            trace = Trace.from_dict(raw)
        except (KeyError, TypeError) as e:
            skipped.append((src, f"parse: {e}"))
            continue

        effective_agent = (
            agent_id or (raw.get("metadata") or {}).get("agent_id") or None
        )
        otlp = trace_to_otlp(trace, agent_id=effective_agent)

        # Figure out output filename + label prefix
        stem = src.stem
        if label == "none":
            out_name = f"{stem}.json"
        else:
            # Don't double-prefix if the source already has safe_/unsafe_
            lowered = stem.lower()
            if lowered.startswith(("safe_", "safe-", "unsafe_", "unsafe-")):
                out_name = f"{stem}.json"
            else:
                out_name = f"{label}_{stem}.json"

        out_path = target_dir / out_name
        out_path.write_text(json.dumps(otlp, indent=2))
        converted += 1

    click.echo(
        click.style("✓ ", fg="green")
        + f"Converted {converted} trace(s) to {target_dir}"
    )
    if skipped:
        click.echo(click.style("  skipped:", fg="yellow"))
        for p, why in skipped:
            click.echo(f"    · {p.name} — {why}")


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

    Use this BEFORE flipping ``SPONSIO_MODE=enforce`` — it answers
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
            for field_value in (ce.assumption, ce.enforcement):
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
        # top) when there's no baseline — every existing script
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
            "(gate failed — fix the regression first)",
            fg="yellow",
        )

    if gate_failures:
        sys.exit(1)


@cli.command()
@click.argument(
    "target",
    type=click.Path(file_okay=True, dir_okay=True, path_type=Path),
    default=".",
    required=False,
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing sponsio.yaml without prompting.",
)
@click.option(
    "--provider",
    type=click.Choice(["openai", "anthropic", "gemini", "bedrock", "none"]),
    default=None,
    help="Skip the provider prompt.",
)
@click.option(
    "--mode",
    type=click.Choice(["observe", "enforce"]),
    default=None,
    help="Skip the mode prompt.",
)
@click.option(
    "--judge-fallback",
    type=click.Choice(["allow", "deny", "skip"]),
    default=None,
    help="Skip the judge-fallback prompt.",
)
@click.option(
    "--no-sample",
    is_flag=True,
    help="Don't include a starter contract block.",
)
@click.option(
    "--with-example",
    is_flag=True,
    help=(
        "Skip the wizard and copy a runnable example bundle "
        "(sponsio.yaml + traces/) into TARGET so you can run "
        "`sponsio eval traces/` immediately.  Mutually exclusive "
        "with the wizard flags."
    ),
)
def init(
    target: Path,
    force: bool,
    provider: str | None,
    mode: str | None,
    judge_fallback: str | None,
    no_sample: bool,
    with_example: bool,
):
    """Interactive setup wizard — generates a starter ``sponsio.yaml``.

    Walks you through provider, API-key strategy, runtime mode, and
    judge fallback in four prompts.  Each ``--flag`` skips the
    corresponding prompt, so the same command can run fully
    non-interactively in CI or docs:

    \b
        sponsio init --provider gemini --mode observe \\
                     --judge-fallback allow --no-sample --force

    \b
    Pass ``--with-example`` to skip the wizard entirely and drop a
    pre-tuned scaffolding (sponsio.yaml + 6 labelled traces) into
    TARGET — useful for `sponsio eval` smoke tests and demos.

    Examples:\n
        sponsio init                          # full wizard\n
        sponsio init src/                     # write to src/sponsio.yaml\n
        sponsio init --provider none          # rule-based parsing only\n
        sponsio init . --with-example         # drop runnable scaffold
    """
    if with_example:
        # Wizard flags don't apply — the bundled YAML is hand-tuned
        # to the bundled traces.  Surface that conflict explicitly
        # rather than silently dropping the user's flags.
        conflicting = [
            name
            for name, val in [
                ("--provider", provider),
                ("--mode", mode),
                ("--judge-fallback", judge_fallback),
                ("--no-sample", no_sample or None),
            ]
            if val is not None
        ]
        if conflicting:
            raise click.UsageError(
                f"--with-example is incompatible with: {', '.join(conflicting)}.  "
                "Run those flags WITHOUT --with-example to use the wizard."
            )
        from sponsio.init_wizard import run_with_example

        try:
            run_with_example(target, force=force)
        except click.ClickException as e:
            click.secho(f"\n{e.message}", fg="red", err=True)
            sys.exit(1)
        return

    from sponsio.init_wizard import run_wizard

    try:
        run_wizard(
            target,
            force=force,
            provider=provider,
            mode=mode,
            judge_fallback=judge_fallback,
            no_sample=no_sample,
        )
    except click.Abort:
        sys.exit(1)


@cli.command()
@click.argument(
    "path",
    type=click.Path(file_okay=True, dir_okay=True, path_type=Path),
    default=".",
    required=False,
)
@click.option(
    "--llm",
    is_flag=True,
    help=(
        "Make a real LLM call to verify connectivity, latency, and "
        "credentials.  Opt-in because it costs a few tokens and ~1s; "
        "default ``doctor`` is fully offline."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help=(
        "Emit a structured JSON report instead of the human-readable "
        "table.  Schema is stable per `schema_version`.  Use for "
        "IDE integrations, CI gates, fleet dashboards, or piping into "
        "`jq` / wrapper scripts."
    ),
)
def doctor(path: Path, llm: bool, as_json: bool):
    """Diagnose your Sponsio install and project wiring.

    Runs a short battery of mostly-offline checks — Python version,
    sponsio import sanity, optional SDK availability, LLM credentials,
    ``sponsio.yaml`` validation, a project-level AST scan, and an
    end-to-end guard smoke-test — and prints a single report telling
    you exactly what to run next.

    Pass ``--llm`` to also make a real LLM round-trip (uses the
    provider/key from ``sponsio.yaml``'s ``extractor:`` section if
    present, env-var auto-detection otherwise).

    Exits non-zero if any check fails (warnings are advisory and don't
    change the exit code), so ``doctor`` is safe to wire into CI as a
    pre-flight sanity gate.

    Examples:\n
        sponsio doctor\n
        sponsio doctor src/\n
        sponsio doctor --llm\n
        sponsio doctor path/to/sponsio.yaml --llm
    """
    from sponsio.doctor import print_report, report_to_dict, run_doctor

    results, exit_code = run_doctor(path, with_llm=llm)
    if as_json:
        # Suppress the human-readable banner — JSON consumers want
        # exactly one parseable document on stdout, nothing else.
        click.echo(json.dumps(report_to_dict(results, exit_code), indent=2))
    else:
        print_report(results)
    sys.exit(exit_code)


# ---------------------------------------------------------------------------
# bench
# ---------------------------------------------------------------------------


@cli.command()
@click.argument(
    "config_path",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
    default="sponsio.yaml",
    required=False,
)
@click.option(
    "--agent",
    "agent_id",
    default=None,
    help=(
        "Agent ID to benchmark.  Defaults to the single agent in the "
        "config; required when the config defines multiple agents."
    ),
)
@click.option(
    "--iterations",
    "-n",
    default=100_000,
    show_default=True,
    help=(
        "Number of synthetic ``guard_before`` calls to issue.  The "
        "first ``--warmup`` samples are excluded from the final "
        "report so JIT / page-fault / cold-cache effects don't skew "
        "the steady-state number."
    ),
)
@click.option(
    "--warmup",
    default=1_000,
    show_default=True,
    help="Iterations discarded from the percentile summary.",
)
@click.option(
    "--actions",
    default=None,
    help=(
        "Comma-separated list of tool names to fire.  When omitted, "
        "bench rotates through every tool referenced by any contract "
        "in the config — so every contract is exercised at least "
        "every ``len(tools)`` checks.  Falls back to a synthetic "
        "``bench_tool`` if no contract mentions a concrete tool name."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help=(
        "Emit the bench report as JSON instead of the aligned table.  "
        "The JSON shape matches ``guard.performance_stats()`` plus "
        "``iterations`` / ``warmup`` / ``wall_clock_s`` fields for "
        "run-over-run diffing in CI."
    ),
)
def bench(
    config_path: Path,
    agent_id: str | None,
    iterations: int,
    warmup: int,
    actions: str | None,
    as_json: bool,
):
    """Run a synthetic benchmark against contracts in ``sponsio.yaml``.

    Quantifies the speed of Sponsio's enforcement for a given
    contract set: steady-state p50/p99 latency per contract, total
    QPS, and the fraction of checks that never invoked an LLM.  Use
    this to:

    \b
    * Get numbers for blog posts / benchmarks ("p99 = 3.2μs").
    * Catch perf regressions: run ``bench --json > perf.json`` on
      ``main`` and in the PR branch, diff the numbers.
    * Validate that a contract you thought was pure-DFA *actually*
      compiles down to one (if the ``pure_det`` bucket is empty or
      the ``sto_live`` bucket is non-zero, it didn't).

    By default ``bench`` invokes each contract-relevant tool in a
    deterministic rotation so every contract fires at least once
    per ``len(tools)`` iterations.  Override with ``--actions`` when
    you want a specific pattern (e.g. skew-test a single hot tool).

    The run happens in ``mode=enforce``, ``verbose=False`` (no
    per-event printing would drown the timing signal), and the
    contract banner is suppressed.

    Examples:\n
        sponsio bench\n
        sponsio bench sponsio.yaml -n 1000000\n
        sponsio bench --actions search_web,send_email\n
        sponsio bench --json > perf.json
    """
    # Import locally: ``bench`` pulls in the full guard machinery,
    # and ``sponsio --help`` shouldn't pay for that at CLI startup.
    import io
    import time
    from contextlib import redirect_stdout

    from sponsio.config import load_config
    from sponsio.integrations.base import BaseGuard
    from sponsio.runtime.perf import format_summary

    parsed = load_config(config_path)
    if not parsed.agents:
        click.secho(
            f"error: {config_path} defines no agents.",
            fg="red",
            err=True,
        )
        sys.exit(2)

    # Resolve the target agent.  Single-agent configs don't need
    # ``--agent``; multi-agent configs do.
    if agent_id is None:
        if len(parsed.agents) == 1:
            agent_id = next(iter(parsed.agents))
        else:
            click.secho(
                f"error: config has multiple agents "
                f"{sorted(parsed.agents)}; pass --agent=<id>.",
                fg="red",
                err=True,
            )
            sys.exit(2)
    elif agent_id not in parsed.agents:
        click.secho(
            f"error: agent {agent_id!r} not found in {config_path}. "
            f"Available: {sorted(parsed.agents)}",
            fg="red",
            err=True,
        )
        sys.exit(2)

    # Figure out which tool names to rotate through.  Priority:
    #   1. explicit ``--actions`` flag
    #   2. tool names referenced in contract NL text (regex-mined)
    #   3. synthetic fallback ``bench_tool`` (a contract with no
    #      tool names still needs *something* to run against)
    tool_set: list[str]
    if actions:
        tool_set = [t.strip() for t in actions.split(",") if t.strip()]
        if not tool_set:
            click.secho("error: --actions produced an empty list.", fg="red", err=True)
            sys.exit(2)
    else:
        tool_set = sorted(_extract_tool_names_from_config(parsed, agent_id))
        if not tool_set:
            tool_set = ["bench_tool"]

    # Silence the banner that BaseGuard prints on __init__ — it
    # would appear *before* the table and look like chaff.  The
    # banner goes to stdout so a redirect is the simplest gag.
    banner_sink = io.StringIO()
    with redirect_stdout(banner_sink):
        guard = BaseGuard(
            config=str(config_path),
            agent_id=agent_id,
            mode="enforce",
            verbose=False,
        )
    # Users who ``| tee``'d stdout still want to see the banner once;
    # emit it to stderr so pure stdout stays parseable for --json.
    banner = banner_sink.getvalue().rstrip()
    if banner and not as_json:
        click.echo(banner, err=True)

    n_tools = len(tool_set)

    # Warmup: we keep the samples that land in the tracker (can't
    # cheaply reach in and delete them mid-run) and compensate by
    # resetting the tracker *after* warmup.  One extra reset is
    # cheaper than hacking the hot loop to branch on "am I past
    # warmup".
    for i in range(warmup):
        guard.guard_before(tool_set[i % n_tools], {"i": i})
    guard._monitor.performance_tracker.reset()

    t0 = time.perf_counter()
    for i in range(iterations):
        guard.guard_before(tool_set[i % n_tools], {"i": i})
    wall_s = time.perf_counter() - t0

    summary = guard._monitor.performance_tracker.summarize()

    if as_json:
        out = summary.to_dict()
        out["iterations"] = iterations
        out["warmup"] = warmup
        out["wall_clock_s"] = round(wall_s, 6)
        out["effective_qps"] = round(iterations / wall_s, 1) if wall_s > 0 else 0.0
        out["tools"] = tool_set
        out["agent_id"] = agent_id
        click.echo(json.dumps(out, indent=2))
        return

    click.echo(
        f"sponsio bench — {iterations:,} iters ({warmup:,} warmup), "
        f"{len(tool_set)} tool{'s' if len(tool_set) != 1 else ''} "
        f"[{', '.join(tool_set[:3])}{'...' if len(tool_set) > 3 else ''}]"
    )
    click.echo(
        f"wall: {wall_s:.3f}s   end-to-end throughput: "
        f"{iterations / wall_s:,.0f} check/s"
        if wall_s > 0
        else f"wall: {wall_s:.3f}s"
    )
    click.echo(format_summary(summary, color=sys.stdout.isatty()))


def _extract_tool_names_from_config(parsed, agent_id: str) -> set[str]:
    """Mine plausible tool names from contract NL strings.

    Cheap regex-based heuristic — full NL parsing would pull in the
    extractor LLM which is both slow and defeats the point of a
    benchmark.  We look for two patterns:

    * the ``tools:`` inventory (highest quality: user-declared)
    * bare identifier-like tokens in each contract's NL string
      that also appear in the ``tools:`` inventory, or that match
      the common ``<verb>_<noun>`` Python-ish tool naming

    Returns an empty set rather than an error when nothing parses —
    caller falls back to a synthetic tool so bench still runs.
    """
    import re as _re

    declared = {t.name for t in parsed.tools if t.name}
    if declared:
        return declared

    tool_re = _re.compile(r"\b[a-z][a-z0-9_]{2,}\b")
    found: set[str] = set()
    ac = parsed.agents.get(agent_id)
    if ac is None:
        return found
    # Scan both assumption and enforcement NL bodies.  Pattern
    # entries (structured, ``pattern:/args:``) are typed and don't
    # reveal a tool name without compilation; skip them — the user
    # should declare ``tools:`` in that case anyway.
    for contract in ac.contracts:
        for field_val in (contract.assumption, contract.enforcement):
            if field_val is None:
                continue
            entries = field_val if isinstance(field_val, list) else [field_val]
            for entry in entries:
                if entry.nl:
                    for tok in tool_re.findall(entry.nl):
                        # Filter common stop-words that happen to
                        # be snake_case-shaped so they don't end up
                        # firing checks with nonsense tool names.
                        if tok in _BENCH_STOPWORDS:
                            continue
                        found.add(tok)
    return found


# A small stopword list: words that look identifier-ish but are
# actually NL glue when they appear in a contract DSL body.  Kept
# minimal — false-positive tool names just waste cycles, false
# *negatives* (dropping a real tool name) are the only dangerous
# direction and these words don't look like tools.
_BENCH_STOPWORDS = {
    "and",
    "or",
    "not",
    "always",
    "never",
    "after",
    "before",
    "whenever",
    "must",
    "should",
    "then",
    "when",
    "with",
    "without",
    "the",
    "is",
    "are",
    "has",
    "have",
    "that",
    "this",
    "contains",
    "called",
    "only",
    "can",
    "cannot",
}


# ---------------------------------------------------------------------------
# onboard
# ---------------------------------------------------------------------------


@cli.command()
@click.argument(
    "target",
    type=click.Path(file_okay=True, dir_okay=True, path_type=Path),
    default=".",
    required=False,
)
@click.option(
    "--agent",
    "agent_id",
    default="agent",
    show_default=True,
    help=(
        "Agent identifier stamped into sponsio.yaml.  Matches "
        "`sponsio scan`'s default so a later `scan --append` lands "
        "in the same agent block."
    ),
)
@click.option(
    "--mode",
    type=click.Choice(["observe", "enforce"]),
    default="observe",
    show_default=True,
    help=(
        "Runtime mode written into sponsio.yaml.  Observe is the "
        "safe default — never blocks, logs every would-have-blocked "
        "decision to ~/.sponsio/sessions/<agent_id>/*.jsonl."
    ),
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing sponsio.yaml without prompting.",
)
@click.option(
    "--no-probe-ollama",
    is_flag=True,
    help=(
        "Skip the localhost:11434 liveness probe.  Useful in CI or "
        "behind strict firewalls where the <500ms probe still times "
        "out slowly and you'd rather jump straight to the starter pack."
    ),
)
@click.option(
    "--apply",
    is_flag=True,
    help=(
        "Auto-patch the detected agent entry file with the Sponsio "
        "wrap.  Writes a `.sponsio.bak` backup and validates the "
        "result re-parses before saving.  Currently supported for "
        "langgraph / langchain; other frameworks fall back to the "
        "printed snippet."
    ),
)
@click.option(
    "--no-doctor",
    is_flag=True,
    help=(
        "Skip the post-onboard `sponsio doctor` run.  By default we "
        "run the full offline check battery so users see whether the "
        "install is healthy before they switch to enforce mode."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the structured OnboardReport as JSON instead of text.",
)
def onboard(
    target: Path,
    agent_id: str,
    mode: str,
    force: bool,
    no_probe_ollama: bool,
    apply: bool,
    no_doctor: bool,
    as_json: bool,
):
    """One-shot project wire-up — detect framework, write sponsio.yaml, print patch.

    Composes `init` + `scan` + `doctor` into a single command so
    first-time users don't have to learn three subcommands just to
    run the guard in observe mode.  Specifically:

    \b
      1. Detects the agent framework from imports + dependencies.
      2. Detects the best available LLM provider (env vars →
         OPENAI_BASE_URL → local Ollama → none).
      3. Writes sponsio.yaml in observe mode with an inferred contract
         set — LLM-inferred when a provider was found, or pure name-
         heuristic starter pack when it wasn't.
      4. Prints the framework-specific 2-line patch the user needs to
         apply to their agent entry point.

    Safe defaults throughout: mode=observe (never blocks on day 1),
    agent_id="agent" (matches `sponsio scan`), and --force off (the
    "I already have sponsio.yaml" case is louder than a silent overwrite).

    Examples:\n
        sponsio onboard\n
        sponsio onboard src/\n
        sponsio onboard . --agent customer_bot\n
        sponsio onboard --force --no-probe-ollama
    """
    from sponsio.onboard import OnboardReport, run_onboard

    def _progress(msg: str) -> None:
        if not as_json:
            click.echo(click.style("· ", fg="cyan", dim=True) + msg, err=True)

    try:
        report: OnboardReport = run_onboard(
            target,
            agent_id=agent_id,
            mode=mode,
            force=force,
            probe_ollama=not no_probe_ollama,
            apply=apply,
            run_doctor=not no_doctor,
            progress=_progress,
        )
    except FileExistsError as e:
        click.echo(click.style("Error: ", fg="red") + str(e), err=True)
        sys.exit(1)

    if as_json:
        click.echo(json.dumps(report.to_dict(), indent=2))
        return

    # Human-readable summary.  Kept compact so the wrap snippet is the
    # last thing the user sees — it's what they need to act on.
    click.echo()
    click.secho(f"✓ {report.out_path}", fg="green")
    click.echo(f"  tools:      {report.tools_count}")
    click.echo(f"  contracts:  {report.contracts_count}")
    click.echo(f"  mode:       {report.mode}")
    click.echo(f"  framework:  {report.framework.framework}")
    click.echo(f"  provider:   {report.provider.provider}")
    if report.starter_pack_used:
        click.secho(
            "  · starter-pack applied (no-LLM safety net)",
            fg="yellow",
            dim=True,
        )

    if report.doctor_results is not None:
        total = len(report.doctor_results)
        fails = sum(1 for r in report.doctor_results if r.status == "fail")
        warns = sum(1 for r in report.doctor_results if r.status == "warn")
        if fails == 0 and warns == 0:
            click.secho(f"  ✓ doctor:   {total}/{total} checks passed", fg="green")
        else:
            click.echo(
                f"  doctor:     {total - fails - warns}/{total} ok"
                + (click.style(f", {warns} warn", fg="yellow") if warns else "")
                + (click.style(f", {fails} fail", fg="red") if fails else "")
            )
            for r in report.doctor_results:
                if r.status in {"fail", "warn"}:
                    color = "red" if r.status == "fail" else "yellow"
                    click.echo(
                        f"    {click.style(r.icon, fg=color)} {r.name}: {r.detail}"
                    )

    for w in report.warnings:
        click.echo()
        click.echo(click.style("  warn: ", fg="yellow") + w)

    # Apply path: show diff when a patch was written, else fall back
    # to the snippet so the user still sees what to do.
    ar = report.apply_result
    if ar is not None and getattr(ar, "applied", False):
        click.echo()
        click.secho(f"✓ patched {ar.file} (backup: {ar.backup})", fg="green")
        if ar.diff:
            click.echo(ar.diff)
    else:
        if ar is not None and ar.reason and not ar.error:
            click.echo()
            click.echo(
                click.style("· apply skipped: ", fg="bright_black", dim=True)
                + ar.reason
            )
        click.echo()
        click.secho("Add this to your agent entry point:", bold=True)
        click.echo()
        for ln in report.wrap_snippet.splitlines():
            click.echo(f"  {click.style(ln, fg='cyan')}")

    click.echo()
    click.echo("Next:")
    click.echo("  sponsio report --since 24h            # what would have been blocked")
    click.echo(
        "  # after a day or two of real traffic, flip `mode: enforce` in sponsio.yaml"
    )
    click.echo()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main():
    cli()


if __name__ == "__main__":
    main()
