"""Tests for sponsio/discovery/validation.py."""

from sponsio.discovery._types import (
    ConstraintStatus,
    DiscoverySource,
    ProposedConstraint,
)
from sponsio.discovery.validation import ValidationPipeline
from sponsio.models.trace import Event, Trace
from sponsio.patterns.library import (
    must_precede,
    mutual_exclusion,
)


def _proposal(formula, **kwargs) -> ProposedConstraint:
    return ProposedConstraint(formula=formula, **kwargs)


class TestSyntacticValidation:
    def test_valid_formula_passes(self):
        pipeline = ValidationPipeline()
        p = _proposal(must_precede("A", "B"))
        result = pipeline.validate(p)
        assert result.ok

    def test_none_formula_fails(self):
        pipeline = ValidationPipeline()
        p = _proposal(None)
        result = pipeline.validate(p)
        assert not result.ok
        assert any("None" in e for e in result.validation_errors)


class TestTrivialityValidation:
    def test_real_pattern_passes(self):
        pipeline = ValidationPipeline()
        p = _proposal(must_precede("check_policy", "issue_refund"))
        result = pipeline.validate(p)
        assert result.ok

    def test_mutual_exclusion_passes(self):
        pipeline = ValidationPipeline()
        p = _proposal(mutual_exclusion("approve", "reject"))
        result = pipeline.validate(p)
        assert result.ok


class TestConsistencyValidation:
    def test_no_existing_passes(self):
        pipeline = ValidationPipeline(existing_formulas=[])
        p = _proposal(must_precede("A", "B"))
        result = pipeline.validate(p)
        assert result.ok

    def test_compatible_patterns_pass(self):
        existing = [must_precede("A", "B")]
        pipeline = ValidationPipeline(existing_formulas=existing)
        p = _proposal(must_precede("C", "D"))
        result = pipeline.validate(p)
        assert result.ok


class TestTraceReplayValidation:
    def test_no_traces_passes(self):
        pipeline = ValidationPipeline(historical_traces=[])
        p = _proposal(must_precede("A", "B"))
        result = pipeline.validate(p)
        assert result.ok

    def test_passing_traces(self):
        traces = [
            Trace(
                events=[
                    Event(ts=0, agent="bot", event_type="tool_call", tool="A"),
                    Event(ts=1, agent="bot", event_type="tool_call", tool="B"),
                ]
            )
            for _ in range(5)
        ]
        pipeline = ValidationPipeline(historical_traces=traces)
        p = _proposal(must_precede("A", "B"))
        result = pipeline.validate(p)
        assert result.ok
        assert "trace_replay" in result.evidence

    def test_failing_traces_adds_warning(self):
        # B without A — violates must_precede
        traces = [
            Trace(
                events=[
                    Event(ts=0, agent="bot", event_type="tool_call", tool="B"),
                ]
            )
            for _ in range(5)
        ]
        pipeline = ValidationPipeline(historical_traces=traces)
        p = _proposal(must_precede("A", "B"))
        result = pipeline.validate(p)
        # Trace replay doesn't reject, just warns
        assert result.ok
        assert result.evidence.get("trace_replay", {}).get("fail_count", 0) > 0


class TestHumanReview:
    def test_auto_extracted_stays_proposed(self):
        pipeline = ValidationPipeline()
        p = _proposal(
            must_precede("A", "B"),
            source=DiscoverySource.AUTO_EXTRACTED,
            status=ConstraintStatus.VERIFIED,  # should be overridden
        )
        result = pipeline.validate(p)
        assert result.status == ConstraintStatus.PROPOSED

    def test_user_defined_keeps_status(self):
        pipeline = ValidationPipeline()
        p = _proposal(
            must_precede("A", "B"),
            source=DiscoverySource.USER_DEFINED,
            status=ConstraintStatus.VERIFIED,
        )
        result = pipeline.validate(p)
        assert result.status == ConstraintStatus.VERIFIED


class TestBatchValidation:
    def test_batch(self):
        pipeline = ValidationPipeline()
        proposals = [
            _proposal(must_precede("A", "B")),
            _proposal(mutual_exclusion("X", "Y")),
        ]
        results = pipeline.validate_batch(proposals)
        assert len(results) == 2
        assert all(r.ok for r in results)
