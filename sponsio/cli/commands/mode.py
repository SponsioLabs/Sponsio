"""``sponsio mode`` — flip an agent between observe and enforce."""

from __future__ import annotations

import re
from pathlib import Path

import click

from sponsio.cli.app import cli


def _patch_mode_in_yaml(text: str, target_mode: str) -> tuple[str, str]:
    r"""Set ``mode:`` to ``target_mode`` in a ``sponsio.yaml`` text body.

    The Python loader reads ``runtime.mode`` AND ``defaults.mode``;
    the TS loader reads only ``runtime.mode``. To stay correct under
    both, this prefers the ``runtime.mode`` line, then falls back to
    ``defaults.mode``, then appends a fresh ``runtime:`` block when
    neither exists.

    A naive ``re.subn(r"^\s*mode:")`` is wrong: it patches whichever
    ``mode:`` line happens to come first in the file. If a yaml has
    both ``runtime.mode`` and ``defaults.mode`` and ``defaults`` is
    listed first, the runtime line silently stays stale and the TS
    loader keeps reading the old value. Walking parent keys avoids
    that whole class of bug.

    Safety policy on appending: when neither block exists, this helper
    will append a fresh ``runtime:`` block ONLY for ``target_mode ==
    "observe"`` (the safe default). For ``target_mode == "enforce"``
    against a malformed / missing-mode yaml, it returns ``"missing"``
    instead of writing, so callers can refuse to silently flip a yaml
    they cannot verify into the blocking posture. This matches the
    OWASP-style principle of explicit-opt-in for enforcement, and
    preserves CI scripts that relied on the old exit-1 behaviour for
    malformed configs.

    Returns:
        ``(new_text, action)`` where ``action`` is one of
        ``"runtime"`` / ``"defaults"`` / ``"appended"`` / ``"unchanged"``
        / ``"missing"``. ``"unchanged"`` means the file already had
        the desired value. ``"missing"`` means no mode line exists and
        target is ``enforce`` so the helper refused to append.
    """
    lines = text.splitlines(keepends=True)
    current_parent: str | None = None
    runtime_idx = -1
    defaults_idx = -1
    mode_line_re = re.compile(r"^(\s+)mode:\s*(observe|enforce)(\s*(?:#.*)?)$")

    for i, raw in enumerate(lines):
        line = raw.rstrip("\n").rstrip("\r")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            # Top-level key like ``runtime:`` / ``defaults:`` / ``agents:``.
            # Track only the most recent top-level key; only valid yaml
            # mappings reach here so ``key:`` parsing is safe.
            if ":" in stripped:
                current_parent = stripped.split(":", 1)[0].strip()
            continue
        if mode_line_re.match(line):
            if current_parent == "runtime" and runtime_idx < 0:
                runtime_idx = i
            elif current_parent == "defaults" and defaults_idx < 0:
                defaults_idx = i

    target_idx = runtime_idx if runtime_idx >= 0 else defaults_idx
    if target_idx >= 0:
        action = "runtime" if target_idx == runtime_idx else "defaults"
        raw = lines[target_idx]
        new_raw = re.sub(
            r"^(\s+mode:\s*)(observe|enforce)(\s*(?:#.*)?)$",
            lambda m: f"{m.group(1)}{target_mode}{m.group(3)}",
            raw.rstrip("\n"),
        )
        # Preserve original line-ending.
        ending = raw[len(raw.rstrip("\n")) :]
        lines[target_idx] = new_raw + ending
        new_text = "".join(lines)
        if new_text == text:
            return text, "unchanged"
        return new_text, action

    # Neither block had a mode line. Append only for the safe default
    # (observe). Refuse to materialise an enforce block out of thin
    # air: a missing mode line is suspicious, and silently flipping
    # such a yaml into the blocking posture would mask CI / config
    # errors that the operator should fix by hand.
    if target_mode != "observe":
        return text, "missing"
    # Using ``runtime:`` (not ``defaults:``) so the TS loader picks it
    # up too. Trailing newline normalised so the appended block doesn't
    # glue to the previous line.
    suffix = "" if text.endswith("\n") or text == "" else "\n"
    appended = f"{suffix}\nruntime:\n  mode: {target_mode}\n"
    return text + appended, "appended"


@cli.command(name="mode")
@click.argument(
    "target_mode",
    metavar="MODE",
    type=click.Choice(["observe", "enforce"]),
)
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("sponsio.yaml"),
    show_default=True,
    help="Path to the sponsio.yaml whose mode should be flipped.",
)
def cmd_mode(target_mode: str, config_path: Path):
    """Flip a sponsio.yaml between `observe` and `enforce` in one shot.

    The expected workflow is:

    \b
        sponsio onboard .            # writes sponsio.yaml in observe
        # ...soak in observe for a day or two, watch `sponsio report`...
        sponsio mode enforce         # one-line flip when you're ready

    Prefers to update the ``runtime.mode:`` line (which both the Python
    and TS loaders read), falling back to ``defaults.mode:`` (Python
    only) or inserting a fresh ``runtime:`` block when neither exists.
    Comments and surrounding lines survive untouched.
    """
    text = config_path.read_text(encoding="utf-8")
    new_text, action = _patch_mode_in_yaml(text, target_mode)
    if action == "unchanged":
        click.echo(
            click.style(
                f"✓ {config_path} is already `mode: {target_mode}` (no change)",
                fg="green",
                dim=True,
            )
        )
        return
    if action == "missing":
        # No mode line in the yaml AND target is enforce. Refuse to
        # create one out of thin air. Tell the operator how to fix it
        # safely (run observe first, edit yaml, or re-run onboard).
        click.echo(
            click.style(
                f"✗ no `mode:` line found in {config_path} and refusing to "
                f"append a fresh ``runtime: mode: enforce`` block. Run "
                f"`sponsio mode observe` first to materialise the block, "
                f"then `sponsio mode enforce` to flip it. Or edit the yaml "
                f"by hand, or re-run `sponsio onboard --force`.",
                fg="red",
            ),
            err=True,
        )
        raise SystemExit(1)
    config_path.write_text(new_text, encoding="utf-8")
    if action == "appended":
        click.echo(
            click.style("✓ ", fg="green")
            + f"{config_path} → mode: {target_mode} (appended runtime: block)"
        )
    else:
        click.echo(
            click.style("✓ ", fg="green")
            + f"{config_path} → {action}.mode: {target_mode}"
        )
