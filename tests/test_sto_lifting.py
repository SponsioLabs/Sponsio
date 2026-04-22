"""Tests for ``eval_sto_confidence`` probabilistic lifting."""

from __future__ import annotations

import pytest

from sponsio.formulas.formula import (
    And,
    Atom,
    Const,
    Eq,
    F,
    G,
    Ge,
    Implies,
    Le,
    Not,
    Or,
    U,
    Var,
    X,
)
from sponsio.models.trace import Trace
from sponsio.patterns.sto_registry import (
    _clear_for_test,
    register_sto_atom,
)
from sponsio.runtime.evaluators import StoResult
from sponsio.runtime.sto_lifting import _all_det, eval_sto_confidence


@pytest.fixture
def clean_registry():
    """Reset the sto registry before and after each test that mutates it."""
    _clear_for_test()
    yield
    _clear_for_test()


def _trace_of_length(n: int) -> Trace:
    """Minimal Trace with n events (content/args don't matter for tests
    that use the mock registry)."""
    from sponsio.models.trace import Event

    return Trace(
        events=[
            Event(ts=i, agent="bot", event_type="tool_call", tool=f"op{i}")
            for i in range(n)
        ]
    )


def _valuations(n: int, **fixed) -> list[dict]:
    """Per-timestep grounded valuations with the same ``fixed`` dict at
    every position."""
    return [dict(fixed) for _ in range(n)]


def _register_constant(predicate: str, value: float) -> None:
    """Register a sto atom that always returns ``value`` regardless of
    timestep or args."""

    @register_sto_atom(predicate)
    def _fn(atom, trace, t):
        return StoResult(score=value, evidence="", suggestion="")


def _register_per_t(predicate: str, values: list[float]) -> None:
    """Register a sto atom that returns a different score per timestep."""

    @register_sto_atom(predicate)
    def _fn(atom, trace, t):
        return StoResult(score=values[t], evidence="", suggestion="")


# ---------------------------------------------------------------------------
# Atom-level lifting
# ---------------------------------------------------------------------------


class TestSingleAtom:
    def test_sto_atom_returns_registered_score(self, clean_registry):
        _register_constant("inj", 0.8)
        trace = _trace_of_length(1)
        vals = _valuations(1)
        assert eval_sto_confidence(
            Atom("inj", atom_type="sto"), vals, trace
        ) == pytest.approx(0.8)

    def test_det_atom_true(self):
        trace = _trace_of_length(1)
        vals = _valuations(1, **{"called(x)": True})
        assert eval_sto_confidence(Atom("called", "x"), vals, trace) == 1.0

    def test_det_atom_false(self):
        trace = _trace_of_length(1)
        vals = _valuations(1)
        assert eval_sto_confidence(Atom("called", "x"), vals, trace) == 0.0

    def test_score_clamped_to_unit_interval(self, clean_registry):
        # Defensive: evaluator returns out-of-range — lifting should clamp.
        _register_constant("buggy_high", 1.5)
        _register_constant("buggy_low", -0.2)
        trace = _trace_of_length(1)
        vals = _valuations(1)
        assert (
            eval_sto_confidence(Atom("buggy_high", atom_type="sto"), vals, trace) == 1.0
        )
        assert (
            eval_sto_confidence(Atom("buggy_low", atom_type="sto"), vals, trace) == 0.0
        )

    def test_unregistered_sto_predicate_raises(self, clean_registry):
        trace = _trace_of_length(1)
        vals = _valuations(1)
        with pytest.raises(KeyError):
            eval_sto_confidence(Atom("nonexistent", atom_type="sto"), vals, trace)


# ---------------------------------------------------------------------------
# Boolean operators — independent product semantics
# ---------------------------------------------------------------------------


class TestBooleanLifting:
    def test_and_independent_product(self, clean_registry):
        _register_constant("a", 0.8)
        _register_constant("b", 0.9)
        trace = _trace_of_length(1)
        vals = _valuations(1)
        formula = And(Atom("a", atom_type="sto"), Atom("b", atom_type="sto"))
        assert eval_sto_confidence(formula, vals, trace) == pytest.approx(0.72)

    def test_or_independent_complement(self, clean_registry):
        _register_constant("a", 0.8)
        _register_constant("b", 0.9)
        trace = _trace_of_length(1)
        vals = _valuations(1)
        # 1 - (1-0.8)(1-0.9) = 1 - 0.02 = 0.98
        formula = Or(Atom("a", atom_type="sto"), Atom("b", atom_type="sto"))
        assert eval_sto_confidence(formula, vals, trace) == pytest.approx(0.98)

    def test_negation(self, clean_registry):
        _register_constant("a", 0.8)
        trace = _trace_of_length(1)
        vals = _valuations(1)
        assert eval_sto_confidence(
            Not(Atom("a", atom_type="sto")), vals, trace
        ) == pytest.approx(0.2)

    def test_implies_as_not_or(self, clean_registry):
        # P → Q  ≡  ¬P ∨ Q  (independent lifting: 1 - p·(1-q))
        _register_constant("p", 0.8)
        _register_constant("q", 0.9)
        trace = _trace_of_length(1)
        vals = _valuations(1)
        # 1 - 0.8 * (1 - 0.9) = 1 - 0.08 = 0.92
        formula = Implies(Atom("p", atom_type="sto"), Atom("q", atom_type="sto"))
        assert eval_sto_confidence(formula, vals, trace) == pytest.approx(0.92)

    def test_pure_det_and_returns_01(self):
        trace = _trace_of_length(1)
        vals_true = _valuations(1, **{"a()": True, "b()": True})
        vals_mixed = _valuations(1, **{"a()": True, "b()": False})
        f = And(Atom("a"), Atom("b"))
        assert eval_sto_confidence(f, vals_true, trace) == 1.0
        assert eval_sto_confidence(f, vals_mixed, trace) == 0.0


# ---------------------------------------------------------------------------
# Temporal operators
# ---------------------------------------------------------------------------


class TestTemporalLifting:
    def test_G_product_across_trace(self, clean_registry):
        _register_per_t("s", [0.9, 0.8, 0.95])
        trace = _trace_of_length(3)
        vals = _valuations(3)
        # Product: 0.9 * 0.8 * 0.95 = 0.684
        assert eval_sto_confidence(
            G(Atom("s", atom_type="sto")), vals, trace
        ) == pytest.approx(0.684)

    def test_F_complement_product_across_trace(self, clean_registry):
        _register_per_t("s", [0.9, 0.8, 0.95])
        trace = _trace_of_length(3)
        vals = _valuations(3)
        # 1 - (0.1 * 0.2 * 0.05) = 1 - 0.001 = 0.999
        assert eval_sto_confidence(
            F(Atom("s", atom_type="sto")), vals, trace
        ) == pytest.approx(0.999)

    def test_X_reads_next_timestep(self, clean_registry):
        _register_per_t("s", [0.1, 0.9])
        trace = _trace_of_length(2)
        vals = _valuations(2)
        # X at t=0 reads t=1 → 0.9
        assert eval_sto_confidence(
            X(Atom("s", atom_type="sto")), vals, trace
        ) == pytest.approx(0.9)

    def test_X_past_end_is_weak(self, clean_registry):
        _register_per_t("s", [0.5])
        trace = _trace_of_length(1)
        vals = _valuations(1)
        # Past end of trace → weak next (1.0, matches det evaluator semantics)
        assert eval_sto_confidence(X(Atom("s", atom_type="sto")), vals, trace) == 1.0

    def test_G_on_empty_tail_is_vacuous(self, clean_registry):
        _register_constant("s", 0.5)
        trace = _trace_of_length(2)
        vals = _valuations(2)
        # Start at t=10 (past end) → G is vacuously 1.0
        assert (
            eval_sto_confidence(G(Atom("s", atom_type="sto")), vals, trace, t=10) == 1.0
        )

    def test_F_on_empty_tail_is_false(self, clean_registry):
        _register_constant("s", 0.5)
        trace = _trace_of_length(2)
        vals = _valuations(2)
        assert (
            eval_sto_confidence(F(Atom("s", atom_type="sto")), vals, trace, t=10) == 0.0
        )

    def test_U_basic(self, clean_registry):
        # phi U psi — at some k, psi holds; until then phi holds.
        # Mock: phi always 1.0, psi = [0, 0, 1.0]
        _register_per_t("phi", [1.0, 1.0, 1.0])
        _register_per_t("psi", [0.0, 0.0, 1.0])
        trace = _trace_of_length(3)
        vals = _valuations(3)
        # Only k=2 contributes: psi(2) * phi(0)*phi(1) = 1 * 1 * 1 = 1
        # Disjunction: 1 - (1-1) = 1.0
        formula = U(Atom("phi", atom_type="sto"), Atom("psi", atom_type="sto"))
        assert eval_sto_confidence(formula, vals, trace) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Mixed det/sto trees
# ---------------------------------------------------------------------------


class TestMixedDetSto:
    def test_implies_with_det_antecedent_sto_consequent(self, clean_registry):
        _register_per_t("pii_free", [0.9, 0.9])
        trace = _trace_of_length(2)
        vals = [
            {"called(send_email)": True},  # t=0: antecedent true
            {"called(send_email)": False},  # t=1: antecedent false
        ]
        # G(called(send_email) → StoAtom("pii_free"))
        inner = Implies(
            Atom("called", "send_email"),
            Atom("pii_free", atom_type="sto"),
        )
        formula = G(inner)

        # t=0: 1.0 → 0.9 implies = 1 - 1*(1-0.9) = 0.9
        # t=1: 0.0 → anything implies = 1 - 0*(1-0.9) = 1.0
        # Product: 0.9 * 1.0 = 0.9
        assert eval_sto_confidence(formula, vals, trace) == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# Arithmetic / set nodes
# ---------------------------------------------------------------------------


class TestArithmeticLifting:
    def test_le_true_contributes_1(self):
        trace = _trace_of_length(1)
        vals = _valuations(1, **{"count(x)": 3})
        formula = Le(Var("count", "x"), Const(5))
        assert eval_sto_confidence(formula, vals, trace) == 1.0

    def test_le_false_contributes_0(self):
        trace = _trace_of_length(1)
        vals = _valuations(1, **{"count(x)": 10})
        formula = Le(Var("count", "x"), Const(5))
        assert eval_sto_confidence(formula, vals, trace) == 0.0

    def test_arithmetic_inside_G(self):
        # G(count(x) <= 5) on a trace where count goes 3, 4, 5, 6
        trace = _trace_of_length(4)
        vals = [
            {"count(x)": 3},
            {"count(x)": 4},
            {"count(x)": 5},
            {"count(x)": 6},
        ]
        formula = G(Le(Var("count", "x"), Const(5)))
        # All timesteps: 1, 1, 1, 0 → product = 0.0
        assert eval_sto_confidence(formula, vals, trace) == 0.0

    def test_ge_and_eq(self):
        trace = _trace_of_length(1)
        vals = _valuations(1, **{"count(x)": 5})
        assert eval_sto_confidence(Ge(Var("count", "x"), Const(3)), vals, trace) == 1.0
        assert eval_sto_confidence(Eq(Var("count", "x"), Const(5)), vals, trace) == 1.0


# ---------------------------------------------------------------------------
# Caching / shared subterms
# ---------------------------------------------------------------------------


class TestCaching:
    def test_shared_subterm_evaluated_once_per_timestep(self, clean_registry):
        call_count = 0

        @register_sto_atom("counted")
        def _fn(atom, trace, t):
            nonlocal call_count
            call_count += 1
            return StoResult(score=0.7, evidence="", suggestion="")

        trace = _trace_of_length(1)
        vals = _valuations(1)
        # Same Atom instance used twice → cache hits on second use
        atom = Atom("counted", atom_type="sto")
        formula = And(atom, atom)
        eval_sto_confidence(formula, vals, trace)
        assert call_count == 1

    def test_cache_across_A_and_G(self, clean_registry):
        call_count = 0

        @register_sto_atom("shared")
        def _fn(atom, trace, t):
            nonlocal call_count
            call_count += 1
            return StoResult(score=0.5, evidence="", suggestion="")

        trace = _trace_of_length(2)
        vals = _valuations(2)
        atom = Atom("shared", atom_type="sto")
        cache: dict = {}
        # Simulate Contract.evaluate() sharing cache between A and G
        _ = eval_sto_confidence(atom, vals, trace, t=0, cache=cache)
        _ = eval_sto_confidence(atom, vals, trace, t=0, cache=cache)
        assert call_count == 1

    def test_same_atom_different_timestep_not_cached(self, clean_registry):
        # Cache key is (id(formula), t) — different t must call evaluator
        call_count = 0

        @register_sto_atom("per_t")
        def _fn(atom, trace, t):
            nonlocal call_count
            call_count += 1
            return StoResult(score=0.5, evidence="", suggestion="")

        trace = _trace_of_length(2)
        vals = _valuations(2)
        atom = Atom("per_t", atom_type="sto")
        cache: dict = {}
        eval_sto_confidence(atom, vals, trace, t=0, cache=cache)
        eval_sto_confidence(atom, vals, trace, t=1, cache=cache)
        assert call_count == 2


# ---------------------------------------------------------------------------
# _all_det helper for fast-path dispatch
# ---------------------------------------------------------------------------


class TestAllDet:
    def test_pure_det_atom(self):
        assert _all_det(Atom("called", "x")) is True

    def test_sto_atom(self):
        assert _all_det(Atom("x", atom_type="sto")) is False

    def test_pure_det_compound(self):
        f = G(Implies(Atom("a"), F(Atom("b"))))
        assert _all_det(f) is True

    def test_mixed_compound(self):
        f = G(Implies(Atom("a"), Atom("b", atom_type="sto")))
        assert _all_det(f) is False

    def test_arithmetic_is_det(self):
        f = G(Le(Var("count", "x"), Const(5)))
        assert _all_det(f) is True

    def test_arithmetic_mixed_with_sto(self):
        f = And(Le(Var("count", "x"), Const(5)), Atom("s", atom_type="sto"))
        assert _all_det(f) is False


# ---------------------------------------------------------------------------
# Pure-det equivalence with LTL evaluator (sanity check for fast-path dispatch)
# ---------------------------------------------------------------------------


class TestPureDetEquivalence:
    """A pure-det formula run through ``eval_sto_confidence`` returns
    exactly 0.0 or 1.0, matching the LTL evaluator's bool output. This
    is what makes the fast-path dispatch safe: we can skip lifting and
    use the LTL evaluator without changing the answer."""

    def test_g_implies_called(self):
        from sponsio.formulas.evaluator import evaluate

        f = G(Implies(Atom("a"), Atom("b")))
        vals_true = [
            {"a()": True, "b()": True},
            {"a()": False, "b()": False},
        ]
        vals_false = [
            {"a()": True, "b()": True},
            {"a()": True, "b()": False},  # violation
        ]
        trace = _trace_of_length(2)
        assert eval_sto_confidence(f, vals_true, trace) == 1.0
        assert evaluate(f, vals_true) is True
        assert eval_sto_confidence(f, vals_false, trace) == 0.0
        assert evaluate(f, vals_false) is False
