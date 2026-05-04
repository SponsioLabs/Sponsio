"""Tests for the new ``extractor:`` and ``judge:`` config sections
and the ``${ENV_VAR}`` interpolation that makes them usable without
committing secrets.

The user-visible contract these tests lock in:

* A YAML file can declare its parse-time ``extractor`` and runtime
  ``judge`` independently — they're conceptually different LLM jobs
  (offline accuracy vs hot-path latency), so they get independent
  ``provider`` / ``model`` / ``api_key`` knobs.
* ``${VAR}`` and ``${VAR:-default}`` expand against the process env
  at load time.  Missing without default expands to empty string
  (matches shell semantics; never raises).
* The judge section also carries the fault-tolerance knobs
  (``fallback_mode``, ``circuit_breaker``, ``failure_threshold``,
  ``cooldown_seconds``) so ops can tune resilience without code
  changes.
* Both sections are optional and default to "no LLM configured" —
  YAML files written before this feature must keep working.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

pytest.importorskip("yaml")

from sponsio.config import (
    ConfigError,
    JudgeSection,
    _interpolate_env,
    build_extractor,
    build_sto_evaluator,
    load_config,
)


# ---------------------------------------------------------------------------
# ${ENV_VAR} interpolation (the primitive)
# ---------------------------------------------------------------------------


class TestInterpolateEnv:
    def test_simple_var(self, monkeypatch):
        monkeypatch.setenv("FOO", "bar")
        assert _interpolate_env("${FOO}") == "bar"

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("FOO", raising=False)
        assert _interpolate_env("${FOO:-default}") == "default"

    def test_set_var_overrides_default(self, monkeypatch):
        monkeypatch.setenv("FOO", "real")
        assert _interpolate_env("${FOO:-default}") == "real"

    def test_missing_no_default_becomes_empty(self, monkeypatch):
        """Matches shell semantics; the consumer (e.g. OpenAI client
        constructor) decides whether empty is fatal.  We don't raise
        because that would make ``${OPTIONAL_VAR}`` impossible to
        express."""
        monkeypatch.delenv("MISSING_THING", raising=False)
        assert _interpolate_env("${MISSING_THING}") == ""

    def test_substring_inside_string(self, monkeypatch):
        monkeypatch.setenv("HOST", "api.example.com")
        assert _interpolate_env("https://${HOST}/v1/") == "https://api.example.com/v1/"

    def test_bare_dollar_not_touched(self):
        """Naked ``$VAR`` is intentionally NOT supported because YAML
        strings often contain dollar signs (currency, regex, template
        languages); we don't want to munch them."""
        assert _interpolate_env("price: $100") == "price: $100"
        assert _interpolate_env("$VAR") == "$VAR"

    def test_walks_dicts_and_lists(self, monkeypatch):
        monkeypatch.setenv("K", "v")
        out = _interpolate_env({"a": "${K}", "b": ["${K}", {"c": "${K}"}], "n": 42})
        assert out == {"a": "v", "b": ["v", {"c": "v"}], "n": 42}

    def test_non_string_passthrough(self):
        assert _interpolate_env(42) == 42
        assert _interpolate_env(None) is None
        assert _interpolate_env(True) is True


# ---------------------------------------------------------------------------
# extractor: section
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "sponsio.yaml"
    p.write_text(textwrap.dedent(body).lstrip())
    return p


class TestExtractorSection:
    def test_absent_section_yields_empty_defaults(self, tmp_path):
        """Backward-compat: configs written before this feature must
        load unchanged."""
        path = _write(
            tmp_path,
            """
            version: 1
            agents:
              bot:
                contracts:
                  - G: "tool `foo` at most 3 times"
            """,
        )
        cfg = load_config(path)
        assert cfg.extractor.provider is None
        assert cfg.extractor.api_key is None

    def test_explicit_extractor_section(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MY_OPENAI_KEY", "sk-test-123")
        path = _write(
            tmp_path,
            """
            version: 1
            extractor:
              provider: openai
              model: gpt-4o
              api_key: ${MY_OPENAI_KEY}
            agents:
              bot:
                contracts:
                  - G: "tool `foo` at most 3 times"
            """,
        )
        cfg = load_config(path)
        assert cfg.extractor.provider == "openai"
        assert cfg.extractor.model == "gpt-4o"
        assert cfg.extractor.api_key == "sk-test-123"

    def test_build_extractor_returns_none_when_unconfigured(self):
        from sponsio.config import ExtractorSection

        assert build_extractor(ExtractorSection()) is None

    def test_invalid_extractor_section_type(self, tmp_path):
        path = _write(
            tmp_path,
            """
            version: 1
            extractor: "not a mapping"
            """,
        )
        with pytest.raises(ConfigError, match="extractor.*mapping"):
            load_config(path)


# ---------------------------------------------------------------------------
# judge: section
# ---------------------------------------------------------------------------


class TestJudgeSection:
    def test_absent_section_yields_safe_defaults(self, tmp_path):
        """Defaults must match :class:`StoEvaluator`'s programmatic
        defaults so an empty section is exactly equivalent to "no
        config at all"."""
        path = _write(
            tmp_path,
            """
            version: 1
            agents:
              bot:
                contracts:
                  - G: "tool `foo` at most 3 times"
            """,
        )
        cfg = load_config(path)
        assert cfg.judge.fallback_mode == "allow"
        assert cfg.judge.circuit_breaker is True
        assert cfg.judge.failure_threshold == 5
        assert cfg.judge.cooldown_seconds == 10.0

    def test_explicit_resilience_knobs(self, tmp_path):
        path = _write(
            tmp_path,
            """
            version: 1
            judge:
              provider: gemini
              model: gemini-2.0-flash
              fallback_mode: deny
              circuit_breaker: false
              failure_threshold: 10
              cooldown_seconds: 30
            agents:
              bot:
                contracts:
                  - G: "tool `foo` at most 3 times"
            """,
        )
        cfg = load_config(path)
        assert cfg.judge.fallback_mode == "deny"
        assert cfg.judge.circuit_breaker is False
        assert cfg.judge.failure_threshold == 10
        assert cfg.judge.cooldown_seconds == 30.0

    def test_invalid_fallback_mode_rejected(self, tmp_path):
        path = _write(
            tmp_path,
            """
            version: 1
            judge:
              fallback_mode: yolo
            """,
        )
        with pytest.raises(ConfigError, match="fallback_mode"):
            load_config(path)

    def test_build_sto_evaluator_requires_cloud(self):
        """OSS ships no StoEvaluator implementation; ``build_sto_evaluator``
        must surface a Cloud-pointing ConfigError instead of silently
        returning some no-op stub."""
        from sponsio.config import ConfigError

        section = JudgeSection(
            fallback_mode="deny",
            circuit_breaker=False,
            failure_threshold=7,
            cooldown_seconds=42.0,
        )
        with pytest.raises(ConfigError, match="sponsio\\[cloud\\]"):
            build_sto_evaluator(section)


# ---------------------------------------------------------------------------
# End-to-end: env interpolation + sections together
# ---------------------------------------------------------------------------


def test_round_trip_with_env_vars(tmp_path, monkeypatch):
    """A realistic config: parse-time uses Anthropic with an env-var
    key; runtime judge uses Gemini with an env-var key; defaults
    fill in the rest.  Locks in the canonical user-facing shape."""
    monkeypatch.setenv("ANTHROPIC_KEY", "sk-ant-xxx")
    monkeypatch.setenv("GEMINI_KEY", "AI-yyy")
    path = _write(
        tmp_path,
        """
        version: 1
        extractor:
          provider: anthropic
          model: claude-3-5-sonnet-20241022
          api_key: ${ANTHROPIC_KEY}
        judge:
          provider: gemini
          model: gemini-2.0-flash
          api_key: ${GEMINI_KEY}
          fallback_mode: allow
        agents:
          bot:
            contracts:
              - G: "tool `transfer` at most 1 times"
        """,
    )
    cfg = load_config(path)
    assert cfg.extractor.provider == "anthropic"
    assert cfg.extractor.api_key == "sk-ant-xxx"
    assert cfg.judge.provider == "gemini"
    assert cfg.judge.api_key == "AI-yyy"
    assert cfg.judge.fallback_mode == "allow"
