"""``sponsio init`` — interactive 4-axis onboarding wizard.

One entry point for first-time setup that covers all four install axes
in a single coherent flow:

1. **Framework wrap** (single) — which agent framework's tools to wrap
   (langgraph / crewai / openai / claude_agent / ... / none).
2. **Protect host agents** (multi) — which IDE host hooks to install
   (claude-code / cursor / openclaw).
3. **Install Sponsio skill** (multi) — which IDEs get the SKILL.md
   drop (axis 2's ``--with-skill`` default already covers picked hosts).
4. **Mode** (single) — enforce (default, block) vs observe (shadow).

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

    Default ``framework=""`` distinguishes "axis 1 not answered" (skip
    onboard) from explicit ``framework="none"`` (bare-loop integrate,
    onboard still runs and emits the generic
    ``import sponsio / guard.guard_before/after`` snippet).
    """

    framework: str = ""
    ide_levels: dict[str, str] = field(default_factory=dict)
    mode: str = "enforce"
    """Default ``enforce`` (not ``observe``) so the user gets actual
    runtime gating right away.  ``observe`` is the safer-on-paper
    pick for shadow rollouts but it's also the silent pick — users
    walked away from the wizard expecting Sponsio to block their
    next destructive call and were surprised that nothing happened.
    Every Next: block already says how to step back to observe via
    ``sponsio mode observe`` if they want to soak first.
    """

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
            # Explicit empty (``framework=``) means "axis 1 not
            # answered" — skip onboard.  Distinct from explicit
            # ``framework=none`` which is the bare-loop pick.
            p.framework = val
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
            p.mode = val or "enforce"
    return p


def format_picks(p: InitPicks) -> str:
    """Inverse of :func:`parse_picks` — round-trip stable."""
    ides_str = ",".join(f"{ide}:{level}" for ide, level in p.ide_levels.items())
    return f"framework={p.framework};ides={ides_str};mode={p.mode}"


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
    scan_ts_already_installed: bool = False,
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

    if picks.framework:
        # ``framework=none`` is a real pick (bare function-calling
        # loop, no framework wrap), NOT a "skip the onboard step"
        # sentinel.  ``sponsio onboard`` handles it natively: writes
        # a generic ``sponsio.yaml`` + emits the ``import sponsio /
        # guard.guard_before/after`` snippet so the user has
        # something concrete to splice in.  Skipping onboard for
        # ``none`` would leave bare-loop users stranded with no
        # scaffold to start from — which is exactly what they
        # came to ``sponsio init`` for.
        if ts_project:
            # The TS CLI ships in ``@sponsio/sdk`` (its ``bin`` field
            # exposes the ``sponsio`` binary).  ``npx sponsio onboard``
            # alone fails because npm tries to fetch a top-level
            # package literally named ``sponsio`` (which doesn't
            # exist on the registry — that's the Python pip name).
            # Emit the install step first so the binary lands in
            # ``node_modules/.bin``, then ``npx sponsio onboard``
            # picks it up locally.  Bonus: ``--save-dev`` records
            # the dep in package.json so subsequent runs are
            # cached.
            #
            # Skip the install when ``@sponsio/sdk`` is ALREADY in
            # node_modules — running ``npm install --save-dev``
            # against an existing entry would overwrite ``npm
            # link``-ed development versions with the published
            # release, silently undoing the user's local-source
            # workflow.
            #
            # Until 2026-05 the bin lived in ``@sponsio/scan-ts``;
            # that package is now a deprecation shim that
            # forwards to ``@sponsio/sdk``.  We always emit the
            # install for the new name; the shim handles the
            # legacy install case at runtime.
            if not scan_ts_already_installed:
                cmds.append(["npm", "install", "--save-dev", "@sponsio/sdk"])
            # Older published versions of ``@sponsio/scan-ts``
            # (alpha.3 and below) didn't accept ``--mode`` on the
            # ``onboard`` subcommand — passing it errored with
            # ``unknown flag: --mode``.  Newer versions do support
            # it, but we route around the older floor by writing
            # the file at the default ``observe`` mode and then
            # using the ``mode`` subcommand to flip if the user
            # picked ``enforce``.  Cheap, idempotent, works on
            # both old and new scanner versions.
            cmds.append(["npx", "sponsio", "onboard", ".", "--force"])
            if picks.mode != "observe":
                cmds.append(["npx", "sponsio", "mode", picks.mode])
        else:
            cmds.append(["sponsio", "onboard", ".", "--mode", picks.mode, "--force"])

    full_hosts = [h for h in picks.hosts if h in SUPPORTED_HOSTS]
    if full_hosts:
        cmds.append(["sponsio", "host", "install", *full_hosts, "--mode", picks.mode])

    # ``"skill"``-level IDEs need an explicit ``skill install`` because
    # they're NOT going through ``host install`` (no hooks wanted).
    # Filter to known skill-install targets so a typo doesn't reach
    # the subcommand and turn into a confusing error.
    for s in picks.skills:
        if s in SUPPORTED_SKILL_TARGETS:
            # ``--force`` so re-running ``sponsio init`` is idempotent.
            # The Skill is a copy of a packaged docs file; replacing
            # an existing install with the latest version is the
            # expected behaviour here, not data loss.  Without it,
            # the second wizard run on a machine that already has
            # the skill bails out mid-dispatch.
            cmds.append(["sponsio", "skill", "install", "--tool", s, "--force"])

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

    Doesn't echo the command before running — the upstream ``preview``
    block already showed the same line, and re-printing it here was
    noticeable visual duplication of "observe" / "enforce" / "--mode"
    pieces in the output.  When a command fails, we still surface
    the failing argv as part of the error so the user knows which
    step blew up.
    """
    if runner is None:
        runner = subprocess.run

    use_env = env if env is not None else os.environ.copy()
    # Mark dispatched subprocesses so they can downsize their own
    # banner / skip post-run prompts that the wizard already covered
    # (notably the "Mode is observe — flip to enforce now?" prompt
    # that ``sponsio onboard`` prints when run directly).  Without
    # this, the wizard asks for mode, dispatches `--mode observe`,
    # and onboard then asks again at the end — confusing the user
    # into thinking their wizard pick didn't take.
    use_env["SPONSIO_INIT_DISPATCH"] = "1"
    for i, cmd in enumerate(commands, 1):
        # Multi-step runs get a thin "[i/N]" header so the user can
        # follow which step's output they're seeing.  Single-step
        # runs skip it — the preview already showed the one command.
        if len(commands) > 1:
            click.echo()
            click.secho(
                f"  [{i}/{len(commands)}] {cmd[0]} {cmd[1] if len(cmd) > 1 else ''}",
                fg="cyan",
                dim=True,
            )
        result = runner(cmd, env=use_env)
        rc = getattr(result, "returncode", 0)
        if rc != 0:
            click.secho(
                f"\n  ✗ step exited {rc}: {' '.join(cmd)} — stopping",
                fg="red",
                err=True,
            )
            return rc
    return 0


def print_next_steps(picks: "InitPicks", *, ts_project: bool = False) -> None:
    """Picks-aware "what now" block printed after a successful apply.

    Each combination of axes leaves the user in a different spot, so
    a generic "go check sponsio.yaml" footer was leaving everyone
    half-onboarded — especially the IDE-only paths (axis 1 empty,
    axes 2/3 populated), where the install completed but the user
    had no concrete next action to take.

    Cases this routes through:

      - **Project framework wrap** picked (axis 1 ≠ "" / "none"):
        the agent's ``sponsio.yaml`` is on disk; point at the
        contract-authoring template + ``sponsio prompt onboard``
        for the IDE-agent semantic pass + the ``sponsio mode
        enforce`` flip when soaked.

      - **Bare-loop framework** (axis 1 = "none"): ``sponsio.yaml``
        is generic; the user still needs to splice
        ``guard.guard_before/after`` calls into their loop manually.
        Point at the snippet that ``sponsio onboard`` printed and
        at the same authoring + flip path.

      - **IDE host plugin** picked (any axis-2 IDE at level=full):
        per-host runtime gating is now live in observe; user should
        try a destructive op in that IDE to see the log, then
        invoke the ``sponsio-<ide>:configure`` skill to tune rules
        per their workflow.

      - **IDE skill only** (any axis-2 IDE at level=skill): the
        knowledge layer is in place but no runtime gating; tell
        the user to ask the IDE's develop agent to "set up Sponsio
        in this project" / "tune contracts" — the skill drives the
        rest.

    Indented col-2 to match the rest of the wizard panel.
    """
    from sponsio.render.tokens import PALETTE
    from sponsio.runtime.terminal import _make_stderr_console

    npx = "npx " if ts_project else ""
    console = _make_stderr_console(None)
    console.print()
    from rich.text import Text

    console.print(Text("  Next:", style=f"bold {PALETTE['brand']}"))

    if picks.framework and picks.framework != "none":
        # Project-framework path.
        click.echo(f"    {npx}sponsio onboard . --emit-context > /tmp/ctx.json")
        click.echo(f"    {npx}sponsio prompt onboard")
        click.echo(
            "      → apply the printed template to ctx.json IN this chat;"
        )
        click.echo(
            "        WAIT for the user to pick proposals; merge into yaml."
        )
        click.echo(f"    {npx}sponsio validate sponsio.yaml")
    elif picks.framework == "none":
        # Bare-loop path.  API name is language-specific: TS uses
        # ``guardBefore``/``guardAfter`` (camelCase, see
        # ts/packages/sdk/src/index.ts), Python uses
        # ``guard_before``/``guard_after`` (snake_case, see
        # sponsio/integrations/base.py).  Print the right one so
        # the user can grep for it in the SDK they're about to
        # call.
        guard_api = (
            "guard.guardBefore / guardAfter"
            if ts_project
            else "guard.guard_before / _after"
        )
        click.echo(
            "    Splice ``wrap_snippet`` from sponsio.yaml's "
            "next-step output"
        )
        click.echo(f"    into your agent loop ({guard_api}).")
        click.echo(f"    {npx}sponsio validate sponsio.yaml")

    # IDE-side guidance — emitted regardless of axis 1.
    full_ides = picks.hosts
    skill_ides = picks.skills
    if full_ides:
        click.echo()
        click.echo(
            f"    {', '.join(full_ides)} — host hook now gates tool calls in observe."
        )
        for ide in full_ides:
            click.echo(f"      Try a destructive op in {ide}; check the log:")
            click.echo("        sponsio report --since 24h")
            click.echo(f"      Tune rules per workflow: ask {ide}'s agent to")
            click.echo(f'        "configure sponsio for {ide}"')
            click.echo(f"        (it'll invoke the sponsio-{ide}:configure skill).")
    if skill_ides:
        click.echo()
        click.echo(f"    {', '.join(skill_ides)} — Agent Skill installed.")
        click.echo("      Ask the IDE's develop agent: \"set up Sponsio in this")
        click.echo('      project" / "tune contracts from policy.md" / "explain')
        click.echo('      why C1 fired" — it has the playbook.')

    # Mode flip is already an explicit step in the wizard (axis 3,
    # observe vs enforce); echoing "flip when ready" here was just
    # repeating the question the user just answered.  When users
    # actually want to flip later they can re-run the wizard or
    # ``sponsio mode enforce`` directly — both are documented in the
    # main skill / CLI docs.


def offer_demo(*, runner=None) -> None:
    """Post-install demo offer.  One scenario (``freeze``), 30s, fast.

    Skipped silently when stdin isn't a TTY (CI / scripts) so the pipe
    path stays deterministic.  ``--no-demo`` on the CLI also short-
    circuits this — that flag's check happens upstream.
    """
    if not sys.stdin.isatty():
        return
    if not _confirm(
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
    """Top of the wizard — banner + a single ``🔍 Detected: …`` line.

    Visual style matches ``sponsio doctor`` / ``sponsio report`` via
    the :mod:`sponsio.render.components` banner.  Body content
    indented col-2 with :func:`indent` so the banner spans full
    width but everything underneath sits inside the same 2-space
    margin trace + report use — universal "banner col-0, content
    col-2" rule.
    """
    from rich.text import Text

    from sponsio.render.components import header_banner, indent
    from sponsio.render.tokens import PALETTE
    from sponsio.runtime.terminal import _make_stderr_console

    console = _make_stderr_console(None)
    console.print()
    console.print(header_banner(tagline="onboarding wizard"))
    console.print()

    runtime_label = {"python": "Python", "ts": "TypeScript", "both": "Python+TS"}.get(
        env.runtime, env.runtime
    )
    ides_str = ", ".join(env.ides_installed) if env.ides_installed else "none"
    summary = Text.assemble(
        ("🔍 Detected: ", f"bold {PALETTE['brand']}"),
        (runtime_label, PALETTE["fg"]),
        (" · ", PALETTE["metadata"]),
        (env.framework, PALETTE["fg"]),
        (" · ", PALETTE["metadata"]),
        (ides_str, PALETTE["fg"]),
        (" · ", PALETTE["metadata"]),
        (env.os_name, PALETTE["fg"]),
    )
    console.print(indent(summary))
    console.print()


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
        qmark="  ?",  # 2-space indent so prompts align with body content
    ).ask()
    if answer is None:  # user hit Ctrl-C
        raise click.Abort()
    return answer


def _confirm(prompt: str, default: bool = False) -> bool:
    """Yes/no via questionary so the ``?`` prompt format matches every
    other wizard question.  Falls back to ``click.confirm`` (the
    ``[Y/n]`` form) when questionary is unavailable.

    The mismatch users flagged was that ``? Sponsio level for X``
    (questionary) and ``Run these? [Y/n]`` (click.confirm) felt like
    two different programs — one keyboard-driven, one text-input.
    Routing every prompt through this helper means the wizard speaks
    one language top to bottom.
    """
    try:
        import questionary
    except ImportError:
        return click.confirm(prompt, default=default)

    answer = questionary.confirm(prompt, default=default, qmark="  ?").ask()
    if answer is None:  # Ctrl-C
        raise click.Abort()
    return answer


def _step(label: str) -> None:
    """Section divider matching the runtime trace renderer.

    Wraps :func:`sponsio.render.components.section_rule` in
    :func:`indent` (2-space pad) so the section header sits inside
    the same body-content margin trace + report use.  Universal
    rule: banner spans full width at col-0; everything under it —
    section rules, bullets, tables, prompts — aligns at col-2.
    """
    from sponsio.render.components import indent, section_rule
    from sponsio.runtime.terminal import _make_stderr_console

    console = _make_stderr_console(None)
    console.print()
    console.print(indent(section_rule(label)))


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
    _step("Framework wrap")
    fw_choices = []
    # ``skip`` (sentinel value ``""``) is the "don't touch project
    # files" pick — wizard runs no ``sponsio onboard``, writes no
    # ``sponsio.yaml``, splices nothing into the agent entry file.
    # Use it when the user only wants axis 2 (IDE skill / host plugin)
    # — e.g. installing the Sponsio Agent Skill into Cursor without
    # changing the current project.  Distinct from ``none`` below
    # which DOES run onboard and emits a generic bare-loop scaffold.
    fw_choices.append(("", "skip — don't touch this project (IDE-only setup)"))
    for fw in SUPPORTED_FRAMEWORKS:
        # ``none`` is a real pick — bare function-calling loop,
        # generic ``guard.guard_before/after`` wiring.  Label it
        # clearly so users don't mistake it for "skip integration".
        if fw == "none":
            label = "none — bare loop (generic guard.guard_before/after)"
        else:
            label = fw
        suffix = "  ← detected" if fw == env.framework else ""
        fw_choices.append((fw, f"{label}{suffix}"))
    # When detection found a real framework, surface it as the
    # default so the keyboard ``enter`` accepts the auto-pick.
    # ``detect_framework`` returns the literal string ``"none"`` as
    # its negative result (FrameworkHint(framework="none") when
    # nothing was found) — treat that case the same as "no detection
    # at all" and default to ``skip`` (sentinel ``""``) instead of
    # silently auto-picking the bare-loop scaffold for users who may
    # have come to ``sponsio init`` only to install IDE skills.
    if env.framework and env.framework != "none":
        fw_default = env.framework
    else:
        fw_default = ""
    framework = _select(
        "Pick framework wrap",
        fw_choices,
        default=fw_default,
    )

    # ---- Axis 2: per-IDE Sponsio level ----
    # Combined what used to be "host install" + "skill install" into
    # one per-IDE single-pick.  ``host install --with-skill`` is the
    # default, so the old "axis 2 (hosts) + axis 3 (skills)" had a
    # confusing overlap (axis 2 already drops the skill into picked
    # hosts).  ``full`` here is "host hooks + skill"; ``skill`` is
    # "SKILL.md only, no enforcement".
    _step("Sponsio integration per IDE")
    click.echo(
        "none   — leave this IDE alone.\n"
        "\n"
        "skill  — knowledge layer only.  Drops SKILL.md so this IDE\n"
        "         (and any IDE that has the skill) knows how to drive\n"
        "         Sponsio across any project.  No runtime gating.\n"
        "\n"
        "full   — skill + host plugin.  The plugin ALSO gates THIS\n"
        "         IDE's own Bash / Edit / Write / MCP calls against\n"
        "         ``~/.sponsio/plugins/_host_<ide>/sponsio.yaml``.\n"
        "         Dual control: teaches develop agents AND protects\n"
        "         THIS IDE's tool calls."
    )
    click.echo()
    ide_levels: dict[str, str] = {}
    for h in SUPPORTED_HOSTS:
        if h not in env.ides_installed:
            click.secho(f"{h} — not installed, skipping", dim=True)
            continue
        level = _select(
            f"Sponsio level for {h}",
            list(IDE_LEVELS),
            default="none",
        )
        if level != "none":
            ide_levels[h] = level

    # Codex doesn't have a host hook today, but `skill install --tool
    # codex` works.  Offer it as a separate skill-only question if the
    # binary's on PATH.
    if shutil.which("codex"):
        if _confirm("Drop Sponsio SKILL.md into Codex too?", default=False):
            ide_levels["codex"] = "skill"

    # ---- Axis 3: mode ----
    _step("Mode for new contracts")
    mode = _select(
        "Mode",
        ["enforce", "observe"],
        default="enforce",
    )

    return InitPicks(
        framework=framework,
        ide_levels=ide_levels,
        mode=mode,
    )
