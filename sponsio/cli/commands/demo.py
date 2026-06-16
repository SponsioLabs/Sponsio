"""``sponsio demo`` — run a packaged demo scenario."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

from sponsio.cli.app import cli


@cli.command()
@click.option(
    "--scenario",
    default="cleanup",
    type=click.Choice(["cleanup", "backup", "wire", "freeze"], case_sensitive=False),
    help="Demo scenario: cleanup (default), backup, wire, freeze",
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

    Four trajectory replays showing unsafe agent behavior and the
    contracts that block it. The default mock mode works from a plain
    PyPI install with no API key and no optional framework SDKs.

    \b
      cleanup . Claude Code cleanup agent deletes `.env` & `.git/`
      backup  . SRE cost-optimizer deletes prod DR backups (OWASP ASI-10)
      wire    . AP copilot wires $847k to an unverified vendor (OWASP ASI-09)
      freeze  . Replit-style agent violates code freeze + hides it (OWASP ASI-10)

    Examples:\n
        sponsio demo\n
        sponsio demo --scenario freeze --fast\n
        sponsio demo --scenario wire --no-guard\n
        sponsio demo --mode integration --scenario freeze
    """
    scenario_map = {
        "cleanup": ("demo_coding_cleanup.py", "Coding Agent \u2014 Cleanup gone rogue"),
        "backup": (
            "demo_backup_delete.py",
            "SRE Cost-Optimizer \u2014 Prod DR backups deleted",
        ),
        "wire": (
            "demo_wire_transfer.py",
            "AP Copilot \u2014 Fraudulent wire transfer",
        ),
        "freeze": (
            "demo_freeze_violation.py",
            "Coding Agent \u2014 Code-freeze violation + coverup",
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

    import sponsio

    # Resolve relative to the installed package, not this file's depth,
    # so it stays correct regardless of where the CLI code lives.
    repo_root = Path(sponsio.__file__).resolve().parent.parent
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
