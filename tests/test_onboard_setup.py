"""Tests for sponsio/onboard_setup.py — interactive setup helpers."""

from __future__ import annotations


from sponsio.onboard_setup import (
    SetupAnswers,
    maybe_no_api_key_warning,
    render_sponsiorc,
    run_setup_prompts,
    write_sponsiorc,
)


# ---------------------------------------------------------------------------
# render_sponsiorc
# ---------------------------------------------------------------------------


class TestRenderSponsiorc:
    def test_full_config(self):
        ans = SetupAnswers(
            framework="langgraph",
            provider="gemini",
            model="gemini-2.5-flash",
            api_key_env="GOOGLE_API_KEY",
        )
        out = render_sponsiorc(ans)
        assert "framework: langgraph" in out
        assert "provider: gemini" in out
        assert "model: gemini-2.5-flash" in out
        assert "api_key_env: GOOGLE_API_KEY" in out
        # Both extractor + judge sections present.
        assert out.count("provider: gemini") == 2
        assert "fallback_mode: allow" in out

    def test_provider_none_omits_model_and_key_env(self):
        ans = SetupAnswers(framework="none", provider="none", model="", api_key_env="")
        out = render_sponsiorc(ans)
        assert "framework: none" in out
        assert "provider: none" in out
        # No model / api_key_env lines when those fields are empty.
        assert "model:" not in out
        assert "api_key_env:" not in out

    def test_no_secrets_in_output(self):
        """Sanity: render must never emit raw key values, only env-var
        names — but if a user accidentally typed the key into the
        prompt instead of an env-var name, we'd at least catch the
        glaring `sk-` / `AIza` shape."""
        ans = SetupAnswers(
            framework="none",
            provider="anthropic",
            model="claude-3-5-sonnet-20241022",
            api_key_env="ANTHROPIC_API_KEY",
        )
        out = render_sponsiorc(ans)
        assert "sk-" not in out
        assert "AIza" not in out


# ---------------------------------------------------------------------------
# write_sponsiorc
# ---------------------------------------------------------------------------


class TestWriteFiles:
    def test_write_sponsiorc_creates_file(self, tmp_path):
        ans = SetupAnswers(
            framework="langgraph",
            provider="gemini",
            model="gemini-2.5-flash",
            api_key_env="GOOGLE_API_KEY",
        )
        path = write_sponsiorc(ans, tmp_path)
        assert path == tmp_path / ".sponsiorc"
        assert path.exists()
        body = path.read_text()
        assert "framework: langgraph" in body

    def test_write_overwrites_existing(self, tmp_path):
        # First write
        a1 = SetupAnswers(framework="none", provider="none", model="", api_key_env="")
        write_sponsiorc(a1, tmp_path)
        # Second write with different answers must replace
        a2 = SetupAnswers(
            framework="langgraph",
            provider="anthropic",
            model="claude-3-5-sonnet-20241022",
            api_key_env="ANTHROPIC_API_KEY",
        )
        write_sponsiorc(a2, tmp_path)
        body = (tmp_path / ".sponsiorc").read_text()
        assert "langgraph" in body
        assert "framework: none" not in body

    def test_no_dotenv_artifacts(self, tmp_path):
        # Sponsio reads os.environ directly (no python-dotenv in the
        # runtime).  Onboard MUST NOT generate ``.env.example`` or
        # mutate ``.gitignore`` for ``.env`` — those would mislead
        # users into a recipe sponsio doesn't actually honour.
        ans = SetupAnswers(
            framework="langgraph",
            provider="gemini",
            model="gemini-2.5-flash",
            api_key_env="GOOGLE_API_KEY",
        )
        write_sponsiorc(ans, tmp_path)
        assert not (tmp_path / ".env").exists()
        assert not (tmp_path / ".env.example").exists()
        assert not (tmp_path / ".gitignore").exists()


# ---------------------------------------------------------------------------
# maybe_no_api_key_warning
# ---------------------------------------------------------------------------


class TestNoKeyWarning:
    def test_provider_none_warns(self):
        ans = SetupAnswers(framework="none", provider="none", model="", api_key_env="")
        msg = maybe_no_api_key_warning(ans)
        assert msg is not None
        assert "name-heuristic" in msg
        assert "GOOGLE_API_KEY" in msg  # specific suggestion
        # Recommend the actually-working recipe (export from shell rc),
        # not the dead-end ``cp .env.example .env`` ritual sponsio
        # doesn't honour.
        assert "export GOOGLE_API_KEY" in msg
        assert ".env.example" not in msg

    def test_provider_set_but_env_missing_warns(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        ans = SetupAnswers(
            framework="langgraph",
            provider="gemini",
            model="gemini-2.5-flash",
            api_key_env="GOOGLE_API_KEY",
        )
        msg = maybe_no_api_key_warning(ans)
        assert msg is not None
        assert "GOOGLE_API_KEY is not set" in msg
        assert "export GOOGLE_API_KEY" in msg
        assert ".env.example" not in msg

    def test_provider_set_and_env_present_silent(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "AIzafake")
        ans = SetupAnswers(
            framework="langgraph",
            provider="gemini",
            model="gemini-2.5-flash",
            api_key_env="GOOGLE_API_KEY",
        )
        assert maybe_no_api_key_warning(ans) is None

    def test_ollama_silent(self):
        # Local provider, intentional no-key path.
        ans = SetupAnswers(
            framework="langgraph",
            provider="ollama",
            model="llama3.1",
            api_key_env="",
        )
        assert maybe_no_api_key_warning(ans) is None


# ---------------------------------------------------------------------------
# run_setup_prompts (non-interactive path)
# ---------------------------------------------------------------------------


class TestRunSetupPromptsNonInteractive:
    def test_accepts_detected_values(self):
        ans = run_setup_prompts(
            detected_framework="langgraph",
            detected_provider="gemini",
            detected_model="gemini-2.5-flash",
            detected_api_key_env="GOOGLE_API_KEY",
            interactive=False,
        )
        assert ans.framework == "langgraph"
        assert ans.provider == "gemini"
        assert ans.model == "gemini-2.5-flash"
        assert ans.api_key_env == "GOOGLE_API_KEY"

    def test_fills_per_provider_defaults(self):
        # Detection found provider but no model — non-interactive
        # should still fall back to the per-provider default model
        # rather than leaving the field empty.
        ans = run_setup_prompts(
            detected_framework="none",
            detected_provider="anthropic",
            detected_model="",
            detected_api_key_env="",
            interactive=False,
        )
        assert ans.provider == "anthropic"
        assert ans.model  # filled from default
        assert ans.api_key_env == "ANTHROPIC_API_KEY"
