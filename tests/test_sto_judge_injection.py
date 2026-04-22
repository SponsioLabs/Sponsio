"""Tests for the new `sto_judge` kwarg on sponsio.Sponsio / BaseGuard / Monitor.

Replaces the global ``set_default_judge`` singleton with explicit per-guard
injection. The ContextVar-based implementation means atoms still read the
judge via ``_require_judge()`` but it's scoped to the evaluation call,
not process-global — so two guards in the same process can use different
judges without interfering.
"""

from __future__ import annotations


from sponsio.formulas.formula import Atom
from sponsio.models.agent import Agent
from sponsio.models.contract import Contract
from sponsio.models.system import System
from sponsio.models.trace import Event
from sponsio.patterns.sto_catalog import set_default_judge
from sponsio.runtime.monitor import RuntimeMonitor


class FakeJudge:
    """Minimal BooleanJudge stand-in that returns a canned confidence."""

    def __init__(self, p_yes: float, name: str = "fake"):
        self.name = name
        self._p_yes = max(1e-9, min(1 - 1e-9, p_yes))
        self.calls = 0

    def judge(self, question):
        self.calls += 1
        return float(self._p_yes), "yes" if self._p_yes >= 0.5 else "no"


def _inject_free_contract(p_yes: float):
    """Helper: build a Contract that will flag iff judge returns low conf."""
    agent = Agent(id="bot")
    return Contract(
        agent=agent,
        enforcement=Atom("injection_free", atom_type="sto", context_scope="event"),
        beta=0.8,
    )


def _event_trace(content: str = "sample response"):
    """One llm_response event so the atom has content to evaluate."""
    return [Event(ts=0, agent="bot", event_type="llm_response", content=content)]


# ---------------------------------------------------------------------------
# Per-monitor judge overrides the global
# ---------------------------------------------------------------------------


class TestPerGuardJudge:
    def test_monitor_judge_wins_over_global(self):
        """Per-monitor sto_judge should take precedence over set_default_judge."""
        # Global judge says "clearly bad" — would fail β=0.8
        global_judge = FakeJudge(p_yes=0.1, name="global")
        set_default_judge(global_judge)
        try:
            # Per-monitor judge says "clearly fine" — should pass β=0.8
            monitor_judge = FakeJudge(p_yes=0.95, name="per_monitor")

            contract = _inject_free_contract(p_yes=0.95)
            sys = System(name="t")
            sys._contracts = [contract]
            mon = RuntimeMonitor(system=sys, sto_judge=monitor_judge)
            mon._trace.events.extend(_event_trace())

            results = mon.check_action("bot", "emit")
            # Per-monitor judge (0.95) ≥ β=0.8 → no violation
            assert not any(r.action == "retrying" for r in results)
            # Monitor judge was used, not the global one
            assert monitor_judge.calls == 1
            assert global_judge.calls == 0
        finally:
            set_default_judge(None)

    def test_falls_back_to_global_when_no_monitor_judge(self):
        global_judge = FakeJudge(p_yes=0.95, name="global")
        set_default_judge(global_judge)
        try:
            contract = _inject_free_contract(p_yes=0.95)
            sys = System(name="t")
            sys._contracts = [contract]
            # No sto_judge passed to monitor → falls back to global
            mon = RuntimeMonitor(system=sys)
            mon._trace.events.extend(_event_trace())

            mon.check_action("bot", "emit")
            assert global_judge.calls == 1
        finally:
            set_default_judge(None)

    def test_raises_when_neither_configured(self):
        set_default_judge(None)
        contract = _inject_free_contract(p_yes=0.5)
        sys = System(name="t")
        sys._contracts = [contract]
        mon = RuntimeMonitor(system=sys)  # no sto_judge
        mon._trace.events.extend(_event_trace())

        results = mon.check_action("bot", "emit")
        # The lifting error propagates via a failed enforcement, which
        # the det path then hands to _handle_enforcement_failure — we
        # should see SOME violation (blocked or retrying — the exact
        # action depends on how the evaluator raised).
        assert any(r.action in ("blocked", "escalated", "retrying") for r in results)

    def test_two_monitors_two_judges_in_same_process(self):
        """ContextVar-based judge isolation: two monitors can have
        different judges without interfering."""
        judge_a = FakeJudge(p_yes=0.95, name="a")  # permissive
        judge_b = FakeJudge(p_yes=0.1, name="b")  # strict

        contract = _inject_free_contract(p_yes=0.5)
        sys_a = System(name="a")
        sys_a._contracts = [contract]
        sys_b = System(name="b")
        sys_b._contracts = [contract]

        mon_a = RuntimeMonitor(system=sys_a, sto_judge=judge_a)
        mon_a._trace.events.extend(_event_trace())
        mon_b = RuntimeMonitor(system=sys_b, sto_judge=judge_b)
        mon_b._trace.events.extend(_event_trace())

        results_a = mon_a.check_action("bot", "emit")
        results_b = mon_b.check_action("bot", "emit")

        # A was permissive — no violation
        assert not any(r.action == "retrying" for r in results_a)
        # B was strict — violation
        assert any(r.action == "retrying" for r in results_b)
        # Each judge saw exactly one call, no cross-talk
        assert judge_a.calls == 1
        assert judge_b.calls == 1


# ---------------------------------------------------------------------------
# sponsio.Sponsio API threading
# ---------------------------------------------------------------------------


class TestInitThreadsJudge:
    def test_init_accepts_sto_judge_and_passes_to_monitor(self):
        import sponsio

        judge = FakeJudge(p_yes=0.9, name="from_init")
        guard = sponsio.Sponsio(
            agent_id="bot",
            contracts=[
                {
                    "enforcement": Atom(
                        "injection_free", atom_type="sto", context_scope="event"
                    ),
                    "beta": 0.8,
                },
            ],
            sto_judge=judge,
            verbose=False,
        )
        # The guard's monitor should hold the judge
        assert guard._monitor._sto_judge is judge

    def test_init_without_sto_judge_leaves_monitor_unset(self):
        import sponsio

        guard = sponsio.Sponsio(
            agent_id="bot",
            contracts=["tool `A` must precede `B`"],  # pure det, no judge needed
            verbose=False,
        )
        assert guard._monitor._sto_judge is None


# ---------------------------------------------------------------------------
# Context manager semantics — scoped judge, restored on exit
# ---------------------------------------------------------------------------


class TestJudgeContextManager:
    def test_use_judge_restores_on_exit(self):
        from sponsio.patterns.sto_catalog import _use_judge, _current_judge

        set_default_judge(None)
        try:
            judge_outer = FakeJudge(p_yes=0.5, name="outer")
            judge_inner = FakeJudge(p_yes=0.9, name="inner")

            with _use_judge(judge_outer):
                assert _current_judge.get() is judge_outer
                with _use_judge(judge_inner):
                    assert _current_judge.get() is judge_inner
                # Inner should be restored to outer
                assert _current_judge.get() is judge_outer
            # Outer restored to None (the initial default)
            assert _current_judge.get() is None
        finally:
            set_default_judge(None)
