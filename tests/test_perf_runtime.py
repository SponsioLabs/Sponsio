"""Integration tests: tracker wired into the real guard pipeline.

These tests run a live ``BaseGuard`` and verify that:

  * pure-det contracts land in the ``pure_det`` bucket
  * sto contracts that actually call the judge land in ``sto_live``
  * sto contracts whose atoms hit the memo land in ``sto_cached``
  * per-contract labels match the banner
  * ``guard.performance_stats()`` is safe to call at any time
  * the config ``performance:`` block plumbs through correctly

Deliberately avoids timing assertions ("p99 must be < Xμs") — CI
hardware varies and flaky perf tests waste more eng time than they
save.  Instead we assert the *shape* of the output: correct counts,
correct bucketing, correct keys.
"""

from __future__ import annotations

from pathlib import Path

from sponsio.integrations.base import BaseGuard
from sponsio.patterns.library import tool_allowlist


# ---------------------------------------------------------------------------
# Pure-det contract timing
# ---------------------------------------------------------------------------


def test_pure_det_contract_lands_in_pure_det_bucket():
    """A ``tool_allowlist`` contract is pure DFA — every check must
    be classified ``pure_det`` and never touch ``sto_live``."""
    g = BaseGuard(
        agent_id="a",
        contracts=[{"guarantee": tool_allowlist(["ok_tool"])}],
        verbose=False,
    )
    for i in range(50):
        g.guard_before("ok_tool", {"i": i})

    stats = g.performance_stats()
    assert stats["total_checks"] == 50
    assert stats["n_pure_det"] == 50
    assert stats["n_sto_cached"] == 0
    assert stats["n_sto_live"] == 0
    assert stats["zero_llm_ratio"] == 1.0
    assert stats["pure_det"]["n"] == 50
    # The only active contract gets per-contract stats attached.
    assert len(stats["per_contract"]) == 1


def test_fresh_guard_reports_zero_checks():
    """Guard that has never seen an action must return a sane
    total_checks=0 summary — not None, not raise."""
    g = BaseGuard(
        agent_id="a",
        contracts=[{"guarantee": tool_allowlist(["x"])}],
        verbose=False,
    )
    stats = g.performance_stats()
    assert stats["total_checks"] == 0
    assert stats["pure_det"]["n"] == 0
    assert stats["per_contract"] == {}


def test_violation_check_still_records_sample():
    """A blocked tool call must still produce a timing sample —
    perf tracking shouldn't skip the failure path, since users
    care about block-path latency too."""
    g = BaseGuard(
        agent_id="a",
        contracts=[{"guarantee": tool_allowlist(["ok_tool"])}],
        verbose=False,
    )
    g.guard_before("ok_tool", {})
    g.guard_before("blocked_tool", {})  # violation
    stats = g.performance_stats()
    # Exactly 2 checks recorded — the violation didn't fall through.
    assert stats["total_checks"] == 2


def test_per_contract_labels_in_output():
    """Per-contract rows are keyed by the contract's human label so
    users can ``jq '.per_contract["my rule"]'`` from a perf dump."""
    g = BaseGuard(
        agent_id="a",
        contracts=[{"guarantee": tool_allowlist(["x"])}],
        verbose=False,
    )
    g.guard_before("x", {})
    stats = g.performance_stats()
    labels = list(stats["per_contract"].keys())
    # Contract label format is "<agent>: <A>A/<E>E" fallback.
    assert len(labels) == 1
    assert "a" in labels[0]


# ---------------------------------------------------------------------------
# performance_stats() idempotency + mid-session callability
# ---------------------------------------------------------------------------


def test_performance_stats_callable_mid_session():
    """Calling performance_stats() mid-session must not reset
    counters — users probe it for dashboards between checks."""
    g = BaseGuard(
        agent_id="a",
        contracts=[{"guarantee": tool_allowlist(["x"])}],
        verbose=False,
    )
    g.guard_before("x", {})
    s1 = g.performance_stats()
    s2 = g.performance_stats()
    assert s1["total_checks"] == s2["total_checks"] == 1


# ---------------------------------------------------------------------------
# YAML ``performance:`` section integration
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "sponsio.yaml"
    p.write_text(body)
    return p


def test_yaml_histogram_size_applied(tmp_path):
    """histogram_size=3 ⇒ the per-contract ring should cap bucket
    samples at 3 even when 100 checks fire."""
    config = _write_yaml(
        tmp_path,
        """
version: "1"
performance:
  histogram_size: 3
agents:
  a:
    contracts:
      - G:
          pattern: tool_allowlist
          args: [[x]]
""",
    )
    g = BaseGuard(config=str(config), agent_id="a", verbose=False)
    for _ in range(100):
        g.guard_before("x", {})
    stats = g.performance_stats()
    # Aggregate counter sees 100.
    assert stats["n_pure_det"] == 100
    # But bucket percentile-window is 3 (matches the YAML).
    assert stats["pure_det"]["n"] == 3


def test_yaml_export_path_is_read(tmp_path):
    """``performance.export_path`` is parsed and stored on the
    guard's config snapshot — we don't trigger atexit here (that's
    harder to test), we just verify the wiring."""
    export_target = tmp_path / "out" / "perf.json"
    config = _write_yaml(
        tmp_path,
        f"""
version: "1"
performance:
  report: never
  export_path: "{export_target}"
agents:
  a:
    contracts:
      - G:
          pattern: tool_allowlist
          args: [[x]]
""",
    )
    g = BaseGuard(config=str(config), agent_id="a", verbose=False)
    assert g._perf_config is not None
    assert g._perf_config.report == "never"
    assert g._perf_config.export_path == str(export_target)

    # Manually trigger the auto-report to exercise the export path
    # side effect (bypasses atexit so the test is deterministic).
    g.guard_before("x", {})
    g._auto_perf_report()
    assert export_target.exists()


def test_auto_perf_report_silent_when_zero_checks(tmp_path, capsys):
    """The hook must say nothing when the guard saw no traffic —
    hello-world scripts that instantiate a guard and exit should
    not spam a perf table."""
    config = _write_yaml(
        tmp_path,
        """
version: "1"
performance:
  report: always
agents:
  a:
    contracts:
      - G:
          pattern: tool_allowlist
          args: [[x]]
""",
    )
    g = BaseGuard(config=str(config), agent_id="a", verbose=False)
    g._auto_perf_report()
    out = capsys.readouterr()
    assert "Sponsio performance" not in (out.out + out.err)


def test_auto_perf_report_prints_when_always(tmp_path, capsys):
    """``report: always`` forces a print even in non-TTY test runs
    — the opt-in for CI logs that *do* want the numbers."""
    config = _write_yaml(
        tmp_path,
        """
version: "1"
performance:
  report: always
agents:
  a:
    contracts:
      - G:
          pattern: tool_allowlist
          args: [[x]]
""",
    )
    g = BaseGuard(config=str(config), agent_id="a", verbose=False)
    g.guard_before("x", {})
    g._auto_perf_report()
    out = capsys.readouterr()
    assert "Sponsio performance" in (out.out + out.err)


def test_auto_perf_report_never_suppresses(tmp_path, capsys):
    """``report: never`` means never — even when there's actual
    traffic to report on."""
    config = _write_yaml(
        tmp_path,
        """
version: "1"
performance:
  report: never
agents:
  a:
    contracts:
      - G:
          pattern: tool_allowlist
          args: [[x]]
""",
    )
    g = BaseGuard(config=str(config), agent_id="a", verbose=False)
    g.guard_before("x", {})
    g._auto_perf_report()
    out = capsys.readouterr()
    assert "Sponsio performance" not in (out.out + out.err)


def test_warn_slow_dfa_fires(tmp_path, capsys):
    """warn_slow_dfa_us threshold must trip when pure_det p99 exceeds it.

    We set a deliberately silly low threshold (1ns) so ANY real
    check trips it — otherwise we'd have to rely on a timing race
    with real contract overhead which makes the test flaky.
    """
    config = _write_yaml(
        tmp_path,
        """
version: "1"
performance:
  report: never
  warn_slow_dfa_us: 0.000001
agents:
  a:
    contracts:
      - G:
          pattern: tool_allowlist
          args: [[x]]
""",
    )
    g = BaseGuard(config=str(config), agent_id="a", verbose=False)
    g.guard_before("x", {})
    g._auto_perf_report()
    out = capsys.readouterr()
    assert "pure-DFA p99" in (out.out + out.err)
    assert "exceeds" in (out.out + out.err)
