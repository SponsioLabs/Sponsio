"""Per-check performance tracking for Sponsio's runtime.

This module turns the "Sponsio is fast" claim into *numbers users
can quote*.  It records the wall-clock cost of every individual
contract check, buckets them by whether an LLM call was made, and
summarises into percentiles + throughput (QPS).

Why it matters:
    Competitor products quantify their speed via caching config
    (``cache_ttl``, ``cache_max_uses`` — "our LLM judge hits cache
    80% of the time, 200μs on hit").  We have a structurally
    different story: most checks never touch an LLM at all because
    the underlying contract is a DFA.  Without per-check timing
    surfaced somewhere, that structural advantage is invisible.

Design choices worth calling out:

1. **Bounded ring buffers**.  We keep the last N samples per
   contract (``collections.deque(maxlen=N)``), not every sample
   ever taken.  A long-running agent doing 10M checks shouldn't
   grow memory without bound, and percentiles on recent traffic
   are what anyone cares about anyway.  Default 10k per contract
   gives us O(0.1ms) to compute a p99 — cheap enough to call on
   every status page hit.

2. **Three buckets, not two**.  ``pure_det`` / ``sto_cached`` /
   ``sto_live``.  Users don't just want to know "was an LLM
   called?" — they want to see that even the sto-contract path
   is fast *most* of the time thanks to the per-atom memo.  The
   third bucket keeps that story visible.

3. **Thread-local LLM counter**.  ``_LLM_CALL_COUNTER`` is
   incremented at the single sto-judge call site and read/reset
   by the monitor around each contract.  This gives us a runtime
   signal of "was an LLM actually invoked for THIS check" that
   no amount of static analysis can provide — the memo cache
   means a sto contract on re-eval has the same runtime shape as
   a pure det one.

4. **``perf_counter_ns`` over ``time.time``**.  ``perf_counter``
   is monotonic, highest-resolution (~20–100ns), and immune to
   system clock adjustments.  ``time.time`` would let an NTP
   step show up as a 50ms "check" that never happened.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Thread-local LLM-call tracking
# ---------------------------------------------------------------------------

# Incremented by the sto-judge call site; read by the monitor around each
# contract eval so we can tell "did this check actually fire an LLM".
# threading.local() rather than contextvars because the sto pipeline
# uses classic thread-synchronous Python code and threads are the unit of
# concurrency agents actually use (asyncio agents serialise through the
# event loop, so per-thread isolation is still correct).
_tls = threading.local()


def _counter() -> int:
    return getattr(_tls, "llm_calls", 0)


def _reset_counter() -> None:
    _tls.llm_calls = 0


def _increment_counter() -> None:
    """Called from the sto-judge invocation site — increments the
    per-thread LLM call tally.  Cheap (a single attribute bump).
    """
    _tls.llm_calls = getattr(_tls, "llm_calls", 0) + 1


# ---------------------------------------------------------------------------
# Sample record + tracker
# ---------------------------------------------------------------------------


@dataclass
class _Sample:
    """One (contract, latency, bucket) observation.

    Kept as a dataclass rather than a tuple because:
      * field access by name keeps the percentile-computation code
        readable ("s.ns" vs "s[0]")
      * adding future dimensions (e.g. ``batch_size``, ``memo_hit``
        for finer-grained bucketing) is a one-line change

    ``bucket`` is one of:
      * ``"pure_det"``   — contract compiled to pure DFA/LTL, no
                           sto atoms, mathematically cannot touch an
                           LLM
      * ``"sto_cached"`` — contract has sto atoms but the LLM counter
                           was zero this check (answer came from the
                           atom memo cache)
      * ``"sto_live"``   — contract actually fired at least one
                           ``judge.judge()`` call this check
    """

    contract_label: str
    ns: int
    bucket: str


@dataclass
class BucketStats:
    """Summary for one of the three bucket types."""

    n: int = 0
    mean_ns: float = 0.0
    p50_ns: float = 0.0
    p95_ns: float = 0.0
    p99_ns: float = 0.0
    max_ns: int = 0
    qps: float = 0.0  # = 1s / mean; derived but cached so callers don't redo it

    def to_human(self) -> dict:
        """Render the bucket with both raw ns and human-readable μs/ms.

        Unit choice: anything under 1ms we show in μs (the natural unit
        for DFA timings); anything at or above we show in ms (the
        natural unit for LLM calls).  No mixing within one row.
        """
        out: dict = {"n": self.n}
        if self.n == 0:
            return out
        unit = "us" if self.p99_ns < 1_000_000 else "ms"
        div = 1_000.0 if unit == "us" else 1_000_000.0
        out.update(
            {
                f"mean_{unit}": round(self.mean_ns / div, 3),
                f"p50_{unit}": round(self.p50_ns / div, 3),
                f"p95_{unit}": round(self.p95_ns / div, 3),
                f"p99_{unit}": round(self.p99_ns / div, 3),
                f"max_{unit}": round(self.max_ns / div, 3),
                "qps": round(self.qps, 1),
            }
        )
        return out


@dataclass
class PerfSummary:
    """Top-level perf summary produced by ``PerformanceTracker.summarize``.

    Field order chosen for the pretty-printed case: counts first
    (tells you *if* anything ran), zero-LLM ratio next (the headline
    story), bucket breakdown last (the details for the skeptical).
    """

    total_checks: int = 0
    n_pure_det: int = 0
    n_sto_cached: int = 0
    n_sto_live: int = 0
    zero_llm_ratio: float = 0.0  # (pure_det + sto_cached) / total
    total_elapsed_ns: int = 0
    pure_det: BucketStats = field(default_factory=BucketStats)
    sto_cached: BucketStats = field(default_factory=BucketStats)
    sto_live: BucketStats = field(default_factory=BucketStats)
    per_contract: dict[str, BucketStats] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """JSON-serialisable form. Uses the human-readable bucket
        rendering so a one-shot ``sponsio eval --json`` dump is
        copy-pastable into a README without a second conversion
        pass."""
        return {
            "total_checks": self.total_checks,
            "n_pure_det": self.n_pure_det,
            "n_sto_cached": self.n_sto_cached,
            "n_sto_live": self.n_sto_live,
            "zero_llm_ratio": round(self.zero_llm_ratio, 4),
            "total_elapsed_ns": self.total_elapsed_ns,
            "pure_det": self.pure_det.to_human(),
            "sto_cached": self.sto_cached.to_human(),
            "sto_live": self.sto_live.to_human(),
            "per_contract": {k: v.to_human() for k, v in self.per_contract.items()},
        }


class PerformanceTracker:
    """Records per-check latency samples and computes bucketed summaries.

    One tracker per ``RuntimeMonitor``.  Thread-safe under its own
    lock — sto contracts that internally fan out to an async judge
    are still serialised at ``check_action``, but we take the lock
    defensively so users running multiple guards in parallel from
    separate threads get correct counts.
    """

    def __init__(self, *, per_contract_ring_size: int = 10_000):
        self._per_contract_ring_size = per_contract_ring_size
        # Per-contract ring buffers.  Separate rings per bucket would
        # be ~3× the memory; we keep one per contract and bucket-tag
        # individual samples instead.  Percentile computation is O(n
        # log n) but n is bounded.
        self._samples: dict[str, deque[_Sample]] = {}
        # Aggregate counters — maintained independently of the rings
        # so ``total_checks`` reflects the full session even after
        # ring rollover drops the oldest samples.
        self._lock = threading.Lock()
        self._n_pure_det = 0
        self._n_sto_cached = 0
        self._n_sto_live = 0
        self._total_elapsed_ns = 0

    def record(self, sample: _Sample) -> None:
        """Append a sample.  Called from ``_check_det`` / ``_check_sto``."""
        with self._lock:
            ring = self._samples.get(sample.contract_label)
            if ring is None:
                ring = deque(maxlen=self._per_contract_ring_size)
                self._samples[sample.contract_label] = ring
            ring.append(sample)

            if sample.bucket == "pure_det":
                self._n_pure_det += 1
            elif sample.bucket == "sto_cached":
                self._n_sto_cached += 1
            else:
                self._n_sto_live += 1
            self._total_elapsed_ns += sample.ns

    def reset(self) -> None:
        """Clear all samples + counters.  Called from ``RuntimeMonitor.reset``."""
        with self._lock:
            self._samples.clear()
            self._n_pure_det = 0
            self._n_sto_cached = 0
            self._n_sto_live = 0
            self._total_elapsed_ns = 0

    @property
    def total_checks(self) -> int:
        with self._lock:
            return self._n_pure_det + self._n_sto_cached + self._n_sto_live

    def summarize(self) -> PerfSummary:
        """Compute percentiles + QPS over the currently-retained samples.

        Runs under the lock — a concurrent ``record`` blocks until
        this returns, which is fine because summarisation is O(n) in
        ring size (≤10k by default) and typically called from
        printing code that isn't on the hot path.
        """
        with self._lock:
            # Snapshot everything under the lock, compute outside —
            # minimises lock hold time even though the computation is
            # fast.
            per_contract_samples = {k: list(v) for k, v in self._samples.items()}
            n_pure_det = self._n_pure_det
            n_sto_cached = self._n_sto_cached
            n_sto_live = self._n_sto_live
            total_elapsed = self._total_elapsed_ns

        by_bucket: dict[str, list[int]] = {
            "pure_det": [],
            "sto_cached": [],
            "sto_live": [],
        }
        per_contract_ns: dict[str, list[int]] = {}
        per_contract_bucket: dict[str, str] = {}

        for label, samples in per_contract_samples.items():
            ns_list = [s.ns for s in samples]
            per_contract_ns[label] = ns_list
            # Use the LAST sample's bucket as the contract's
            # characteristic bucket.  A contract either goes through
            # the det fast path or the sto path — it doesn't switch
            # halfway.  If future work introduces conditional
            # pipelines we'll revisit.
            per_contract_bucket[label] = samples[-1].bucket if samples else "pure_det"
            for s in samples:
                by_bucket[s.bucket].append(s.ns)

        total = n_pure_det + n_sto_cached + n_sto_live
        zero_llm_ratio = (n_pure_det + n_sto_cached) / total if total else 0.0

        summary = PerfSummary(
            total_checks=total,
            n_pure_det=n_pure_det,
            n_sto_cached=n_sto_cached,
            n_sto_live=n_sto_live,
            zero_llm_ratio=zero_llm_ratio,
            total_elapsed_ns=total_elapsed,
            pure_det=_compute_bucket(by_bucket["pure_det"]),
            sto_cached=_compute_bucket(by_bucket["sto_cached"]),
            sto_live=_compute_bucket(by_bucket["sto_live"]),
            per_contract={
                label: _compute_bucket(ns_list)
                for label, ns_list in per_contract_ns.items()
            },
        )
        return summary

    # -----------------------------------------------------------------
    # Convenience I/O — used by the YAML ``performance.export_path``
    # hook and by ``sponsio bench``.
    # -----------------------------------------------------------------

    def export_json(self, path: str | Path) -> Path:
        """Write the current summary to ``path`` as JSON. Returns the path."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.summarize().to_dict(), indent=2))
        return out


# ---------------------------------------------------------------------------
# Bucket computation — pure function, unit-testable in isolation
# ---------------------------------------------------------------------------


def _compute_bucket(ns_list: list[int]) -> BucketStats:
    """Percentiles + QPS for one bucket's samples.

    QPS is mean-based (``1e9 / mean_ns``) rather than median-based
    because mean reflects sustainable throughput — the metric
    consumers of "checks per second" actually care about when
    capacity planning.  Median would over-represent the fast path.

    Percentiles use the "nearest-rank" definition (NumPy's
    ``interpolation='nearest'``) rather than linear interpolation:
    we're working with integer nanoseconds and users are more likely
    to recognise an actual sample value in the output than an
    interpolated one.
    """
    if not ns_list:
        return BucketStats()

    sorted_ns = sorted(ns_list)
    n = len(sorted_ns)

    def _percentile(pct: float) -> int:
        # Nearest-rank: index = ceil(pct/100 * n) − 1, clamped to [0, n-1]
        idx = max(0, min(n - 1, int((pct / 100.0) * n + 0.999999) - 1))
        return sorted_ns[idx]

    total_ns = sum(sorted_ns)
    mean_ns = total_ns / n
    qps = 1e9 / mean_ns if mean_ns > 0 else 0.0

    return BucketStats(
        n=n,
        mean_ns=mean_ns,
        p50_ns=_percentile(50),
        p95_ns=_percentile(95),
        p99_ns=_percentile(99),
        max_ns=sorted_ns[-1],
        qps=qps,
    )


# ---------------------------------------------------------------------------
# Per-check timing context manager
# ---------------------------------------------------------------------------


class CheckTimer:
    """Context manager that times one contract check and records it.

    Usage (inside ``RuntimeMonitor._check_det``)::

        with CheckTimer(tracker, label, is_pure_det=contract.is_pure_det) as t:
            verdict = self._verifier.check_contract(contract)
        # exit: tracker has a sample with the bucket auto-selected
        #       from is_pure_det + whether the TLS LLM counter moved

    Why a class and not a decorator:
      * Needs per-instance state (start time, bucket classification)
      * Plays cleanly with ``try/finally`` around the body
      * The timed body has early-``continue`` paths in the monitor
        that a decorator couldn't capture without reshaping the
        function
    """

    __slots__ = ("_tracker", "_label", "_is_pure_det", "_start_ns", "_counter_at_start")

    def __init__(
        self,
        tracker: PerformanceTracker | None,
        label: str,
        *,
        is_pure_det: bool,
    ):
        self._tracker = tracker
        self._label = label
        self._is_pure_det = is_pure_det
        self._start_ns = 0
        self._counter_at_start = 0

    def __enter__(self) -> "CheckTimer":
        # Capture the LLM-call counter at entry so we can diff on
        # exit. We DON'T reset it — another contract in the same
        # check_action might want to see the cumulative count.
        self._counter_at_start = _counter()
        self._start_ns = time.perf_counter_ns()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        elapsed = time.perf_counter_ns() - self._start_ns
        if self._tracker is None:
            return

        if self._is_pure_det:
            bucket = "pure_det"
        else:
            llm_calls_this_check = _counter() - self._counter_at_start
            bucket = "sto_live" if llm_calls_this_check > 0 else "sto_cached"

        self._tracker.record(
            _Sample(
                contract_label=self._label,
                ns=elapsed,
                bucket=bucket,
            )
        )


# ---------------------------------------------------------------------------
# Pretty printer — used by ``guard.print_performance()`` and
# ``sponsio bench`` for the human-readable table.
# ---------------------------------------------------------------------------


def format_summary(summary: PerfSummary, *, color: bool = True) -> str:
    """Render a PerfSummary as a multi-line aligned table.

    Intentionally avoids third-party table libraries — the format is
    stable output we want to be able to diff in test fixtures.  ANSI
    colour is optional and off-by-default in non-TTY contexts (the
    guard decides whether to enable it).
    """
    if summary.total_checks == 0:
        return "(no contract checks recorded)"

    def _c(s: str, code: str) -> str:
        if not color:
            return s
        return f"\x1b[{code}m{s}\x1b[0m"

    lines: list[str] = []
    pct = summary.zero_llm_ratio * 100
    lines.append(
        f"{_c('Sponsio performance', '1;36')}: "
        f"{summary.total_checks:,} checks, "
        f"{_c(f'{pct:.1f}%', '1;32')} with zero LLM calls "
        f"({summary.n_pure_det:,} pure-DFA + {summary.n_sto_cached:,} sto-cached, "
        f"{summary.n_sto_live:,} sto-live)"
    )

    buckets = [
        ("pure DFA", summary.pure_det, "32"),
        ("sto (memo)", summary.sto_cached, "34"),
        ("sto (live)", summary.sto_live, "33"),
    ]
    # Hide the two sto rows entirely when nobody's using sto contracts.
    # The det-only deployment is the common case (every demo we ship,
    # most user agents on day one) and two ``— — — — —`` lines are
    # pure noise there.  The memo-vs-live distinction is preserved for
    # users who DO have sto rules — both rows reappear as soon as
    # either bucket has a single sample.
    if summary.sto_cached.n == 0 and summary.sto_live.n == 0:
        buckets = buckets[:1]
    # Header
    lines.append(
        f"  {'bucket':<12}  {'n':>8}  {'p50':>10}  {'p99':>10}  {'max':>10}  {'QPS':>12}"
    )
    for name, stats, col in buckets:
        if stats.n == 0:
            lines.append(
                f"  {name:<12}  {'—':>8}  {'—':>10}  {'—':>10}  {'—':>10}  {'—':>12}"
            )
            continue
        lines.append(
            f"  {_c(name, col):<{12 + (len(col) + 9 if color else 0)}}  "
            f"{stats.n:>8,}  "
            f"{_fmt_ns(stats.p50_ns):>10}  "
            f"{_fmt_ns(stats.p99_ns):>10}  "
            f"{_fmt_ns(stats.max_ns):>10}  "
            f"{_fmt_qps(stats.qps):>12}"
        )
    return "\n".join(lines)


def _fmt_ns(ns: float) -> str:
    """Render a ns duration in the best-fit unit (ns/μs/ms/s).

    Cutoffs chosen for readability: anything sub-μs stays in ns so
    users can see "580ns" and go "wow, that's a DFA"; μs-range goes
    to μs; the ms/s bands are for LLM-touching samples.
    """
    if ns < 1_000:
        return f"{int(ns)}ns"
    if ns < 1_000_000:
        return f"{ns / 1_000:.1f}μs"
    if ns < 1_000_000_000:
        return f"{ns / 1_000_000:.1f}ms"
    return f"{ns / 1_000_000_000:.2f}s"


def _fmt_qps(qps: float) -> str:
    """1,428,571 → "1.43M/s".  One decimal for k/M, two for G."""
    if qps >= 1e9:
        return f"{qps / 1e9:.2f}G/s"
    if qps >= 1e6:
        return f"{qps / 1e6:.2f}M/s"
    if qps >= 1e3:
        return f"{qps / 1e3:.1f}k/s"
    return f"{qps:.0f}/s"
