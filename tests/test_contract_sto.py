"""Tests for Contract's alpha/beta fields and is_pure_det dispatch."""

from __future__ import annotations

import pytest

from sponsio.formulas.formula import Atom, G, Implies
from sponsio.models.agent import Agent
from sponsio.models.contract import Contract


class TestAlphaBetaDefaults:
    def test_defaults_1_1(self):
        c = Contract(agent=Agent(id="bot"), enforcement=Atom("x"))
        assert c.alpha == 1.0
        assert c.beta == 1.0

    def test_explicit_alpha_beta(self):
        c = Contract(
            agent=Agent(id="bot"),
            enforcement=Atom("x", atom_type="sto"),
            alpha=0.7,
            beta=0.95,
        )
        assert c.alpha == 0.7
        assert c.beta == 0.95


class TestAlphaBetaValidation:
    def test_alpha_too_high_raises(self):
        with pytest.raises(ValueError, match="alpha"):
            Contract(agent=Agent(id="bot"), enforcement=Atom("x"), alpha=1.5)

    def test_alpha_negative_raises(self):
        with pytest.raises(ValueError, match="alpha"):
            Contract(agent=Agent(id="bot"), enforcement=Atom("x"), alpha=-0.1)

    def test_beta_too_high_raises(self):
        with pytest.raises(ValueError, match="beta"):
            Contract(agent=Agent(id="bot"), enforcement=Atom("x"), beta=1.2)

    def test_beta_boundary_values_ok(self):
        Contract(agent=Agent(id="bot"), enforcement=Atom("x"), beta=0.0)
        Contract(agent=Agent(id="bot"), enforcement=Atom("x"), beta=1.0)


class TestIsPureDet:
    def test_pure_det_single_atom(self):
        c = Contract(agent=Agent(id="bot"), enforcement=Atom("x"))
        assert c.is_pure_det is True

    def test_pure_det_compound(self):
        c = Contract(
            agent=Agent(id="bot"),
            assumption=Atom("y"),
            enforcement=G(Implies(Atom("a"), Atom("b"))),
        )
        assert c.is_pure_det is True

    def test_sto_atom_in_enforcement(self):
        c = Contract(
            agent=Agent(id="bot"),
            enforcement=Atom("pii", atom_type="sto"),
            beta=0.95,
        )
        assert c.is_pure_det is False

    def test_sto_atom_in_assumption(self):
        c = Contract(
            agent=Agent(id="bot"),
            assumption=Atom("untrusted", atom_type="sto"),
            enforcement=Atom("safe"),
            alpha=0.7,
        )
        assert c.is_pure_det is False

    def test_mixed_in_single_formula(self):
        c = Contract(
            agent=Agent(id="bot"),
            enforcement=G(Implies(Atom("a"), Atom("pii_free", atom_type="sto"))),
            beta=0.95,
        )
        assert c.is_pure_det is False

    def test_alpha_below_1_forces_lifted_path(self):
        c = Contract(
            agent=Agent(id="bot"),
            enforcement=Atom("x"),
            alpha=0.7,
        )
        # Pure-det atoms but non-default alpha → must take lifted path
        assert c.is_pure_det is False

    def test_beta_below_1_forces_lifted_path(self):
        c = Contract(
            agent=Agent(id="bot"),
            enforcement=Atom("x"),
            beta=0.5,
        )
        assert c.is_pure_det is False

    def test_list_enforcement_pure_det(self):
        c = Contract(
            agent=Agent(id="bot"),
            enforcement=[Atom("a"), Atom("b"), Atom("c")],
        )
        assert c.is_pure_det is True

    def test_list_enforcement_mixed(self):
        c = Contract(
            agent=Agent(id="bot"),
            enforcement=[Atom("a"), Atom("pii", atom_type="sto")],
            beta=0.9,
        )
        assert c.is_pure_det is False

    def test_raw_string_enforcement_forces_lifted_path(self):
        # NL string that hasn't been compiled — _unwrap returns None, so
        # is_pure_det conservatively returns False to force lifting path
        # (safe because raw strings can't run through LTL evaluator anyway).
        c = Contract(
            agent=Agent(id="bot"),
            enforcement="tool `A` must precede `B`",
        )
        assert c.is_pure_det is False


class TestDetFormulaWrapperSupport:
    def test_detformula_unwraps(self):
        from sponsio.patterns.library import must_precede

        det = must_precede("A", "B")
        c = Contract(agent=Agent(id="bot"), enforcement=det)
        assert c.is_pure_det is True


class TestMakeContractsWithAlphaBeta:
    def test_make_contracts_reads_alpha_beta(self):
        from sponsio.models.contract import make_contracts

        result = make_contracts(
            Agent(id="bot"),
            contracts=[
                {
                    "A": Atom("untrusted", atom_type="sto"),
                    "E": Atom("safe", atom_type="sto"),
                    "alpha": 0.7,
                    "beta": 0.95,
                },
            ],
        )
        assert len(result) == 1
        c = result[0]
        assert c.alpha == 0.7
        assert c.beta == 0.95

    def test_make_contracts_defaults_alpha_beta(self):
        from sponsio.models.contract import make_contracts

        result = make_contracts(
            Agent(id="bot"),
            enforcements=[Atom("x")],
        )
        assert result[0].alpha == 1.0
        assert result[0].beta == 1.0
