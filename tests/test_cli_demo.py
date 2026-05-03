from __future__ import annotations

from click.testing import CliRunner

from sponsio.cli import cli


def test_demo_default_mock_runs_without_optional_sdks():
    result = CliRunner().invoke(cli, ["demo", "--fast"])

    assert result.exit_code == 0
    # Title line was renamed in the session-view refactor.
    assert "with Sponsio runtime contract enforcement" in result.output
    # Either the in-process monitor's lowercase line (Phase 2 stream)
    # or the session view's uppercase verdict (Phase 2.5) suffices —
    # both indicate at least one contract fired and was reported.
    assert "BLOCKED" in result.output or "blocked" in result.output


def test_demo_no_guard_replays_breach():
    result = CliRunner().invoke(
        cli, ["demo", "--scenario", "wire", "--no-guard", "--fast"]
    )

    assert result.exit_code == 0
    assert "no Sponsio" in result.output
    assert "unverified vendor" in result.output
