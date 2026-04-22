from __future__ import annotations

from click.testing import CliRunner

from sponsio.cli import cli


def test_demo_default_mock_runs_without_optional_sdks():
    result = CliRunner().invoke(cli, ["demo", "--fast"])

    assert result.exit_code == 0
    assert "with Sponsio mock replay" in result.output
    assert "blocked" in result.output


def test_demo_no_guard_replays_breach():
    result = CliRunner().invoke(
        cli, ["demo", "--scenario", "loan", "--no-guard", "--fast"]
    )

    assert result.exit_code == 0
    assert "no Sponsio" in result.output
    assert "AML audit trail corrupted" in result.output
