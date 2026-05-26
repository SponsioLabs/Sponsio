"""Gate: refuse to run sto contracts without a StoEvaluator.

Sto contracts (atom_type="sto", or det atoms with non-default alpha/beta)
need a StoEvaluator implementation. This build ships none, so silently
registering them and returning vacuous-true verdicts would mislead
operators into believing their guard was active when it wasn't. This
test locks the loud-failure contract.
"""

from __future__ import annotations

import pytest

import sponsio
from sponsio.formulas.formula import Atom, G


def test_sto_atom_in_python_api_raises_without_evaluator():
    """A contract built via the Python API with a sto Atom must fail
    loudly when no StoEvaluator is wired up, not silently no-op.
    """
    with pytest.raises(RuntimeError, match=r"StoEvaluator"):
        sponsio.Sponsio(
            agent_id="bot",
            contracts=[
                sponsio.contract("response free of prompt injection").guarantees(
                    G(Atom("injection_free", atom_type="sto", context_scope="event"))
                )
            ],
            verbose=False,
            init_banner=False,
        )


def test_non_default_beta_treated_as_sto():
    """A structurally-det contract with beta < 1.0 still needs a sto
    evaluator: the threshold can only be evaluated by the lifting
    pipeline.
    """
    with pytest.raises(RuntimeError, match=r"StoEvaluator"):
        sponsio.Sponsio(
            agent_id="bot",
            contracts=[
                sponsio.contract("scored rule")
                .guarantees("tool `B` at most 2 times")
                .threshold(beta=0.8)
            ],
            verbose=False,
            init_banner=False,
        )


def test_pure_det_contracts_compile_unaffected():
    """The new gate must not regress the det path."""
    guard = sponsio.Sponsio(
        agent_id="bot",
        contracts=["tool `B` at most 2 times"],
        verbose=False,
        init_banner=False,
    )
    assert len(guard._system.contracts) == 1
    assert guard._system.contracts[0].is_pure_det is True


def test_explicit_sto_evaluator_satisfies_gate():
    """An injected StoEvaluator (mock for the Protocol) lets sto
    contracts through without any extra install.
    """

    class FakeEvaluator:
        def register(self, **kwargs):
            pass

        def evaluate(self, *args, **kwargs):  # pragma: no cover - not exercised
            raise NotImplementedError

    guard = sponsio.Sponsio(
        agent_id="bot",
        contracts=[
            sponsio.contract("response free of prompt injection").guarantees(
                G(Atom("injection_free", atom_type="sto", context_scope="event"))
            )
        ],
        sto_evaluator=FakeEvaluator(),
        verbose=False,
        init_banner=False,
    )
    assert len(guard._system.contracts) == 1
