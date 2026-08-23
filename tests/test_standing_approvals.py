"""Standing approvals: the human decision that outlives its escalation.

The loop under test: a person answers "Always allow" in the console; the
guard pulls that standing list at construction; ``EscalateToHuman`` releases
the covered call as ``observed`` instead of re-asking. The failure policy is
the whole point — anything short of an exact, authenticated match escalates
exactly as before. A standing approval can release a call, never block one.
"""

from __future__ import annotations

from sponsio.models.result import Violation
from sponsio.runtime.standing import (
    StandingApproval,
    StandingRegistry,
    load_from_cloud,
    registry,
)
from sponsio.runtime.strategies import ActionContext, EscalateToHuman


def _grant(agent="flow-agent", tool="issue_refund", contract="At most one refund"):
    return StandingApproval(
        agent=agent, tool=tool, contract=contract, approval_id="a1",
        decided_at="2026-08-23T00:00:00+00:00",
    )


def _violation(desc="issue_refund limited to 1 invocations"):
    class _F:  # the formula's own desc — the OTHER spelling the trace may carry
        desc = "At most one refund"

    return Violation(agent_id="flow-agent", formula=_F(), kind="guarantee", desc=desc)


def _ctx(agent="flow-agent", tool="issue_refund"):
    return ActionContext(agent_id=agent, action=tool, trace_length=3, metadata={})


# -- matching ---------------------------------------------------------------


def test_a_grant_matches_on_all_three_coordinates():
    reg = StandingRegistry()
    reg.load([_grant()])
    assert reg.covers("flow-agent", "issue_refund", "At most one refund") is not None
    assert reg.covers("OTHER-agent", "issue_refund", "At most one refund") is None
    assert reg.covers("flow-agent", "other_tool", "At most one refund") is None
    assert reg.covers("flow-agent", "issue_refund", "a different rule") is None


def test_matching_normalizes_like_the_console_does():
    reg = StandingRegistry()
    reg.load([_grant(contract="At  most one `refund`")])
    assert reg.covers("flow-agent", "issue_refund", "at most ONE refund") is not None


def test_either_spelling_of_the_contract_matches():
    """The console stores whichever desc the trace carried — authored
    sentence or compiled formula description. The caller offers both."""
    reg = StandingRegistry()
    reg.load([_grant(contract="At most one refund")])
    hit = reg.covers(
        "flow-agent", "issue_refund",
        "issue_refund limited to 1 invocations",  # formula spelling: no match
        "At most one refund",                      # authored: match
    )
    assert hit is not None


def test_no_contract_desc_never_matches():
    reg = StandingRegistry()
    reg.load([_grant()])
    assert reg.covers("flow-agent", "issue_refund") is None
    assert reg.covers("flow-agent", "issue_refund", "", None) is None


# -- the strategy honors a grant -------------------------------------------


def test_a_covered_escalation_is_released_as_observed_without_paging():
    paged = []
    strategy = EscalateToHuman(reason="human only", notify=lambda *a: paged.append(a))
    registry().load([_grant(contract="At most one refund")])
    try:
        out = strategy.enforce(_violation(), _ctx())
    finally:
        registry().clear()
    assert out.action == "observed"
    assert "standing approval" in out.message.lower()
    assert paged == []  # nobody gets paged for a covered call — that's the point


def test_an_uncovered_escalation_still_escalates_and_pages():
    paged = []
    strategy = EscalateToHuman(reason="human only", notify=lambda *a: paged.append(a))
    registry().clear()
    out = strategy.enforce(_violation(), _ctx())
    assert out.action == "escalated"
    assert len(paged) == 1


def test_a_grant_for_another_agent_never_releases_this_one():
    strategy = EscalateToHuman(reason="human only")
    registry().load([_grant(agent="someone-else", contract="At most one refund")])
    try:
        out = strategy.enforce(_violation(), _ctx())
    finally:
        registry().clear()
    assert out.action == "escalated"


# -- fail-closed fetch ------------------------------------------------------


def test_fetch_failure_loads_nothing_and_everything_escalates():
    class _DeadClient:
        configured = True

        def standing_approvals(self):
            raise RuntimeError("cloud is down")

    registry().load([_grant()])  # pre-seed to prove failure CLEARS
    assert load_from_cloud(_DeadClient(), quiet=True) == 0
    assert len(registry()) == 0


def test_unconfigured_client_loads_nothing():
    class _NoCreds:
        configured = False

    assert load_from_cloud(_NoCreds(), quiet=True) == 0
    assert len(registry()) == 0


def test_fetch_loads_the_server_rows():
    class _Client:
        configured = True

        def standing_approvals(self):
            return [
                {"agent": "flow-agent", "tool": "issue_refund",
                 "contract": "At most one refund", "id": "x",
                 "decidedAt": "2026-08-23T00:00:00+00:00"},
                {"agent": "a2", "contract": "no tool -> dropped"},
            ]

    try:
        assert load_from_cloud(_Client(), quiet=True) == 1
        assert registry().covers("flow-agent", "issue_refund", "At most one refund")
    finally:
        registry().clear()
