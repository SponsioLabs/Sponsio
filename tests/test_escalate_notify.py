"""Tests for ``EscalateToHuman`` notifier side effect.

The architectural premise pinned down here: ``EscalateToHuman`` is
semantically distinct from ``DetBlock`` because it carries real side
effects (Slack / email / paging). Without those notifiers wired, it
*does* collapse to "block with different wording". that's
acceptable, but the notifier capability must work when supplied.

Contract:

* No notifiers: enforce returns an ``escalated`` outcome and fires
  no side effects (functionally identical to DetBlock except for the
  action literal).
* Single callable notifier: invoked once with ``(violation, context,
  reason)``.
* List of notifiers: each invoked in order with the same triple.
* Notifier raising an exception does NOT crash ``enforce``. the
  exception is converted to a ``RuntimeWarning`` and the remaining
  notifiers still fire, the escalation outcome still returns. Agent
  loops don't go down because Slack is flaky.
* Wrong types in ``notify`` arg raise ``TypeError`` at construction
  (fail loud at config time, not at first violation).
* End-to-end through ``Sponsio()`` policy dict: notifier fires when
  the guarded contract violates.
"""

from __future__ import annotations

import warnings

import pytest

from sponsio import contract
from sponsio.core import Sponsio
from sponsio.models.result import Violation
from sponsio.patterns import tool_allowlist
from sponsio.runtime.strategies import (
    ActionContext,
    EscalateToHuman,
)


def _make_violation(desc: str = "test violation") -> Violation:
    return Violation(
        agent_id="bot",
        formula=None,
        kind="guarantee",
        desc=desc,
        details=f"runtime violation: {desc}",
    )


def _make_context(action: str = "do_thing") -> ActionContext:
    return ActionContext(agent_id="bot", action=action)


class TestEscalateNoNotify:
    def test_no_notifiers_still_returns_escalated(self) -> None:
        """Backwards compatibility: existing call sites that just do
        ``EscalateToHuman()`` keep working unchanged."""
        s = EscalateToHuman(reason="needs CFO")
        result = s.enforce(_make_violation(), _make_context())
        assert result.action == "escalated"
        assert "CFO" in result.message or "CFO" in result.agent_msg


class TestEscalateSingleNotifier:
    def test_single_callable_fires_once(self) -> None:
        calls: list[tuple] = []

        def hook(violation, context, reason):
            calls.append((violation.desc, context.action, reason))

        s = EscalateToHuman(reason="needs review", notify=hook)
        s.enforce(_make_violation("refund > 10k"), _make_context("issue_refund"))

        assert len(calls) == 1
        assert calls[0] == ("refund > 10k", "issue_refund", "needs review")


class TestEscalateMultipleNotifiers:
    def test_list_of_notifiers_all_fire_in_order(self) -> None:
        order: list[str] = []

        def slack(*_args):
            order.append("slack")

        def email(*_args):
            order.append("email")

        def pagerduty(*_args):
            order.append("pagerduty")

        s = EscalateToHuman(notify=[slack, email, pagerduty])
        s.enforce(_make_violation(), _make_context())

        assert order == ["slack", "email", "pagerduty"]


class TestEscalateNotifierIsolation:
    def test_failing_notifier_does_not_crash_enforce(self) -> None:
        """A broken Slack webhook must not take the agent loop down."""
        survived: list[str] = []

        def broken(*_args):
            raise RuntimeError("slack outage")

        def alive(*_args):
            survived.append("alive")

        s = EscalateToHuman(notify=[broken, alive])

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = s.enforce(_make_violation(), _make_context())

        # Outcome still surfaces. the agent gets the escalation it
        # would have got even if Slack were healthy.
        assert result.action == "escalated"
        # The second notifier ran even though the first raised.
        assert survived == ["alive"]
        # A RuntimeWarning names the offending notifier so the
        # operator can fix it.
        warned = [
            w
            for w in caught
            if issubclass(w.category, RuntimeWarning) and "broken" in str(w.message)
        ]
        assert len(warned) == 1


class TestEscalateInvalidNotify:
    def test_rejects_non_callable_at_construction(self) -> None:
        with pytest.raises(TypeError, match="notify"):
            EscalateToHuman(notify=42)  # type: ignore[arg-type]

    def test_rejects_list_containing_non_callable(self) -> None:
        def ok(*_):
            pass

        with pytest.raises(TypeError, match="notify"):
            EscalateToHuman(notify=[ok, "not_a_callable"])  # type: ignore[list-item]


class TestEscalateEndToEndThroughGuard:
    def test_notifier_fires_when_guarded_contract_violates(self) -> None:
        """Verify the notifier actually fires through the full guard
        path, not just the strategy in isolation. The pattern: contract
        explicitly maps its desc to ``EscalateToHuman`` via the
        ``policy={...}`` kwarg."""
        events: list[str] = []

        def notify(violation, context, reason):
            events.append(f"{context.agent_id}.{context.action}:{reason}")

        # Use ``policy={...}`` to bind the strategy to the contract's
        # desc. Without this, the default-policy builder would assign
        # DetBlock. same path users take in production when they
        # want a specific strategy for a specific rule.
        formula = tool_allowlist(["search"])
        # The ``policy`` dict is keyed by the formula desc. same
        # lookup the monitor uses. tool_allowlist's auto-generated
        # desc is what the policy must match.
        guard = Sponsio(
            agent_id="bot",
            contracts=[contract("approved tools").guarantees(formula)],
            policy={
                formula.desc: EscalateToHuman(reason="oncall review", notify=notify)
            },
            mode="enforce",
            verbose=False,
        )
        # Wrong tool → contract fires → EscalateToHuman runs → notify
        # fires.
        result = guard.guard_before("rm_rf", {})
        assert any(v.action == "escalated" for v in result.det_violations)
        assert events == ["bot.rm_rf:oncall review"]
