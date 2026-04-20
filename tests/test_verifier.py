"""Standalone tests for the TraceVerifier — pure formal evaluation without monitor.

Exercises:
  - Verdict / ContractVerdict shapes
  - check() on single formulas
  - check_contract() assumption short-circuit + enforcement list
  - Incremental grounding correctness across multiple sync() calls
  - Incremental eval correctness (G-cache hit/miss/rollback)
  - Trace shrinkage auto-reset
  - Standalone use (no monitor) with Contract objects
"""

from __future__ import annotations

from sponsio.models.agent import Agent
from sponsio.models.contract import Contract
from sponsio.models.trace import Event, Trace
from sponsio.patterns.library import (
    arg_blacklist,
    must_precede,
    no_reversal,
    rate_limit,
)
from sponsio.runtime.verifier import ContractVerdict, TraceVerifier, Verdict


def _trace(*tool_calls: str) -> Trace:
    """Build a trace of tool-only events for the given tool name sequence."""
    return Trace(
        events=[
            Event(ts=i, agent="bot", event_type="tool_call", tool=t)
            for i, t in enumerate(tool_calls)
        ]
    )


class TestVerdict:
    def test_verdict_is_truthy(self):
        v = Verdict(holds=True, desc="ok")
        assert bool(v) is True

    def test_verdict_is_falsy(self):
        v = Verdict(holds=False, desc="nope")
        assert bool(v) is False

    def test_contract_verdict_bool_reflects_holds(self):
        cv = ContractVerdict(
            assumptions=[Verdict(holds=True, desc="a")],
            enforcements=[Verdict(holds=True, desc="e")],
        )
        assert bool(cv) is True

    def test_contract_verdict_false_on_enforcement_violation(self):
        cv = ContractVerdict(
            assumptions=[Verdict(holds=True, desc="a")],
            enforcements=[Verdict(holds=False, desc="e")],
        )
        assert bool(cv) is False
        assert len(cv.enforcement_violations) == 1

    def test_contract_verdict_false_on_assumption_failure(self):
        cv = ContractVerdict(
            assumptions=[Verdict(holds=False, desc="a")],
            enforcements=[],
        )
        assert bool(cv) is False
        assert cv.first_assumption_failure is not None
        assert cv.first_assumption_failure.desc == "a"


class TestCheck:
    def test_must_precede_satisfied(self):
        v = TraceVerifier()
        v.sync(_trace("verify", "transfer"))
        result = v.check(must_precede("verify", "transfer"))
        assert result.holds is True

    def test_must_precede_violated(self):
        v = TraceVerifier()
        v.sync(_trace("transfer"))
        result = v.check(must_precede("verify", "transfer"))
        assert result.holds is False

    def test_rate_limit_satisfied(self):
        v = TraceVerifier()
        v.sync(_trace("X", "X"))
        assert v.check(rate_limit("X", 3)).holds is True

    def test_rate_limit_violated(self):
        v = TraceVerifier()
        v.sync(_trace("X", "X", "X", "X"))
        assert v.check(rate_limit("X", 3)).holds is False

    def test_verdict_carries_formula_and_desc(self):
        v = TraceVerifier()
        v.sync(_trace("A", "B"))
        f = must_precede("A", "B")
        result = v.check(f)
        assert result.desc == "A must precede B"
        assert result.formula is f


class TestCheckContract:
    def test_unconditional_contract_passes(self):
        contract = Contract(
            agent=Agent(id="bot"),
            enforcement=rate_limit("X", 3),
        )
        v = TraceVerifier()
        v.sync(_trace("X", "X"))
        cv = v.check_contract(contract)
        assert cv.holds is True
        assert len(cv.assumptions) == 0
        assert len(cv.enforcements) == 1
        assert cv.enforcements[0].holds is True

    def test_assumption_failure_skips_enforcements(self):
        contract = Contract(
            agent=Agent(id="bot"),
            assumption=must_precede("auth", "act"),
            enforcement=rate_limit("act", 3),
        )
        v = TraceVerifier()
        v.sync(_trace("act"))  # auth never called → assumption fails
        cv = v.check_contract(contract)
        assert cv.assumption_holds is False
        assert cv.first_assumption_failure is not None
        assert len(cv.enforcements) == 0  # skipped

    def test_assumption_and_enforcement_both_evaluated_when_a_holds(self):
        contract = Contract(
            agent=Agent(id="bot"),
            assumption=must_precede("auth", "act"),
            enforcement=rate_limit("act", 1),  # will be violated
        )
        v = TraceVerifier()
        v.sync(_trace("auth", "act", "act"))
        cv = v.check_contract(contract)
        assert cv.assumption_holds is True
        assert cv.holds is False
        assert cv.enforcements[0].holds is False

    def test_list_valued_enforcement_all_checked(self):
        contract = Contract(
            agent=Agent(id="bot"),
            enforcement=[rate_limit("X", 3), rate_limit("Y", 3)],
        )
        v = TraceVerifier()
        v.sync(_trace("X", "X", "Y", "Y"))
        cv = v.check_contract(contract)
        assert len(cv.enforcements) == 2
        assert cv.holds is True


class TestIncrementalGrounding:
    def test_sync_appends_only_new_events(self):
        v = TraceVerifier()
        v.sync(_trace("a", "b"))
        assert len(v.valuations) == 2
        assert v._grounded_upto == 2

        # Extend the trace; sync should only ground the new event.
        v.sync(_trace("a", "b", "c"))
        assert len(v.valuations) == 3
        assert v._grounded_upto == 3

    def test_sync_auto_resets_on_trace_shrink(self):
        v = TraceVerifier()
        v.sync(_trace("a", "b", "c"))
        assert len(v.valuations) == 3

        # Shrink to 1 event — should trigger a full reset + re-ground.
        v.sync(_trace("d"))
        assert len(v.valuations) == 1
        assert v.valuations[0].get("called(d)") is True
        # The stale "called(a/b/c)" entries should NOT leak.
        for key in v.valuations[0]:
            assert "called(a)" not in key
            assert "called(b)" not in key
            assert "called(c)" not in key

    def test_sync_auto_resets_when_content_atoms_change(self):
        v = TraceVerifier()
        v.sync(_trace("X"), content_atoms=None)
        first_valuations = list(v.valuations)

        # Different content_atoms → full re-ground.
        v.sync(_trace("X"), content_atoms={"arg_has": {("X", "pattern")}})
        assert len(v.valuations) == 1
        assert v.valuations is not first_valuations

    def test_incremental_count_matches_batch(self):
        """Incremental syncs must produce the same count values as a single batch sync."""
        full_trace = _trace("X", "X", "X", "Y", "X")

        # Batch: sync once at the end
        v_batch = TraceVerifier()
        v_batch.sync(full_trace)

        # Incremental: sync after each append
        v_inc = TraceVerifier()
        for n in range(1, len(full_trace.events) + 1):
            partial = Trace(events=full_trace.events[:n])
            v_inc.sync(partial)

        assert len(v_batch.valuations) == len(v_inc.valuations)
        for a, b in zip(v_batch.valuations, v_inc.valuations):
            assert a == b


class TestIncrementalEval:
    def test_g_cache_hits_on_stable_true(self):
        """Re-evaluating the same G-rooted formula should use the cache."""
        v = TraceVerifier()
        f = rate_limit("X", 1_000_000)
        v.sync(_trace("X"))
        assert v.check(f).holds is True

        v.sync(_trace("X", "X"))
        # Cache was {(scanned=1, True)}; we should only scan position 1.
        assert v.check(f).holds is True
        # Sanity: scanned_upto is at the latest length
        raw = f.formula
        assert v._g_cache[raw] == (2, True)

    def test_g_cache_transitions_to_false_and_sticks(self):
        v = TraceVerifier()
        f = rate_limit("X", 2)
        v.sync(_trace("X"))
        assert v.check(f).holds is True

        v.sync(_trace("X", "X"))
        assert v.check(f).holds is True

        v.sync(_trace("X", "X", "X"))
        assert v.check(f).holds is False

        # Subsequent calls should still report False from the cache.
        assert v.check(f).holds is False

    def test_nested_temporal_falls_through_to_full_eval(self):
        """no_reversal contains nested G and must not be cached prematurely."""
        v = TraceVerifier()
        f = no_reversal("approve", "deny")

        v.sync(_trace("approve"))
        assert v.check(f).holds is True

        v.sync(_trace("approve", "deny"))
        # This is the bug we fixed: caching the outer G at len=1 as
        # True and then only checking position 1 would miss that the
        # inner G's scope extended.
        assert v.check(f).holds is False

    def test_reset_clears_g_cache(self):
        v = TraceVerifier()
        f = rate_limit("X", 1)
        v.sync(_trace("X", "X"))
        assert v.check(f).holds is False  # violated

        v.reset()
        v.sync(_trace("X"))
        assert v.check(f).holds is True  # clean slate


class TestCheckAssumption:
    def test_check_assumption_unconditional(self):
        contract = Contract(
            agent=Agent(id="bot"),
            enforcement=rate_limit("X", 3),
        )
        v = TraceVerifier()
        v.sync(_trace("X"))
        result = v.check_assumption(contract)
        assert result.holds is True
        assert result.desc == "true"

    def test_check_assumption_fails_short_circuit(self):
        contract = Contract(
            agent=Agent(id="bot"),
            assumption=[
                must_precede("a", "b"),
                must_precede("c", "d"),  # should not reach here
            ],
            enforcement=rate_limit("X", 3),
        )
        v = TraceVerifier()
        v.sync(_trace("b"))  # first assumption fails
        result = v.check_assumption(contract)
        assert result.holds is False
        assert "a must precede b" in result.desc.lower()


class TestAgainstMonitor:
    """Integration check: TraceVerifier used standalone on the same trace
    as the monitor should return the same verdict."""

    def test_verifier_matches_monitor_on_rate_limit(self):
        import sponsio

        guard = sponsio.init(
            agent_id="bot",
            contracts=["tool `X` at most 2 times"],
            verbose=False,
        )
        guard.guard_before("X")
        guard.guard_before("X")  # at limit
        # Using verifier directly
        v = guard.monitor.verifier
        result = v.check(rate_limit("X", 2))
        assert result.holds is True

        # One more — monitor would block; but verifier sees the trace
        # state and still reports "at limit". The blocked event is
        # popped by guard, so verifier should still see the old state.
        blocked = guard.guard_before("X")
        assert blocked.blocked is True
        # TraceVerifier re-evaluated correctly after rollback reset.
        assert v.check(rate_limit("X", 2)).holds is True

    def test_verifier_accessible_from_monitor_property(self):
        import sponsio

        guard = sponsio.init(
            agent_id="bot",
            contracts=["tool `X` at most 2 times"],
            verbose=False,
        )
        assert isinstance(guard.monitor.verifier, TraceVerifier)


class TestCheckNL:
    def test_check_nl_happy_path_holds(self):
        v = TraceVerifier()
        v.sync(_trace("verify", "transfer"))
        result = v.check_nl("tool `verify` must precede `transfer`")
        assert result.holds is True

    def test_check_nl_happy_path_violated(self):
        v = TraceVerifier()
        v.sync(_trace("transfer"))
        result = v.check_nl("tool `verify` must precede `transfer`")
        assert result.holds is False

    def test_check_nl_rate_limit(self):
        v = TraceVerifier()
        v.sync(_trace("X", "X", "X", "X"))
        assert v.check_nl("tool `X` at most 3 times").holds is False
        assert v.check_nl("tool `X` at most 10 times").holds is True

    def test_check_nl_rejects_sto_rule(self):
        import pytest

        v = TraceVerifier()
        v.sync(_trace("X"))
        with pytest.raises(ValueError, match="sto"):
            v.check_nl("response must not contain PII")

    def test_check_nl_rejects_garbage(self):
        import pytest

        v = TraceVerifier()
        v.sync(_trace("X"))
        with pytest.raises(ValueError):
            v.check_nl("asdkjfhasdjkfh nonsense")


class TestBaseGuardCheckNL:
    """BaseGuard.check_nl is a thin wrapper around TraceVerifier.check_nl
    with optional span emission for OTEL / dashboard visibility."""

    def test_invisible_by_default(self):
        """Default behavior: no spans, no side effects."""
        import sponsio

        guard = sponsio.init(
            agent_id="bot",
            contracts=["tool `X` at most 3 times"],
            verbose=False,
        )
        guard.guard_before("X")
        before_spans = len(guard.check_spans)

        verdict = guard.check_nl("tool `X` at most 10 times")
        assert verdict.holds is True
        # No new span added.
        assert len(guard.check_spans) == before_spans

    def test_emit_spans_creates_agent_turn(self):
        """emit_spans=True adds a synthetic <check_nl> turn to check_spans."""
        import sponsio

        guard = sponsio.init(
            agent_id="bot",
            contracts=["tool `X` at most 3 times"],
            verbose=False,
        )
        guard.guard_before("X")
        before_spans = len(guard.check_spans)

        verdict = guard.check_nl("tool `X` at most 10 times", emit_spans=True)
        assert verdict.holds is True
        assert len(guard.check_spans) == before_spans + 1
        span = guard.last_check_span
        assert span.action == "<check_nl>"
        assert span.status == "ok"
        assert span.det_violations == 0

    def test_emit_spans_on_violation(self):
        """Failed ad-hoc check produces a violated span with kind='adhoc'."""
        import sponsio
        from sponsio.models.spans import GuaranteeSpan, ViolationSpan

        guard = sponsio.init(
            agent_id="bot",
            contracts=["tool `X` at most 10 times"],
            verbose=False,
        )
        guard.guard_before("X")
        guard.guard_before("X")

        verdict = guard.check_nl("tool `X` at most 1 times", emit_spans=True)
        assert verdict.holds is False

        root = guard.last_check_span
        assert root.action == "<check_nl>"
        assert root.status == "violated"
        assert root.det_violations == 1

        # The tree should contain a violated GuaranteeSpan + a ViolationSpan(kind="adhoc").
        contract_check = root.children[0]
        guars = [c for c in contract_check.children if isinstance(c, GuaranteeSpan)]
        assert len(guars) == 1
        assert guars[0].result is False

        viols = [c for c in contract_check.children if isinstance(c, ViolationSpan)]
        assert len(viols) == 1
        assert viols[0].kind == "adhoc"
        assert viols[0].severity == "LOW"

    def test_emit_spans_does_not_block(self):
        """check_nl must never apply a strategy — blocked=False always."""
        import sponsio

        guard = sponsio.init(
            agent_id="bot",
            contracts=["tool `X` at most 10 times"],
            verbose=False,
        )
        guard.guard_before("X")

        verdict = guard.check_nl("tool `X` at most 0 times", emit_spans=True)
        assert verdict.holds is False
        assert guard.last_check_span.blocked is False

    def test_emit_spans_routes_through_otel(self):
        """When emit_spans=True and an otel_exporter is attached, the span
        tree should be exported to the OTEL backend."""
        import sponsio

        exported: list = []

        class FakeExporter:
            def export(self, span):
                exported.append(span)

        guard = sponsio.init(
            agent_id="bot",
            contracts=["tool `X` at most 3 times"],
            otel_exporter=FakeExporter(),
            verbose=False,
        )
        guard.guard_before("X")
        before = len(exported)

        guard.check_nl("tool `X` at most 10 times", emit_spans=True)

        assert len(exported) > before
        last = exported[-1]
        assert last.action == "<check_nl>"

    def test_emit_spans_false_does_not_export_to_otel(self):
        import sponsio

        exported: list = []

        class FakeExporter:
            def export(self, span):
                exported.append(span)

        guard = sponsio.init(
            agent_id="bot",
            contracts=["tool `X` at most 3 times"],
            otel_exporter=FakeExporter(),
            verbose=False,
        )
        guard.guard_before("X")
        before = len(exported)

        # emit_spans defaults to False.
        guard.check_nl("tool `X` at most 10 times")

        # No new exports after the check_nl call.
        assert len(exported) == before


class TestBackCompatAlias:
    def test_verifier_alias_still_works(self):
        """The original ``Verifier`` name is kept as a deprecated alias."""
        from sponsio.runtime.verifier import TraceVerifier, Verifier

        assert Verifier is TraceVerifier


class TestArgBlacklistCacheable:
    """arg_blacklist = G(Not(arg_field_has(...))) — flat G, should cache."""

    def test_arg_blacklist_benefits_from_cache(self):
        v = TraceVerifier()
        f = arg_blacklist("bash", "cmd", ["rm -rf"])

        trace = Trace(
            events=[
                Event(
                    ts=0,
                    agent="bot",
                    event_type="tool_call",
                    tool="bash",
                    args={"cmd": "ls /tmp"},
                ),
                Event(
                    ts=1,
                    agent="bot",
                    event_type="tool_call",
                    tool="bash",
                    args={"cmd": "cat foo.txt"},
                ),
            ]
        )
        v.sync(trace, content_atoms={"arg_field_has": {("bash", "cmd", "rm -rf")}})
        assert v.check(f).holds is True
        # G-cache should have registered this formula
        raw = f.formula
        assert raw in v._g_cache
