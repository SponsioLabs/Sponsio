"""``sponsio doctor`` — offline health checks."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from sponsio.cli.app import cli


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

    Runs a short battery of mostly-offline checks. Python version,
    sponsio import sanity, optional SDK availability, LLM credentials,
    ``sponsio.yaml`` validation, a project-level AST scan, and an
    end-to-end guard smoke-test. and prints a single report telling
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
        # Suppress the human-readable banner. JSON consumers want
        # exactly one parseable document on stdout, nothing else.
        click.echo(json.dumps(report_to_dict(results, exit_code), indent=2))
    else:
        print_report(results)
    sys.exit(exit_code)
