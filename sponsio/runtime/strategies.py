"""Enforcement strategies for runtime constraint violations.

This build ships det-only strategies: DetBlock | EscalateToHuman |
WarnOnly. The sto-pipeline strategies (RetryWithConstraint,
RedirectToSafe) and their feedback / lesson formatting are an
extension point; no implementation is included.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol, runtime_checkable

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

    Action discriminator semantics. What each value contracts about
    the next step. Integration adapters MUST honour these when
    rendering the result back into framework primitives:

    =============  ============  =================  ===============================
    action         tool runs?    agent informed?    expected agent reaction
    =============  ============  =================  ===============================
    ``blocked``    no            yes (refusal)      abandon this action
    ``escalated``  no            yes (refusal)      abandon; humans get notified
    ``redirected`` substituted   no (transparent)   continue, sees ``fallback_action``
    ``warned``     yes           no (log only)      no change (user wants this)
    ``observed``   yes           no                 shadow-mode wrapper around any
                                                    above outcome (mode is observe)
    ``allowed``    yes           n/a                no violation, normal pass
    ``retrying``   reserved      reserved           reserved: sto pipeline only,
                                                    not reachable in OSS (no sto
                                                    evaluator ships in this build)
    =============  ============  =================  ===============================

    On the close calls between adjacent actions:

    * ``blocked`` vs ``escalated``: both refuse and tell the agent to
      abandon. The difference is side effects. ``EscalateToHuman``
      fires user-supplied notifiers (Slack, email, paging); ``DetBlock``
      does not. With no notifier wired up, the outcomes look identical
      to the agent; dashboards still distinguish them by the action
      literal.
    * ``warned`` vs ``observed``: both let the tool run without telling
      the agent. ``warned`` is an explicit user choice ("this rule
      should log but never block"); ``observed`` is a runtime
      downgrade ("the guard is in observe mode so this would-be block
      becomes a log entry"). Operators reading a report can tell from
      the literal whether to expect the rule to keep firing (warned)
      or whether flipping to enforce will start blocking (observed).

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

    @staticmethod
    def for_det_redirect(
        violation: Violation,
        context: ActionContext,
        safe: str,
        message: str = "",
    ) -> EnforcementResult:
        """Outcome for ``RedirectToSafe``.

        The unsafe action is suppressed (caller rolls it back from the
        trace, same as a block) and the integration adapter substitutes
        the ``safe`` tool. ``fallback_action`` carries the safe tool
        name so the adapter knows what to invoke; ``agent_msg`` stays
        empty by default — the substitution is meant to be transparent
        to the model, which simply sees the safe tool's result.
        """
        rule = _rule_id_from_violation(violation)
        msg = (
            f"REDIRECTED: {context.agent_id}.{context.action} → {safe}"
            + (f" ({message})" if message else "")
        )
        return EnforcementResult(
            action="redirected",
            message=msg,
            rule_id=rule,
            fallback_action=safe,
            agent_msg="",
            alternatives=[safe],
        )

    # OutcomeBuilder.for_sto_* helpers (for_sto_retry,
    # for_sto_block_after_max) are an extension point not part of this
    # build, alongside the strategies that consume them.


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
    """Refuse the call AND fire user-supplied notifiers (Slack, email, …).

    Semantically distinct from :class:`DetBlock`. ``DetBlock`` is a
    silent refuse — the agent gets a refusal message and that's the
    end of the story. ``EscalateToHuman`` is a refuse that also
    *reaches out*: webhook fires, Slack message posts, oncall pages.
    Without at least one notifier, this collapses to "DetBlock with a
    different message" — accept that explicitly by not passing
    ``notify`` if a notification side effect isn't wired up yet.

    The strategy never *waits* for human approval to come back
    (Sponsio doesn't carry an approval store in OSS). The outcome
    surfaces as ``action="escalated"`` so dashboards / reporters can
    distinguish "this just got refused" from "this just got refused
    AND a human was paged."

    Args:
        reason: Free text rendered into the agent message and passed
            verbatim to each notifier. Should describe *why* this
            specific contract is human-only ("amount > $50k requires
            CFO approval", "production database write outside change
            window").
        notify: Optional notifier or list of notifiers. Each is called
            with ``(violation, context, reason)`` whenever ``enforce``
            fires. Exceptions raised by a notifier are caught and
            logged (via ``warnings.warn``) so a Slack outage doesn't
            crash the agent loop — but the escalation outcome still
            returns, the AI still sees the refusal. Notifier
            signature: ``(violation: Violation, context: ActionContext,
            reason: str) -> None``.

    Example::

        def slack_oncall(violation, context, reason):
            slack.post(
                channel="#oncall",
                text=f"Sponsio escalation: {context.agent_id}."
                     f"{context.action} blocked. Reason: {reason}",
            )

        guard = Sponsio(
            policy={"large-refund-rule": EscalateToHuman(
                reason="refund > $10k requires CFO approval",
                notify=[slack_oncall, email_finance_lead],
            )},
            ...
        )
    """

    def __init__(
        self,
        reason: str = "",
        notify: Callable | list[Callable] | None = None,
    ) -> None:
        self._reason = reason
        if notify is None:
            self._notifiers: list[Callable] = []
        elif callable(notify):
            self._notifiers = [notify]
        elif isinstance(notify, list) and all(callable(n) for n in notify):
            self._notifiers = list(notify)
        else:
            raise TypeError(
                "EscalateToHuman.notify must be a callable, list of "
                "callables, or None."
            )

    def enforce(
        self, violation: Violation, context: ActionContext
    ) -> EnforcementResult:
        # Fire notifiers *before* returning the outcome so a synchronous
        # webhook (Slack, PagerDuty) lands while the trace is fresh.
        # Failures are isolated per-notifier: one broken hook doesn't
        # silence the others, and none of them takes the agent loop
        # down. The outcome itself is unchanged — escalation is still
        # an escalation even if every notifier was offline.
        import warnings as _warnings

        for fn in self._notifiers:
            try:
                fn(violation, context, self._reason)
            except Exception as exc:  # noqa: BLE001 — notifier sandbox
                _warnings.warn(
                    f"EscalateToHuman notifier {getattr(fn, '__name__', repr(fn))} "
                    f"raised {type(exc).__name__}: {exc}. Escalation outcome "
                    f"is still surfacing; fix the notifier to silence this.",
                    RuntimeWarning,
                    stacklevel=2,
                )
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


class RedirectToSafe:
    """Substitute the offending call with a pre-declared safe tool.

    Use when an action that's banned in this specific context has a
    pre-approved equivalent: ``issue_refund`` → ``log_refund_request``,
    ``run_sql_destructive`` → ``select_only_dryrun``, ``send_email`` →
    ``draft_email``. The model keeps making progress; it just can't do
    the unsafe thing.

    Both ``unsafe`` and ``safe`` must be tools the integration adapter
    already knows about — Sponsio doesn't synthesize tools. The safe
    tool should accept the same arguments as the unsafe one (or the
    adapter must coerce them); a schema mismatch will confuse the
    model when it sees an unexpected result shape.

    The trace is rolled back the same way ``DetBlock`` rolls back, so
    downstream contracts (count limits, ordering) don't double-count
    the attempted call. The adapter records the substitute tool's
    real call via the normal ``guard_before(safe, args)`` path, so
    audit logs honestly show what executed.
    """

    def __init__(self, safe: str, message: str = "") -> None:
        if not isinstance(safe, str) or not safe.strip():
            raise ValueError(
                "RedirectToSafe: 'safe' must be a non-empty tool name."
            )
        self._safe = safe
        self._message = message

    def enforce(
        self, violation: Violation, context: ActionContext
    ) -> EnforcementResult:
        return OutcomeBuilder.for_det_redirect(
            violation, context, safe=self._safe, message=self._message
        )


# Sto-pipeline strategy ``RetryWithConstraint`` is an extension point
# not part of this build. Only the deterministic strategies above
# (DetBlock / EscalateToHuman / WarnOnly / RedirectToSafe) are
# exported here.
