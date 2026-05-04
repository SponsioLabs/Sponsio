"""``sponsio init`` — interactive 4-axis onboarding wizard.

One entry point for first-time setup that covers all four install axes
in a single coherent flow:

1. **Framework wrap** (single) — which agent framework's tools to wrap
   (langgraph / crewai / openai / claude_agent / ... / none).
2. **Protect host agents** (multi) — which IDE host hooks to install
   (claude-code / cursor / openclaw).
3. **Install Sponsio skill** (multi) — which IDEs get the SKILL.md
   drop (axis 2's ``--with-skill`` default already covers picked hosts).
4. **Mode** (single) — observe (default, shadow) vs enforce (block).

Two surfaces converge on the same dispatch table:

* **TTY**: ``sponsio init`` — sequential ``click.prompt`` /
  ``click.confirm`` (matches ``onboard_setup.py``'s style — no new
  dependency).  Header / section rules render via the existing
  :mod:`sponsio.render.components` primitives so the wizard panel
  matches Sponsio's runtime trace style.

* **Non-TTY**: ``sponsio init --plan '<picks>'`` for dry-run preview,
  ``sponsio init --apply '<picks>'`` for execution.  Used by the
  IDE-agent-driven onboarding wizard prompt — both paths share the
  same :class:`InitPicks` dataclass + :func:`plan_commands` mapping,
  so the CLI dry-run and the IDE-agent dry-run are guaranteed to
  match.

Why this isn't fused into ``sponsio onboard``:

* ``sponsio onboard`` is the library-style API for ONE project's
  framework-wrap; it doesn't know about host hooks or skill drops.
  ``sponsio init`` is the higher-level orchestrator that ALSO calls
  ``host install`` / ``skill install`` per the user's axis picks.
  Keeping them separate means each command stays focused; ``init``
  calls ``onboard`` (and the other two) under the hood.

The previous single-axis wizard (provider / judge / mode prompts → wrote
``sponsio.yaml`` directly) was deprecated in favour of this design.
``sponsio onboard --interactive`` + ``.sponsiorc`` already covers
provider/api-key configuration, so we don't need a parallel surface.

The ``install_example`` / ``run_with_example`` helpers below are
unchanged — they back ``sponsio init --with-example`` (drop a
pre-tuned scaffolding for ``sponsio eval`` smoke tests).
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import click

from sponsio.onboard import detect_framework

# ---------------------------------------------------------------------------
# Choice tables — single source of truth.  TTY picker, picks parser, and
# help text all read from these.
# ---------------------------------------------------------------------------

# Order matters — these are the labels printed in the framework axis.
# Detected framework gets ◉; everything else listed here can be picked
# as override.  ``none`` is a real value (bare-loop / I'll-wire-it-
# myself), not a sentinel.
SUPPORTED_FRAMEWORKS: tuple[str, ...] = (
    "langgraph",
    "langchain",
    "crewai",
    "openai",
    "anthropic",
    "claude_agent",
    "openai_agents",
    "google_adk",
    "vercel_ai",
    "mcp",
    "none",
)

# Hosts that ``sponsio host install`` knows how to wire.  Order matches
# the panel layout — claude-code first because it's the most common,
# openclaw last because it's least.
SUPPORTED_HOSTS: tuple[str, ...] = ("claude-code", "cursor", "openclaw")

# Same set as ``sponsio skill install --tool`` accepts.  ``codex`` isn't
# an axis-2 host (no hook integration yet), but ``skill install`` can
# still drop SKILL.md there.
SUPPORTED_SKILL_TARGETS: tuple[str, ...] = ("claude-code", "cursor", "codex")

# Map host name → the binary that proves an IDE is installed.  Used by
# :func:`detect_environment` to decide which IDEs get the
# ``(installed)`` tag in the panel.
_HOST_BINARY: dict[str, str] = {
    "claude-code": "claude",
    "cursor": "cursor",
    "openclaw": "openclaw",
}


# ---------------------------------------------------------------------------
# Picks dataclass + serialisation
# ---------------------------------------------------------------------------


# Per-IDE Sponsio level — single source of truth for axis 2.
# ``host install`` already drops the SKILL.md by default
# (``--with-skill`` is on), so a separate "install skill" axis was
# redundant + confusing.  Folded into a per-IDE level pick:
#   "none"  — don't touch this IDE
#   "skill" — drop SKILL.md only (agent learns Sponsio; tools NOT gated)
#   "full"  — host hooks + skill (the canonical "protect this IDE" pick)
IDE_LEVELS: tuple[str, ...] = ("none", "skill", "full")


@dataclass
class InitPicks:
    """The 3-axis selection a user (or the IDE agent) hands to
    ``sponsio init``.  Symmetric across TTY + non-TTY paths.

    Axes:
      1. ``framework`` — wrap the agent code in this repo (single)
      2. ``ide_levels`` — per-IDE Sponsio level (multi, single-pick each)
      3. ``mode`` — observe vs enforce (single)
    """

    framework: str = "none"
    ide_levels: dict[str, str] = field(default_factory=dict)
    mode: str = "observe"

    @property
    def hosts(self) -> list[str]:
        """IDEs picked at level ``"full"`` — these get ``host install``."""
        return [ide for ide, level in self.ide_levels.items() if level == "full"]

    @property
    def skills(self) -> list[str]:
        """IDEs picked at level ``"skill"`` — these get ``skill install``
        only.  ``"full"`` IDEs are NOT in this list, since
        ``host install --with-skill`` (the default) already drops the
        skill into them."""
        return [ide for ide, level in self.ide_levels.items() if level == "skill"]


def parse_picks(spec: str) -> InitPicks:
    """Parse a picks string into :class:`InitPicks`.

    Format::

        framework=<name>;ides=<ide>:<level>,<ide>:<level>;mode=<observe|enforce>

    Where ``<level>`` is one of :data:`IDE_LEVELS`.  Legacy format with
    separate ``hosts=`` / ``skills=`` lists is also accepted for
    backward compat — ``hosts=X`` ↔ ``ides=X:full``,
    ``skills=X`` ↔ ``ides=X:skill``.

    Unknown segments are silently ignored (forward-compat).  Unknown
    values within a known axis are dropped in :func:`plan_commands`,
    keeping this parser a pure string→struct transform.
    """
    p = InitPicks()
    if not spec:
        return p
    for segment in spec.split(";"):
        segment = segment.strip()
        if not segment or "=" not in segment:
            continue
        key, _, val = segment.partition("=")
        key = key.strip().lower()
        val = val.strip()
        if key == "framework":
            p.framework = val or "none"
        elif key == "ides":
            for entry in val.split(","):
                entry = entry.strip()
                if not entry or ":" not in entry:
                    continue
                ide, _, level = entry.partition(":")
                ide = ide.strip()
                level = level.strip()
                if ide and level in IDE_LEVELS and level != "none":
                    p.ide_levels[ide] = level
        elif key == "hosts":
            # Legacy: ``hosts=X,Y`` → both at ``full``.
            for v in val.split(","):
                v = v.strip()
                if v:
                    p.ide_levels[v] = "full"
        elif key == "skills":
            # Legacy: ``skills=X`` → ``skill``, but only if not already
            # picked at ``full`` (full implies skill, no demotion).
            for v in val.split(","):
                v = v.strip()
                if v and p.ide_levels.get(v) != "full":
                    p.ide_levels[v] = "skill"
        elif key == "mode":
            p.mode = val or "observe"
    return p


def format_picks(p: InitPicks) -> str:
    """Inverse of :func:`parse_picks` — round-trip stable."""
    ides_str = ",".join(f"{ide}:{level}" for ide, level in p.ide_levels.items())
    return (
        f"framework={p.framework};"
        f"ides={ides_str};"
        f"mode={p.mode}"
    )


# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------


@dataclass
class Environment:
    """What ``sponsio init`` saw when it probed the project + machine.

    Drives the panel pre-fills (◉ markers, "(installed)" labels).
    """

    runtime: str  # "python" | "ts" | "both"
    framework: str  # framework.name from detect_framework
    framework_evidence: str
    ides_installed: list[str]  # subset of SUPPORTED_HOSTS that have a binary on PATH
    os_name: str


def _runtime_signal(root: Path) -> str:
    """Decide whether the project is Python, TS, or both.

    Py signal: ``pyproject.toml`` / ``requirements.txt`` / ``*.py`` at
    root.  TS signal: ``package.json`` at root.  Both → caller asks the
    user which one to wire.
    """
    has_py = (
        (root / "pyproject.toml").exists()
        or (root / "requirements.txt").exists()
        or any(root.glob("*.py"))
    )
    has_ts = (root / "package.json").exists()
    if has_py and has_ts:
        return "both"
    if has_ts:
        return "ts"
    return "python"


def detect_environment(root: Path) -> Environment:
    """Probe the project + machine.  Pure side-effect-free reads."""
    fw = detect_framework(root)
    ides = [h for h in SUPPORTED_HOSTS if shutil.which(_HOST_BINARY[h])]
    return Environment(
        runtime=_runtime_signal(root),
        framework=fw.framework,
        framework_evidence=fw.evidence,
        ides_installed=ides,
        os_name=platform.system(),
    )


# ---------------------------------------------------------------------------
# Plan — picks → list of argv vectors
# ---------------------------------------------------------------------------


def plan_commands(
    picks: InitPicks,
    *,
    ts_project: bool = False,
) -> list[list[str]]:
    """Return the argv vectors ``sponsio init --apply`` would run.

    Mirrors the IDE-agent wizard prompt's step-2 mapping so dry-run and
    the agent's preview surface the SAME command list.

    The per-IDE level picks resolve like this:
      - ``"full"`` IDEs → one ``sponsio host install`` covering all of
        them (``--with-skill`` is the default, so SKILL.md is dropped
        as a side effect).
      - ``"skill"`` IDEs → one ``sponsio skill install`` per IDE.
      - ``"none"`` IDEs → no command emitted.

    Filters typos (an IDE not in :data:`SUPPORTED_HOSTS` /
    :data:`SUPPORTED_SKILL_TARGETS`) silently — the user's job is to
    pick from the panel, not to spell the value verbatim.
    """
    cmds: list[list[str]] = []

    if picks.framework and picks.framework != "none":
        if ts_project:
            cmds.append(
                ["npx", "sponsio", "onboard", ".", "--mode", picks.mode, "--force"]
            )
        else:
            cmds.append(
                ["sponsio", "onboard", ".", "--mode", picks.mode, "--force"]
            )

    full_hosts = [h for h in picks.hosts if h in SUPPORTED_HOSTS]
    if full_hosts:
        cmds.append(
            ["sponsio", "host", "install", *full_hosts, "--mode", picks.mode]
        )

    # ``"skill"``-level IDEs need an explicit ``skill install`` because
    # they're NOT going through ``host install`` (no hooks wanted).
    # Filter to known skill-install targets so a typo doesn't reach
    # the subcommand and turn into a confusing error.
    for s in picks.skills:
        if s in SUPPORTED_SKILL_TARGETS:
            cmds.append(["sponsio", "skill", "install", "--tool", s])

    return cmds


# ---------------------------------------------------------------------------
# Apply — run the commands.  Subprocess so each gets clean env and the
# user sees output land in real time.
# ---------------------------------------------------------------------------


def apply_commands(
    commands: list[list[str]],
    *,
    env: dict | None = None,
    runner=None,
) -> int:
    """Run ``commands`` in sequence, surface output verbatim.

    Returns the first non-zero exit code, or 0 on success.  Stops at
    the first failure — half-applied state is worse than a clear error.

    ``runner`` is a test seam: pass a callable that takes argv +
    keyword args and returns an object with ``.returncode``.  Defaults
    to :func:`subprocess.run`.
    """
    if runner is None:
        runner = subprocess.run

    use_env = env if env is not None else os.environ.copy()
    for cmd in commands:
        click.echo()
        click.secho("→ " + " ".join(cmd), fg="cyan")
        result = runner(cmd, env=use_env)
        rc = getattr(result, "returncode", 0)
        if rc != 0:
            click.secho(f"✗ exited {rc} — stopping", fg="red", err=True)
            return rc
    return 0


def offer_demo(*, runner=None) -> None:
    """Post-install demo offer.  One scenario (``freeze``), 30s, fast.

    Skipped silently when stdin isn't a TTY (CI / scripts) so the pipe
    path stays deterministic.  ``--no-demo`` on the CLI also short-
    circuits this — that flag's check happens upstream.
    """
    if not sys.stdin.isatty():
        return
    if not click.confirm(
        "Want to see Sponsio block one tool call? (~30s)",
        default=False,
    ):
        return
    if runner is None:
        runner = subprocess.run
    runner(["sponsio", "demo", "--scenario", "freeze", "--fast"])


# ---------------------------------------------------------------------------
# Interactive picker (TTY)
# ---------------------------------------------------------------------------


def _print_panel_header(env: Environment) -> None:
    """Top of the wizard — banner + detected metadata.

    Uses Rich primitives from :mod:`sponsio.render.components` so the
    visual style matches ``sponsio doctor`` / ``sponsio report`` output
    instead of inventing a parallel aesthetic.
    """
    from rich.console import Console

    from sponsio.render.components import header_banner, header_meta

    console = Console(file=sys.stderr, soft_wrap=True)
    console.print()
    console.print(header_banner(tagline="onboarding wizard"))
    console.print()
    ides_str = " · ".join(env.ides_installed) if env.ides_installed else "none"
    console.print(
        header_meta(
            [
                ("framework", env.framework),
                ("ides", ides_str),
                ("os", env.os_name),
            ]
        )
    )
    console.print()


def _section(label: str) -> None:
    from rich.console import Console

    from sponsio.render.components import section_rule

    console = Console(file=sys.stderr, soft_wrap=True)
    console.print(section_rule(label))


def _select(prompt: str, choices: list, default=None):
    """Single-pick keyboard menu — questionary if available, else
    click.prompt with show_choices for graceful fallback.

    ``choices`` may be a list of strings OR a list of
    ``(value, display)`` tuples — questionary handles the tuple form
    natively; the click fallback uses the value side only.
    """
    try:
        import questionary
    except ImportError:
        # Click fallback — flatten tuples to value-only.
        plain = [c[0] if isinstance(c, tuple) else c for c in choices]
        return click.prompt(
            prompt,
            default=default,
            type=click.Choice(plain, case_sensitive=False),
            show_choices=True,
        )

    q_choices = []
    for c in choices:
        if isinstance(c, tuple):
            q_choices.append(questionary.Choice(title=c[1], value=c[0]))
        else:
            q_choices.append(questionary.Choice(title=c, value=c))
    answer = questionary.select(
        prompt,
        choices=q_choices,
        default=default,
        instruction="(↑/↓ to move, Enter to confirm)",
    ).ask()
    if answer is None:  # user hit Ctrl-C
        raise click.Abort()
    return answer


def run_interactive(env: Environment) -> InitPicks:
    """Walk three axes via keyboard-driven menus.

    - Framework wrap: single-pick from supported list (detected = default).
    - Per-IDE level: for each detected IDE, single-pick from
      ``none / skill / full`` so users see the host-vs-skill tradeoff
      ON ONE LINE per IDE instead of stitched across two confusing
      multi-axes.
    - Mode: single-pick (observe / enforce).

    Header (banner + detected metadata) renders via
    :mod:`sponsio.render.components` so the visual style matches
    ``sponsio doctor`` / ``sponsio report``.  The picker itself uses
    questionary (an Inquirer.js-style keyboard menu) for the actual
    arrow-key navigation — text-input "type the option name" was the
    UX issue users flagged on first contact.
    """
    _print_panel_header(env)

    # ---- Axis 1: framework wrap ----
    _section("framework wrap")
    fw_choices = []
    for fw in SUPPORTED_FRAMEWORKS:
        suffix = "  ← detected" if fw == env.framework else ""
        fw_choices.append((fw, f"{fw}{suffix}"))
    framework = _select(
        "Pick framework wrap",
        fw_choices,
        default=env.framework,
    )

    # ---- Axis 2: per-IDE Sponsio level ----
    # Combined what used to be "host install" + "skill install" into
    # one per-IDE single-pick.  ``host install --with-skill`` is the
    # default, so the old "axis 2 (hosts) + axis 3 (skills)" had a
    # confusing overlap (axis 2 already drops the skill into picked
    # hosts).  ``full`` here is "host hooks + skill"; ``skill`` is
    # "SKILL.md only, no enforcement".
    _section("Sponsio integration per IDE")
    click.echo(
        "    none   — leave this IDE alone\n"
        "    skill  — drop SKILL.md so the agent learns Sponsio "
        "(no enforcement)\n"
        "    full   — host hooks + SKILL.md (the canonical 'protect "
        "this IDE' pick)\n"
    )
    ide_levels: dict[str, str] = {}
    for h in SUPPORTED_HOSTS:
        if h not in env.ides_installed:
            click.secho(f"    {h:<14} (not installed — skipping)", dim=True)
            continue
        level = _select(
            f"  Sponsio level for {h}",
            list(IDE_LEVELS),
            default="none",
        )
        if level != "none":
            ide_levels[h] = level

    # Codex doesn't have a host hook today, but `skill install --tool
    # codex` works.  Offer it as a separate skill-only question if the
    # binary's on PATH.
    if shutil.which("codex"):
        if click.confirm(
            "  Drop Sponsio SKILL.md into Codex too?", default=False
        ):
            ide_levels["codex"] = "skill"

    # ---- Axis 3: mode ----
    _section("mode for new contracts")
    mode = _select(
        "Mode",
        ["observe", "enforce"],
        default="observe",
    )

    return InitPicks(
        framework=framework,
        ide_levels=ide_levels,
        mode=mode,
    )


# ---------------------------------------------------------------------------
# `--with-example` path — drop a pre-tuned scaffold for ``sponsio eval``
# smoke tests.  Orthogonal to the 4-axis wizard.
# ---------------------------------------------------------------------------


def _is_under_cwd(p: Path) -> bool:
    """Best-effort relative-path renderer; falls back to abs if cross-tree."""
    try:
        p.resolve().relative_to(Path.cwd().resolve())
        return True
    except ValueError:
        return False


def install_example(
    target_dir: Path, *, force: bool = False, example: str = "eval"
) -> list[Path]:
    """Drop the bundled ``init_examples/<example>`` tree into ``target_dir``.

    Returns the list of files written, in the order they were written,
    so the CLI can print a tidy "✓ wrote X" summary.

    Refuses to clobber existing files unless ``force=True`` — the "I
    already have a sponsio.yaml" path is way more common than "I want
    to overwrite mine," so quiet overwrite would be a foot-gun.  When
    forcing, we still don't ``rmtree(target_dir)``; only the example's
    own files get replaced.
    """
    from sponsio.init_examples import example_root

    src = example_root(example)
    if not src.exists():
        raise click.UsageError(
            f"Bundled example {example!r} not found "
            f"(expected at {src}).  Reinstall sponsio or pick a different name."
        )

    target_dir.mkdir(parents=True, exist_ok=True)

    # Walk the source tree, computing destination paths and checking for
    # collisions BEFORE writing anything — partial copies are the worst
    # kind of failure (user thinks it worked, eval blows up).
    plan: list[tuple[Path, Path]] = []
    for src_file in sorted(src.rglob("*")):
        if src_file.is_dir():
            continue
        rel = src_file.relative_to(src)
        dst = target_dir / rel
        plan.append((src_file, dst))

    if not force:
        existing = [str(d.relative_to(target_dir)) for _, d in plan if d.exists()]
        if existing:
            raise click.ClickException(
                "Refusing to overwrite existing file(s): "
                + ", ".join(existing)
                + "\nRe-run with --force to replace them."
            )

    written: list[Path] = []
    for src_file, dst in plan:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_file, dst)
        written.append(dst)
    return written


def run_with_example(
    target: Path, *, force: bool = False, example: str = "eval"
) -> list[Path]:
    """``sponsio init --with-example`` entry point.

    Resolves ``target`` to a directory (a ``.yaml`` argument is an
    error here — example mode writes a *tree*, not a single file),
    copies the bundle, and prints the next-step recipe so the user can
    run ``sponsio eval`` immediately.
    """
    if target.suffix in {".yaml", ".yml"}:
        raise click.UsageError(
            f"--with-example writes a directory tree, not a single YAML file "
            f"(got target={target}).  Pass a directory, e.g. `sponsio init . --with-example`."
        )

    target_dir = target if target.exists() else target
    target_dir.mkdir(parents=True, exist_ok=True)

    written = install_example(target_dir, force=force, example=example)

    # ``p`` and ``target_dir`` may be symlinked (``/tmp`` → ``/private/tmp``
    # on macOS is the common case). ``_is_under_cwd`` already resolves
    # both sides, so resolve them again here before ``relative_to`` or
    # we raise ``ValueError: 'x' is not in the subpath of 'y'`` on a
    # path we just confirmed IS under cwd.
    cwd_resolved = Path.cwd().resolve()

    click.echo()
    for p in written:
        click.secho("  ✓ ", fg="green", nl=False)
        click.echo(p.resolve().relative_to(cwd_resolved) if _is_under_cwd(p) else p)

    click.echo()
    click.secho("Next steps:", bold=True)
    rel_str = (
        str(target_dir.resolve().relative_to(cwd_resolved))
        if _is_under_cwd(target_dir)
        else str(target_dir)
    )
    click.echo(
        f"  sponsio eval {rel_str}/traces \\\n"
        f"      --config {rel_str}/sponsio.yaml \\\n"
        f"      --agent customer_bot"
    )
    click.echo()
    click.echo(
        "Then edit `sponsio.yaml` to swap in your own contracts and tools, "
        "and replace `traces/` with traces from your real agent runs."
    )
    return written
