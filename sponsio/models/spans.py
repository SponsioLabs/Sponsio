"""Structured observability spans for contract enforcement.

Each span captures one phase of the contract check pipeline with
timing, result, and contextual attributes. Spans form a tree
rooted at ``AgentTurnSpan``.

Naming convention: ``sponsio.<span_type>`` -- OTel-ready when we add export.

Span taxonomy::

    sponsio.agent_turn              # Root: one check_action() call
    +-- sponsio.contract_check      # One contract being evaluated
    |   +-- sponsio.precondition    # Assumption evaluation
    |   +-- sponsio.guarantee       # Guarantee formula evaluation
    |   +-- sponsio.violation       # Violation details (only if violated)
    |   +-- sponsio.enforcement     # Strategy application
    +-- sponsio.soft_check          # Sto pipeline results
        +-- sponsio.soft_eval       # Individual sto constraint
            +-- sponsio.violation
            +-- sponsio.enforcement
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterator


# ---------------------------------------------------------------------------
# Base span
# ---------------------------------------------------------------------------


@dataclass
class Span:
    """Base span with common fields.

    Attributes:
        span_type: OTel-style span name (e.g. ``"sponsio.agent_turn"``).
        start_time: Monotonic clock timestamp (``time.monotonic()``).
        end_time: Monotonic clock timestamp when the span finished.
        status: Outcome -- ``"ok"``, ``"violated"``, or ``"error"``.
        attributes: Arbitrary key-value pairs (OTel-compatible).
        children: Nested child spans forming the span tree.
    """

    span_type: str
    start_time: float
    end_time: float | None = None
    status: str = "ok"
    attributes: dict[str, Any] = field(default_factory=dict)
    children: list[Span] = field(default_factory=list)

    @property
    def duration_ms(self) -> float | None:
        """Wall-clock duration in milliseconds, or None if not yet ended."""
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000

    def finish(self, status: str | None = None) -> None:
        """Record the end time and optionally set the status."""
        self.end_time = time.monotonic()
        if status is not None:
            self.status = status

    def to_dict(self) -> dict[str, Any]:
        """Recursively serialize the span tree to a JSON-compatible dict."""
        d: dict[str, Any] = {
            "span_type": self.span_type,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "status": self.status,
        }
        if self.attributes:
            d["attributes"] = self.attributes
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d

    def to_flat_list(self) -> list[dict[str, Any]]:
        """Flatten the span tree to a list (depth-first, for OTel export)."""
        result: list[dict[str, Any]] = []
        self._flatten(result)
        return result

    def _flatten(self, acc: list[dict[str, Any]]) -> None:
        flat = {
            "span_type": self.span_type,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "attributes": dict(self.attributes),
            "child_count": len(self.children),
        }
        acc.append(flat)
        for child in self.children:
            child._flatten(acc)

    def walk(self) -> Iterator[Span]:
        """Depth-first iteration over this span and all descendants."""
        yield self
        for child in self.children:
            yield from child.walk()


# ---------------------------------------------------------------------------
# Typed span subclasses
# ---------------------------------------------------------------------------


@dataclass
class AgentTurnSpan(Span):
    """Root span for one ``check_action()`` call.

    Attributes:
        agent_id: Agent that triggered the check.
        action: The tool/action being checked.
        total_contracts_checked: Number of contracts evaluated.
        det_violations: Count of det violations detected.
        sto_violations: Count of sto violations detected.
        blocked: Whether the action was ultimately blocked.
    """

    agent_id: str = ""
    action: str = ""
    total_contracts_checked: int = 0
    det_violations: int = 0
    sto_violations: int = 0
    blocked: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["agent_id"] = self.agent_id
        d["action"] = self.action
        d["total_contracts_checked"] = self.total_contracts_checked
        d["det_violations"] = self.det_violations
        d["sto_violations"] = self.sto_violations
        d["blocked"] = self.blocked
        return d


@dataclass
class ContractCheckSpan(Span):
    """Span for evaluating one contract.

    Attributes:
        contract_name: Human-readable contract description.
        pipeline: ``"hard"`` or ``"sto"``.
    """

    contract_name: str = ""
    pipeline: str = "hard"

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["contract_name"] = self.contract_name
        d["pipeline"] = self.pipeline
        return d


@dataclass
class PreconditionSpan(Span):
    """Span for assumption (precondition) evaluation.

    Attributes:
        formula_desc: Human-readable description of the assumption.
        result: True if the assumption holds.
    """

    formula_desc: str = ""
    result: bool = True

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["formula_desc"] = self.formula_desc
        d["result"] = self.result
        return d


@dataclass
class GuaranteeSpan(Span):
    """Span for guarantee evaluation.

    Attributes:
        formula_desc: Human-readable description of the guarantee.
        result: True if the guarantee is satisfied.
    """

    formula_desc: str = ""
    result: bool = True

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["formula_desc"] = self.formula_desc
        d["result"] = self.result
        return d


@dataclass
class ViolationSpan(Span):
    """Span recording violation details.

    Attributes:
        kind: ``"assumption"``, ``"guarantee"``, ``"sto"``, or
            ``"liveness"``. The ``"liveness"`` kind is emitted only by
            :meth:`BaseGuard.finish_session` when a pending ``F(...)``
            obligation was never discharged before session end;
            runtime violations never use this kind.
        severity: ``"HIGH"``, ``"MEDIUM"``, or ``"LOW"``.
        evidence: Human-readable evidence string.
    """

    kind: str = ""
    severity: str = "HIGH"
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["kind"] = self.kind
        d["severity"] = self.severity
        d["evidence"] = self.evidence
        return d


@dataclass
class EnforcementSpan(Span):
    """Span for strategy application.

    Attributes:
        strategy: Strategy class name (e.g. ``"DetBlock"``).
        result_action: Enforcement outcome (``"blocked"``, ``"escalated"``,
            ``"retrying"``, ``"redirected"``).
    """

    strategy: str = ""
    result_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["strategy"] = self.strategy
        d["result_action"] = self.result_action
        return d


@dataclass
class StoCheckSpan(Span):
    """Container span for the sto evaluation pipeline."""

    pass


@dataclass
class StoEvalSpan(Span):
    """Span for a single sto constraint evaluation.

    Attributes:
        constraint_name: Name of the sto constraint.
        score: Evaluation score in [0, 1].
        threshold: Pass/fail threshold.
        passed: Whether the score met the threshold.
    """

    constraint_name: str = ""
    score: float = 0.0
    threshold: float = 0.5
    passed: bool = True

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["constraint_name"] = self.constraint_name
        d["score"] = self.score
        d["threshold"] = self.threshold
        d["passed"] = self.passed
        return d


# ---------------------------------------------------------------------------
# Tree rendering
# ---------------------------------------------------------------------------

# ANSI codes for optional colorization
_COLORS = {
    "ok": "\033[32m",  # green
    "violated": "\033[31m",  # red
    "error": "\033[33m",  # yellow
    "reset": "\033[0m",
    "dim": "\033[2m",
    "bold": "\033[1m",
}

_STATUS_ICONS = {
    "ok": "\u2713",  # checkmark
    "violated": "\u2717",  # cross
    "error": "!",
}

_SPAN_LABELS = {
    "sponsio.agent_turn": "Agent Turn",
    "sponsio.contract_check": "Contract",
    "sponsio.precondition": "Precondition",
    "sponsio.guarantee": "Guarantee",
    "sponsio.violation": "Violation",
    "sponsio.enforcement": "Enforcement",
    "sponsio.sto_check": "Soft Pipeline",
    "sponsio.sto_eval": "Soft Eval",
}


def render_tree(span: Span, colorize: bool = True, indent: int = 0) -> str:
    """Pretty-print a span tree.

    Args:
        span: Root span to render.
        colorize: Whether to include ANSI color codes.
        indent: Current indentation level (internal, for recursion).

    Returns:
        Multi-line string representation of the span tree.
    """
    lines: list[str] = []
    _render_span(span, lines, colorize, indent)
    return "\n".join(lines)


def _render_span(span: Span, lines: list[str], colorize: bool, indent: int) -> None:
    prefix = "  " * indent
    icon = _STATUS_ICONS.get(span.status, "?")
    label = _SPAN_LABELS.get(span.span_type, span.span_type)

    # Build the description based on span type
    desc = _span_description(span)
    dur = f"  [{span.duration_ms:.0f}ms]" if span.duration_ms is not None else ""

    # Status marker (suppress if description already conveys pass/fail)
    _pass_fail_words = {"SATISFIED", "VIOLATED", "PASSED", "FAILED"}
    desc_has_verdict = any(w in desc for w in _pass_fail_words)
    if span.status != "ok" and not desc_has_verdict:
        status_str = f" -- {span.status.upper()}"
    else:
        status_str = ""

    if colorize:
        c = _COLORS.get(span.status, "")
        r = _COLORS["reset"]
        d = _COLORS["dim"]
        line = f"{prefix}{c}{icon}{r} {label}: {desc}{status_str}{d}{dur}{r}"
    else:
        line = f"{prefix}{icon} {label}: {desc}{status_str}{dur}"

    lines.append(line)

    for child in span.children:
        _render_span(child, lines, colorize, indent + 1)


def _span_description(span: Span) -> str:
    """Extract a human-readable description from a typed span."""
    if isinstance(span, AgentTurnSpan):
        return f"{span.agent_id}.{span.action}" if span.action else span.agent_id
    if isinstance(span, ContractCheckSpan):
        return span.contract_name or "(unnamed)"
    if isinstance(span, PreconditionSpan):
        satisfied = "SATISFIED" if span.result else "VIOLATED"
        return f"{span.formula_desc} -- {satisfied}" if span.formula_desc else satisfied
    if isinstance(span, GuaranteeSpan):
        satisfied = "SATISFIED" if span.result else "VIOLATED"
        return f"{span.formula_desc} -- {satisfied}" if span.formula_desc else satisfied
    if isinstance(span, ViolationSpan):
        parts = [span.kind]
        if span.severity:
            parts.append(f"severity={span.severity}")
        return " | ".join(parts)
    if isinstance(span, EnforcementSpan):
        return (
            f"{span.strategy} -> {span.result_action}"
            if span.strategy
            else span.result_action
        )
    if isinstance(span, StoCheckSpan):
        return "sto pipeline"
    if isinstance(span, StoEvalSpan):
        passed = "PASSED" if span.passed else "FAILED"
        return f"{span.constraint_name} (score={span.score:.2f}, threshold={span.threshold:.2f}) -- {passed}"
    return ""


# ---------------------------------------------------------------------------
# SpanCollector -- context manager for building span trees
# ---------------------------------------------------------------------------


class SpanCollector:
    """Collects spans during a ``check_action()`` call.

    Usage::

        with SpanCollector("agent_1", "process_refund") as collector:
            # ... do checks, call collector.start_* / finish_* ...
            pass
        span_tree = collector.root  # AgentTurnSpan with all children

    The collector is synchronous (matches RuntimeMonitor's threading model).
    """

    def __init__(self, agent_id: str, action: str) -> None:
        self.root = AgentTurnSpan(
            span_type="sponsio.agent_turn",
            start_time=time.monotonic(),
            agent_id=agent_id,
            action=action,
        )
        self._stack: list[Span] = [self.root]

    def __enter__(self) -> SpanCollector:
        return self

    def __exit__(self, *exc: object) -> None:
        self.root.finish()

    @property
    def current(self) -> Span:
        """The currently open span (top of stack)."""
        return self._stack[-1]

    def start_span(self, span: Span) -> Span:
        """Add a child span to the current span and push it onto the stack."""
        self.current.children.append(span)
        self._stack.append(span)
        return span

    def finish_span(self, status: str | None = None) -> Span:
        """Finish the current span and pop it from the stack.

        Args:
            status: Optional status override.

        Returns:
            The finished span — the one that was just popped.

        Raises:
            RuntimeError: When called with no child spans on the stack
                (``len(_stack) <= 1``). Previously this silently returned
                the root, which let mismatched ``start_span`` / ``finish_span``
                pairs corrupt the whole tree without any signal. A
                miscount is almost always a bug in the caller — surface
                it loudly (#15).
        """
        if len(self._stack) <= 1:
            raise RuntimeError(
                "SpanCollector.finish_span: stack underflow — no child span "
                "is open, so there is nothing to finish. Check that every "
                "``finish_span`` has a matching earlier ``start_*_span`` / "
                "``start_span`` call. (Previously this call silently "
                "returned the root, which let mismatched pairs corrupt "
                "the span tree invisibly.)"
            )
        span = self._stack.pop()
        span.finish(status)
        return span

    # -- Convenience methods for common span types --

    def start_contract_check(
        self, contract_name: str, pipeline: str = "hard"
    ) -> ContractCheckSpan:
        """Start a contract check span."""
        span = ContractCheckSpan(
            span_type="sponsio.contract_check",
            start_time=time.monotonic(),
            contract_name=contract_name,
            pipeline=pipeline,
        )
        self.start_span(span)
        return span

    def start_precondition(self, formula_desc: str) -> PreconditionSpan:
        """Start a precondition evaluation span."""
        span = PreconditionSpan(
            span_type="sponsio.precondition",
            start_time=time.monotonic(),
            formula_desc=formula_desc,
        )
        self.start_span(span)
        return span

    def start_guarantee(self, formula_desc: str) -> GuaranteeSpan:
        """Start a guarantee evaluation span."""
        span = GuaranteeSpan(
            span_type="sponsio.enforcement",
            start_time=time.monotonic(),
            formula_desc=formula_desc,
        )
        self.start_span(span)
        return span

    def add_violation(
        self, kind: str, severity: str = "HIGH", evidence: str = ""
    ) -> ViolationSpan:
        """Add a violation span as a child of the current span (no push)."""
        span = ViolationSpan(
            span_type="sponsio.violation",
            start_time=time.monotonic(),
            status="violated",
            kind=kind,
            severity=severity,
            evidence=evidence,
        )
        span.finish("violated")
        self.current.children.append(span)
        return span

    def add_enforcement(self, strategy: str, result_action: str) -> EnforcementSpan:
        """Add an enforcement span as a child of the current span (no push)."""
        span = EnforcementSpan(
            span_type="sponsio.guarantee",
            start_time=time.monotonic(),
            strategy=strategy,
            result_action=result_action,
        )
        span.finish()
        self.current.children.append(span)
        return span

    def start_sto_check(self) -> StoCheckSpan:
        """Start the sto pipeline container span."""
        span = StoCheckSpan(
            span_type="sponsio.sto_check",
            start_time=time.monotonic(),
        )
        self.start_span(span)
        return span

    def start_sto_eval(
        self,
        constraint_name: str,
        score: float = 0.0,
        threshold: float = 0.5,
        passed: bool = True,
    ) -> StoEvalSpan:
        """Start a sto constraint evaluation span."""
        span = StoEvalSpan(
            span_type="sponsio.sto_eval",
            start_time=time.monotonic(),
            constraint_name=constraint_name,
            score=score,
            threshold=threshold,
            passed=passed,
        )
        self.start_span(span)
        return span
