"""Tests for OutcomeBuilder and the structured EnforcementResult fields.

The point of these tests is *not* the human-facing message string —
that's already covered indirectly by the rest of the suite (we kept
the legacy phrasing byte-identical to avoid breaking anyone parsing
session-log lines). Here we lock in the **new structured fields**:

* ``rule_id`` — must be a stable identifier integrations can group on.
* ``agent_msg`` — must be tuned per ``action`` so the LLM reacts the
  right way (block voice vs retry voice).
* ``retry_hint`` — only populated for ``retrying``.
* ``alternatives`` — populated for ``blocked`` when callers pass them.

The hard contract these fields encode is ``action`` ↔ agent reaction.
A ``blocked`` outcome must read like "abandon this action"; a
``retrying`` outcome must read like "regenerate addressing X". If
those voices ever drift, the integration-side rendering also drifts
and we lose the block-vs-retry distinction at the agent level.
"""

from sponsio.formulas.formula import Atom, Implies, G
from sponsio.models.result import Violation
from sponsio.patterns.library import DetFormula
from sponsio.runtime.strategies import (
    ActionContext,
    DetBlock,
    EscalateToHuman,
    OutcomeBuilder,
    WarnOnly,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _det_violation(
    desc: str = "rate exceeded", pattern: str = "rate_limit"
) -> Violation:
    inner = G(Implies(Atom("called", "refund"), Atom("count_with", "refund", "5")))
    formula = DetFormula(formula=inner, desc=desc, pattern_name=pattern)
    return Violation(
        agent_id="bot",
        formula=formula,
        kind="guarantee",
        desc=desc,
    )


def _ctx() -> ActionContext:
    return ActionContext(agent_id="bot", action="refund")


# ---------------------------------------------------------------------------
# Field defaults — backwards compatibility
# ---------------------------------------------------------------------------


def test_legacy_constructor_still_works_with_only_action_and_message():
    """Pre-existing call sites that constructed
    ``EnforcementResult(action=..., message=...)`` must not break.
    """
    from sponsio.runtime.strategies import EnforcementResult

    r = EnforcementResult(action="blocked", message="legacy")
    assert r.rule_id == ""
    assert r.agent_msg == ""
    assert r.retry_hint is None
    assert r.alternatives == []


# ---------------------------------------------------------------------------
# Block voice vs retry voice — the core block/retry distinction
# ---------------------------------------------------------------------------


def test_block_outcome_tells_agent_to_abandon():
    out = DetBlock().enforce(_det_violation(), _ctx())
    assert out.action == "blocked"
    # Voice check: block messaging must steer the agent away from
    # retrying the same action. We assert on a stable cue word
    # ("Choose a different") that the OutcomeBuilder uses; if this
    # phrasing changes, agent behaviour changes too — surface it.
    assert "different" in out.agent_msg.lower()
    # No retry hint for blocked outcomes.
    assert out.retry_hint is None


# Sto-pipeline retry / redirect tests live in sponsio-cloud's test
# suite alongside ``RetryWithConstraint`` / ``RedirectToSafe`` /
# ``StoResult`` / ``FeedbackGenerator`` / ``OutcomeBuilder.for_sto_*``.


# ---------------------------------------------------------------------------
# rule_id — stable identifier for integration-side grouping
# ---------------------------------------------------------------------------


def test_rule_id_pulled_from_det_formula_pattern_name():
    out = DetBlock().enforce(_det_violation(pattern="rate_limit"), _ctx())
    assert out.rule_id == "rate_limit"


def test_rule_id_falls_back_to_violation_desc_when_no_formula_pattern():
    """When no ``DetFormula.pattern_name`` is attached, the builder must
    fall back to ``violation.desc`` as the rule_id."""
    v = Violation(
        agent_id="bot",
        formula=None,
        kind="guarantee",
        desc="rate_limit_Bash",
    )
    out = OutcomeBuilder.for_det_block(v, _ctx())
    assert out.rule_id == "rate_limit_Bash"


# ---------------------------------------------------------------------------
# alternatives + redirect/escalate/warn fields
# ---------------------------------------------------------------------------


def test_alternatives_round_trip_through_block_outcome():
    out = OutcomeBuilder.for_det_block(
        _det_violation(),
        _ctx(),
        alternatives=["refund_partial", "escalate_to_supervisor"],
    )
    assert out.alternatives == ["refund_partial", "escalate_to_supervisor"]


def test_escalate_carries_wait_voice_in_agent_msg():
    out = EscalateToHuman(reason="manual review required").enforce(
        _det_violation(), _ctx()
    )
    assert out.action == "escalated"
    # Wait/pause voice — distinct from block (abandon) and retry
    # (regenerate). Agent should hold, not switch tactics.
    assert "wait" in out.agent_msg.lower() or "paused" in out.agent_msg.lower()


def test_warn_carries_no_agent_msg():
    """WarnOnly is non-blocking; the agent shouldn't see anything.
    agent_msg must be empty so integrations don't inject log noise
    into the agent's context.
    """
    out = WarnOnly().enforce(_det_violation(), _ctx())
    assert out.action == "warned"
    assert out.agent_msg == ""


# ---------------------------------------------------------------------------
# Backwards compat: legacy retry_prompt mirrored alongside retry_hint
# ---------------------------------------------------------------------------


# ``test_retry_hint_and_legacy_retry_prompt_carry_same_value`` moved
# to sponsio-cloud's test suite — it exercises the sto retry path,
# which now lives in that package.
