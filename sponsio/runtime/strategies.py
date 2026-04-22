"""Enforcement strategies for runtime constraint violations.

Det violations -> DetBlock | EscalateToHuman ONLY.
Sto violations -> RetryWithConstraint | RedirectToSafe ONLY.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from sponsio.models.result import Violation
from sponsio.runtime.evaluators import StoResult
from sponsio.runtime.feedback import FeedbackGenerator


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

    Attributes:
        action: What enforcement action was taken. The enforcing
            actions are ``"blocked"``, ``"escalated"``, ``"retrying"``,
            and ``"redirected"``. The non-enforcing actions
            ``"allowed"``, ``"warned"`` (explicit non-blocking warning),
            and ``"observed"`` (shadow-mode downgrade) are emitted by
            the monitor itself to surface passes and would-be violations
            to callbacks without actually blocking execution.
        message: Human-readable explanation.
        retry_prompt: Discriminative feedback for retry (sto path only).
        fallback_action: Substitute action for redirect (sto path only).
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
        return EnforcementResult(
            action="blocked",
            message=(
                f"BLOCKED: {context.agent_id}.{context.action} — "
                f"det constraint violated: {violation.desc or violation.kind}"
            ),
        )


class EscalateToHuman:
    """Pauses execution and escalates to a human for approval.

    Use for enterprise workflows requiring human-in-the-loop oversight.
    """

    def __init__(self, reason: str = "") -> None:
        self._reason = reason

    def enforce(
        self, violation: Violation, context: ActionContext
    ) -> EnforcementResult:
        reason = self._reason or violation.desc or "det constraint violation"
        return EnforcementResult(
            action="escalated",
            message=(
                f"ESCALATED: {context.agent_id}.{context.action} — "
                f"awaiting human approval: {reason}"
            ),
        )


class WarnOnly:
    """Records the violation but allows execution to continue.

    Use for non-critical constraints where you want visibility
    without blocking the agent (e.g. rate limits on logging tools).
    """

    def enforce(
        self, violation: Violation, context: ActionContext
    ) -> EnforcementResult:
        return EnforcementResult(
            action="warned",
            message=(
                f"WARNING (non-blocking): {context.agent_id}.{context.action} — "
                f"{violation.desc or violation.kind}"
            ),
        )


# --- Sto constraint strategies (probabilistic, graded) ---


class RetryWithConstraint:
    """Retries the action with discriminative feedback on sto violation.

    Generates a targeted re-prompt using the FeedbackGenerator and injects
    it for the agent to regenerate its output.
    """

    def __init__(
        self,
        max_retries: int = 2,
        feedback_generator: FeedbackGenerator | None = None,
    ) -> None:
        self._max_retries = max_retries
        self._feedback_generator = feedback_generator or FeedbackGenerator()
        self._retry_counts: dict[str, int] = {}

    @property
    def max_retries(self) -> int:
        return self._max_retries

    def enforce(
        self,
        violation: Violation,
        context: ActionContext,
        sto_result: StoResult | None = None,
        feedback_template: str | None = None,
    ) -> EnforcementResult:
        """Enforces via retry with discriminative feedback.

        Args:
            violation: The detected sto violation.
            context: Action context.
            sto_result: The StoResult from scored evaluation.
            feedback_template: Optional template override for feedback.

        Returns:
            EnforcementResult with retry_prompt if retries remain,
            or a blocked result if max retries exceeded.
        """
        key = f"{context.agent_id}.{context.action}.{violation.desc}"
        count = self._retry_counts.get(key, 0)

        if count >= self._max_retries:
            self._retry_counts.pop(key, None)
            return EnforcementResult(
                action="blocked",
                message=(
                    f"BLOCKED after {self._max_retries} retries: "
                    f"{context.agent_id}.{context.action} — {violation.desc}"
                ),
            )

        self._retry_counts[key] = count + 1

        prompt = None
        if sto_result is not None:
            prompt = self._feedback_generator.generate(
                prop_name=violation.desc or violation.kind,
                result=sto_result,
                template=feedback_template,
            )

        return EnforcementResult(
            action="retrying",
            message=(
                f"RETRY ({count + 1}/{self._max_retries}): "
                f"{context.agent_id}.{context.action} — {violation.desc}"
            ),
            retry_prompt=prompt,
        )

    def reset(self, key: str | None = None) -> None:
        """Resets retry counts. If key is None, resets all."""
        if key is None:
            self._retry_counts.clear()
        else:
            self._retry_counts.pop(key, None)


class RedirectToSafe:
    """Substitutes a safe alternative action when a sto constraint is violated.

    Use when there is a clear, safe fallback for the violated action.
    """

    def __init__(self, fallback: Any = None, fallback_message: str = "") -> None:
        self._fallback = fallback
        self._fallback_message = fallback_message

    def enforce(
        self, violation: Violation, context: ActionContext
    ) -> EnforcementResult:
        return EnforcementResult(
            action="redirected",
            message=(
                f"REDIRECTED: {context.agent_id}.{context.action} — "
                f"{self._fallback_message or violation.desc or 'sto constraint violated'}"
            ),
            fallback_action=self._fallback,
        )
