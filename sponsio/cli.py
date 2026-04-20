"""Sponsio CLI entry point."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import click


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
    default="customer",
    type=click.Choice(["customer", "coding", "mcp"], case_sensitive=False),
    help="Demo scenario: customer (default), coding, mcp",
)
@click.option(
    "--real",
    is_flag=True,
    default=False,
    help="Use real LLM via LangGraph (requires GOOGLE_API_KEY)",
)
def demo(scenario: str, real: bool):
    """Run a Sponsio demo in your terminal.

    Default: mock mode, no API key needed.

    Examples:\n
        sponsio demo --scenario customer\n
        sponsio demo --scenario coding\n
        sponsio demo --scenario mcp\n
        sponsio demo --scenario customer --real
    """
    scenario_map = {
        "customer": "demo_customer_service.py",
        "coding": "demo_coding_agent.py",
        "mcp": "demo_mcp_leak.py",
    }

    script_name = scenario_map[scenario]
    examples_dir = os.path.join(os.path.dirname(__file__), "..", "examples", "demo")
    script_path = os.path.normpath(os.path.join(examples_dir, script_name))

    if not os.path.exists(script_path):
        click.echo(
            click.style(f"Error: demo script not found at {script_path}", fg="red")
        )
        sys.exit(1)

    if real and not os.environ.get("GOOGLE_API_KEY"):
        click.echo(
            click.style("Error: --real requires GOOGLE_API_KEY to be set.", fg="red")
        )
        click.echo("  export GOOGLE_API_KEY=your_key")
        sys.exit(1)

    env = os.environ.copy()
    env["USE_MOCK"] = "0" if real else "1"

    scenario_labels = {
        "customer": "Customer Service \u2014 Refund Workflow",
        "coding": "Coding Agent \u2014 Database Safety",
        "mcp": "MCP Agent \u2014 Data Leak Prevention",
    }
    mode_label = "real LLM" if real else "mock mode"

    click.echo()
    click.echo(click.style("Sponsio Demo", bold=True))
    click.echo(click.style(f"  {scenario_labels[scenario]}", fg="cyan"))
    click.echo(
        click.style(f"  Mode: {mode_label}", fg="green" if not real else "yellow")
    )
    click.echo()

    try:
        subprocess.run([sys.executable, script_path], env=env, check=True)
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

    Examples:\n
        sponsio validate "tool `A` must precede `B`"\n
        sponsio validate --config sponsio.yaml\n
        sponsio validate --config sponsio.yaml --agent customer_bot
    """
    from sponsio.generation.nl_to_contract import parse_nl_unified

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
                        result = parse_nl_unified(nl)
                        ok = result.is_det or result.is_sto
                        if result.is_det:
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
                            kind = "ERROR"
                            nl = entry.nl
                else:
                    nl = str(entry)
                    result = parse_nl_unified(nl)
                    ok = result.is_det or result.is_sto

                    if result.is_det:
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


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def _resolve_entry(entry):
    """Resolve a constraint entry (string or ConstraintEntry) to (nl_text, parsed_result).

    For structured entries (pattern + args), compiles directly.
    For NL strings, runs through parse_nl_unified.
    """
    from sponsio.config import ConstraintEntry, _compile_structured
    from sponsio.generation.nl_to_contract import UnifiedParseResult, parse_nl_unified

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
            return nl, parse_nl_unified(nl)
    else:
        nl = str(entry)
        return nl, parse_nl_unified(nl)


def _otel_to_trace(data: dict):
    """Convert OTEL JSON to Sponsio Trace. Delegates to otel_consumer module."""
    from sponsio.tracer.otel_consumer import otel_to_trace

    return otel_to_trace(data)


@cli.command()
@click.option(
    "--trace",
    "-t",
    "trace_path",
    required=True,
    type=click.Path(exists=True),
    help="OTEL trace JSON file",
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

    # Load trace
    with open(trace_path) as f:
        trace_data = json.load(f)

    trace = _otel_to_trace(trace_data)

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
@click.option("--port", "-p", default=8000, type=int, help="Port (default: 8000)")
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
    type=click.Choice(["gemini", "openai"]),
    help="LLM provider (default: auto-detect from env)",
)
@click.option("--out", "-o", type=click.Path(), help="Write sponsio.yaml to this path")
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
    "--push/--no-push",
    default=True,
    help="Auto-push the YAML to the local dashboard if it's running (default: on)",
)
@click.option(
    "--push-url",
    default="http://127.0.0.1:8000",
    help="Dashboard URL to push to (default: http://127.0.0.1:8000)",
)
def scan(
    paths: tuple[str, ...],
    agent: str,
    llm: bool,
    model: str | None,
    provider: str | None,
    out: str | None,
    append: bool,
    policy: tuple[str, ...],
    push: bool,
    push_url: str,
):
    """Scan source code and policy docs to propose contracts.

    Analyzes tool definitions, decorators, and call patterns to infer
    safety constraints. Optionally extracts constraints from policy
    documents (.md/.txt) using the discovered tool inventory as context.

    \b
    Examples:
      sponsio scan src/                                # rule-based only
      sponsio scan src/ --llm                          # + LLM inference
      sponsio scan src/ --policy security.md --llm     # code + policy
      sponsio scan src/ -o sponsio.yaml                # write config
      sponsio scan src/ -o sponsio.yaml --append       # add to existing
      sponsio scan src/ --no-push                      # skip dashboard push
    """
    from sponsio.discovery.extractors.code_analysis import CodeAnalyzer

    analyzer = CodeAnalyzer(use_llm=llm, llm_model=model, provider=provider)
    source_paths = list(paths)

    # Extract tool inventory for policy document context
    tool_inventory = analyzer.get_tool_inventory(source_paths) if policy else None

    yaml_content = analyzer.generate_yaml(
        source_paths,
        agent_id=agent,
        policy_paths=list(policy),
        tool_inventory=tool_inventory,
    )

    if out:
        if append and os.path.exists(out):
            # Append new guarantees to existing file
            with open(out) as f:
                existing = f.read()
            yaml_content = _merge_yaml(existing, yaml_content)
        with open(out, "w") as f:
            f.write(yaml_content)
        click.echo(
            click.style("✓ ", fg="green")
            + f"Config written to {click.style(out, bold=True)}"
        )
    else:
        click.echo(yaml_content)

    if push:
        _push_scan_to_dashboard(
            yaml_content=yaml_content,
            filename=os.path.basename(out) if out else "sponsio.yaml",
            dashboard_url=push_url,
            source_paths=source_paths,
        )


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
# main
# ---------------------------------------------------------------------------


def main():
    cli()


if __name__ == "__main__":
    main()
