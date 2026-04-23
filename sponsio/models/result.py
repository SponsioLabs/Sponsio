"""Verification result dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sponsio.formulas.formula import Formula
    from sponsio.patterns.library import DetFormula


# Valid violation categories. Reporting pipelines and enforcement
# strategies dispatch on this discriminator.
_VALID_VIOLATION_KINDS: frozenset[str] = frozenset(
    {"guarantee", "assumption", "composition", "refinement", "sto"}
)


@dataclass
class Violation:
    """A single contract violation detected during verification.

    Attributes:
        agent_id: Identifier of the agent whose contract was violated.
        formula: The formula that was violated.
        kind: Category of violation — ``"guarantee"``, ``"assumption"``,
            ``"composition"``, or ``"refinement"``.
        desc: Human-readable description of the violation.
        details: Additional context such as a counterexample trace.
    """

    agent_id: str
    formula: Formula | DetFormula
    kind: str  # "guarantee", "assumption", "composition"
    desc: str = ""
    details: str = ""

    def __post_init__(self) -> None:
        """Data-integrity checks (#16).

        An empty ``agent_id`` routes violations into the wrong bucket in
        per-agent aggregations (``report()`` groups by agent). An unknown
        ``kind`` silently skips strategy dispatch in ``EnforcementStrategy``
        (``if kind == "guarantee"`` / ``elif kind == "assumption"`` falls
        through to the default no-op), so the user sees a Violation object
        in the result but the runtime never applied a policy.
        """
        if not isinstance(self.agent_id, str) or not self.agent_id:
            raise ValueError(
                f"Violation.agent_id must be a non-empty string (got {self.agent_id!r})."
            )
        if not isinstance(self.kind, str) or self.kind not in _VALID_VIOLATION_KINDS:
            raise ValueError(
                f"Violation.kind={self.kind!r} is not recognized. "
                f"Valid values: {sorted(_VALID_VIOLATION_KINDS)}. "
                "An unknown kind silently skips enforcement dispatch."
            )

    def __repr__(self) -> str:
        return f"Violation(agent={self.agent_id!r}, kind={self.kind!r}, desc={self.desc!r})"


@dataclass
class CheckedProperty:
    """A property that was checked — passed or failed."""

    agent_id: str
    formula: Formula | DetFormula
    kind: str  # "guarantee", "assumption"
    satisfied: bool
    desc: str = ""


@dataclass
class RiskScore:
    """Quantified risk assessment from a verification run.

    Attributes:
        score: Overall risk score in [0.0, 1.0]. 0.0 = safe, 1.0 = critical.
        checks_run: Total number of contract checks performed.
        violations_count: Number of violations detected.
        severity_breakdown: Counts by violation kind
            (e.g. ``{"guarantee": 2, "composition": 1}``).
        timestamp: ISO format timestamp of the assessment.
    """

    score: float
    checks_run: int
    violations_count: int
    severity_breakdown: dict[str, int] = field(default_factory=dict)
    timestamp: str = ""


@dataclass
class VerificationResult:
    """Aggregate result of a verification run.

    Attributes:
        system_name: Name of the system that was verified.
        ok: True if no violations were found.
        violations: List of detected violations.
        checked: List of all properties that were evaluated.
        composition_ok: True if assume-guarantee composition holds.
        composition_details: Human-readable composition check lines.
        deployment_requirements: Agent assumptions not discharged by any
            peer guarantee — conditions the deployment environment must
            satisfy for the system to work correctly.
        summary: One-line summary of the verification outcome.
    """

    system_name: str
    ok: bool = True
    violations: list[Violation] = field(default_factory=list)
    checked: list[CheckedProperty] = field(default_factory=list)
    composition_ok: bool | None = None
    composition_details: list[str] = field(default_factory=list)
    deployment_requirements: list[str] = field(default_factory=list)
    summary: str = ""
    risk_score: RiskScore | None = None

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:
        status = "PASS" if self.ok else f"FAIL ({len(self.violations)} violations)"
        return f"VerificationResult({self.system_name!r}: {status})"

    def report(self) -> str:
        """Generates a human-readable report string."""
        lines = [repr(self)]
        for v in self.violations:
            lines.append(f"  - {v.desc or v.kind}: {v.details}")
        return "\n".join(lines)
