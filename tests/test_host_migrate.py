"""Tests for ``sponsio host migrate`` and the legacy-bucket
deprecation warning in :mod:`sponsio.guard_stdin`.

The runtime fallback from ``_host_<name>/`` to ``_host/`` exists so
existing installs keep enforcing after the per-host routing change,
but it's also been the source of "I deleted X but Sponsio still
blocks" — the legacy bucket silently kicks in.  These tests pin
two pieces of the consolidation story:

1. The fallback still works (regression preservation), but emits a
   visible deprecation notice that names the consolidation command.

2. ``sponsio host migrate <name>`` copies legacy → per-host with the
   ``agents:`` key rewritten, then deletes the legacy file by default.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from sponsio.cli import cli
from sponsio.guard_stdin import _LEGACY_FALLBACK_WARNED, _resolve_library


# ---------------------------------------------------------------------------
# Legacy fallback deprecation warning
# ---------------------------------------------------------------------------


class TestLegacyFallbackWarning:
    def setup_method(self):
        # Each test starts with a clean once-per-process flag so the
        # warning emits as expected.
        _LEGACY_FALLBACK_WARNED.clear()

    def test_per_host_yaml_supersedes_legacy_silently(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        # Per-host bucket present → legacy is ignored, no warning.
        monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
        per_host = tmp_path / "_host_claude_code" / "sponsio.yaml"
        per_host.parent.mkdir(parents=True)
        per_host.write_text("# per-host\n")
        legacy = tmp_path / "_host" / "sponsio.yaml"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("# legacy\n")

        path, agent_id = _resolve_library("_host_claude_code")
        captured = capsys.readouterr()

        assert path == per_host
        assert agent_id == "_host_claude_code"
        assert "legacy" not in captured.err

    def test_legacy_fallback_emits_visible_warning_once(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        # No per-host yaml → falls through to _host AND warns.  The
        # user can't miss the dual-yaml situation any more.
        monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
        legacy = tmp_path / "_host" / "sponsio.yaml"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("# legacy\n")

        path, agent_id = _resolve_library("_host_claude_code")
        captured = capsys.readouterr()

        assert path == legacy
        assert agent_id == "_host"
        assert "legacy `_host`" in captured.err
        assert "sponsio host migrate claude-code" in captured.err

        # Second call in the same process: silent (per-process dedupe).
        _resolve_library("_host_claude_code")
        captured2 = capsys.readouterr()
        assert "legacy" not in captured2.err

    def test_no_warning_when_neither_yaml_exists(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        # Fresh install, no rules anywhere → no warning, no error;
        # callers handle the missing-library case downstream.
        monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
        path, agent_id = _resolve_library("_host_claude_code")
        captured = capsys.readouterr()
        assert "legacy" not in captured.err


# ---------------------------------------------------------------------------
# `sponsio host migrate <names>`
# ---------------------------------------------------------------------------


def _legacy_yaml_text() -> str:
    """Realistic legacy ``_host/sponsio.yaml`` body — captures the
    ``agents: _host:`` shape the migrate command must rewrite."""
    return (
        "version: '1'\n"
        "defaults:\n"
        "  mode: enforce\n"
        "agents:\n"
        "  _host:\n"
        "    tool_rename:\n"
        "      exec: Bash\n"
        "    include:\n"
        "      - sponsio:capability/shell\n"
        "    contracts:\n"
        "      - desc: Block sensitive paths\n"
        "        G:\n"
        "          pattern: arg_blacklist\n"
        "          args: [Read, file_path, ['(^|/)\\.ssh/']]\n"
    )


class TestHostMigrate:
    def test_migrate_single_host_writes_per_host_and_deletes_legacy(
        self, tmp_path: Path, monkeypatch
    ):
        monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
        legacy = tmp_path / "_host" / "sponsio.yaml"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(_legacy_yaml_text())

        runner = CliRunner()
        result = runner.invoke(cli, ["host", "migrate", "claude-code"])
        assert result.exit_code == 0, result.output

        per_host = tmp_path / "_host_claude_code" / "sponsio.yaml"
        assert per_host.exists()
        # Agents key got rewritten so BaseGuard finds the agent block.
        content = per_host.read_text(encoding="utf-8")
        assert "_host_claude_code:" in content
        assert "_host:" not in content.replace("_host_claude_code", "")
        # Original payload preserved (the contracts list, not just
        # the agent key).
        assert "Block sensitive paths" in content
        # Legacy bucket gone by default — no more dual-yaml confusion.
        assert not legacy.exists()

    def test_migrate_to_multiple_hosts_in_one_invocation(
        self, tmp_path: Path, monkeypatch
    ):
        monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
        legacy = tmp_path / "_host" / "sponsio.yaml"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(_legacy_yaml_text())

        runner = CliRunner()
        result = runner.invoke(cli, ["host", "migrate", "claude-code", "cursor"])
        assert result.exit_code == 0, result.output

        # Both per-host buckets populated from the same legacy body.
        for bucket in ("_host_claude_code", "_host_cursor"):
            target = tmp_path / bucket / "sponsio.yaml"
            assert target.exists()
            assert f"{bucket}:" in target.read_text()

    def test_migrate_refuses_to_overwrite_per_host_without_force(
        self, tmp_path: Path, monkeypatch
    ):
        monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
        legacy = tmp_path / "_host" / "sponsio.yaml"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(_legacy_yaml_text())
        per_host = tmp_path / "_host_claude_code" / "sponsio.yaml"
        per_host.parent.mkdir(parents=True)
        per_host.write_text("# user customisation\n")

        runner = CliRunner()
        result = runner.invoke(cli, ["host", "migrate", "claude-code"])
        assert result.exit_code != 0
        assert "already exists" in result.output
        # User customisation untouched.
        assert per_host.read_text() == "# user customisation\n"
        # Legacy still in place because we aborted before deletion.
        assert legacy.exists()

    def test_migrate_force_overwrites(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
        legacy = tmp_path / "_host" / "sponsio.yaml"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(_legacy_yaml_text())
        per_host = tmp_path / "_host_claude_code" / "sponsio.yaml"
        per_host.parent.mkdir(parents=True)
        per_host.write_text("# stale\n")

        runner = CliRunner()
        result = runner.invoke(cli, ["host", "migrate", "claude-code", "--force"])
        assert result.exit_code == 0, result.output
        # Now contains the migrated content, not the stale stub.
        content = per_host.read_text()
        assert "_host_claude_code:" in content
        assert "Block sensitive paths" in content

    def test_migrate_keep_legacy_preserves_old_file(self, tmp_path: Path, monkeypatch):
        # Escape hatch for users who want to retain the legacy file
        # for an audit / rollback window.
        monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
        legacy = tmp_path / "_host" / "sponsio.yaml"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(_legacy_yaml_text())

        runner = CliRunner()
        result = runner.invoke(cli, ["host", "migrate", "claude-code", "--keep-legacy"])
        assert result.exit_code == 0, result.output
        assert legacy.exists()

    def test_migrate_errors_when_legacy_missing(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
        runner = CliRunner()
        result = runner.invoke(cli, ["host", "migrate", "claude-code"])
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_migrate_rejects_unknown_host_name(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
        legacy = tmp_path / "_host" / "sponsio.yaml"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(_legacy_yaml_text())

        runner = CliRunner()
        result = runner.invoke(cli, ["host", "migrate", "definitely-not"])
        assert result.exit_code != 0
        assert "unknown host" in result.output


# ---------------------------------------------------------------------------
# `sponsio host install` legacy-bucket nudge + `plugin init` deprecation
# ---------------------------------------------------------------------------


class TestInstallSurfaceWarnings:
    def test_plugin_init_prints_deprecation_notice(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
        runner = CliRunner()
        # Default Click runner merges stdout+stderr into ``output``.
        # The deprecation goes to err=True (stderr in production), so
        # reading from output here is fine for the assertion.
        result = runner.invoke(cli, ["plugin", "init"])
        # Init still succeeds — we deprecate, not break.
        assert result.exit_code == 0, result.output
        assert "deprecated" in result.output
        assert "sponsio host install" in result.output
        assert "sponsio host migrate" in result.output
