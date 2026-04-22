"""Unit tests for :mod:`sponsio.runtime.perf`.

These are all pure-Python unit tests — no guard, no monitor, no
YAML.  We're verifying the mechanical correctness of:

  * percentile math (nearest-rank)
  * bucket classification (``pure_det`` / ``sto_cached`` / ``sto_live``)
  * ring buffer boundedness
  * thread-local LLM counter behaviour
  * JSON round-trip shape

Integration-level tests that wire the tracker through a real guard
live in ``test_perf_runtime.py``.
"""

from __future__ import annotations

import threading

import pytest

from sponsio.runtime.perf import (
    CheckTimer,
    PerformanceTracker,
    PerfSummary,
    _Sample,
    _compute_bucket,
    _counter,
    _fmt_ns,
    _fmt_qps,
    _increment_counter,
    _reset_counter,
    format_summary,
)


# ---------------------------------------------------------------------------
# Percentile math
# ---------------------------------------------------------------------------


class TestComputeBucket:
    def test_empty_returns_zeroed_stats(self):
        """No samples ⇒ all fields zero; callers should treat this as
        "bucket inactive" rather than "bucket fast"."""
        stats = _compute_bucket([])
        assert stats.n == 0
        assert stats.p50_ns == 0
        assert stats.qps == 0.0

    def test_single_sample_all_percentiles_equal(self):
        """One sample ⇒ every percentile (p50/p95/p99) collapses to
        that one value.  Protects against off-by-one in the
        nearest-rank index formula."""
        stats = _compute_bucket([1000])
        assert stats.n == 1
        assert stats.p50_ns == 1000
        assert stats.p95_ns == 1000
        assert stats.p99_ns == 1000
        assert stats.max_ns == 1000
        assert stats.mean_ns == 1000
        # QPS = 1s / 1000ns = 1,000,000/s
        assert stats.qps == pytest.approx(1_000_000.0)

    def test_percentiles_nearest_rank(self):
        """100 samples 1..100 → p50 should land on sample 50, p99 on
        sample 99.  Using nearest-rank (NOT linear interpolation),
        so the values are exact ints from the input."""
        samples = list(range(1, 101))
        stats = _compute_bucket(samples)
        assert stats.n == 100
        assert stats.p50_ns == 50
        assert stats.p95_ns == 95
        assert stats.p99_ns == 99
        assert stats.max_ns == 100

    def test_qps_from_mean(self):
        """QPS = 1e9 / mean — consumers plug this straight into
        capacity-planning math so the formula must be exact, not
        based on median.
        """
        stats = _compute_bucket([1_000, 2_000, 3_000])
        assert stats.mean_ns == 2000
        assert stats.qps == pytest.approx(500_000.0)

    def test_human_rendering_units(self):
        """to_human() picks μs when p99 < 1ms, ms otherwise — a
        mixed "μs + ms" row would be awful to read so the unit choice
        is all-or-nothing per bucket."""
        fast = _compute_bucket([500, 1_000, 2_000]).to_human()
        assert "p99_us" in fast
        assert "p99_ms" not in fast

        slow = _compute_bucket([5_000_000, 10_000_000]).to_human()
        assert "p99_ms" in slow
        assert "p99_us" not in slow


# ---------------------------------------------------------------------------
# PerformanceTracker
# ---------------------------------------------------------------------------


class TestPerformanceTracker:
    def test_record_updates_counters(self):
        t = PerformanceTracker()
        t.record(_Sample("c1", 100, "pure_det"))
        t.record(_Sample("c1", 200, "sto_cached"))
        t.record(_Sample("c1", 300, "sto_live"))
        s = t.summarize()
        assert s.n_pure_det == 1
        assert s.n_sto_cached == 1
        assert s.n_sto_live == 1
        assert s.total_checks == 3

    def test_zero_llm_ratio(self):
        """``zero_llm_ratio`` = (pure_det + sto_cached) / total.
        The headline metric — must be exact, not off-by-one."""
        t = PerformanceTracker()
        for _ in range(7):
            t.record(_Sample("c", 100, "pure_det"))
        for _ in range(2):
            t.record(_Sample("c", 200, "sto_cached"))
        for _ in range(1):
            t.record(_Sample("c", 500_000_000, "sto_live"))
        s = t.summarize()
        assert s.total_checks == 10
        assert s.zero_llm_ratio == pytest.approx(0.9)

    def test_ring_buffer_bounded(self):
        """A 5-slot ring must never hold more than 5 samples per
        contract, even after 1000 record()s.  Aggregate counter
        still sees all 1000 — that's a feature (total count
        survives rollover)."""
        t = PerformanceTracker(per_contract_ring_size=5)
        for i in range(1000):
            t.record(_Sample("c", i + 1, "pure_det"))
        s = t.summarize()
        # Total counter wasn't capped.
        assert s.n_pure_det == 1000
        # But percentile computation only sees the last 5 samples
        # (ring rolled to [996..1000]).
        assert s.pure_det.n == 5
        assert s.pure_det.max_ns == 1000

    def test_reset_wipes_everything(self):
        """After reset, summarize() is indistinguishable from a
        fresh tracker — important because ``sponsio bench`` uses
        reset() to drop warmup samples."""
        t = PerformanceTracker()
        for i in range(50):
            t.record(_Sample("c", i, "pure_det"))
        t.reset()
        s = t.summarize()
        assert s.total_checks == 0
        assert s.per_contract == {}

    def test_thread_safe_concurrent_record(self):
        """8 threads racing record() must not corrupt counters.
        Tracker protects all state with a lock; this test only fails
        if that lock is ever removed or bypassed."""
        t = PerformanceTracker(per_contract_ring_size=100_000)
        N = 1000

        def worker():
            for i in range(N):
                t.record(_Sample("c", i + 1, "pure_det"))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        s = t.summarize()
        assert s.n_pure_det == 8 * N

    def test_summarize_dict_shape_stable(self):
        """to_dict() is stable public API — tests pin the key set
        so future additions don't accidentally break ``perf.json``
        consumers (CI dashboards, doctor --json, etc)."""
        t = PerformanceTracker()
        t.record(_Sample("c", 100, "pure_det"))
        d = t.summarize().to_dict()
        expected_keys = {
            "total_checks",
            "n_pure_det",
            "n_sto_cached",
            "n_sto_live",
            "zero_llm_ratio",
            "total_elapsed_ns",
            "pure_det",
            "sto_cached",
            "sto_live",
            "per_contract",
        }
        assert set(d.keys()) == expected_keys

    def test_export_json_creates_parent_dirs(self, tmp_path):
        """export_json must mkdir(parents=True) — consumers
        routinely want to write to ``.sponsio/perf/YYYY-MM-DD.json``
        which doesn't exist yet."""
        t = PerformanceTracker()
        t.record(_Sample("c", 100, "pure_det"))
        out = t.export_json(tmp_path / "deep" / "nested" / "perf.json")
        assert out.exists()
        import json as _json

        data = _json.loads(out.read_text())
        assert data["total_checks"] == 1


# ---------------------------------------------------------------------------
# Thread-local LLM counter
# ---------------------------------------------------------------------------


class TestLlmCounter:
    def setup_method(self):
        _reset_counter()

    def test_increment_accumulates(self):
        assert _counter() == 0
        _increment_counter()
        _increment_counter()
        assert _counter() == 2

    def test_reset(self):
        _increment_counter()
        _reset_counter()
        assert _counter() == 0

    def test_thread_isolation(self):
        """One thread's increments must NOT leak into another — the
        whole bucket-classification scheme depends on this."""
        _increment_counter()
        _increment_counter()
        assert _counter() == 2

        other_count = [None]

        def worker():
            # Fresh thread starts at 0.
            other_count[0] = _counter()

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert other_count[0] == 0
        # Our count is unaffected.
        assert _counter() == 2


# ---------------------------------------------------------------------------
# CheckTimer
# ---------------------------------------------------------------------------


class TestCheckTimer:
    def setup_method(self):
        _reset_counter()

    def test_pure_det_bucket_never_sto(self):
        """``is_pure_det=True`` ⇒ bucket is always ``pure_det``,
        even if someone maliciously bumps the LLM counter inside
        the block (they shouldn't, but the contract is: det fast
        path is structurally incapable of LLM calls)."""
        t = PerformanceTracker()
        with CheckTimer(t, "c", is_pure_det=True):
            _increment_counter()  # should be ignored for bucketing
        s = t.summarize()
        assert s.n_pure_det == 1
        assert s.n_sto_live == 0

    def test_sto_cached_when_no_llm_call(self):
        """sto contract whose block doesn't increment the LLM
        counter lands in ``sto_cached`` — the interpretation is
        "the atom memo answered without calling the judge"."""
        t = PerformanceTracker()
        with CheckTimer(t, "c", is_pure_det=False):
            pass
        s = t.summarize()
        assert s.n_sto_cached == 1
        assert s.n_sto_live == 0

    def test_sto_live_when_counter_moves(self):
        """sto contract whose block increments the counter
        (simulating an actual ``judge.judge()`` call) lands in
        ``sto_live``."""
        t = PerformanceTracker()
        with CheckTimer(t, "c", is_pure_det=False):
            _increment_counter()
        s = t.summarize()
        assert s.n_sto_live == 1
        assert s.n_sto_cached == 0

    def test_timer_records_nonzero_ns(self):
        """The sample must have a positive ns value — a zero means
        perf_counter_ns isn't being called on exit."""
        t = PerformanceTracker()
        with CheckTimer(t, "c", is_pure_det=True):
            # Spin briefly to guarantee elapsed > 0.
            for _ in range(100):
                pass
        s = t.summarize()
        assert s.pure_det.max_ns > 0

    def test_tracker_none_is_no_op(self):
        """Timer with a ``None`` tracker is a safe no-op — lets
        callers do ``CheckTimer(self._perf_tracker, ...)`` without
        a None check even if the tracker was never wired up."""
        with CheckTimer(None, "c", is_pure_det=True):
            pass
        # No assertion — we're verifying it doesn't raise.


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


class TestFormatSummary:
    def test_empty_summary(self):
        assert format_summary(PerfSummary(), color=False).startswith("(no")

    def test_non_empty_contains_qps_and_percentile(self):
        t = PerformanceTracker()
        for i in range(10):
            t.record(_Sample("c", (i + 1) * 1000, "pure_det"))
        text = format_summary(t.summarize(), color=False)
        assert "pure DFA" in text
        assert "p99" in text
        assert "QPS" in text


def test_fmt_ns_unit_ladder():
    """Units must step correctly at the ns→μs→ms→s boundaries."""
    assert _fmt_ns(500) == "500ns"
    assert _fmt_ns(1_500) == "1.5μs"
    assert _fmt_ns(2_500_000) == "2.5ms"
    assert _fmt_ns(2_500_000_000) == "2.50s"


def test_fmt_qps_unit_ladder():
    assert _fmt_qps(50) == "50/s"
    assert _fmt_qps(5_500) == "5.5k/s"
    assert _fmt_qps(1_500_000) == "1.50M/s"
    assert _fmt_qps(2_500_000_000) == "2.50G/s"
