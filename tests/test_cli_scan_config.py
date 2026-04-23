"""Tests for ``sponsio scan --config`` — extractor: section wiring.

The contract these tests lock in:

* ``--config sponsio.yaml`` reads the ``extractor:`` section and
  passes ``provider`` / ``model`` / ``api_key`` / ``base_url`` to
  the underlying ``CodeAnalyzer``.
* Explicit CLI flags (``--provider`` etc.) keep precedence over
  YAML values — the user can always override on a one-off.
* ``--config`` implies ``--llm`` because configuring an extractor
  and then not using it would be confusing.
* A YAML with no ``extractor:`` section emits a friendly warning
  but still runs the rule-based pass.
* ``${ENV_VAR}`` interpolation works end-to-end (the YAML loader
  expands at parse time, so by the time ``CodeAnalyzer`` sees the
  key it's a real string from the env).
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

pytest.importorskip("yaml")

from sponsio.cli import scan


def _write_yaml(path: Path, body: str) -> Path:
    path.write_text(textwrap.dedent(body).lstrip())
    return path


@pytest.fixture
def src_dir(tmp_path: Path) -> Path:
    """Minimal source tree so ``CodeAnalyzer.extract`` has something
    to walk — content doesn't matter; we only assert on what the
    analyzer was constructed with."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("def hello():\n    return 1\n")
    return src


def _capture_analyzer_init():
    """Patch ``CodeAnalyzer`` to capture init kwargs without running
    the real (slow, network-touching) extraction."""
    captured: dict = {}

    class _FakeAnalyzer:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def get_tool_inventory(self, _paths):
            return []

        def generate_yaml(
            self,
            _paths,
            agent_id,
            policy_paths=None,
            tool_inventory=None,
            trace_paths=None,
            trace_min_support=1,
            trace_confidence_threshold=0.95,
        ):
            # Return a minimal-but-parseable scan YAML — the post-scan
            # summary parses this for tool/contract counts.
            return f"version: 1\nagents:\n  {agent_id}:\n    contracts: []\n"

    return captured, _FakeAnalyzer


class TestScanConfigWiring:
    def test_yaml_extractor_passes_through_to_analyzer(
        self, tmp_path: Path, src_dir: Path, monkeypatch
    ):
        monkeypatch.setenv("FAKE_KEY", "sk-test-123")
        cfg = _write_yaml(
            tmp_path / "sponsio.yaml",
            """
            version: 1
            extractor:
              provider: anthropic
              model: claude-3-5-sonnet-20241022
              api_key: ${FAKE_KEY}
            """,
        )
        captured, FakeAnalyzer = _capture_analyzer_init()

        runner = CliRunner()
        with patch(
            "sponsio.discovery.extractors.code_analysis.CodeAnalyzer", FakeAnalyzer
        ):
            result = runner.invoke(
                scan,
                [str(src_dir), "--config", str(cfg), "--no-push"],
            )

        assert result.exit_code == 0, result.output
        assert captured["provider"] == "anthropic"
        assert captured["llm_model"] == "claude-3-5-sonnet-20241022"
        assert captured["api_key"] == "sk-test-123"
        # --config implies --llm
        assert captured["use_llm"] is True

    def test_cli_flags_override_yaml(self, tmp_path: Path, src_dir: Path):
        """The user's one-off override on the command line must win.
        Otherwise editing YAML to bypass a CLI flag would be the only
        escape hatch — slow loop."""
        cfg = _write_yaml(
            tmp_path / "sponsio.yaml",
            """
            version: 1
            extractor:
              provider: anthropic
              model: claude-3-5-sonnet-20241022
            """,
        )
        captured, FakeAnalyzer = _capture_analyzer_init()

        runner = CliRunner()
        with patch(
            "sponsio.discovery.extractors.code_analysis.CodeAnalyzer", FakeAnalyzer
        ):
            result = runner.invoke(
                scan,
                [
                    str(src_dir),
                    "--config",
                    str(cfg),
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-4o-mini",
                    "--no-push",
                ],
            )

        assert result.exit_code == 0, result.output
        assert captured["provider"] == "openai"
        assert captured["llm_model"] == "gpt-4o-mini"

    def test_config_implies_llm(self, tmp_path: Path, src_dir: Path):
        """Even without ``--llm``, ``--config`` should turn it on
        and surface that decision in stderr — no silent behavior."""
        cfg = _write_yaml(
            tmp_path / "sponsio.yaml",
            """
            version: 1
            extractor:
              provider: gemini
              model: gemini-2.0-flash
            """,
        )
        captured, FakeAnalyzer = _capture_analyzer_init()

        runner = CliRunner()
        with patch(
            "sponsio.discovery.extractors.code_analysis.CodeAnalyzer", FakeAnalyzer
        ):
            result = runner.invoke(
                scan,
                [str(src_dir), "--config", str(cfg), "--no-push"],
            )

        assert result.exit_code == 0, result.output
        assert captured["use_llm"] is True
        assert "implies --llm" in result.output or "--llm" in result.stderr

    def test_yaml_without_extractor_section_warns(self, tmp_path: Path, src_dir: Path):
        """A YAML that just has agents/contracts is still valid input;
        we shouldn't error, just point out there's nothing to inherit."""
        cfg = _write_yaml(
            tmp_path / "sponsio.yaml",
            """
            version: 1
            agents:
              bot:
                contracts:
                  - E: "tool `x` at most 0 times"
            """,
        )
        captured, FakeAnalyzer = _capture_analyzer_init()

        runner = CliRunner()
        with patch(
            "sponsio.discovery.extractors.code_analysis.CodeAnalyzer", FakeAnalyzer
        ):
            result = runner.invoke(
                scan,
                [str(src_dir), "--config", str(cfg), "--no-push"],
            )

        assert result.exit_code == 0, result.output
        assert "no `extractor:` section" in result.output
        # Without an extractor section but --config implies --llm, so
        # provider stays None → CodeAnalyzer falls back to env-detection
        assert captured["provider"] is None

    def test_no_config_uses_env_detection(self, tmp_path: Path, src_dir: Path):
        """Backward compat: omitting --config keeps the existing
        env-var auto-detection path untouched."""
        captured, FakeAnalyzer = _capture_analyzer_init()

        runner = CliRunner()
        with patch(
            "sponsio.discovery.extractors.code_analysis.CodeAnalyzer", FakeAnalyzer
        ):
            result = runner.invoke(
                scan,
                [str(src_dir), "--llm", "--provider", "openai", "--no-push"],
            )

        assert result.exit_code == 0, result.output
        assert captured["provider"] == "openai"
        # api_key not threaded in — analyzer reads OPENAI_API_KEY itself
        assert captured["api_key"] is None
