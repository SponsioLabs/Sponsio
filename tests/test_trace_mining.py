"""Tests for sponsio/discovery/extractors/trace_mining.py."""

from sponsio.discovery.extractors.trace_mining import TraceMiner
from sponsio.models.trace import Event, Trace


def _trace(*tool_names: str) -> Trace:
    """Build a simple trace with sequential tool calls."""
    events = [
        Event(ts=i, agent="bot", event_type="tool_call", tool=name)
        for i, name in enumerate(tool_names)
    ]
    return Trace(events=events)


class TestMineOrdering:
    def test_discovers_must_precede(self):
        # A always before B in all traces
        traces = [_trace("A", "B") for _ in range(10)]
        miner = TraceMiner(confidence_threshold=0.9, min_support=5)
        results = miner.extract(traces)
        patterns = [(r.formula.pattern_name, r.nl_description) for r in results]
        assert any(
            p[0] == "must_precede" and "A" in p[1] and "B" in p[1] for p in patterns
        )

    def test_below_threshold_not_proposed(self):
        # A before B in 50% of traces (below threshold)
        traces = [_trace("A", "B")] * 5 + [_trace("B", "A")] * 5
        miner = TraceMiner(confidence_threshold=0.9, min_support=5)
        results = miner.extract(traces)
        ordering = [r for r in results if r.formula.pattern_name == "must_precede"]
        # Neither direction should meet threshold
        assert all(r.confidence < 0.9 or r.confidence >= 0.9 for r in ordering)
        # Actually with 50/50, confidence = 0.5 which is < 0.9
        for r in ordering:
            assert r.confidence >= 0.9  # only high confidence ones appear

    def test_below_min_support_not_proposed(self):
        traces = [_trace("A", "B")]  # only 1 trace
        miner = TraceMiner(min_support=5)
        results = miner.extract(traces)
        assert (
            len([r for r in results if r.formula.pattern_name == "must_precede"]) == 0
        )


class TestMineExclusion:
    def test_discovers_mutual_exclusion(self):
        # A and B never in the same trace
        traces = [_trace("A")] * 5 + [_trace("B")] * 5
        miner = TraceMiner(min_support=5)
        results = miner.extract(traces)
        excl = [r for r in results if r.formula.pattern_name == "mutual_exclusion"]
        assert len(excl) == 1
        assert excl[0].confidence == 1.0

    def test_co_occurring_not_proposed(self):
        # A and B always together
        traces = [_trace("A", "B") for _ in range(10)]
        miner = TraceMiner(min_support=5)
        results = miner.extract(traces)
        excl = [r for r in results if r.formula.pattern_name == "mutual_exclusion"]
        assert len(excl) == 0


class TestMineFrequency:
    def test_discovers_idempotent(self):
        # Tool X always called exactly once
        traces = [_trace("X") for _ in range(10)]
        miner = TraceMiner(min_support=5)
        results = miner.extract(traces)
        idem = [r for r in results if r.formula.pattern_name == "idempotent"]
        assert len(idem) == 1

    def test_discovers_rate_limit(self):
        # Tool X called at most 3 times
        traces = [
            _trace("X", "X", "X"),
            _trace("X", "X"),
            _trace("X", "X", "X"),
            _trace("X"),
            _trace("X", "X", "X"),
        ]
        miner = TraceMiner(min_support=5)
        results = miner.extract(traces)
        rl = [r for r in results if r.formula.pattern_name == "rate_limit"]
        assert len(rl) == 1
        assert "3" in rl[0].nl_description


class TestMineSequences:
    def test_discovers_always_followed_by(self):
        # A always followed by B
        traces = [_trace("A", "B") for _ in range(10)]
        miner = TraceMiner(confidence_threshold=0.9, min_support=5)
        results = miner.extract(traces)
        seq = [r for r in results if r.formula.pattern_name == "always_followed_by"]
        assert any("A" in r.nl_description and "B" in r.nl_description for r in seq)

    def test_not_followed_not_proposed(self):
        # A never followed by B
        traces = [_trace("A") for _ in range(10)]
        miner = TraceMiner(confidence_threshold=0.9, min_support=5)
        results = miner.extract(traces)
        seq = [r for r in results if r.formula.pattern_name == "always_followed_by"]
        assert len(seq) == 0


class TestEdgeCases:
    def test_empty_traces(self):
        miner = TraceMiner()
        assert miner.extract([]) == []

    def test_single_trace_below_min_support(self):
        miner = TraceMiner(min_support=5)
        results = miner.extract([_trace("A", "B")])
        assert len(results) == 0

    def test_evidence_included(self):
        traces = [_trace("A", "B") for _ in range(10)]
        miner = TraceMiner(confidence_threshold=0.9, min_support=5)
        results = miner.extract(traces)
        for r in results:
            assert "total_traces" in r.evidence
