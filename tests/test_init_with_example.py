"""Tests for ``sponsio init --with-example``.

Three layers of coverage:

1. ``install_example`` — pure function: copies, refuses to clobber,
   honours ``--force``, returns the file plan it wrote.
2. CLI ``init --with-example`` — happy path, conflicting flags
   rejected, exit codes, and the eval command in the printed
   "Next steps" actually runs against the dropped scaffold.
3. End-to-end ``sponsio eval`` against the dropped scaffold —
   the whole point of the feature is that the user can run eval
   immediately, so we lock that in.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

pytest.importorskip("yaml")

from sponsio.cli import init
from sponsio.init_wizard import install_example


class TestInstallExample:
    def test_drops_full_bundle(self, tmp_path: Path):
        """Smoke test: every file from the bundle lands under target."""
        written = install_example(tmp_path)

        assert (tmp_path / "sponsio.yaml").exists()
        assert (tmp_path / "README.md").exists()
        traces = list((tmp_path / "traces").glob("*.json"))
        assert len(traces) == 6

        # Plan must reflect every file we actually wrote — used by
        # the CLI to print a tidy summary.
        assert len(written) == len(traces) + 2  # yaml + readme + 6 traces

    def test_refuses_to_clobber_without_force(self, tmp_path: Path):
        """Common sub-case: user already ran `sponsio init` and now
        adds `--with-example` thinking it's additive.  The clobber
        guard saves their hand-edited sponsio.yaml."""
        (tmp_path / "sponsio.yaml").write_text("# my hand-edited config\n")

        with pytest.raises(Exception) as exc_info:
            install_example(tmp_path, force=False)
        assert "sponsio.yaml" in str(exc_info.value)
        # Original content must be intact (no partial copy).
        assert (tmp_path / "sponsio.yaml").read_text() == "# my hand-edited config\n"

    def test_force_overwrites_only_planned_files(self, tmp_path: Path):
        """``--force`` replaces the example's files but must NOT
        rmtree the target — sibling files have to survive (e.g. the
        user's `src/`)."""
        (tmp_path / "sponsio.yaml").write_text("# stale\n")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "agent.py").write_text("def hi(): ...\n")

        install_example(tmp_path, force=True)

        assert "tool `verify_identity`" in (tmp_path / "sponsio.yaml").read_text()
        # Sibling untouched
        assert (tmp_path / "src" / "agent.py").exists()

    def test_unknown_example_raises(self, tmp_path: Path):
        with pytest.raises(Exception) as exc_info:
            install_example(tmp_path, example="does_not_exist")
        assert "does_not_exist" in str(exc_info.value)


class TestCliInitWithExample:
    def test_drops_runnable_scaffold(self, tmp_path: Path):
        runner = CliRunner()
        result = runner.invoke(init, [str(tmp_path), "--with-example"])
        assert result.exit_code == 0, result.output
        # Output should hint at the next eval command
        assert "sponsio eval" in result.output
        assert "customer_bot" in result.output
        assert (tmp_path / "sponsio.yaml").exists()
        assert (tmp_path / "traces").is_dir()

    def test_yaml_target_rejected(self, tmp_path: Path):
        """``--with-example`` writes a tree, not a single file —
        passing a `.yaml` argument is a category error and we
        catch it explicitly so the failure mode is obvious."""
        runner = CliRunner()
        result = runner.invoke(init, [str(tmp_path / "sponsio.yaml"), "--with-example"])
        assert result.exit_code != 0
        assert "directory" in result.output.lower()

    def test_conflicting_flags_rejected(self, tmp_path: Path):
        """Wizard flags don't apply with --with-example because the
        bundled YAML is hand-tuned to the bundled traces.  Surface
        the conflict instead of silently dropping flags."""
        runner = CliRunner()
        result = runner.invoke(
            init,
            [str(tmp_path), "--with-example", "--provider", "gemini"],
        )
        assert result.exit_code != 0
        assert "--provider" in result.output

    def test_clobber_guard_message_actionable(self, tmp_path: Path):
        """Pre-existing sponsio.yaml + --with-example without --force
        must print a clear "use --force" hint, not just bail."""
        (tmp_path / "sponsio.yaml").write_text("# mine\n")

        runner = CliRunner()
        result = runner.invoke(init, [str(tmp_path), "--with-example"])
        assert result.exit_code != 0
        assert "--force" in result.output

    def test_force_overwrite(self, tmp_path: Path):
        (tmp_path / "sponsio.yaml").write_text("# stale\n")
        runner = CliRunner()
        result = runner.invoke(init, [str(tmp_path), "--with-example", "--force"])
        assert result.exit_code == 0, result.output
        assert "verify_identity" in (tmp_path / "sponsio.yaml").read_text()


class TestEndToEnd:
    def test_dropped_scaffold_runs_eval(self, tmp_path: Path):
        """The whole point: after `init --with-example`, the printed
        eval command must produce the exact 6-case report we
        document.  This is the test that catches "looks fine but
        eval bombs" regressions in either the wizard or the
        bundle."""
        from sponsio.config import load_config
        from sponsio.eval_runner import discover_cases, run_eval

        runner = CliRunner()
        result = runner.invoke(init, [str(tmp_path), "--with-example"])
        assert result.exit_code == 0, result.output

        cfg = load_config(tmp_path / "sponsio.yaml")
        contracts: list[str] = []
        for ce in cfg.agents["customer_bot"].contracts:
            ext = ce.enforcement
            contracts.extend(ext if isinstance(ext, list) else [ext])

        cases = discover_cases(tmp_path / "traces")
        assert len(cases) == 6

        report = run_eval(cases, contracts)
        assert (report.n_safe, report.n_unsafe) == (3, 3)
        # Combined coverage should still be perfect (matches README)
        assert report.overall_fpr == 0.0
        assert report.overall_fnr == 0.0
