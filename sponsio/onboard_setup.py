"""Interactive setup for `sponsio onboard`.

Splits the "ask the user about their environment + write the rcfile"
piece off the heavy ``run_onboard`` pipeline.  This module owns:

* Interactive prompts (click) with detected defaults.  Falls back to
  silent default-acceptance when stdin isn't a TTY (CI / pre-commit
  hooks / docker entrypoint scripts) so the same command line works
  in both contexts without a flag.
* Writing ``.sponsiorc`` (yaml — framework / extractor / judge
  config, no secrets) so future ``sponsio`` commands in the project
  pick up the user's framework + LLM choice.
* The "you really should set an API key" warning when the chosen
  ``api_key_env`` isn't actually populated in the current shell.

We deliberately do NOT write ``.env.example`` / patch ``.gitignore``
for ``.env``: sponsio reads ``os.environ`` directly (no python-dotenv
in the runtime), so a ``.env``-based recipe would silently fail.
Users keep secrets in their shell rc / direnv / system keychain.

Why this is separate from ``onboard.py``: that module is the
library-style API consumed by tests and other tooling; this one is
TTY-aware UX glue that's only meaningful when called from the
``sponsio onboard`` CLI command.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Choices kept in sync with the framework registry in
# `sponsio/onboard.py:_PROVIDER_PRIORITY` and the framework adapters
# under `sponsio/integrations/`.  ``none`` is a real value (means
# "framework-agnostic, use generic guard.guard_before/after wiring")
# rather than a sentinel — the user picking it should be acceptable.
FRAMEWORK_CHOICES = (
    "none",
    "langgraph",
    "langchain",
    "crewai",
    "claude_agent",
    "openai_agents",
    "openai",
    "google_adk",
    "vercel_ai",
    "mcp",
)

PROVIDER_CHOICES = (
    "gemini",
    "anthropic",
    "openai",
    "ollama",
    "none",
)

# Conservative provider→model defaults used when the user accepts the
# `Model?` prompt with no detected value.  Mirrors the choices in
# ``sponsio/onboard.py:_detect_provider`` — keep them aligned.
_DEFAULT_MODEL_BY_PROVIDER = {
    "gemini": "gemini-2.5-flash-lite",
    "anthropic": "claude-3-5-sonnet-20241022",
    "openai": "gpt-4o-mini",
    "ollama": "llama3.1",
    "none": "",
}

# Suggested env-var name per provider — what most projects use.  The
# user can override; we don't lock this down.
_DEFAULT_API_KEY_ENV_BY_PROVIDER = {
    "gemini": "GOOGLE_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "ollama": "",  # local, no key
    "none": "",
}


@dataclass
class SetupAnswers:
    """User-supplied or default-accepted answers from the setup prompts.

    All fields are present even when interactive mode is skipped — in
    that case they hold whatever ``run_onboard``'s detection logic
    inferred so writing ``.sponsiorc`` doesn't need a separate code
    path for the silent case.
    """

    framework: str
    provider: str
    model: str
    api_key_env: str

    @property
    def api_key_set_in_env(self) -> bool:
        """True when ``api_key_env`` is non-empty and the var is set in
        the current shell (so the no-key warning can be skipped)."""
        if not self.api_key_env:
            return False
        return bool(os.environ.get(self.api_key_env))


def stdin_is_tty() -> bool:
    """Check whether stdin is an interactive terminal.

    Used to pick the default for ``--interactive``: a real terminal
    gets prompts, an automation context (CI, pipe, docker entrypoint)
    silently accepts detected defaults.  Robust to ``sys.stdin`` being
    a closed / non-File-like stub in unusual embedding scenarios — we
    fall back to non-interactive on any AttributeError.
    """
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def run_setup_prompts(
    *,
    detected_framework: str,
    detected_provider: str,
    detected_model: str,
    detected_api_key_env: str,
    interactive: bool,
) -> SetupAnswers:
    """Collect setup answers, either by prompting or by accepting defaults.

    Args:
        detected_framework: ``run_onboard``'s framework detection result
            (e.g. ``langgraph`` from ``import langgraph`` in scanned
            files, or ``none`` when nothing matched).
        detected_provider: Provider name from
            ``run_onboard._detect_provider`` — ``gemini`` / ``anthropic`` /
            ``openai`` / ``ollama`` / ``none``.
        detected_model: Model string the detector picked (we forward
            it as-is, falling back to a per-provider default only if
            empty).
        detected_api_key_env: Env-var name the detector matched —
            ``GOOGLE_API_KEY`` / ``ANTHROPIC_API_KEY`` / etc.
        interactive: When False, returns the detected values verbatim
            without prompting.  Caller decides this based on TTY +
            ``--interactive`` / ``--no-interactive`` flag.

    Returns:
        ``SetupAnswers`` populated from prompts (or detected values).
    """
    if not interactive:
        return SetupAnswers(
            framework=detected_framework or "none",
            provider=detected_provider or "none",
            model=detected_model
            or _DEFAULT_MODEL_BY_PROVIDER.get(detected_provider or "none", ""),
            api_key_env=detected_api_key_env
            or _DEFAULT_API_KEY_ENV_BY_PROVIDER.get(detected_provider or "none", ""),
        )

    # Lazy click import so non-CLI callers (tests, library use) don't
    # take the dep when they pass interactive=False.
    import click

    click.echo()
    click.secho(
        "▸ Configure sponsio",
        bold=True,
        fg="cyan",
    )
    click.secho(
        "  Press Enter to accept the [defaults] in cyan.",
        dim=True,
    )
    click.echo()

    # Aligned label width so default values line up vertically — looks
    # less raggy than the click default where a "Framework" prompt and
    # an "API key env var" prompt have very different left-margins.
    # 18 chars covers the longest label ("API key env var") with one
    # trailing space.
    def _label(text: str) -> str:
        return f"  {text:<18}"

    framework = click.prompt(
        _label("Framework"),
        default=detected_framework or "none",
        type=click.Choice(FRAMEWORK_CHOICES, case_sensitive=False),
        show_choices=False,
        show_default=True,
    )

    provider = click.prompt(
        _label("LLM provider"),
        default=detected_provider or "none",
        type=click.Choice(PROVIDER_CHOICES, case_sensitive=False),
        show_choices=False,
        show_default=True,
    )

    # Per-provider defaults so accepting `provider=anthropic` doesn't
    # leave you with the gemini model still in the prompt.
    model_default = (
        detected_model
        if detected_model and provider == (detected_provider or "")
        else _DEFAULT_MODEL_BY_PROVIDER.get(provider, "")
    )

    if provider == "none":
        model = ""
        api_key_env = ""
    else:
        model = click.prompt(
            _label("Model"),
            default=model_default,
            type=str,
            show_default=True,
        )
        env_default = (
            detected_api_key_env
            if detected_api_key_env and provider == (detected_provider or "")
            else _DEFAULT_API_KEY_ENV_BY_PROVIDER.get(provider, "")
        )
        if provider == "ollama":
            # Local provider; leave the env-var prompt out — Ollama
            # doesn't need a key.
            api_key_env = ""
        else:
            api_key_env = click.prompt(
                _label("API key env var"),
                default=env_default,
                type=str,
                show_default=True,
            )

    return SetupAnswers(
        framework=framework,
        provider=provider,
        model=model,
        api_key_env=api_key_env,
    )


def render_sponsiorc(answers: SetupAnswers) -> str:
    """Render the ``.sponsiorc`` yaml body.

    Single source of truth for the template; the file-write helper
    just calls this and writes the result.  Tested directly so format
    changes don't need a tmp-path round trip.
    """
    lines = [
        "# .sponsiorc — sponsio environment / runtime config",
        "# Generated by `sponsio onboard`.  Safe to commit (no secrets).",
        "# `api_key_env` names the env var sponsio reads at runtime —",
        "# export it from your shell rc / direnv / secret manager.",
        "",
        f"framework: {answers.framework}",
        "",
        "# Parse-time LLM used by `sponsio scan`",
        "# to infer contracts from your tool definitions.",
        "extractor:",
        f"  provider: {answers.provider or 'none'}",
    ]
    if answers.model:
        lines.append(f"  model: {answers.model}")
    if answers.api_key_env:
        lines.append(f"  api_key_env: {answers.api_key_env}")
    lines.extend(
        [
            "",
            "# Runtime stochastic-judge: not supported in this build.",
            "# Used at agent runtime to score stochastic atoms",
            "# (injection_free, harmful, etc.). This stanza is ignored",
            "# unless a sto evaluator is plugged in. Same provider as",
            "# extractor by default; override here for a cheaper /",
            "# different hot-path model.",
            "judge:",
            f"  provider: {answers.provider or 'none'}",
        ]
    )
    if answers.model:
        lines.append(f"  model: {answers.model}")
    if answers.api_key_env:
        lines.append(f"  api_key_env: {answers.api_key_env}")
    lines.extend(
        [
            "  fallback_mode: allow  # allow|deny|skip on judge LLM failure",
            "",
        ]
    )
    return "\n".join(lines)


def write_sponsiorc(answers: SetupAnswers, target_dir: Path) -> Path:
    """Write ``.sponsiorc`` to ``target_dir``, returning its path."""
    path = target_dir / ".sponsiorc"
    path.write_text(render_sponsiorc(answers), encoding="utf-8")
    return path


def maybe_no_api_key_warning(answers: SetupAnswers) -> str | None:
    """Return a multiline warning when sponsio will fall back to the
    name-heuristic starter pack instead of LLM-inferred contracts.

    Two trigger cases:

    1. User picked a key-needing provider (gemini / anthropic / openai)
       but the relevant env var isn't set in the current shell —
       silent failure mode where they think they're using the LLM
       but actually getting starter pack.
    2. ``provider == "none"`` — typically because no LLM credentials
       were detected at onboard time and the user accepted the
       default.  This is the more common new-user case and the one
       most likely to cause "demo doesn't catch anything" confusion.

    Returns None for ``provider == "ollama"`` (local, intentional
    no-key path) — Ollama running locally produces real LLM output.

    The recommended workflow is to ``export`` the key in the user's
    shell rc / direnv / system keychain — wherever they already
    manage secrets.  Sponsio reads ``os.environ`` directly and does
    not load ``.env`` files (no python-dotenv in the runtime), so a
    ``cp .env.example .env`` ritual would be a dead-end recipe.

    Caller is responsible for surrounding click.style / blank lines
    so the visual treatment matches the rest of the onboard output.
    """
    if answers.provider == "ollama":
        return None

    if answers.provider == "none":
        return (
            "⚠ No LLM provider configured — contracts will be\n"
            "    name-heuristic only (no LLM-inferred rules from your\n"
            "    tool docstrings).  This catches obvious patterns but\n"
            "    will miss domain-specific risks.  Demos like\n"
            "    backup_delete / wire_transfer rely on LLM-inferred\n"
            "    path / amount bounds — those won't be there.\n"
            "\n"
            "    To upgrade (Gemini's free tier is plenty for onboard):\n"
            "      1. Get a key at https://aistudio.google.com/apikey\n"
            "      2. export GOOGLE_API_KEY=...   # or add to ~/.zshrc / direnv\n"
            "      3. Re-run: sponsio onboard --force"
        )

    # Provider is set (gemini/anthropic/openai) but env var is empty.
    if not answers.api_key_set_in_env:
        return (
            f"⚠ {answers.api_key_env} is not set in your shell.\n"
            "    Without an API key, sponsio falls back to name-heuristic\n"
            "    contracts only — they catch obvious patterns but miss\n"
            "    domain-specific risks.  Effect on demos like\n"
            "    backup_delete / wire_transfer is significant: the\n"
            "    LLM-inferred path / amount bounds won't be there.\n"
            "\n"
            "    Strongly recommended:\n"
            f"      export {answers.api_key_env}=...   # add to your shell rc / direnv\n"
            "      sponsio onboard --force"
        )

    return None
