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
                  - G: "tool `a` must precede `b`"
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
                  - G: "tool `a` must precede `b`"
            """
        ).lstrip(),
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(validate, ["--config", str(cfg)], catch_exceptions=False)
    assert result.exit_code == 0
    assert "treating" not in result.output.lower()


# ---------------------------------------------------------------------------
# A rule that cannot fire must not validate green
# ---------------------------------------------------------------------------


def _write(tmp_path, body):
    p = tmp_path / "sponsio.yaml"
    p.write_text(body)
    return str(p)


def test_a_config_with_no_contracts_fails(tmp_path):
    """ "All 0 contract(s) validated" reads as yes to the person who came
    here to be told their rules are sound. A mistyped `contarcts:` key
    lands exactly here, and the guard then arms nothing."""
    cfg = _write(
        tmp_path,
        'version: "1"\nagents:\n  bot:\n    contarcts:\n'
        '      - G: "tool `a` must precede `b`"\n',
    )
    r = CliRunner().invoke(validate, ["--config", cfg])
    assert r.exit_code == 1
    assert "no contracts found" in r.output


def test_an_include_that_resolves_to_nothing_fails(tmp_path):
    """`sponsio:core/universal` is a stub with zero rules, and the README
    leads with it."""
    cfg = _write(
        tmp_path,
        'version: "1"\nagents:\n  bot:\n'
        "    include: [sponsio:core/universal]\n    contracts: []\n",
    )
    r = CliRunner().invoke(validate, ["--config", cfg])
    assert r.exit_code == 1
    assert "no contracts found" in r.output


def test_a_contract_naming_an_undeclared_tool_fails(tmp_path):
    """Both names are misspelled and neither is in `tools:`. The contract
    parses, arms, prints ACTIVE, and watches a tool nobody calls."""
    cfg = _write(
        tmp_path,
        'version: "1"\ntools:\n  - name: unlock_door\n    description: unlock\n'
        "agents:\n  bot:\n    contracts:\n"
        '      - G: "must call `check_warrenty` before `dispatch_contracter`"\n',
    )
    r = CliRunner().invoke(validate, ["--config", cfg])
    assert r.exit_code == 1
    assert "can never fire" in r.output
    assert "check_warrenty" in r.output
    assert "dispatch_contracter" in r.output


def test_declared_tools_pass(tmp_path):
    cfg = _write(
        tmp_path,
        'version: "1"\ntools:\n  - name: a\n    description: a\n'
        "  - name: b\n    description: b\n"
        'agents:\n  bot:\n    contracts:\n      - G: "tool `a` must precede `b`"\n',
    )
    r = CliRunner().invoke(validate, ["--config", cfg])
    assert r.exit_code == 0, r.output
    assert "1 contract(s) validated" in r.output


def test_no_tools_block_means_no_tool_check(tmp_path):
    """Without a declared inventory there is nothing to check against,
    and a warning on every contract would be noise."""
    cfg = _write(
        tmp_path,
        'version: "1"\nagents:\n  bot:\n    contracts:\n'
        '      - G: "tool `whatever` must precede `other`"\n',
    )
    r = CliRunner().invoke(validate, ["--config", cfg])
    assert r.exit_code == 0, r.output
