"""Tests for ``sponsio init`` and the underlying wizard module.

Covers two layers:

* ``render_yaml`` — pure function from :class:`WizardChoices` to
  YAML text.  Golden-style snapshot tests for the canonical shapes
  users will actually see (LLM provider with env-var key; bedrock
  with no key; "none" provider).
* ``run_wizard`` end-to-end via Click's CliRunner — verifies the
  non-interactive flag path produces a loadable ``sponsio.yaml`` and
  the interactive path produces the same file given canned input.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

pytest.importorskip("yaml")

from sponsio.cli import init
from sponsio.config import load_config
from sponsio.init_wizard import WizardChoices, render_yaml


# ---------------------------------------------------------------------------
# render_yaml — pure
# ---------------------------------------------------------------------------


class TestRenderYaml:
    def test_full_llm_config_round_trips_through_loader(self, tmp_path: Path):
        """The wizard's output must always parse cleanly with the
        very same loader that production Sponsio uses — otherwise
        we'd be generating "documentation YAML" rather than working
        config."""
        choices = WizardChoices(
            provider="openai",
            model="gpt-4o-mini",
            api_key_env_var="OPENAI_API_KEY",
            mode="observe",
            judge_fallback="allow",
            sample_contract=True,
        )
        text = render_yaml(choices)
        out = tmp_path / "sponsio.yaml"
        out.write_text(text)
        cfg = load_config(out)
        # extractor + judge populated
        assert cfg.extractor.provider == "openai"
        assert cfg.extractor.model == "gpt-4o-mini"
        assert cfg.judge.provider == "openai"
        assert cfg.judge.fallback_mode == "allow"
        # Sample contract present
        assert "my_agent" in cfg.agents

    def test_provider_none_omits_extractor_section(self):
        """When the user picks "no LLM", we must NOT emit an
        ``extractor:`` block — otherwise ``build_extractor`` would
        try to construct a ``UnifiedExtractor(provider=None)`` based
        on an empty section.  The comment-only block lives in its
        place to remind users they can opt in later."""
        choices = WizardChoices(
            provider="none",
            model=None,
            api_key_env_var=None,
            mode="observe",
            judge_fallback="allow",
            sample_contract=False,
        )
        text = render_yaml(choices)
        # No `extractor:` *mapping key* (a top-level YAML key starts
        # at column 0).  The literal substring may appear in a
        # comment that nudges the user to opt in later — that's fine.
        assert not any(line.startswith("extractor:") for line in text.splitlines())
        # judge: still present (that section uses sane defaults
        # with or without an LLM)
        assert any(line.startswith("judge:") for line in text.splitlines())

    def test_bedrock_omits_api_key_field(self, tmp_path: Path):
        """Bedrock authenticates via the AWS credential chain — no
        API-key env var.  Emitting ``api_key: ${...}`` would be
        actively misleading."""
        choices = WizardChoices(
            provider="bedrock",
            model="anthropic.claude-3-5-sonnet-20241022-v2:0",
            api_key_env_var=None,
            mode="enforce",
            judge_fallback="deny",
            sample_contract=False,
        )
        text = render_yaml(choices)
        assert "api_key" not in text
        assert "provider: bedrock" in text
        out = tmp_path / "sponsio.yaml"
        out.write_text(text)
        cfg = load_config(out)
        assert cfg.judge.fallback_mode == "deny"

    def test_no_sample_omits_agents_block(self):
        choices = WizardChoices(
            provider="gemini",
            model="gemini-2.0-flash",
            api_key_env_var="GOOGLE_API_KEY",
            mode="observe",
            judge_fallback="allow",
            sample_contract=False,
        )
        text = render_yaml(choices)
        assert "agents:" not in text


# ---------------------------------------------------------------------------
# CLI end-to-end
# ---------------------------------------------------------------------------


class TestCliInit:
    def test_non_interactive_writes_loadable_yaml(self, tmp_path: Path):
        """The four ``--flag`` options skip every prompt — useful
        for CI smoke tests and copy-pasteable docs commands."""
        runner = CliRunner()
        target = tmp_path / "sponsio.yaml"
        result = runner.invoke(
            init,
            [
                str(target),
                "--provider",
                "gemini",
                "--mode",
                "observe",
                "--judge-fallback",
                "allow",
                "--no-sample",
                "--force",
            ],
        )
        assert result.exit_code == 0, result.output
        assert target.exists()
        cfg = load_config(target)
        assert cfg.extractor.provider == "gemini"
        assert cfg.judge.fallback_mode == "allow"

    def test_existing_file_aborts_without_force(self, tmp_path: Path):
        """The wizard must NOT silently overwrite a hand-edited
        ``sponsio.yaml`` — that would destroy real user work.  The
        ``n`` answer to "Overwrite?" exits non-zero."""
        runner = CliRunner()
        target = tmp_path / "sponsio.yaml"
        target.write_text("# precious\nversion: 1\n")

        result = runner.invoke(
            init,
            [
                str(target),
                "--provider",
                "none",
                "--mode",
                "observe",
                "--judge-fallback",
                "allow",
            ],
            input="n\n",
        )
        assert result.exit_code != 0
        assert target.read_text() == "# precious\nversion: 1\n"

    def test_force_overwrites(self, tmp_path: Path):
        runner = CliRunner()
        target = tmp_path / "sponsio.yaml"
        target.write_text("# precious\n")
        result = runner.invoke(
            init,
            [
                str(target),
                "--force",
                "--provider",
                "gemini",
                "--mode",
                "observe",
                "--judge-fallback",
                "allow",
                "--no-sample",
            ],
        )
        assert result.exit_code == 0, result.output
        # Old content gone
        assert "# precious" not in target.read_text()
        assert "provider: gemini" in target.read_text()

    def test_directory_target_writes_sponsio_yaml(self, tmp_path: Path):
        """Passing a directory should produce ``<dir>/sponsio.yaml``
        — the most common invocation is ``sponsio init`` in the
        project root."""
        runner = CliRunner()
        result = runner.invoke(
            init,
            [
                str(tmp_path),
                "--provider",
                "gemini",
                "--mode",
                "observe",
                "--judge-fallback",
                "allow",
                "--no-sample",
                "--force",
            ],
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "sponsio.yaml").exists()

    def test_interactive_pipeline_with_canned_input(self, tmp_path: Path, monkeypatch):
        """End-to-end interactive simulation: every prompt answered
        with the keystroke sequence below.  Locks in the prompt
        order and the default-acceptance behavior — if a future
        refactor reorders prompts, this test fails loudly."""
        # Make sure no env-var "credentials detected" hint changes
        # the default-provider selection.
        for var in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "AWS_ACCESS_KEY_ID",
            "AWS_PROFILE",
        ):
            monkeypatch.delenv(var, raising=False)

        runner = CliRunner()
        target = tmp_path / "sponsio.yaml"
        # Inputs (one per prompt, in order):
        #   provider choice "3" = gemini
        #   env-var name (Enter = default GOOGLE_API_KEY)
        #   mode (Enter = observe)
        #   judge fallback (Enter = allow)
        result = runner.invoke(
            init,
            [str(target), "--no-sample", "--force"],
            input="3\n\n\n\n",
        )
        assert result.exit_code == 0, result.output
        cfg = load_config(target)
        assert cfg.extractor.provider == "gemini"
        # Env var not set; ``${GOOGLE_API_KEY}`` expands to "" then
        # the loader normalises empty strings to ``None``.
        assert cfg.extractor.api_key is None

    def test_non_interactive_uses_default_env_var(self, tmp_path: Path):
        """When ``--provider`` is supplied, we shouldn't *also*
        prompt for the env-var name (would defeat the purpose of
        non-interactive mode).  Verifies the default env var made
        it into the YAML."""
        runner = CliRunner()
        target = tmp_path / "sponsio.yaml"
        result = runner.invoke(
            init,
            [
                str(target),
                "--provider",
                "anthropic",
                "--mode",
                "observe",
                "--judge-fallback",
                "allow",
                "--no-sample",
                "--force",
            ],
        )
        assert result.exit_code == 0, result.output
        text = target.read_text()
        assert "api_key: ${ANTHROPIC_API_KEY}" in text
