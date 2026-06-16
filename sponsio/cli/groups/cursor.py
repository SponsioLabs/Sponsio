"""``sponsio cursor`` — Cursor IDE hook integration (guard / install-hooks)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from sponsio.cli.app import cli


@cli.group()
def cursor():
    """Cursor IDE integration. install hooks, run as a hook handler.

    Cursor 1.7+ ships a deny-capable hook system (``hooks.json``).
    Sponsio plugs in as the command for the relevant pre-* events, so
    every Shell/Read/Write/MCP call gets evaluated against the
    Sponsio contract library before Cursor executes it.

    Two subcommands:

    * ``sponsio cursor install-hooks``. one-time setup that writes
      ``~/.cursor/hooks.json`` (or project-scoped ``.cursor/hooks.json``)
      so Cursor calls back into ``sponsio cursor guard`` per tool call.

    * ``sponsio cursor guard --event <name>``. runtime hook handler.
      Reads a Cursor hook payload from stdin, evaluates it, writes the
      Cursor-shaped JSON decision and signals deny via exit code 2.
    """


_CURSOR_HOOK_EVENTS = (
    "preToolUse",
    "beforeShellExecution",
    "beforeMCPExecution",
    "beforeReadFile",
    "beforeTabFileRead",
    "beforeSubmitPrompt",
    "postToolUse",
    "afterShellExecution",
    "afterMCPExecution",
    "afterFileEdit",
    "subagentStart",
    "subagentStop",
)


@cursor.command(name="guard")
@click.option(
    "--event",
    "hook_event",
    type=click.Choice(_CURSOR_HOOK_EVENTS),
    default="preToolUse",
    show_default=True,
    help="Which Cursor hook event this invocation is handling.",
)
def cursor_guard(hook_event: str):
    """Cursor hook handler. evaluates one Cursor hook payload.

    Wired into ``hooks.json`` per Cursor's command-based hook protocol::

        {
          "version": 1,
          "hooks": {
            "preToolUse": [{"command": "sponsio cursor guard --event preToolUse",
                             "failClosed": true}]
          }
        }

    Reads the Cursor JSON payload from stdin, normalises it to
    Sponsio's plugin-id routing scheme, runs the per-plugin contract
    library, and writes the Cursor-shaped reply
    (``{"permission":"deny","user_message":..., "agent_message":...}``
    + exit 2) on a violation.

    Exits 0 on every internal error so a Sponsio bug never wedges a
    real tool call.
    """
    from sponsio.integrations.cursor import run_cursor_stdin

    sys.exit(run_cursor_stdin(hook_event))


@cursor.command(name="install-hooks")
@click.option(
    "--scope",
    type=click.Choice(["user", "project"]),
    default="user",
    show_default=True,
    help=(
        "``user`` → ``~/.cursor/hooks.json`` (covers every Cursor "
        "session for this user).  ``project`` → ``./.cursor/hooks.json`` "
        "(covers only this repo, follows committed config)."
    ),
)
@click.option(
    "--fail-closed/--fail-open",
    default=True,
    show_default=True,
    help=(
        "When the hook script itself fails (Sponsio crashes, missing "
        "library, …), should Cursor block the tool call?  Default is "
        "fail-closed: Sponsio failure → tool call blocked, surface a "
        "user message.  Set ``--fail-open`` to prefer availability "
        "over enforcement."
    ),
)
@click.option(
    "--force",
    is_flag=True,
    help=(
        "Overwrite the entire ``hooks.json``.  Default behaviour merges "
        "Sponsio's hook entries into the existing file. leaves any "
        "user-authored hooks untouched."
    ),
)
@click.option(
    "--binary",
    "binary_override",
    type=str,
    default=None,
    help=(
        "Absolute path to the ``sponsio`` binary to invoke from the "
        "hook.  Defaults to the binary backing the current process. "
        "always an absolute path, since Cursor launches hook "
        "subprocesses from launchd's bare PATH which excludes venvs "
        "and ``~/.local/bin``.  Pass ``--binary sponsio`` to fall "
        "back to bare-name lookup at hook fire time."
    ),
)
def cursor_install_hooks(
    scope: str, fail_closed: bool, force: bool, binary_override: str | None
):
    """Install Sponsio as a Cursor hook handler.

    Writes (or merges into) Cursor's ``hooks.json`` so Cursor invokes
    ``sponsio cursor guard --event <name>`` for the events Sponsio
    cares about (``preToolUse``, ``beforeShellExecution``,
    ``beforeMCPExecution``, ``beforeReadFile``, ``beforeSubmitPrompt``,
    ``postToolUse``).

    After installing, restart Cursor so the new ``hooks.json`` is
    picked up.  Run ``sponsio doctor`` to verify the install.
    """
    target = (
        Path.cwd() / ".cursor" / "hooks.json"
        if scope == "project"
        else Path.home() / ".cursor" / "hooks.json"
    )

    # Cursor launches hook subprocesses from launchd's bare PATH.
    # ``.zshrc`` / venv activate scripts are NOT sourced.  A bare
    # ``sponsio`` will resolve via that minimal PATH, which on macOS
    # commonly hits a stale user-pip install at
    # ``~/Library/Python/3.x/bin/sponsio`` instead of the active venv.
    # Default to the absolute path of the binary backing the current
    # process so the hook always invokes the *same* sponsio the user
    # ran ``install-hooks`` from.
    if binary_override:
        bin_cmd = binary_override
    else:
        import shutil

        # ``sys.argv[0]`` is the cleanest pointer to the running
        # console-script when invoked via the entry-point shim;
        # fall back to ``shutil.which`` if for some reason it's
        # relative (e.g. test harness invocation).
        candidate = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
        if candidate and candidate.is_absolute() and candidate.exists():
            bin_cmd = str(candidate)
        else:
            resolved = shutil.which("sponsio")
            bin_cmd = resolved or "sponsio"

    sponsio_hooks: dict[str, list[dict]] = {
        "preToolUse": [
            {
                "command": f"{bin_cmd} cursor guard --event preToolUse",
                "failClosed": fail_closed,
            }
        ],
        "beforeShellExecution": [
            {
                "command": f"{bin_cmd} cursor guard --event beforeShellExecution",
                "failClosed": fail_closed,
            }
        ],
        "beforeMCPExecution": [
            {
                "command": f"{bin_cmd} cursor guard --event beforeMCPExecution",
                "failClosed": fail_closed,
            }
        ],
        "beforeReadFile": [
            {
                "command": f"{bin_cmd} cursor guard --event beforeReadFile",
                "failClosed": fail_closed,
            }
        ],
        "beforeSubmitPrompt": [
            {
                "command": f"{bin_cmd} cursor guard --event beforeSubmitPrompt",
            }
        ],
        "postToolUse": [
            {
                "command": f"{bin_cmd} cursor guard --event postToolUse",
            }
        ],
    }

    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and not force:
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except json.JSONDecodeError:
            click.echo(
                f"⚠  {target} exists but is not valid JSON. refusing to "
                "merge.  Re-run with --force to overwrite, or fix the "
                "file by hand.",
                err=True,
            )
            sys.exit(1)
        merged = dict(existing)
        merged.setdefault("version", 1)
        existing_hooks = (
            merged.get("hooks") if isinstance(merged.get("hooks"), dict) else {}
        )
        for event_name, entries in sponsio_hooks.items():
            keep: list[dict] = []
            for prior in existing_hooks.get(event_name, []) or []:
                # Keep non-Sponsio entries verbatim; replace any prior
                # Sponsio entry so version drift gets cleaned up.
                if (
                    isinstance(prior, dict)
                    and isinstance(prior.get("command"), str)
                    and "cursor guard --event" in prior["command"]
                ):
                    continue
                keep.append(prior)
            existing_hooks[event_name] = keep + entries
        merged["hooks"] = existing_hooks
        out = merged
    else:
        out = {"version": 1, "hooks": sponsio_hooks}

    target.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    click.echo(f"✔  Wrote Cursor hooks to {target}")
    click.echo(
        "   Restart Cursor (or open a new composer session) so the new "
        "hooks.json is picked up."
    )
    click.echo("   Verify with: cat " + str(target) + " | jq '.hooks | keys'")
