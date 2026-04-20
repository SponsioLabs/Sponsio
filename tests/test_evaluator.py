"""Unit tests for sponsio/formulas/evaluator.py — finite-trace evaluation."""

import pytest
from sponsio.formulas.evaluator import evaluate
from sponsio.formulas.formula import (
    Atom,
    Not,
    And,
    Or,
    Implies,
    G,
    F,
    X,
    U,
    Le,
    Lt,
    Ge,
    Gt,
    Eq,
    Var,
    Const,
)


# Helpers
def atom(name: str) -> Atom:
    return Atom("p", name)


def state(**kwargs) -> dict:
    """Build a predicate valuation dict from keyword args."""
    return kwargs


# ---------------------------------------------------------------------------
# Edge cases: empty / past-end trace
# ---------------------------------------------------------------------------


def test_empty_trace_G_true():
    # G(phi) is vacuously true on empty trace (weak semantics)
    assert evaluate(G(atom("x")), []) is True


def test_empty_trace_F_false():
    # F(phi) is vacuously false on empty trace
    assert evaluate(F(atom("x")), []) is False


def test_past_end_non_F_true():
    trace = [{"p(x)": True}]
    assert evaluate(atom("x"), trace, pos=5) is True  # non-F → True past end


def test_past_end_F_false():
    trace = [{"p(x)": True}]
    assert evaluate(F(atom("x")), trace, pos=5) is False


# ---------------------------------------------------------------------------
# Propositional
# ---------------------------------------------------------------------------


def test_atom_true():
    trace = [{"p(x)": True}]
    assert evaluate(atom("x"), trace) is True


def test_atom_false_missing():
    trace = [{}]
    assert evaluate(atom("x"), trace) is False


def test_not_negates_true():
    trace = [{"p(x)": True}]
    assert evaluate(Not(atom("x")), trace) is False


def test_not_negates_false():
    trace = [{}]
    assert evaluate(Not(atom("x")), trace) is True


def test_and_both_true():
    trace = [{"p(x)": True, "p(y)": True}]
    assert evaluate(And(atom("x"), atom("y")), trace) is True


def test_and_one_false():
    trace = [{"p(x)": True}]
    assert evaluate(And(atom("x"), atom("y")), trace) is False


def test_or_one_true():
    trace = [{"p(x)": True}]
    assert evaluate(Or(atom("x"), atom("y")), trace) is True


def test_or_both_false():
    trace = [{}]
    assert evaluate(Or(atom("x"), atom("y")), trace) is False


def test_implies_false_antecedent():
    # F -> anything is True
    trace = [{}]
    assert evaluate(Implies(atom("x"), atom("y")), trace) is True


def test_implies_true_antecedent_true_consequent():
    trace = [{"p(x)": True, "p(y)": True}]
    assert evaluate(Implies(atom("x"), atom("y")), trace) is True


def test_implies_true_antecedent_false_consequent():
    trace = [{"p(x)": True}]
    assert evaluate(Implies(atom("x"), atom("y")), trace) is False


# ---------------------------------------------------------------------------
# Temporal: G
# ---------------------------------------------------------------------------


def test_G_holds_at_all_positions():
    trace = [{"p(x)": True}, {"p(x)": True}, {"p(x)": True}]
    assert evaluate(G(atom("x")), trace) is True


def test_G_fails_at_one_position():
    trace = [{"p(x)": True}, {}, {"p(x)": True}]
    assert evaluate(G(atom("x")), trace) is False


def test_G_empty_trace_vacuous():
    assert evaluate(G(atom("x")), []) is True


# ---------------------------------------------------------------------------
# Temporal: F
# ---------------------------------------------------------------------------


def test_F_eventually_true():
    trace = [{}, {}, {"p(x)": True}]
    assert evaluate(F(atom("x")), trace) is True


def test_F_never_true():
    trace = [{}, {}, {}]
    assert evaluate(F(atom("x")), trace) is False


def test_F_empty_trace_false():
    assert evaluate(F(atom("x")), []) is False


# ---------------------------------------------------------------------------
# Temporal: X (Next)
# ---------------------------------------------------------------------------


def test_X_next_position():
    trace = [{}, {"p(x)": True}]
    assert evaluate(X(atom("x")), trace) is True


def test_X_at_end_weak():
    # Weak next: at end of trace X(phi) is True
    trace = [{"p(x)": True}]
    assert evaluate(X(atom("x")), trace, pos=0) is True  # pos+1 == len


def test_X_false_at_next():
    trace = [{}, {}]
    assert evaluate(X(atom("x")), trace) is False


# ---------------------------------------------------------------------------
# Temporal: U (Until)
# ---------------------------------------------------------------------------


def test_U_psi_immediately():
    trace = [{"p(y)": True}]
    assert evaluate(U(atom("x"), atom("y")), trace) is True


def test_U_phi_holds_until_psi():
    trace = [{"p(x)": True}, {"p(x)": True}, {"p(y)": True}]
    assert evaluate(U(atom("x"), atom("y")), trace) is True


def test_U_phi_violated_before_psi():
    # phi fails at pos 1, psi never seen — should fail
    trace = [{"p(x)": True}, {}, {"p(y)": True}]
    assert evaluate(U(atom("x"), atom("y")), trace) is False


def test_U_psi_never_true():
    trace = [{"p(x)": True}, {"p(x)": True}]
    assert evaluate(U(atom("x"), atom("y")), trace) is False


# ---------------------------------------------------------------------------
# Arithmetic: Le / Lt / Ge / Gt / Eq
# ---------------------------------------------------------------------------


def test_le_satisfied():
    trace = [{"count(x)": 2}]
    assert evaluate(Le(Var("count", "x"), Const(3)), trace) is True


def test_le_equal():
    trace = [{"count(x)": 3}]
    assert evaluate(Le(Var("count", "x"), Const(3)), trace) is True


def test_le_violated():
    trace = [{"count(x)": 4}]
    assert evaluate(Le(Var("count", "x"), Const(3)), trace) is False


def test_lt_satisfied():
    trace = [{"count(x)": 2}]
    assert evaluate(Lt(Var("count", "x"), Const(3)), trace) is True


def test_lt_equal_violated():
    trace = [{"count(x)": 3}]
    assert evaluate(Lt(Var("count", "x"), Const(3)), trace) is False


def test_ge_satisfied():
    trace = [{"count(x)": 5}]
    assert evaluate(Ge(Var("count", "x"), Const(3)), trace) is True


def test_gt_satisfied():
    trace = [{"count(x)": 4}]
    assert evaluate(Gt(Var("count", "x"), Const(3)), trace) is True


def test_eq_satisfied():
    trace = [{"count(x)": 3}]
    assert evaluate(Eq(Var("count", "x"), Const(3)), trace) is True


def test_eq_violated():
    trace = [{"count(x)": 2}]
    assert evaluate(Eq(Var("count", "x"), Const(3)), trace) is False


def test_var_missing_defaults_zero():
    # Missing var defaults to 0
    trace = [{}]
    assert evaluate(Le(Var("count", "x"), Const(0)), trace) is True


# ---------------------------------------------------------------------------
# TypeError on unknown node type
# ---------------------------------------------------------------------------


def test_unknown_formula_type_raises():
    class Bogus:
        pass

    with pytest.raises(TypeError):
        evaluate(Bogus(), [{}])  # type: ignore
