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


@dataclass
class InitPicks:
    """The four-axis selection a user (or the IDE agent) hands to
    ``sponsio init``.  Symmetric in TTY + non-TTY paths."""

    framework: str = "none"
    hosts: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    mode: str = "observe"


def parse_picks(spec: str) -> InitPicks:
    """Parse a picks string into :class:`InitPicks`.

    Format::

        framework=<name>;hosts=<a>,<b>;skills=<a>,<b>;mode=<observe|enforce>

    Empty value lists are explicit (``hosts=``).  Unknown segments are
    silently ignored — forward-compat for added axes.  Unknown values
    within a known axis are dropped in :func:`plan_commands` so this
    parser stays a pure string→struct transform.
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
        elif key == "hosts":
            p.hosts = [v.strip() for v in val.split(",") if v.strip()]
        elif key == "skills":
            p.skills = [v.strip() for v in val.split(",") if v.strip()]
        elif key == "mode":
            p.mode = val or "observe"
    return p


def format_picks(p: InitPicks) -> str:
    """Inverse of :func:`parse_picks` — round-trip stable."""
    return (
        f"framework={p.framework};"
        f"hosts={','.join(p.hosts)};"
        f"skills={','.join(p.skills)};"
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
    the agent's preview surface the SAME command list.  Skips axes whose
    pick is empty.  Filters typos (a host name not in
    :data:`SUPPORTED_HOSTS`) silently — the user's job is to pick from
    the panel, not to spell the value verbatim.
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

    if picks.hosts:
        valid_hosts = [h for h in picks.hosts if h in SUPPORTED_HOSTS]
        if valid_hosts:
            cmds.append(
                ["sponsio", "host", "install", *valid_hosts, "--mode", picks.mode]
            )

    # Skill install only for IDEs NOT already covered by axis 2's
    # ``--with-skill`` default — avoids the redundant double-drop.
    extra_skills = [
        s
        for s in picks.skills
        if s in SUPPORTED_SKILL_TARGETS and s not in picks.hosts
    ]
    for s in extra_skills:
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


def run_interactive(env: Environment) -> InitPicks:
    """Walk the four axes via sequential ``click.prompt`` /
    ``click.confirm`` calls.  Returns the answers as :class:`InitPicks`.

    Visual style: panel header (banner + meta grid) printed once, each
    axis gets a section rule + a short ◉/○ summary + the prompt.
    Single-line results — no live re-paint, matching the existing
    ``onboard_setup`` style.
    """
    _print_panel_header(env)

    # ---- Axis 1: framework wrap (single) ----
    _section("framework wrap")
    for fw in SUPPORTED_FRAMEWORKS:
        marker = "◉" if fw == env.framework else "○"
        suffix = "  ← detected" if fw == env.framework else ""
        click.echo(f"    {marker} {fw}{suffix}")
    framework = click.prompt(
        "  Pick framework",
        default=env.framework,
        type=click.Choice(SUPPORTED_FRAMEWORKS, case_sensitive=False),
        show_choices=False,
    )

    # ---- Axis 2: protect host agents (multi) ----
    _section("protect host agents")
    for h in SUPPORTED_HOSTS:
        installed = h in env.ides_installed
        suffix = "(installed)" if installed else "not installed"
        click.echo(f"    {h:<14} {suffix}")
    click.echo()
    hosts: list[str] = []
    for h in SUPPORTED_HOSTS:
        if h not in env.ides_installed:
            continue
        if click.confirm(f"  Install Sponsio hooks for {h}?", default=False):
            hosts.append(h)

    # ---- Axis 3: install skill in IDEs not covered by axis 2 ----
    _section("install Sponsio skill in")
    skills: list[str] = []
    remaining = [
        s
        for s in SUPPORTED_SKILL_TARGETS
        if s in env.ides_installed and s not in hosts
    ]
    if not remaining:
        click.echo("    (axis 2 already covers every detected IDE — skipping)")
    else:
        for s in remaining:
            if click.confirm(f"  Install skill in {s}?", default=False):
                skills.append(s)

    # ---- Axis 4: mode (single, default observe) ----
    _section("mode for new contracts")
    click.echo("    ◉ observe       ○ enforce")
    mode = click.prompt(
        "  Mode",
        default="observe",
        type=click.Choice(["observe", "enforce"], case_sensitive=False),
        show_choices=False,
    )

    return InitPicks(
        framework=framework,
        hosts=hosts,
        skills=skills,
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
