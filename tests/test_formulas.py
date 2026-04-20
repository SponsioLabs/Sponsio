"""Unit tests for sponsio/formulas/formula.py — AST nodes."""

import pytest
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
    collect_atoms,
)


# ---------------------------------------------------------------------------
# Atom
# ---------------------------------------------------------------------------


def test_atom_key_single_arg():
    a = Atom("called", "fraud_check")
    assert a.key() == "called(fraud_check)"


def test_atom_key_multi_arg():
    a = Atom("precedes", "A", "B")
    assert a.key() == "precedes(A, B)"


def test_atom_repr():
    a = Atom("called", "tool_x")
    assert repr(a) == "called('tool_x')"


def test_atom_is_frozen():
    a = Atom("called", "x")
    with pytest.raises((AttributeError, TypeError)):
        a.predicate = "other"  # type: ignore


def test_atom_hashable():
    a1 = Atom("called", "x")
    a2 = Atom("called", "x")
    assert hash(a1) == hash(a2)
    assert a1 == a2


# ---------------------------------------------------------------------------
# Propositional operators
# ---------------------------------------------------------------------------


def test_not_wraps_formula():
    a = Atom("called", "x")
    f = ~a
    assert isinstance(f, Not)
    assert f.child == a


def test_and_combines():
    a = Atom("called", "x")
    b = Atom("called", "y")
    f = a & b
    assert isinstance(f, And)
    assert f.left == a
    assert f.right == b


def test_or_combines():
    a = Atom("called", "x")
    b = Atom("called", "y")
    f = a | b
    assert isinstance(f, Or)
    assert f.left == a
    assert f.right == b


def test_implies_via_rshift():
    a = Atom("called", "x")
    b = Atom("called", "y")
    f = a >> b
    assert isinstance(f, Implies)
    assert f.left == a
    assert f.right == b


# ---------------------------------------------------------------------------
# Temporal nodes
# ---------------------------------------------------------------------------


def test_G_wraps_child():
    a = Atom("called", "x")
    f = G(a)
    assert isinstance(f, G)
    assert f.child == a
    assert repr(f) == f"G({repr(a)})"


def test_F_wraps_child():
    a = Atom("called", "x")
    f = F(a)
    assert isinstance(f, F)
    assert f.child == a


def test_X_wraps_child():
    a = Atom("called", "x")
    f = X(a)
    assert isinstance(f, X)
    assert f.child == a


def test_U_combines():
    a = Atom("called", "x")
    b = Atom("called", "y")
    f = U(a, b)
    assert isinstance(f, U)
    assert f.left == a
    assert f.right == b


# ---------------------------------------------------------------------------
# Arithmetic nodes
# ---------------------------------------------------------------------------


def test_var_key_no_args():
    v = Var("cost")
    assert v.key() == "cost"


def test_var_key_with_args():
    v = Var("count", "issue_refund")
    assert v.key() == "count(issue_refund)"


def test_const_repr():
    c = Const(42)
    assert repr(c) == "42"


def test_le_creates_node():
    f = Le(Var("x"), Const(5))
    assert isinstance(f, Le)
    assert repr(f) == "(Var('x') <= 5)"


def test_arithmetic_comparisons_exist():
    v = Var("x")
    c = Const(3)
    assert isinstance(Lt(v, c), Lt)
    assert isinstance(Ge(v, c), Ge)
    assert isinstance(Gt(v, c), Gt)
    assert isinstance(Eq(v, c), Eq)


# ---------------------------------------------------------------------------
# collect_atoms
# ---------------------------------------------------------------------------


def test_collect_atoms_single():
    a = Atom("called", "x")
    assert collect_atoms(a) == {a}


def test_collect_atoms_nested():
    a = Atom("called", "x")
    b = Atom("called", "y")
    f = G(a >> b)
    atoms = collect_atoms(f)
    assert atoms == {a, b}


def test_collect_atoms_complex():
    a = Atom("called", "x")
    b = Atom("precedes", "x", "y")
    c = Atom("perm", "admin")
    f = G(a & b) | F(c)
    atoms = collect_atoms(f)
    assert atoms == {a, b, c}


def test_collect_atoms_arithmetic_returns_empty():
    # Arithmetic nodes don't contain Atoms
    f = Le(Var("x"), Const(1))
    assert collect_atoms(f) == set()
