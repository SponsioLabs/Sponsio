"""Enforcement strategies for runtime constraint violations.

OSS strategies are det-only: DetBlock | EscalateToHuman | WarnOnly.
The sto-pipeline strategies (RetryWithConstraint, RedirectToSafe) and
their feedback / lesson formatting live in
``sponsio_cloud.sto.strategies`` — see ``pip install sponsio[cloud]``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from sponsio.models.result import Violation


@dataclass
class ActionContext:
    """Context about the action being checked.

    Attributes:
        agent_id: The agent attempting the action.
        action: The action/tool being invoked.
        trace_length: Number of events in the current trace.
        metadata: Additional context (args, content, etc.).
    """

    agent_id: str
    action: str
    trace_length: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass
class EnforcementResult:
    """Result of applying an enforcement strategy.

    Action discriminator semantics — what each value contracts about
    the next step. Integration adapters MUST honour these when
    rendering the result back into framework primitives:

    =============  ============  =================  ===============================
    action         tool runs?    agent informed?    expected agent reaction
    =============  ============  =================  ===============================
    ``blocked``    no            yes (refusal)      abandon this action
    ``retrying``   no            yes (lesson)       regenerate with ``retry_hint``
    ``escalated``  paused        depends            wait (may unblock via approval)
    ``redirected`` substituted   no (transparent)   continue, sees ``fallback_action``
    ``warned``     yes           no (log only)      no change
    ``allowed``    yes           —                  no enforcement, normal pass
    ``observed``   yes           —                  shadow-mode downgrade of any of
                                                    the above
    =============  ============  =================  ===============================

    Attributes:
        action: What enforcement action was taken. See the table above
            for the agent-side semantics each value commits to.
        message: Human-facing explanation — for logs, dashboards,
            session-log entries. NOT the right thing to inject into the
            agent's next turn (use ``agent_msg`` for that).
        retry_prompt: Legacy field — the discriminative feedback for
            sto retry. New code should populate ``retry_hint`` instead;
            we keep ``retry_prompt`` populated for backwards-compat
            with integrations that already read it.
        fallback_action: Substitute action for ``redirected`` — opaque
            payload the integration injects in place of the original
            tool result (string, dict, structured object — depends on
            framework).
        score: Sto-pipeline extra — confidence score that triggered
            this result. ``None`` for det.
        threshold: Sto-pipeline extra — the threshold the score missed.
            ``None`` for det.
        rule_id: Stable identifier for the contract / pattern that
            fired (``DetFormula.pattern_name``, contract id, sto atom
            name). Lets integrations group "violations of the same
            rule" without parsing free-text messages.
        agent_msg: What the agent should see on its next turn. Should
            be phrased to nudge the LLM toward the right reaction:
            blocked → "this action was rejected, choose another";
            retrying → "your output failed X, try again with Y".
            Defaults to empty; integrations fall back to ``message``
            when not set.
        retry_hint: Concrete "to fix this, do <X>" guidance attached
            to ``retrying`` outcomes. Distinct from ``agent_msg`` so
            integrations can format the two parts differently (e.g.
            agent_msg as a tool-error body, retry_hint as a follow-up
            instruction).
        alternatives: Suggested replacement actions for ``blocked`` /
            ``redirected``. Optional — integrations can render as a
            list to the agent ("try one of: <a>, <b>, <c>").
    """

    action: Literal[
        "blocked",
        "escalated",
        "retrying",
        "redirected",
        "allowed",
        "warned",
        "observed",
    ]
    message: str
    retry_prompt: str | None = None
    fallback_action: Any | None = None
    # Sto-pipeline extras — populated when a stochastic enforcement
    # triggered this result. Reporters / dashboards surface these to
    # explain "violation flagged, confidence 0.42 vs β=0.9".
    score: float | None = None
    threshold: float | None = None
    # Structured fields for integration-side rendering. All have
    # safe defaults so existing call sites that constructed
    # ``EnforcementResult(action=..., message=...)`` keep working;
    # ``OutcomeBuilder`` populates them for new code paths.
    rule_id: str = ""
    agent_msg: str = ""
    retry_hint: str | None = None
    alternatives: list[str] = field(default_factory=list)


def _rule_id_from_violation(violation: Violation) -> str:
    """Best-effort stable rule identifier for an outcome.

    Pulls ``pattern_name`` off ``DetFormula`` when present, otherwise
    falls back to ``violation.kind``. Sto evaluators set ``desc`` to
    the atom name (``injection_free``, ``tone_match``) — that becomes
    the rule_id when no formula is attached.
    """
    formula = getattr(violation, "formula", None)
    pattern_name = getattr(formula, "pattern_name", "") if formula else ""
    if pattern_name:
        return pattern_name
    if violation.desc:
        return violation.desc
    return violation.kind


class OutcomeBuilder:
    """Builds structured ``EnforcementResult`` payloads.

    Centralises the message / agent_msg / hint phrasing so each
    strategy doesn't reinvent string formatting. Two reasons we keep
    this separate from the strategies:

    1. **Message phrasing decides agent behaviour.** The same
       constraint can produce "this is forbidden" (agent abandons) or
       "this didn't pass X, try Y" (agent retries). Putting the
       phrasing here lets us tune block / retry voice consistently.
    2. **Integrations need structured fields.** Free-text messages
       force adapters to regex-parse to extract anything useful. The
       builder fills ``rule_id`` / ``alternatives`` / ``retry_hint``
       so adapters can render natively (Claude Agent
       ``permissionDecision``, OpenAI synthetic tool result, CrewAI
       error dict) without string archaeology.
    """

    @staticmethod
    def for_det_block(
        violation: Violation,
        context: ActionContext,
        alternatives: list[str] | None = None,
    ) -> EnforcementResult:
        rule = _rule_id_from_violation(violation)
        desc = violation.desc or violation.kind
        message = (
            f"BLOCKED: {context.agent_id}.{context.action} — "
            f"det constraint violated: {desc}"
        )
        agent_msg = (
            f"The action `{context.action}` was rejected by policy "
            f"({rule}): {desc}. Choose a different approach."
        )
        return EnforcementResult(
            action="blocked",
            message=message,
            rule_id=rule,
            agent_msg=agent_msg,
            alternatives=list(alternatives or []),
        )

    @staticmethod
    def for_det_escalate(
        violation: Violation,
        context: ActionContext,
        reason: str = "",
    ) -> EnforcementResult:
        rule = _rule_id_from_violation(violation)
        why = reason or violation.desc or "det constraint violation"
        message = (
            f"ESCALATED: {context.agent_id}.{context.action} — "
            f"awaiting human approval: {why}"
        )
        agent_msg = (
            f"The action `{context.action}` is paused awaiting human "
            f"approval ({rule}). Wait for the approval signal."
        )
        return EnforcementResult(
            action="escalated",
            message=message,
            rule_id=rule,
            agent_msg=agent_msg,
        )

    @staticmethod
    def for_det_warn(
        violation: Violation,
        context: ActionContext,
    ) -> EnforcementResult:
        rule = _rule_id_from_violation(violation)
        desc = violation.desc or violation.kind
        message = (
            f"WARNING (non-blocking): {context.agent_id}.{context.action} — {desc}"
        )
        return EnforcementResult(
            action="warned",
            message=message,
            rule_id=rule,
        )

    # OutcomeBuilder.for_sto_* helpers (for_sto_retry,
    # for_sto_block_after_max, for_sto_redirect) live in the
    # proprietary sponsio-cloud package alongside the strategies that
    # consume them. The OSS engine builds only det outcomes here.


@runtime_checkable
class EnforcementStrategy(Protocol):
    """Protocol for enforcement strategies."""

    def enforce(
        self, violation: Violation, context: ActionContext
    ) -> EnforcementResult:
        """Applies the enforcement strategy to a violation.

        Args:
            violation: The detected violation.
            context: Context about the action that triggered the violation.

        Returns:
            An EnforcementResult describing the enforcement action taken.
        """
        ...


# --- Det constraint strategies (formal, binary) ---


class DetBlock:
    """Blocks execution immediately when a det constraint is violated.

    Use for high-risk actions: transfers, data deletion, irreversible operations.
    """

    def enforce(
        self, violation: Violation, context: ActionContext
    ) -> EnforcementResult:
        return OutcomeBuilder.for_det_block(violation, context)


class EscalateToHuman:
    """Pauses execution and escalates to a human for approval.

    Use for enterprise workflows requiring human-in-the-loop oversight.
    """

    def __init__(self, reason: str = "") -> None:
        self._reason = reason

    def enforce(
        self, violation: Violation, context: ActionContext
    ) -> EnforcementResult:
        return OutcomeBuilder.for_det_escalate(violation, context, reason=self._reason)


class WarnOnly:
    """Records the violation but allows execution to continue.

    Use for non-critical constraints where you want visibility
    without blocking the agent (e.g. rate limits on logging tools).
    """

    def enforce(
        self, violation: Violation, context: ActionContext
    ) -> EnforcementResult:
        return OutcomeBuilder.for_det_warn(violation, context)


# Sto-pipeline strategies (RetryWithConstraint, RedirectToSafe) live
# in the proprietary ``sponsio-cloud`` package — see
# ``sponsio_cloud/sto/strategies.py``. OSS exports only the
# deterministic strategies above (DetBlock / EscalateToHuman /
# WarnOnly).
