"""Tests for ``sponsio scan`` helpful messaging when 0 tools are found."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from sponsio.cli import scan


def test_scan_zero_tools_prints_friendly_note(tmp_path: Path) -> None:
    """A tree with no discoverable .py tools should explain *why* the count
    is zero (not just ``--llm`` for contracts)."""
    d = tmp_path / "mostly_empty"
    d.mkdir()
    (d / "notes.txt").write_text("no python tools here\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(scan, [str(d)], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    low = result.output.lower()
    assert "0 tool" in low
    assert "0 tools usually" in low
    assert ".venv" in result.output  # user learns deps dirs are skipped
