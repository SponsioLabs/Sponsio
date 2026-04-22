"""``sponsio init`` — interactive guided setup.

Generates a starter ``sponsio.yaml`` after walking the user through
the four decisions that actually matter on first run:

1. **LLM provider** for parse-time extraction (or rule-based, no key).
2. **API key strategy** — ``${ENV_VAR}`` (recommended) vs paste-in.
3. **Runtime mode** — ``observe`` (safe default) vs ``enforce``.
4. **Judge resilience** — ``allow`` (default) / ``deny`` / ``skip``.

Why a wizard at all?  ``cp examples/sponsio.yaml .`` would
technically work, but new users routinely don't know which provider
they have credentials for, what an env-var interpolation looks like,
or that ``observe`` is the safe first run.  CrabTrap and similar
tools all converged on a "guided init" pattern for the same reason.

The module is split from ``sponsio.cli`` so the wizard logic is
testable in isolation (no Click runner needed for the core).
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import click


def _is_non_interactive() -> bool:
    """True when stdin isn't a TTY or the user asked for non-interactive.

    Historically each prompt required its own ``--flag`` to skip; if the
    user missed one (``sponsio init --provider none --mode observe ...``
    without ``--judge-fallback``), the command silently hung in CI waiting
    for stdin. Detecting a non-TTY / explicit ``SPONSIO_NONINTERACTIVE=1``
    lets us fall back to sane defaults across *all* prompts uniformly.
    """
    if os.environ.get("SPONSIO_NONINTERACTIVE"):
        return True
    try:
        return not sys.stdin.isatty()
    except (AttributeError, ValueError):
        return True


# ---------------------------------------------------------------------------
# Provider catalogue
# ---------------------------------------------------------------------------

# Keep this short — the goal is "pick one and start", not "browse the
# full catalog".  Each entry maps to (default model, env-var name).
# Bedrock uses AWS credential chain (no env-var key field).
_PROVIDER_DEFAULTS: dict[str, tuple[str, str | None]] = {
    "openai": ("gpt-4o-mini", "OPENAI_API_KEY"),
    "anthropic": ("claude-3-5-sonnet-20241022", "ANTHROPIC_API_KEY"),
    "gemini": ("gemini-2.0-flash", "GOOGLE_API_KEY"),
    "bedrock": ("anthropic.claude-3-5-sonnet-20241022-v2:0", None),
}

_PROVIDER_BLURBS = {
    "openai": "OpenAI ChatGPT family — broadest atom coverage",
    "anthropic": "Anthropic Claude — strong at policy reasoning",
    "gemini": "Google Gemini — 1500 req/day FREE tier (lowest friction)",
    "bedrock": "AWS Bedrock — uses AWS credential chain (no key needed)",
    "none": "No LLM — rule-based parsing only (limited atom coverage)",
}


# ---------------------------------------------------------------------------
# Wizard state (extracted from prompts so the YAML rendering is testable)
# ---------------------------------------------------------------------------


@dataclass
class WizardChoices:
    """Everything the wizard collects, before we render YAML.

    Kept as a separate type so the ``render_yaml`` function is a
    pure function of these choices — easy to unit-test, easy to
    diff in golden tests, easy to call from scripts that want to
    skip the prompts entirely.
    """

    provider: str  # one of _PROVIDER_DEFAULTS or "none"
    model: str | None
    api_key_env_var: str | None  # env var name OR None for "none"/bedrock
    mode: str  # observe | enforce
    judge_fallback: str  # allow | deny | skip
    sample_contract: bool  # write a starter contract block

    @property
    def has_llm(self) -> bool:
        return self.provider != "none"


# ---------------------------------------------------------------------------
# YAML rendering (pure)
# ---------------------------------------------------------------------------


def render_yaml(choices: WizardChoices) -> str:
    """Render ``sponsio.yaml`` from the wizard choices.

    Pure function — no I/O, no prompts.  Always emits the same
    canonical layout (extractor → judge → tools → agents) so
    diffing two generated files is meaningful.  Comments inline
    explain the non-obvious knobs to the user *reading the file*
    (the wizard already explained them at prompt time).
    """
    lines: list[str] = ["version: 1", ""]

    # extractor:
    if choices.has_llm:
        lines.append("# Parse-time LLM (used by `sponsio scan` to turn code/docs into")
        lines.append(
            "# contracts).  Offline & one-shot — favour accuracy over latency."
        )
        lines.append("extractor:")
        lines.append(f"  provider: {choices.provider}")
        if choices.model:
            lines.append(f"  model: {choices.model}")
        if choices.api_key_env_var:
            lines.append(f"  api_key: ${{{choices.api_key_env_var}}}")
        lines.append("")
    else:
        lines.append("# No LLM extractor — `sponsio scan` will fall back to rule-based")
        lines.append("# parsing.  Add an `extractor:` section to enable richer atoms.")
        lines.append("")

    # judge:
    lines.append(
        "# Runtime sto-judge (evaluates stochastic atoms like `injection_free`)"
    )
    lines.append("# on the agent's hot path.  Favour cheap+fast model; fault tolerance")
    lines.append("# matters because LLM outages must NOT cascade into agent outages.")
    lines.append("judge:")
    if choices.has_llm:
        lines.append(f"  provider: {choices.provider}")
        # Default to the same model — most users want one knob to
        # turn here; advanced users can split later.
        if choices.model:
            lines.append(f"  model: {choices.model}")
        if choices.api_key_env_var:
            lines.append(f"  api_key: ${{{choices.api_key_env_var}}}")
    lines.append(
        f"  fallback_mode: {choices.judge_fallback}  # allow|deny|skip on judge failure"
    )
    lines.append("  circuit_breaker: true")
    lines.append("")

    # defaults:
    lines.append("defaults:")
    lines.append(
        f"  mode: {choices.mode}  # observe|enforce — observe = shadow (safe default)"
    )
    lines.append("")

    if choices.sample_contract:
        lines.append("# Starter contract.  Replace with your own — see")
        lines.append("# https://docs.sponsio.dev/contracts for the full DSL.")
        lines.append("agents:")
        lines.append("  my_agent:")
        lines.append("    contracts:")
        lines.append("      - desc: PII guard")
        lines.append('        E: "tool `send_email` arg `body` must not contain pii"')

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Interactive prompts (Click)
# ---------------------------------------------------------------------------


def _prompt_provider() -> str:
    """Interactive provider picker.  Detects existing env vars and
    nudges the user toward the one they already have credentials for.
    """
    detected = []
    for prov, (_model, env) in _PROVIDER_DEFAULTS.items():
        if env and os.environ.get(env):
            detected.append(prov)
    # Also recognise the AWS chain implying Bedrock
    if os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"):
        detected.append("bedrock")

    options = list(_PROVIDER_DEFAULTS.keys()) + ["none"]
    # Default = first detected, else gemini (cheapest free tier), else 1
    if detected:
        default_idx = options.index(detected[0]) + 1
    elif "gemini" in options:
        default_idx = options.index("gemini") + 1
    else:
        default_idx = 1

    if _is_non_interactive():
        choice = options[default_idx - 1]
        click.echo(f"  (non-interactive) provider = {choice}")
        return choice

    click.echo("\nWhich LLM provider should `sponsio scan` use?")
    for i, prov in enumerate(options, 1):
        marker = "  (credentials detected)" if prov in detected else ""
        click.echo(f"  [{i}] {prov:<10} — {_PROVIDER_BLURBS[prov]}{marker}")

    choice = click.prompt(
        "Choose",
        type=click.IntRange(1, len(options)),
        default=default_idx,
        show_default=True,
    )
    return options[choice - 1]


def _prompt_api_key_env_var(provider: str) -> str | None:
    """Returns the env var *name*, never the raw key.

    Two reasons we never store raw keys in YAML:
    1. It's almost certainly going to get committed by accident.
    2. ``${ENV_VAR}`` interpolation makes ops handoff trivial — the
       same YAML works in dev/staging/prod with different keys.
    """
    if provider == "bedrock" or provider == "none":
        return None
    default_var = _PROVIDER_DEFAULTS[provider][1]
    if _is_non_interactive():
        return default_var
    click.echo(
        f"\nKeys live in env vars (never in YAML).  Default: ${{{default_var}}}."
    )
    var_name = click.prompt(
        "Env var name",
        default=default_var,
        show_default=True,
    )
    return var_name


def _prompt_mode() -> str:
    if _is_non_interactive():
        return "observe"
    click.echo(
        "\nRuntime mode:\n"
        "  observe   shadow — never blocks; emits audit events  (recommended for first run)\n"
        "  enforce   blocks violations at guard_before/after  (run `sponsio eval` first to verify FPR)"
    )
    return click.prompt(
        "Mode",
        type=click.Choice(["observe", "enforce"]),
        default="observe",
        show_default=True,
    )


def _prompt_judge_fallback() -> str:
    if _is_non_interactive():
        return "allow"
    click.echo(
        "\nWhen the runtime LLM judge fails (timeout, 5xx, key revoked):\n"
        "  allow   pass through (agent keeps working — recommended)\n"
        "  deny    treat as violation (fail closed; only for high-stakes deployments)\n"
        "  skip    omit the result (no signal either way)"
    )
    return click.prompt(
        "Judge fallback",
        type=click.Choice(["allow", "deny", "skip"]),
        default="allow",
        show_default=True,
    )


# ---------------------------------------------------------------------------
# Top-level entrypoint
# ---------------------------------------------------------------------------


def install_example(
    target_dir: Path, *, force: bool = False, example: str = "eval"
) -> list[Path]:
    """Drop the bundled ``init_examples/<example>`` tree into ``target_dir``.

    Returns the list of files written, in the order they were
    written, so the CLI can print a tidy "✓ wrote X" summary.

    Refuses to clobber existing files unless ``force=True`` — the
    "I already have a sponsio.yaml" path is way more common than
    "I want to overwrite mine," so quiet overwrite would be a
    foot-gun.  When forcing, we still don't ``rmtree(target_dir)``;
    only the example's own files get replaced.

    Why a separate function from ``run_wizard``?  Because the example
    bundle already contains a hand-tuned ``sponsio.yaml`` whose
    contracts are matched to the bundled traces.  Layering wizard
    prompts on top would either (a) silently discard the user's
    answers, or (b) produce a YAML that doesn't match the traces.
    Either way the next ``sponsio eval traces/`` would surprise
    them.  Cleanest: this is a different verb.
    """
    from sponsio.init_examples import example_root

    src = example_root(example)
    if not src.exists():
        raise click.UsageError(
            f"Bundled example {example!r} not found "
            f"(expected at {src}).  Reinstall sponsio or pick a different name."
        )

    target_dir.mkdir(parents=True, exist_ok=True)

    # Walk the source tree, computing destination paths and checking
    # for collisions BEFORE writing anything — partial copies are the
    # worst kind of failure (user thinks it worked, eval blows up).
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
    copies the bundle, and prints the next-step recipe so the user
    can run ``sponsio eval`` immediately.
    """
    if target.suffix in {".yaml", ".yml"}:
        raise click.UsageError(
            f"--with-example writes a directory tree, not a single YAML file "
            f"(got target={target}).  Pass a directory, e.g. `sponsio init . --with-example`."
        )

    target_dir = target if target.exists() else target
    target_dir.mkdir(parents=True, exist_ok=True)

    written = install_example(target_dir, force=force, example=example)

    # ``p`` and ``target_dir`` may be symlinked (``/tmp`` -> ``/private/tmp``
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


def _is_under_cwd(p: Path) -> bool:
    """Best-effort relative-path renderer; falls back to abs if cross-tree."""
    try:
        p.resolve().relative_to(Path.cwd().resolve())
        return True
    except ValueError:
        return False


def run_wizard(
    target: Path,
    *,
    force: bool = False,
    provider: str | None = None,
    mode: str | None = None,
    judge_fallback: str | None = None,
    no_sample: bool = False,
) -> tuple[Path, WizardChoices]:
    """Run the wizard, write ``sponsio.yaml`` to ``target``, return both.

    Any of ``provider`` / ``mode`` / ``judge_fallback`` supplied
    skip the corresponding prompt — lets ``sponsio init --provider
    gemini --mode observe`` run fully non-interactively (useful for
    docs / CI / scripted onboarding).
    """
    out_path = target if target.suffix in {".yaml", ".yml"} else target / "sponsio.yaml"

    if out_path.exists() and not force:
        click.echo(f"\n{out_path} already exists.")
        if _is_non_interactive():
            # No TTY to confirm on — safer to abort than to silently
            # overwrite. Users who actually want overwrite pass --force.
            click.echo("Aborted (non-interactive); pass --force to overwrite.")
            raise click.Abort()
        if not click.confirm("Overwrite?", default=False):
            click.echo("Aborted; no file written.")
            raise click.Abort()

    click.echo("Sponsio init — let's wire up your config.\n")
    click.echo("Press Enter to accept the default at each prompt; Ctrl-C to bail.")

    chosen_provider = provider or _prompt_provider()
    if chosen_provider not in {*_PROVIDER_DEFAULTS, "none"}:
        raise click.UsageError(
            f"Unknown provider {chosen_provider!r}; "
            f"choose one of {list(_PROVIDER_DEFAULTS) + ['none']}"
        )

    if chosen_provider == "none":
        chosen_model: str | None = None
        env_var: str | None = None
    else:
        default_model, default_env = _PROVIDER_DEFAULTS[chosen_provider]
        chosen_model = default_model
        env_var = (
            None
            if chosen_provider == "bedrock"
            else (provider and default_env)  # non-interactive: keep default
            or _prompt_api_key_env_var(chosen_provider)
        )

    chosen_mode = mode or _prompt_mode()
    chosen_fallback = judge_fallback or _prompt_judge_fallback()

    choices = WizardChoices(
        provider=chosen_provider,
        model=chosen_model,
        api_key_env_var=env_var,
        mode=chosen_mode,
        judge_fallback=chosen_fallback,
        sample_contract=not no_sample,
    )

    yaml_text = render_yaml(choices)

    click.echo("\n--- Generated sponsio.yaml ---")
    click.echo(yaml_text)
    click.echo("--- end ---\n")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml_text)
    click.secho(f"Wrote {out_path}", fg="green")
    click.echo("\nNext steps:")
    if chosen_provider != "none" and env_var:
        click.echo(f"  1. export {env_var}=...   (set your real key)")
        click.echo("  2. sponsio doctor       (verify everything wires up)")
        click.echo("  3. sponsio scan src/    (extract contracts from your code)")
    else:
        click.echo("  1. sponsio doctor       (verify everything wires up)")
        click.echo("  2. sponsio scan src/    (extract contracts from your code)")

    return out_path, choices
