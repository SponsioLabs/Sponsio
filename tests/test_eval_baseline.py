"""Tests for the ``sponsio eval --baseline`` regression gate.

Three concerns covered:

1. ``diff_reports`` (pure function): correct deltas, contract
   add/remove/change classification, missing values handled.
2. ``BaselineDiff.gate_violations``: --max-fpr-delta / --max-fnr-delta
   fire when (and only when) they should.
3. CLI end-to-end: a regression flips the exit code; a clean run
   doesn't; ``--write-baseline`` only fires on green.

These tests are the contract for "Sponsio in CI" — if any of them
break, every downstream user's pipeline breaks with them, so the
expected behaviours are pinned aggressively.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

pytest.importorskip("yaml")

from sponsio.cli import eval_cmd
from sponsio.eval_runner import (
    BaselineDiff,
    ContractMetrics,
    EvalReport,
    diff_reports,
)


# ---------------------------------------------------------------------------
# Pure: diff_reports
# ---------------------------------------------------------------------------


def _report(**kwargs) -> EvalReport:
    """Tiny factory so each test reads as data, not setup."""
    contracts = kwargs.pop("contracts", [])
    r = EvalReport(**kwargs)
    for c in contracts:
        r.contracts.append(ContractMetrics(**c))
    return r


class TestDiffReports:
    def test_unchanged_overall_yields_zero_delta(self):
        baseline = {
            "n_cases": 10,
            "overall": {"fpr": 0.05, "fnr": 0.10},
            "contracts": [],
        }
        cur = _report(
            n_cases=10, overall_fp=1, overall_tn=19, overall_fn=1, overall_tp=9
        )
        # FPR = 1/20 = 0.05, FNR = 1/10 = 0.10 → both match baseline
        d = diff_reports(baseline, cur)
        assert d.overall_fpr_delta == pytest.approx(0.0)
        assert d.overall_fnr_delta == pytest.approx(0.0)

    def test_overall_fpr_regression_surfaces_as_positive_delta(self):
        baseline = {
            "n_cases": 100,
            "overall": {"fpr": 0.02, "fnr": 0.05},
            "contracts": [],
        }
        # 5% overblock — 2pp worse than baseline
        cur = _report(
            n_cases=100, overall_fp=5, overall_tn=95, overall_fn=5, overall_tp=95
        )
        d = diff_reports(baseline, cur)
        assert d.overall_fpr_delta == pytest.approx(0.03)  # 5% - 2%

    def test_added_contract_marked(self):
        baseline = {
            "overall": {"fpr": 0.0, "fnr": 0.0},
            "contracts": [
                {"nl": "old contract", "fpr": 0.0, "fnr": 0.0},
            ],
        }
        cur = _report(contracts=[{"contract_nl": "new contract", "tp": 1, "tn": 9}])
        d = diff_reports(baseline, cur)
        statuses = {c.contract_nl: (c.in_baseline, c.in_current) for c in d.contracts}
        assert statuses["new contract"] == (False, True)
        assert statuses["old contract"] == (True, False)

    def test_changed_contract_keeps_both_rates(self):
        baseline = {
            "overall": {"fpr": None, "fnr": None},
            "contracts": [
                {"nl": "C", "fpr": 0.10, "fnr": 0.00},
            ],
        }
        cur = _report(
            contracts=[{"contract_nl": "C", "tp": 5, "fp": 1, "tn": 9, "fn": 0}]
        )
        # FPR = 1/10 = 0.10 (unchanged), FNR = 0
        d = diff_reports(baseline, cur)
        c = next(c for c in d.contracts if c.contract_nl == "C")
        assert c.in_baseline and c.in_current
        assert c.fpr_before == pytest.approx(0.10)
        assert c.fpr_after == pytest.approx(0.10)
        assert c.fpr_delta == pytest.approx(0.0)

    def test_missing_baseline_rate_yields_none_delta(self):
        """Don't pretend None→0.05 is a +5pp regression; it's a
        baseline that didn't have signal yet."""
        baseline = {"overall": {"fpr": None, "fnr": None}, "contracts": []}
        cur = _report(
            n_cases=10, overall_fp=1, overall_tn=9, overall_fn=0, overall_tp=0
        )
        d = diff_reports(baseline, cur)
        assert d.overall_fpr_delta is None  # baseline FPR was None


# ---------------------------------------------------------------------------
# Gate violations
# ---------------------------------------------------------------------------


class TestGateViolations:
    def _diff(self, fpr_before, fpr_after, fnr_before=None, fnr_after=None):
        return BaselineDiff(
            overall_fpr_before=fpr_before,
            overall_fpr_after=fpr_after,
            overall_fnr_before=fnr_before,
            overall_fnr_after=fnr_after,
        )

    def test_no_gates_no_violations(self):
        d = self._diff(0.02, 0.10)  # huge regression
        assert d.gate_violations() == []  # but no gates configured

    def test_fpr_within_budget_passes(self):
        d = self._diff(0.02, 0.025)  # +0.5pp
        assert d.gate_violations(max_fpr_delta=0.01) == []  # budget = 1pp

    def test_fpr_exceeding_budget_fails(self):
        d = self._diff(0.02, 0.05)  # +3pp
        violations = d.gate_violations(max_fpr_delta=0.01)
        assert len(violations) == 1
        assert "FPR" in violations[0]
        assert "3.00pp" in violations[0]

    def test_zero_tolerance_fnr_gate(self):
        """``--max-fnr-delta 0`` is the strictest setting: any new
        miss fails.  Operators use it for high-stakes contracts where
        a single missed incident is unacceptable."""
        d = self._diff(0.0, 0.0, fnr_before=0.0, fnr_after=0.001)
        violations = d.gate_violations(max_fnr_delta=0.0)
        assert len(violations) == 1
        assert "FNR" in violations[0]

    def test_unset_baseline_rate_skips_gate(self):
        """No signal in baseline means the gate has nothing to
        compare against — skip rather than fail."""
        d = self._diff(None, 0.05)
        assert d.gate_violations(max_fpr_delta=0.0) == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def example_corpus(tmp_path: Path) -> Path:
    """Drop the bundled example into tmp_path so we have a real
    corpus to evaluate against."""
    from sponsio.init_wizard import install_example

    install_example(tmp_path, force=True)
    return tmp_path


def _run_eval(runner: CliRunner, *args) -> "object":
    return runner.invoke(eval_cmd, list(args))


class TestCliBaseline:
    def test_baseline_diff_renders_in_human_output(
        self, tmp_path: Path, example_corpus: Path
    ):
        """Round trip: produce a baseline JSON, then re-run with
        --baseline and confirm the diff section renders."""
        runner = CliRunner()

        # First run → write baseline
        baseline = tmp_path / "baseline.json"
        result = _run_eval(
            runner,
            str(example_corpus / "traces"),
            "--config",
            str(example_corpus / "sponsio.yaml"),
            "--agent",
            "customer_bot",
            "--write-baseline",
            str(baseline),
        )
        assert result.exit_code == 0, result.output
        assert baseline.exists()
        # Sanity-check the baseline JSON is parseable
        data = json.loads(baseline.read_text())
        assert "overall" in data

        # Second run → diff against baseline (same corpus → no delta)
        result = _run_eval(
            runner,
            str(example_corpus / "traces"),
            "--config",
            str(example_corpus / "sponsio.yaml"),
            "--agent",
            "customer_bot",
            "--baseline",
            str(baseline),
        )
        assert result.exit_code == 0, result.output
        assert "Baseline diff" in result.output
        assert "overall FPR" in result.output

    def test_gate_passes_when_clean(self, tmp_path: Path, example_corpus: Path):
        """Identical corpus → 0pp delta → gate passes."""
        runner = CliRunner()
        baseline = tmp_path / "baseline.json"
        _run_eval(
            runner,
            str(example_corpus / "traces"),
            "-c",
            str(example_corpus / "sponsio.yaml"),
            "-a",
            "customer_bot",
            "--write-baseline",
            str(baseline),
        )
        result = _run_eval(
            runner,
            str(example_corpus / "traces"),
            "-c",
            str(example_corpus / "sponsio.yaml"),
            "-a",
            "customer_bot",
            "--baseline",
            str(baseline),
            "--max-fpr-delta",
            "0.0",
            "--max-fnr-delta",
            "0.0",
        )
        assert result.exit_code == 0, result.output

    def test_gate_fails_on_regression(self, tmp_path: Path, example_corpus: Path):
        """Synthesize a baseline where FPR was 0; current FPR is 0
        too BUT we set max_fpr_delta to a negative number to force a
        gate violation deterministically.  (We can't naturally
        regress the bundled corpus without rewriting trace files;
        synthetic baseline is the cleanest way to test the gate
        wiring without coupling to corpus internals.)"""
        runner = CliRunner()

        # Baseline reports a "perfect" prior run by hand
        synthetic_baseline = tmp_path / "synthetic.json"
        synthetic_baseline.write_text(
            json.dumps(
                {
                    "n_cases": 6,
                    "n_safe": 3,
                    "n_unsafe": 3,
                    "n_unlabelled": 0,
                    "overall": {
                        "tp": 3,
                        "fp": 0,
                        "fn": 0,
                        "tn": 3,
                        "fpr": 0.0,
                        "fnr": 0.0,
                    },
                    "contracts": [],
                }
            )
        )

        # Current run is also clean (FPR=0, FNR=0) → delta = 0
        # We set --max-fpr-delta = -0.01 so even a 0-delta is "above"
        # the budget.  This is the deterministic way to test the
        # gate's failure path without manufacturing real regressions.
        # NOTE: in real usage --max-fpr-delta is always >= 0.
        # ...except: 0.0 - 0.0 = 0.0, which is NOT > -0.01, so it'd
        # actually pass.  Use a baseline with non-zero FPR instead.
        synthetic_baseline.write_text(
            json.dumps(
                {
                    "n_cases": 6,
                    "overall": {"fpr": 0.5, "fnr": 0.5},
                    "contracts": [],
                }
            )
        )
        # current FPR = 0, baseline FPR = 0.5 → delta = -0.5 (improvement)
        # That should NOT trip a gate (improvements never fail).  Now
        # do it the other way: baseline says FPR=0, current is the
        # corpus (FPR=0 too), threshold = 0 → no gate fail.
        # → To actually FAIL, we need current > baseline.

        # Corrected approach: synthesize a baseline with very low
        # rates AND modify a trace so current rates are higher.  We
        # do that by adding a trace that the contracts will misfire
        # on — easiest: rename one of the safe traces to look unsafe
        # (so the contracts pass it but it's labelled unsafe → FN).
        traces = example_corpus / "traces"
        (traces / "safe_normal_refund.json").rename(traces / "unsafe_relabelled.json")

        synthetic_baseline.write_text(
            json.dumps(
                {
                    "n_cases": 6,
                    "overall": {"fpr": 0.0, "fnr": 0.0},
                    "contracts": [],
                }
            )
        )

        result = _run_eval(
            runner,
            str(traces),
            "-c",
            str(example_corpus / "sponsio.yaml"),
            "-a",
            "customer_bot",
            "--baseline",
            str(synthetic_baseline),
            "--max-fnr-delta",
            "0.0",
        )
        # Mislabeled corpus → FNR > 0; gate had 0pp budget → fail
        assert result.exit_code == 1, result.output
        assert "FNR" in result.output

    def test_max_delta_without_baseline_errors(
        self, tmp_path: Path, example_corpus: Path
    ):
        """Catch the user's typo before running a 30s eval against
        a corpus that won't even gate-check."""
        runner = CliRunner()
        result = _run_eval(
            runner,
            str(example_corpus / "traces"),
            "-c",
            str(example_corpus / "sponsio.yaml"),
            "-a",
            "customer_bot",
            "--max-fpr-delta",
            "0.01",  # but no --baseline
        )
        assert result.exit_code == 2
        assert "--baseline" in result.output

    def test_write_baseline_skipped_on_gate_failure(
        self, tmp_path: Path, example_corpus: Path
    ):
        """Critical safety: a regressing PR must NOT auto-overwrite
        the baseline it failed against — that would silently launder
        the regression into the new bar."""
        runner = CliRunner()
        # Force a gate failure with a relabelled-trace trick (same
        # mechanism as test_gate_fails_on_regression).
        traces = example_corpus / "traces"
        (traces / "safe_normal_refund.json").rename(traces / "unsafe_oops.json")

        baseline = tmp_path / "baseline.json"
        baseline.write_text(
            json.dumps(
                {
                    "n_cases": 6,
                    "overall": {"fpr": 0.0, "fnr": 0.0},
                    "contracts": [],
                }
            )
        )

        new_baseline = tmp_path / "new_baseline.json"
        result = _run_eval(
            runner,
            str(traces),
            "-c",
            str(example_corpus / "sponsio.yaml"),
            "-a",
            "customer_bot",
            "--baseline",
            str(baseline),
            "--max-fnr-delta",
            "0.0",
            "--write-baseline",
            str(new_baseline),
        )
        assert result.exit_code == 1
        assert not new_baseline.exists(), (
            "write-baseline must not poison the baseline on a failed gate"
        )
        assert "skipped writing" in result.output

    def test_json_output_includes_diff(self, tmp_path: Path, example_corpus: Path):
        """Dashboards / wrapper scripts consume --json; the diff
        section must be there alongside the report."""
        runner = CliRunner()
        baseline = tmp_path / "baseline.json"
        _run_eval(
            runner,
            str(example_corpus / "traces"),
            "-c",
            str(example_corpus / "sponsio.yaml"),
            "-a",
            "customer_bot",
            "--write-baseline",
            str(baseline),
        )
        result = _run_eval(
            runner,
            str(example_corpus / "traces"),
            "-c",
            str(example_corpus / "sponsio.yaml"),
            "-a",
            "customer_bot",
            "--baseline",
            str(baseline),
            "--json",
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Flat report fields preserved (backward compat with
        # pre-baseline JSON consumers); diff lives under a sibling key.
        assert "n_cases" in data
        assert "overall" in data
        assert "baseline_diff" in data
        assert "overall" in data["baseline_diff"]
