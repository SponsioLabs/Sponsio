"""Tests for ``sponsio validate`` CLI — especially path vs --config UX."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

pytest.importorskip("yaml")

from sponsio.cli import validate


def test_validate_lone_yaml_path_auto_treats_as_config(tmp_path: Path) -> None:
    """Forgot ``--config``: a single existing path to a sponsio-like YAML
    should validate as a project file, not as an inline contract string."""
    cfg = tmp_path / "sponsio.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            version: 1
            agents:
              bot:
                contracts:
                  - E: "tool `a` must precede `b`"
            """
        ).lstrip(),
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(validate, [str(cfg)], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "treating" in result.output.lower()
    assert "must_precede" in result.output.lower() or "det" in result.output.lower()


def test_validate_random_yaml_not_auto_routed(tmp_path: Path) -> None:
    """A YAML file without Sponsio markers is not treated as --config."""
    other = tmp_path / "k8s.yaml"
    other.write_text("apiVersion: v1\nkind: ConfigMap\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(validate, [str(other)], catch_exceptions=False)
    assert result.exit_code != 0


def test_validate_explicit_config_unchanged(tmp_path: Path) -> None:
    cfg = tmp_path / "x.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            version: 1
            agents:
              bot:
                contracts:
                  - E: "tool `a` must precede `b`"
            """
        ).lstrip(),
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(validate, ["--config", str(cfg)], catch_exceptions=False)
    assert result.exit_code == 0
    assert "treating" not in result.output.lower()
