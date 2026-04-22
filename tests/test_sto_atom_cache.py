"""Tests for per-contract atom memoization in sto lifting.

Covers Option B of sto-refactor: the `atom_cache` parameter on
`eval_sto_confidence` + its per-contract persistent instance on
RuntimeMonitor. Without this cache, formulas like `G(sto_atom)` on a
growing trace would trigger O(n) judge calls per new event — quadratic
total. With the cache, each (atom, position) is judged exactly once
and total LLM cost is linear in trace length.
"""

from __future__ import annotations

import pytest

from sponsio.formulas.formula import And, Atom, G, Not
from sponsio.models.agent import Agent
from sponsio.models.contract import Contract
from sponsio.models.system import System
from sponsio.models.trace import Event
from sponsio.patterns.sto_registry import (
    _clear_for_test,
    register_sto_atom,
)
from sponsio.runtime.evaluators import StoResult
from sponsio.runtime.monitor import RuntimeMonitor
from sponsio.runtime.sto_lifting import eval_sto_confidence
from sponsio.tracer.grounding import ground


# ---------------------------------------------------------------------------
# Counting atom evaluator — registers as a sto atom + counts invocations
# ---------------------------------------------------------------------------


@pytest.fixture
def counting_atom():
    """Register a sto atom whose evaluator counts each invocation.

    Returns a dict so tests can read the counter. Teardown clears the
    registry.
    """
    _clear_for_test()
    counter = {"calls": 0, "calls_per_position": {}}

    @register_sto_atom("counting_atom")
    def _eval(atom, trace, t):
        counter["calls"] += 1
        counter["calls_per_position"][t] = counter["calls_per_position"].get(t, 0) + 1
        return StoResult(score=0.95, evidence="", suggestion="")

    yield counter
    _clear_for_test()


def _trace(n: int):
    """Build a trace of n llm_response events."""
    return [
        Event(ts=i, agent="bot", event_type="llm_response", content=f"event_{i}")
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Unit-level tests for eval_sto_confidence
# ---------------------------------------------------------------------------


class TestEvalLevelAtomCache:
    def test_without_cache_atom_re_evaluated_every_call(self, counting_atom):
        """Baseline: without atom_cache, repeated calls re-evaluate."""
        from sponsio.models.trace import Trace

        atom = Atom("counting_atom", atom_type="sto")
        trace = Trace(events=_trace(3))
        valuations = ground(trace)

        # Three separate calls, no shared atom_cache
        eval_sto_confidence(atom, valuations, trace, t=0)
        eval_sto_confidence(atom, valuations, trace, t=0)
        eval_sto_confidence(atom, valuations, trace, t=0)

        assert counting_atom["calls"] == 3

    def test_with_shared_atom_cache_single_call(self, counting_atom):
        """With atom_cache passed in, same (atom, position) only judged once."""
        from sponsio.models.trace import Trace

        atom = Atom("counting_atom", atom_type="sto")
        trace = Trace(events=_trace(3))
        valuations = ground(trace)

        atom_cache: dict = {}
        eval_sto_confidence(atom, valuations, trace, t=0, atom_cache=atom_cache)
        eval_sto_confidence(atom, valuations, trace, t=0, atom_cache=atom_cache)
        eval_sto_confidence(atom, valuations, trace, t=0, atom_cache=atom_cache)

        # Only the first call invokes the judge.
        assert counting_atom["calls"] == 1
        assert (id(atom), 0) in atom_cache

    def test_G_over_growing_trace_linear_cost(self, counting_atom):
        """The killer test: G(atom) on a trace growing from 1 to 5 events,
        cache persisted across check calls. Expected: 5 total judge calls
        (one per unique position), NOT 1+2+3+4+5=15."""
        from sponsio.models.trace import Trace

        atom = Atom("counting_atom", atom_type="sto")
        formula = G(atom)
        atom_cache: dict = {}

        events = _trace(5)
        for n in range(1, 6):
            trace = Trace(events=events[:n])
            valuations = ground(trace)
            eval_sto_confidence(formula, valuations, trace, t=0, atom_cache=atom_cache)

        # If atom_cache works: exactly 5 judge calls, one per position.
        # Without cache: 1 + 2 + 3 + 4 + 5 = 15.
        assert counting_atom["calls"] == 5
        # Each position hit exactly once
        for pos in range(5):
            assert counting_atom["calls_per_position"][pos] == 1


# ---------------------------------------------------------------------------
# Monitor-level integration: atom cache survives across check_actions
# ---------------------------------------------------------------------------


class TestMonitorLevelAtomCache:
    def test_G_contract_on_growing_trace_linear_llm_cost(self, counting_atom):
        """End-to-end: a monitor with G(sto_atom) enforcement, driven
        by 5 successive llm_response events via check_action. Total
        judge invocations must be linear in trace length, not quadratic."""
        atom = Atom("counting_atom", atom_type="sto")
        agent = Agent(id="bot")
        contract = Contract(agent=agent, enforcement=G(atom), beta=0.9)

        sys = System(name="t")
        sys._contracts = [contract]
        mon = RuntimeMonitor(system=sys)

        # Drive 5 events through the monitor
        for i in range(5):
            mon.check_action(
                "bot",
                "<llm_response>",
                event_type="llm_response",
                metadata={"content": f"event_{i}"},
            )

        # Linear cost: 5 events × 1 atom = 5 judge calls.
        # Quadratic cost would be 1 + 2 + 3 + 4 + 5 = 15.
        assert counting_atom["calls"] == 5

    def test_multiple_contracts_separate_atom_caches(self, counting_atom):
        """Two contracts referencing the same sto atom predicate —
        they use DISTINCT Atom instances (two `Atom(...)` calls), so
        each contract gets its own cache slot. Expected: 2 calls per
        event (not 1), because the instances are different."""
        a1 = Atom("counting_atom", atom_type="sto")
        a2 = Atom("counting_atom", atom_type="sto")  # different instance
        agent = Agent(id="bot")
        c1 = Contract(agent=agent, enforcement=G(a1), beta=0.9)
        c2 = Contract(agent=agent, enforcement=G(a2), beta=0.9)

        sys = System(name="t")
        sys._contracts = [c1, c2]
        mon = RuntimeMonitor(system=sys)

        for i in range(3):
            mon.check_action(
                "bot",
                "<llm_response>",
                event_type="llm_response",
                metadata={"content": f"event_{i}"},
            )

        # 3 events × 2 distinct atom instances = 6 judge calls
        # (not 3 × 2 × 2 = 12 which would be quadratic)
        assert counting_atom["calls"] == 6

    def test_reset_clears_atom_cache(self, counting_atom):
        """reset() must clear the atom cache — positions about to be
        reused in a fresh session would otherwise return stale scores."""
        atom = Atom("counting_atom", atom_type="sto")
        agent = Agent(id="bot")
        contract = Contract(agent=agent, enforcement=G(atom), beta=0.9)

        sys = System(name="t")
        sys._contracts = [contract]
        mon = RuntimeMonitor(system=sys)

        mon.check_action(
            "bot",
            "<llm_response>",
            event_type="llm_response",
            metadata={"content": "first"},
        )
        assert counting_atom["calls"] == 1

        mon.reset()

        # After reset, position 0 is a new event; atom should be
        # re-evaluated even though a prior call scored position 0.
        mon.check_action(
            "bot",
            "<llm_response>",
            event_type="llm_response",
            metadata={"content": "second"},
        )
        assert counting_atom["calls"] == 2


# ---------------------------------------------------------------------------
# Compound formulas: caching works through boolean/temporal ops
# ---------------------------------------------------------------------------


class TestCompoundCaching:
    def test_G_and_atom_cache_shared_within_contract(self, counting_atom):
        """G(atom1 ∧ atom2) — each atom at each position judged once,
        even though G unrolls through the whole trace."""
        a1 = Atom("counting_atom", atom_type="sto")
        a2 = Atom("counting_atom", atom_type="sto")
        formula = G(And(a1, a2))

        from sponsio.models.trace import Trace

        trace = Trace(events=_trace(4))
        valuations = ground(trace)

        atom_cache: dict = {}
        eval_sto_confidence(formula, valuations, trace, t=0, atom_cache=atom_cache)

        # 4 positions × 2 distinct atoms = 8 calls (one per position per atom)
        assert counting_atom["calls"] == 8

        # Second call: everything cached, zero new judge calls
        eval_sto_confidence(formula, valuations, trace, t=0, atom_cache=atom_cache)
        assert counting_atom["calls"] == 8

    def test_not_wraps_cached_atom(self, counting_atom):
        """Not(atom) — negation wraps the atom result, cache still hits."""
        from sponsio.models.trace import Trace

        atom = Atom("counting_atom", atom_type="sto")
        formula = G(Not(atom))
        trace = Trace(events=_trace(3))
        valuations = ground(trace)

        atom_cache: dict = {}
        eval_sto_confidence(formula, valuations, trace, t=0, atom_cache=atom_cache)
        assert counting_atom["calls"] == 3

        eval_sto_confidence(formula, valuations, trace, t=0, atom_cache=atom_cache)
        assert counting_atom["calls"] == 3  # all cached
