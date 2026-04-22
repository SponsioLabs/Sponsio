"""Tests for the new ``atom_type`` and sto metadata fields on Atom."""

from __future__ import annotations

import pytest

from sponsio.formulas.formula import And, Atom, G, Not


class TestAtomDefaults:
    def test_default_atom_type_is_det(self):
        a = Atom("called", "pay")
        assert a.atom_type == "det"

    def test_default_sto_metadata_is_none(self):
        a = Atom("called", "pay")
        assert a.output_type is None
        assert a.context_scope is None
        assert a.context_k is None

    def test_existing_positional_constructor_still_works(self):
        a = Atom("called", "pay", "fraud_check")
        assert a.predicate == "called"
        assert a.args == ("pay", "fraud_check")
        assert a.atom_type == "det"

    def test_desc_positional_kwarg_still_works(self):
        a = Atom("called", "pay", desc="payment was called")
        assert a.desc == "payment was called"


class TestStoAtomConstruction:
    def test_explicit_sto_atom(self):
        a = Atom(
            "injection_detected",
            atom_type="sto",
            output_type="classify",
            context_scope="event",
        )
        assert a.atom_type == "sto"
        assert a.output_type == "classify"
        assert a.context_scope == "event"
        assert a.context_k is None

    def test_sto_atom_with_context_k(self):
        a = Atom(
            "pii",
            atom_type="sto",
            output_type="classify",
            context_scope="last_k",
            context_k=3,
        )
        assert a.context_scope == "last_k"
        assert a.context_k == 3

    def test_sto_atom_with_args(self):
        # args are positional; sto metadata comes as keyword
        a = Atom("pii", "ssn", "email", atom_type="sto")
        assert a.args == ("ssn", "email")
        assert a.atom_type == "sto"


class TestAtomImmutability:
    def test_atom_is_frozen(self):
        a = Atom("called", "pay")
        with pytest.raises((AttributeError, TypeError)):
            a.atom_type = "sto"  # type: ignore[misc]

    def test_atom_hashable(self):
        a = Atom("called", "pay")
        b = Atom("called", "pay", atom_type="sto")
        # Different atom_type → different hash values (both still hashable)
        hash(a)
        hash(b)


class TestAtomKeyStable:
    def test_key_does_not_depend_on_atom_type(self):
        # atom_type is runtime dispatch metadata — it must NOT leak into
        # the grounding key, because det and sto evaluators index the
        # same predicate space.
        det = Atom("called", "pay")
        sto = Atom("called", "pay", atom_type="sto")
        assert det.key() == sto.key()

    def test_key_uses_only_predicate_and_args(self):
        a = Atom("called", "pay", desc="irrelevant", atom_type="sto")
        assert a.key() == "called(pay)"


class TestDetPipelineZeroRegression:
    """The existing det evaluator must not read any new field.

    This is a sanity regression test: construct a formula the old way
    (no atom_type argument), evaluate it on a trivial grounded trace,
    and make sure nothing breaks.
    """

    def test_det_evaluator_ignores_new_fields(self):
        from sponsio.formulas.evaluator import evaluate

        # A simple formula: G(called(X))
        f = G(Atom("called", "X"))
        trace = [{"called(X)": True}, {"called(X)": True}]
        assert evaluate(f, trace) is True

        # Same formula with a nested Not/And using Atoms
        f2 = G(Not(And(Atom("a"), Atom("b"))))
        trace2 = [{"a": True, "b": False}, {"a": False, "b": True}]
        assert evaluate(f2, trace2) is True
