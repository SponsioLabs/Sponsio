"""Tests for the DFA (formula-progression) LTL monitor.

Two layers:

1. **Direct unit tests** on :class:`DFAEvaluator` — verify progression
   semantics for each LTL operator independently (G, F, U, X, And, Or,
   Not, Implies, arithmetic). Tests assert 3-valued verdicts ``⊤/⊥/?``
   at each step, plus ``finalize()`` behavior for pending obligations.

2. **Differential tests** comparing the DFA backend to the stateless
   recursive evaluator via :class:`TraceVerifier(backend=...)`. For
   every Sponsio pattern on representative traces, both backends must
   return the same boolean verdict. The recursive backend is ground
   truth.

3. **Integration tests** verifying that ``TraceVerifier(backend="dfa")``
   handles rollback, reset, incremental sync, and session-end liveness
   (``include_liveness=True``) correctly.
"""

from __future__ import annotations

import pytest

from sponsio.formulas.dfa_evaluator import DFAEvaluator
from sponsio.formulas.formula import (
    And,
    Atom,
    Const,
    F,
    G,
    Le,
    Not,
    Or,
    U,
    Var,
    X,
)
from sponsio.models.agent import Agent
from sponsio.models.contract import Contract
from sponsio.models.trace import Event, Trace
from sponsio.patterns.library import (
    always_followed_by,
    arg_allowlist,
    arg_blacklist,
    bounded_retry,
    must_precede,
    no_reversal,
    rate_limit,
)
from sponsio.runtime.verifier import TraceVerifier


def _trace(*tool_calls: str) -> Trace:
    return Trace(
        events=[
            Event(ts=i, agent="bot", event_type="tool_call", tool=t)
            for i, t in enumerate(tool_calls)
        ]
    )


# ---------------------------------------------------------------------------
# Layer 1: direct unit tests for DFAEvaluator
# ---------------------------------------------------------------------------


class TestProgressionAtoms:
    def test_single_atom_true(self):
        dfa = DFAEvaluator(Atom("called", "X"))
        assert dfa.step({"called(X)": True}) == "⊤"

    def test_single_atom_false(self):
        dfa = DFAEvaluator(Atom("called", "X"))
        assert dfa.step({"called(Y)": True}) == "⊥"

    def test_not_atom(self):
        dfa = DFAEvaluator(Not(Atom("called", "X")))
        assert dfa.step({"called(X)": True}) == "⊥"

    def test_and_short_circuits(self):
        dfa = DFAEvaluator(And(Atom("called", "X"), Atom("called", "Y")))
        # Both must hold at the SAME step for non-temporal And
        assert dfa.step({"called(X)": True, "called(Y)": True}) == "⊤"

    def test_or(self):
        dfa = DFAEvaluator(Or(Atom("called", "X"), Atom("called", "Y")))
        assert dfa.step({"called(X)": True}) == "⊤"


class TestProgressionG:
    """G(φ) stays ? while φ holds, becomes ⊥ the moment φ fails."""

    def test_g_remains_undecided_when_phi_holds(self):
        dfa = DFAEvaluator(G(Atom("called", "X")))
        for _ in range(3):
            assert dfa.step({"called(X)": True}) == "?"

    def test_g_becomes_false_on_first_violation(self):
        dfa = DFAEvaluator(G(Atom("called", "X")))
        dfa.step({"called(X)": True})
        assert dfa.step({}) == "⊥"

    def test_g_stays_false_after_violation(self):
        dfa = DFAEvaluator(G(Atom("called", "X")))
        dfa.step({})
        assert dfa.step({"called(X)": True}) == "⊥"
        assert dfa.step({"called(X)": True}) == "⊥"

    def test_g_finalize_vacuous_true(self):
        """G(φ) still undecided at session end → vacuously True (weak semantics)."""
        dfa = DFAEvaluator(G(Atom("called", "X")))
        dfa.step({"called(X)": True})
        dfa.step({"called(X)": True})
        assert dfa.peek() == "?"
        assert dfa.finalize() == "⊤"


class TestProgressionF:
    """F(φ) stays ? until a witness, then ⊤ forever."""

    def test_f_becomes_true_on_witness(self):
        dfa = DFAEvaluator(F(Atom("called", "X")))
        assert dfa.step({}) == "?"
        assert dfa.step({"called(X)": True}) == "⊤"

    def test_f_stays_true_after_witness(self):
        dfa = DFAEvaluator(F(Atom("called", "X")))
        dfa.step({"called(X)": True})
        assert dfa.step({}) == "⊤"

    def test_f_finalize_without_witness_is_false(self):
        dfa = DFAEvaluator(F(Atom("called", "X")))
        dfa.step({})
        dfa.step({})
        assert dfa.peek() == "?"
        assert dfa.finalize() == "⊥"


class TestProgressionU:
    """φ U ψ: ψ must hold at some point, φ holds until then."""

    def test_u_witness_immediately(self):
        f = U(Atom("called", "left"), Atom("called", "right"))
        dfa = DFAEvaluator(f)
        assert dfa.step({"called(right)": True}) == "⊤"

    def test_u_left_holds_then_right(self):
        f = U(Atom("called", "left"), Atom("called", "right"))
        dfa = DFAEvaluator(f)
        assert dfa.step({"called(left)": True}) == "?"
        assert dfa.step({"called(right)": True}) == "⊤"

    def test_u_left_fails_before_right(self):
        f = U(Atom("called", "left"), Atom("called", "right"))
        dfa = DFAEvaluator(f)
        # Neither left nor right → left fails → ⊥
        assert dfa.step({}) == "⊥"

    def test_u_finalize_without_witness(self):
        f = U(Atom("called", "left"), Atom("called", "right"))
        dfa = DFAEvaluator(f)
        dfa.step({"called(left)": True})
        assert dfa.peek() == "?"
        # Session ends without right ever firing.
        assert dfa.finalize() == "⊥"


class TestProgressionX:
    def test_x_peels_off(self):
        dfa = DFAEvaluator(X(Atom("called", "X")))
        # First event: X peels off, residual = Atom("called", "X")
        dfa.step({})
        assert dfa.peek() == "?"
        assert dfa.step({"called(X)": True}) == "⊤"

    def test_x_finalize_weak_next(self):
        """X(φ) unresolved at session end = vacuously True (weak next)."""
        dfa = DFAEvaluator(X(Atom("called", "X")))
        # Zero events: X is still pending → finalize = ⊤
        assert dfa.finalize() == "⊤"


class TestProgressionArithmetic:
    def test_rate_limit_pattern(self):
        """G(count(X) ≤ 3) violates on the 4th call."""
        f = G(Le(Var("count", "X"), Const(3)))
        dfa = DFAEvaluator(f)
        for count in [1, 2, 3]:
            v = {"count(X)": count, "called(X)": True}
            assert dfa.step(v) == "?"
        # 4th call: count=4 > 3 → violation
        assert dfa.step({"count(X)": 4, "called(X)": True}) == "⊥"


class TestSnapshotRestore:
    def test_snapshot_preserves_state(self):
        dfa = DFAEvaluator(G(Atom("called", "X")))
        dfa.step({"called(X)": True})
        snap = dfa.snapshot()

        # Advance past the snapshot and violate
        dfa.step({})
        assert dfa.peek() == "⊥"

        # Restore: back to the ok state
        dfa.restore(snap)
        assert dfa.peek() == "?"

    def test_reset_rewinds_to_initial(self):
        dfa = DFAEvaluator(G(Atom("called", "X")))
        dfa.step({})  # violate
        assert dfa.peek() == "⊥"
        dfa.reset()
        assert dfa.peek() == "?"
        assert dfa.steps_consumed == 0


# ---------------------------------------------------------------------------
# Layer 2: differential tests against the recursive backend
# ---------------------------------------------------------------------------


def _verdict_both(
    formula, trace: Trace, agents: dict | None = None
) -> tuple[bool, bool]:
    """Return (recursive_verdict, dfa_verdict) for the same (formula, trace)."""
    v_rec = TraceVerifier(agents=agents, backend="recursive")
    v_rec.sync(trace)
    rec = v_rec.check(formula).holds

    v_dfa = TraceVerifier(agents=agents, backend="dfa")
    v_dfa.sync(trace)
    dfa = v_dfa.check(formula).holds
    return rec, dfa


class TestDifferentialPatterns:
    """Same (formula, trace) must give the same verdict on both backends."""

    @pytest.mark.parametrize(
        "sequence,expected",
        [
            (("verify", "transfer"), True),  # A before B → ok
            (("transfer",), False),  # B without A → violated
            (("verify",), True),  # Just A → ok (B never called)
            (("verify", "transfer", "transfer"), True),  # Once A seen, stable ok
        ],
    )
    def test_must_precede(self, sequence, expected):
        f = must_precede("verify", "transfer")
        rec, dfa = _verdict_both(f, _trace(*sequence))
        assert rec == dfa
        assert rec is expected

    @pytest.mark.parametrize(
        "n,limit,expected",
        [
            (2, 3, True),  # under limit
            (3, 3, True),  # at limit
            (4, 3, False),  # over limit
            (0, 1, True),  # empty trace
        ],
    )
    def test_rate_limit(self, n, limit, expected):
        f = rate_limit("X", limit)
        trace = _trace(*(["X"] * n))
        rec, dfa = _verdict_both(f, trace)
        assert rec == dfa
        assert rec is expected

    @pytest.mark.parametrize(
        "sequence,expected",
        [
            (("approve",), True),  # only approve, no contradiction
            (("approve", "deny"), False),  # reversal → violated
            (("deny",), True),  # deny without prior approve — vacuous
            (("approve", "approve"), True),  # double approve ok
        ],
    )
    def test_no_reversal_nested_g(self, sequence, expected):
        """no_reversal has nested G — this is the pattern that 3a couldn't
        cache. The DFA backend must match recursive."""
        f = no_reversal("approve", "deny")
        rec, dfa = _verdict_both(f, _trace(*sequence))
        assert rec == dfa
        assert rec is expected

    def test_bounded_retry(self):
        f = bounded_retry("retry", 2)
        for n, expected in [(0, True), (1, True), (2, True), (3, False)]:
            rec, dfa = _verdict_both(f, _trace(*(["retry"] * n)))
            assert rec == dfa, f"n={n}: rec={rec}, dfa={dfa}"
            assert rec is expected

    def test_arg_blacklist(self):
        """Content-atom formula: arg_field_has(...) predicate lookup."""
        f = arg_blacklist("bash", "cmd", ["rm -rf"])
        from sponsio.tracer.grounding import collect_content_atoms

        # Safe case
        trace_safe = Trace(
            events=[
                Event(
                    ts=0,
                    agent="bot",
                    event_type="tool_call",
                    tool="bash",
                    args={"cmd": "ls /tmp"},
                ),
            ]
        )
        v_rec = TraceVerifier(backend="recursive")
        v_rec.sync(trace_safe, collect_content_atoms([f]))
        v_dfa = TraceVerifier(backend="dfa")
        v_dfa.sync(trace_safe, collect_content_atoms([f]))
        assert v_rec.check(f).holds is True
        assert v_dfa.check(f).holds is True

        # Unsafe case
        trace_bad = Trace(
            events=[
                Event(
                    ts=0,
                    agent="bot",
                    event_type="tool_call",
                    tool="bash",
                    args={"cmd": "rm -rf /"},
                ),
            ]
        )
        v_rec2 = TraceVerifier(backend="recursive")
        v_rec2.sync(trace_bad, collect_content_atoms([f]))
        v_dfa2 = TraceVerifier(backend="dfa")
        v_dfa2.sync(trace_bad, collect_content_atoms([f]))
        assert v_rec2.check(f).holds is False
        assert v_dfa2.check(f).holds is False

    def test_arg_allowlist(self):
        """Dual of arg_blacklist: arg must match one of the allowed patterns."""
        f = arg_allowlist(
            "send_money", "recipient", ["US-internal-001", "US-internal-002"]
        )
        from sponsio.tracer.grounding import collect_content_atoms

        # Allowed: matches one of the listed patterns
        trace_ok = Trace(
            events=[
                Event(
                    ts=0,
                    agent="bot",
                    event_type="tool_call",
                    tool="send_money",
                    args={"recipient": "US-internal-001", "amount": 100},
                ),
            ]
        )
        v_rec = TraceVerifier(backend="recursive")
        v_rec.sync(trace_ok, collect_content_atoms([f]))
        v_dfa = TraceVerifier(backend="dfa")
        v_dfa.sync(trace_ok, collect_content_atoms([f]))
        assert v_rec.check(f).holds is True
        assert v_dfa.check(f).holds is True

        # Blocked: matches no allowed pattern
        trace_bad = Trace(
            events=[
                Event(
                    ts=0,
                    agent="bot",
                    event_type="tool_call",
                    tool="send_money",
                    args={"recipient": "ATTACKER-IBAN-999", "amount": 100},
                ),
            ]
        )
        v_rec2 = TraceVerifier(backend="recursive")
        v_rec2.sync(trace_bad, collect_content_atoms([f]))
        v_dfa2 = TraceVerifier(backend="dfa")
        v_dfa2.sync(trace_bad, collect_content_atoms([f]))
        assert v_rec2.check(f).holds is False
        assert v_dfa2.check(f).holds is False

    def test_arg_allowlist_empty_patterns_raises(self):
        """An empty allowlist would block every call - reject at construction."""
        import pytest

        with pytest.raises(ValueError, match="non-empty"):
            arg_allowlist("send_money", "recipient", [])


class TestDifferentialIncremental:
    """Both backends must agree when the trace grows incrementally."""

    def test_rate_limit_incremental(self):
        f = rate_limit("X", 2)
        v_rec = TraceVerifier(backend="recursive")
        v_dfa = TraceVerifier(backend="dfa")

        for n in range(1, 5):
            trace = _trace(*(["X"] * n))
            v_rec.sync(trace)
            v_dfa.sync(trace)
            rec = v_rec.check(f).holds
            dfa = v_dfa.check(f).holds
            assert rec == dfa, f"n={n}: rec={rec}, dfa={dfa}"

    def test_must_precede_incremental(self):
        f = must_precede("A", "B")
        v_rec = TraceVerifier(backend="recursive")
        v_dfa = TraceVerifier(backend="dfa")

        sequence = ["A", "B", "B", "A"]
        for n in range(1, len(sequence) + 1):
            trace = _trace(*sequence[:n])
            v_rec.sync(trace)
            v_dfa.sync(trace)
            assert v_rec.check(f).holds == v_dfa.check(f).holds


# ---------------------------------------------------------------------------
# Layer 3: integration with TraceVerifier (backend switch, contracts, etc.)
# ---------------------------------------------------------------------------


class TestTraceVerifierBackendSwitch:
    def test_default_backend_is_recursive(self):
        v = TraceVerifier()
        assert v._backend == "recursive"

    def test_dfa_backend_opt_in(self):
        v = TraceVerifier(backend="dfa")
        assert v._backend == "dfa"

    def test_check_contract_dfa(self):
        """check_contract works under dfa backend."""
        contract = Contract(
            agent=Agent(id="bot"),
            enforcement=rate_limit("X", 2),
        )
        v = TraceVerifier(backend="dfa")
        v.sync(_trace("X", "X"))
        cv = v.check_contract(contract)
        assert cv.holds is True

        v.sync(_trace("X", "X", "X"))
        cv2 = v.check_contract(contract)
        assert cv2.holds is False

    def test_reset_wipes_dfa_state(self):
        v = TraceVerifier(backend="dfa")
        f = rate_limit("X", 1)
        v.sync(_trace("X", "X"))
        assert v.check(f).holds is False  # violated

        v.reset()
        v.sync(_trace("X"))
        assert v.check(f).holds is True  # clean slate


class TestLivenessFinalize:
    """DFA backend's finalize() must collapse pending F(...) to ⊥ at session end."""

    def test_liveness_pending_without_include_liveness_passes(self):
        """Runtime semantics: ? is not a violation — don't block."""
        contract = Contract(
            agent=Agent(id="bot"),
            enforcement=always_followed_by("A", "B"),
        )
        v = TraceVerifier(backend="dfa")
        v.sync(_trace("A"))  # B never fired

        # During runtime: liveness is skipped entirely, so contract holds.
        cv = v.check_contract(contract, include_liveness=False)
        assert cv.holds is True
        assert len(cv.enforcements) == 0  # liveness skipped

    def test_liveness_pending_with_include_liveness_fails(self):
        """Session-end semantics: ? collapses to ⊥."""
        contract = Contract(
            agent=Agent(id="bot"),
            enforcement=always_followed_by("A", "B"),
        )
        v = TraceVerifier(backend="dfa")
        v.sync(_trace("A"))

        cv = v.check_contract(contract, include_liveness=True)
        assert cv.holds is False
        assert len(cv.enforcements) == 1
        assert cv.enforcements[0].holds is False

    def test_liveness_discharged(self):
        """F(...) with a witness before session end → ⊤ on finalize."""
        contract = Contract(
            agent=Agent(id="bot"),
            enforcement=always_followed_by("A", "B"),
        )
        v = TraceVerifier(backend="dfa")
        v.sync(_trace("A", "B"))

        cv = v.check_contract(contract, include_liveness=True)
        assert cv.holds is True


class TestDFAMatchesFinishSessionBehavior:
    """End-to-end: BaseGuard.finish_session() works identically under dfa
    backend (swap via _monitor._verifier)."""

    def test_finish_session_via_dfa(self):
        import sponsio

        guard = sponsio.Sponsio(
            agent_id="bot",
            contracts=["tool `A` must always be followed by `B`"],
            verbose=False,
        )
        # Swap the verifier backend. This is a hack for testing —
        # normally you'd pass backend at TraceVerifier construction.
        from sponsio.runtime.verifier import TraceVerifier

        guard.monitor._verifier = TraceVerifier(backend="dfa")

        guard.guard_before("A")
        pending = guard.finish_session()
        assert len(pending) == 1
        assert pending[0].holds is False
