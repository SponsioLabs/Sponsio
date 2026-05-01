"""Tests for ``sponsio eval`` and the underlying eval runner.

The contract these tests lock in:

* Filename label convention: ``safe_*.json`` and ``unsafe_*.json``
  (case-insensitive, ``-`` separator also accepted).
* Per-contract confusion matrix correctly counts TP/FP/FN/TN.
* Overall ("any contract blocks → blocked") aggregation works.
* FPR / FNR rates are computed *only* over labelled cases —
  unlabelled traces flow into ``n_unlabelled`` but never poison
  the rates.
* Sto / unparseable contracts show up as ``skipped`` rather than
  silently being treated as always-passing (which would inflate
  the overblock count).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from sponsio.cli import eval_cmd
from sponsio.eval_runner import (
    _label_from_filename,
    discover_cases,
    run_eval,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _trace_with_calls(*tool_calls: str, agent: str = "bot") -> dict:
    """Build a minimal OTLP JSON trace with the given tool-call sequence."""
    spans = []
    for i, name in enumerate(tool_calls):
        spans.append(
            {
                "traceId": "t1",
                "spanId": f"s{i}",
                "name": name,
                "startTimeUnixNano": str((i + 1) * 1_000_000_000),
                "endTimeUnixNano": str((i + 1) * 1_000_000_000 + 500_000_000),
                "status": {"code": 1},
            }
        )
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": agent}}
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "sponsio"},
                        "spans": spans,
                    }
                ],
            }
        ],
    }


def _write(path: Path, name: str, *tool_calls: str) -> Path:
    """Write a labelled trace file and return its path."""
    out = path / name
    out.write_text(json.dumps(_trace_with_calls(*tool_calls)))
    return out


# ---------------------------------------------------------------------------
# Label parsing
# ---------------------------------------------------------------------------


class TestLabelFromFilename:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("safe_login.json", "safe"),
            ("unsafe_drop.json", "unsafe"),
            ("SAFE_login.json", "safe"),  # case-insensitive
            ("UNSAFE-drop.json", "unsafe"),  # `-` separator accepted
            ("safe-cancel-flow.json", "safe"),
            ("login_flow.json", "unknown"),  # no prefix
            ("trace.json", "unknown"),
            # Requires the literal ``_`` or ``-`` separator, not just
            # the substring — otherwise ``unsafely_named.json`` would
            # mislabel.  Conservative wins.
            ("unsafely_named.json", "unknown"),
        ],
    )
    def test_prefix_parsing(self, name, expected):
        assert _label_from_filename(name) == expected


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscoverCases:
    def test_walks_directory_non_recursively(self, tmp_path):
        """Eval corpora are usually shallow; recursive walk would
        sweep ``node_modules/`` and friends."""
        _write(tmp_path, "safe_a.json", "verify", "transfer")
        _write(tmp_path, "unsafe_b.json", "transfer")
        nested = tmp_path / "subdir"
        nested.mkdir()
        _write(nested, "safe_c.json", "verify")

        cases = discover_cases(tmp_path)
        names = {c.name for c in cases}
        assert names == {"safe_a.json", "unsafe_b.json"}

    def test_skips_malformed_json(self, tmp_path):
        """A junk file in the corpus must not crash the run — eval
        is the kind of thing you run iteratively while building
        the corpus."""
        (tmp_path / "safe_ok.json").write_text(json.dumps(_trace_with_calls("verify")))
        (tmp_path / "safe_broken.json").write_text("not json {{{")
        (tmp_path / "safe_emptylist.json").write_text("[]")  # not a dict

        cases = discover_cases(tmp_path)
        names = {c.name for c in cases}
        assert "safe_ok.json" in names
        # broken / shape-wrong files are silently skipped
        assert "safe_broken.json" not in names

    def test_single_file_path(self, tmp_path):
        path = _write(tmp_path, "unsafe_x.json", "transfer")
        cases = discover_cases(path)
        assert len(cases) == 1
        assert cases[0].label == "unsafe"


# ---------------------------------------------------------------------------
# run_eval — the matrix
# ---------------------------------------------------------------------------


class TestRunEval:
    def test_perfect_contract(self, tmp_path):
        """The contract bans ``transfer`` outright.  Cases where the
        agent calls ``transfer`` are labelled unsafe and should be
        TP; cases without it are safe TN.  Net: precision=recall=1.0,
        FPR=FNR=0."""
        _write(tmp_path, "unsafe_a.json", "transfer")
        _write(tmp_path, "unsafe_b.json", "verify", "transfer")
        _write(tmp_path, "safe_a.json", "verify")
        _write(tmp_path, "safe_b.json", "lookup", "verify")

        cases = discover_cases(tmp_path)
        report = run_eval(cases, ["tool `transfer` at most 0 times"])
        m = report.contracts[0]
        assert (m.tp, m.fp, m.fn, m.tn) == (2, 0, 0, 2)
        assert m.precision == 1.0
        assert m.recall == 1.0
        assert m.fpr == 0.0
        assert m.fnr == 0.0

    def test_overblocking_contract(self, tmp_path):
        """A contract that bans something legitimate traffic *also*
        does → high FPR.  This is exactly the scenario eval is
        designed to make visible before the user flips enforce on."""
        _write(tmp_path, "unsafe_a.json", "transfer")
        _write(tmp_path, "safe_a.json", "lookup")  # also blocked, oops
        _write(tmp_path, "safe_b.json", "lookup")

        cases = discover_cases(tmp_path)
        # Contract bans `lookup` at most 0 times — bans the safe traffic
        report = run_eval(cases, ["tool `lookup` at most 0 times"])
        m = report.contracts[0]
        # Not blocking the unsafe trace (no `lookup`), but blocking both safes
        assert (m.tp, m.fp, m.fn, m.tn) == (0, 2, 1, 0)
        assert m.fpr == 1.0
        assert m.fnr == 1.0

    def test_unlabelled_cases_excluded_from_rates(self, tmp_path):
        """Files without ``safe_``/``unsafe_`` prefix must be visible
        in ``n_unlabelled`` but NEVER counted into the confusion
        matrix — otherwise a corpus growing organically would silently
        bias the rates."""
        _write(tmp_path, "safe_a.json", "verify")
        _write(tmp_path, "unsafe_a.json", "transfer")
        _write(tmp_path, "random_trace.json", "verify")  # no label

        cases = discover_cases(tmp_path)
        report = run_eval(cases, ["tool `transfer` at most 0 times"])
        assert report.n_cases == 3
        assert report.n_unlabelled == 1
        m = report.contracts[0]
        # Only the two labelled cases contribute
        assert m.tp + m.fp + m.fn + m.tn == 2

    def test_overall_any_contract_blocks(self, tmp_path):
        """Overall (corpus-wide) FPR/FNR uses an OR over contracts:
        if *any* contract trips, the trace is "blocked".  This is the
        correct production semantics — a single trigger-happy contract
        can poison the agent's overblock rate, and eval needs to make
        that visible."""
        _write(tmp_path, "safe_a.json", "lookup")
        _write(tmp_path, "unsafe_a.json", "transfer")

        cases = discover_cases(tmp_path)
        # Two contracts — one perfect, one overblocking
        report = run_eval(
            cases,
            [
                "tool `transfer` at most 0 times",  # catches unsafe
                "tool `lookup` at most 0 times",  # overblocks safe
            ],
        )
        # Overall: safe was blocked (FP), unsafe was blocked (TP)
        assert report.overall_tp == 1
        assert report.overall_fp == 1
        assert report.overall_fn == 0
        assert report.overall_tn == 0
        assert report.overall_fpr == 1.0
        assert report.overall_fnr == 0.0

    def test_no_labelled_cases_yields_none_rates(self, tmp_path):
        """All-unlabelled corpus: rates are ``None`` (not zero).
        Distinguishing "not measured" from "zero" matters because
        we do NOT want to display ``FPR: 0.0%`` and lull the user
        into a false sense of security."""
        _write(tmp_path, "trace_one.json", "verify")
        _write(tmp_path, "trace_two.json", "transfer")

        cases = discover_cases(tmp_path)
        report = run_eval(cases, ["tool `transfer` at most 0 times"])
        m = report.contracts[0]
        assert m.precision is None
        assert m.recall is None
        assert m.fpr is None
        assert m.fnr is None
        assert report.overall_fpr is None
        assert report.overall_fnr is None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCliEval:
    def test_inline_contract_directory(self, tmp_path):
        _write(tmp_path, "safe_a.json", "verify")
        _write(tmp_path, "unsafe_a.json", "transfer")

        runner = CliRunner()
        result = runner.invoke(
            eval_cmd, [str(tmp_path), "tool `transfer` at most 0 times"]
        )
        assert result.exit_code == 0, result.output
        assert "TP" in result.output and "FP" in result.output
        # Pretty output mentions both labels
        assert "1 safe" in result.output
        assert "1 unsafe" in result.output

    def test_json_output_machine_readable(self, tmp_path):
        _write(tmp_path, "safe_a.json", "verify")
        _write(tmp_path, "unsafe_a.json", "transfer")

        runner = CliRunner()
        result = runner.invoke(
            eval_cmd,
            [str(tmp_path), "tool `transfer` at most 0 times", "--json"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["n_safe"] == 1
        assert data["n_unsafe"] == 1
        assert data["overall"]["tp"] == 1

    def test_config_path(self, tmp_path):
        """Resolving contracts from ``sponsio.yaml`` is the primary
        mode users will run — they don't want to retype contracts on
        the command line."""
        traces = tmp_path / "traces"
        traces.mkdir()
        _write(traces, "safe_a.json", "verify")
        _write(traces, "unsafe_a.json", "transfer")

        cfg = tmp_path / "sponsio.yaml"
        cfg.write_text(
            "version: 1\n"
            "agents:\n"
            "  bot:\n"
            "    contracts:\n"
            '      - E: "tool `transfer` at most 0 times"\n'
        )

        runner = CliRunner()
        result = runner.invoke(eval_cmd, [str(traces), "--config", str(cfg)])
        assert result.exit_code == 0, result.output
        assert "TP" in result.output

    def test_empty_corpus_exits_cleanly(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        runner = CliRunner()
        result = runner.invoke(eval_cmd, [str(empty), "tool `x` at most 0 times"])
        assert result.exit_code == 0
        assert "No trace files" in result.output

    def test_rejects_config_plus_inline_contracts(self, tmp_path):
        """Same guardrail as ``sponsio check`` — mixing the two
        contract sources is ambiguous."""
        traces = tmp_path / "traces"
        traces.mkdir()
        _write(traces, "safe_a.json", "verify")
        cfg = tmp_path / "sponsio.yaml"
        cfg.write_text(
            'version: 1\nagents:\n  bot:\n    contracts:\n      - E: "tool `x` at most 0 times"\n'
        )

        runner = CliRunner()
        result = runner.invoke(
            eval_cmd,
            [str(traces), "tool `y` at most 0 times", "--config", str(cfg)],
        )
        assert result.exit_code != 0
        assert "cannot use both" in result.output
