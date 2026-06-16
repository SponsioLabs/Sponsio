"""``sponsio init`` — interactive project setup wizard."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from sponsio.cli.app import cli


@cli.command()
@click.argument(
    "target",
    type=click.Path(file_okay=True, dir_okay=True, path_type=Path),
    default=".",
    required=False,
)
@click.option(
    "--plan",
    "plan_spec",
    default=None,
    help=(
        "Print the would-run commands for these picks, don't run them.  "
        "Used by IDE-agent wizard prompts for the dry-run preview step."
    ),
)
@click.option(
    "--apply",
    "apply_spec",
    default=None,
    help=(
        "Run the commands for these picks non-interactively.  Picks "
        "format: ``framework=<name>;ides=<ide>:<level>,<ide>:<level>;"
        "mode=observe|enforce`` where ``<level>`` is one of ``none`` / "
        "``skill`` / ``full``.  Legacy ``hosts=`` / ``skills=`` form is "
        "still accepted (``hosts=X`` ↔ ``X:full``, ``skills=X`` ↔ "
        "``X:skill``)."
    ),
)
@click.option(
    "--no-demo",
    is_flag=True,
    help="Skip the post-install demo offer.",
)
def init(
    target: Path,
    plan_spec: str | None,
    apply_spec: str | None,
    no_demo: bool,
):
    """Interactive 4-axis onboarding wizard.

    Walks you through the four decisions that actually matter on
    first run:

    \b
      1. framework wrap        which agent framework to wrap (or "none")
      2. protect host agents   install hooks for claude-code / cursor / openclaw
      3. install Sponsio skill drop SKILL.md into IDEs not picked above
      4. mode                  observe (shadow, default) or enforce (block)

    Three modes:

    \b
      sponsio init                     # interactive TTY (humans)
      sponsio init --plan '<picks>'    # print commands, don't run
      sponsio init --apply '<picks>'   # run commands non-interactively

    Picks string format::

        framework=<name>;ides=<ide>:<level>,<ide>:<level>;mode=<observe|enforce>

    Where ``<level>`` is ``none`` / ``skill`` / ``full``.  Legacy
    ``hosts=<a>,<b>;skills=<a>,<b>`` form is still accepted.

    Examples:\n
        sponsio init\n
        sponsio init --plan 'framework=langgraph;ides=cursor:skill;mode=observe'\n
        sponsio init --apply 'framework=langgraph;ides=cursor:skill;mode=observe'
    """
    from sponsio.init_wizard import (
        apply_commands,
        detect_environment,
        offer_demo,
        parse_picks,
        plan_commands,
        run_interactive,
    )

    if plan_spec is not None and apply_spec is not None:
        raise click.UsageError("--plan and --apply are mutually exclusive")

    target_dir = target if target.is_dir() or not target.suffix else target.parent
    env = detect_environment(target_dir)
    # Detect a pre-existing ``@sponsio/sdk`` install (via ``npm
    # install`` OR ``npm link``) so we can skip the redundant
    # install step in plan.  Skipping is critical for ``npm link``
    # workflows. running ``npm install --save-dev`` against a
    # linked package overwrites the symlink with the published
    # release, silently undoing the user's local-source testing.
    # The legacy ``@sponsio/scan-ts`` package is now a deprecation
    # shim that re-exports ``@sponsio/sdk``'s CLI; counting it as
    # "installed" lets users on the old name finish ``sponsio
    # init`` without an extra install round-trip.
    # The package dir alone isn't enough: a half-broken install can
    # leave ``node_modules/@sponsio/sdk/`` populated but the bin
    # symlink ``node_modules/.bin/sponsio`` missing. which would
    # then make ``npx sponsio onboard`` fall through to the npm
    # public registry (404, since pip-side ``sponsio`` shadows the
    # name).  Require BOTH for the skip-install path.
    _scan_ts_pkg = (target_dir / "node_modules" / "@sponsio" / "sdk").exists() or (
        target_dir / "node_modules" / "@sponsio" / "scan-ts"
    ).exists()
    _scan_ts_bin = (target_dir / "node_modules" / ".bin" / "sponsio").exists()
    _scan_ts_installed = _scan_ts_pkg and _scan_ts_bin

    # ---- non-TTY paths: --plan / --apply ----
    if plan_spec is not None:
        picks = parse_picks(plan_spec)
        cmds = plan_commands(
            picks,
            ts_project=env.runtime == "ts",
            scan_ts_already_installed=_scan_ts_installed,
        )
        if not cmds:
            click.echo(
                "Nothing to do. picks select no framework wrap and no "
                "IDE protection.  Re-run with at least one ``framework=`` "
                "or ``ides=<ide>:full|skill``."
            )
            return
        for cmd in cmds:
            click.echo("would run: " + " ".join(cmd))
        return

    if apply_spec is not None:
        picks = parse_picks(apply_spec)
    else:
        picks = run_interactive(env)

    cmds = plan_commands(
        picks,
        ts_project=env.runtime == "ts",
        scan_ts_already_installed=_scan_ts_installed,
    )
    if not cmds:
        click.echo()
        click.secho(
            "Nothing to install. every axis was set to 'none'.  "
            "Re-run `sponsio init` and pick at least one framework "
            "wrap or IDE level (skill / full).",
            fg="yellow",
        )
        return

    # Dry-run preview before running, even on the interactive path.
    # gives the user a final chance to spot a wrong pick.  Indented
    # col-2 to match the wizard's body content margin (banner col-0,
    # everything below at col-2).
    click.echo()
    click.secho("  preview", bold=True, fg="cyan")
    for cmd in cmds:
        click.echo("    → " + " ".join(cmd))
    click.echo()

    # Skip the confirm gate when called via ``--apply``. the IDE
    # agent already showed me the dry-run preview before invoking,
    # and a second confirmation here would corrupt structured output.
    if apply_spec is None:
        from sponsio.init_wizard import _confirm as _wizard_confirm

        if not _wizard_confirm("Run these?", default=True):
            click.echo()
            click.secho("  ✘  No changes made.", fg="yellow")
            click.echo(
                "      Re-run `sponsio init` whenever you're ready, "
                "or pass\n      `sponsio init --plan '<picks>'` to "
                "preview the commands without prompts."
            )
            return

    rc = apply_commands(cmds)
    if rc != 0:
        sys.exit(rc)

    # Picks-aware "what now" block.  Each combination of axes leaves
    # the user in a different spot. IDE-only installs especially
    # were ending without any concrete next action.  Route through
    # the helper so each path gets a tailored handoff.
    from sponsio.init_wizard import print_next_steps as _print_next_steps

    _print_next_steps(picks, ts_project=env.runtime == "ts")

    if not no_demo:
        offer_demo()
