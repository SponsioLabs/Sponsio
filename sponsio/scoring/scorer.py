"""Safety scoring for agent tool configurations.

Evaluates a set of tool definitions for safety risks using a deduction-based
scoring model.  Starts at 100 and deducts points for configuration red flags
(Category 1) and contract compliance gaps (Category 2).

Usage::

    from sponsio.scoring import score_tools, ToolDef

    tools = [
        ToolDef("query_users", "Read user records from database",
                {"user_id": "str"}),
        ToolDef("send_email", "Send email to a recipient",
                {"to": "str", "body": "str"}),
    ]
    report = score_tools(tools)
    print(report.grade, report.score)  # "D", 62
    for d in report.deductions:
        print(d)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence, Set
from urllib.parse import quote


# ---------------------------------------------------------------------------
# Input type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolDef:
    """A tool definition to be scored.

    Attributes:
        name: Tool name (e.g. ``"send_email"``).
        description: Human-readable description of what the tool does.
        parameters: Parameter names mapped to type hints or descriptions.
    """

    name: str
    description: str
    parameters: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass
class Deduction:
    """A single scoring deduction.

    Attributes:
        check_id: Identifier for the check that triggered this deduction.
        points_lost: Points deducted (positive integer).
        description: What the check found.
        affected_tools: Tool names involved.
        suggested_contract: NL contract string that would fix this issue.
    """

    check_id: str
    points_lost: int
    description: str
    affected_tools: List[str]
    suggested_contract: str

    def __repr__(self) -> str:
        return (
            f"Deduction({self.check_id!r}, -{self.points_lost}, "
            f"tools={self.affected_tools!r})"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_id": self.check_id,
            "points_lost": self.points_lost,
            "description": self.description,
            "affected_tools": self.affected_tools,
            "suggested_contract": self.suggested_contract,
        }


_GRADE_COLORS: Dict[str, str] = {
    "A+": "brightgreen",
    "A": "green",
    "B": "yellowgreen",
    "C": "yellow",
    "D": "orange",
    "F": "red",
}


def badge_url(grade: str, score: int) -> str:
    """Build a shields.io badge URL for a safety grade."""
    color = _GRADE_COLORS.get(grade, "lightgrey")
    label = quote(f"{grade} {score}%")
    return f"https://img.shields.io/badge/Sponsio_Safety-{label}-{color}"


@dataclass
class ScoringReport:
    """Result of scoring a tool configuration.

    Attributes:
        score: Safety score in [0, 100].
        grade: Letter grade (A+ through F).
        agent_name: Name of the agent being scored.
        timestamp: ISO-format UTC timestamp of when the report was created.
        deductions: Individual deductions applied.
        suggested_contracts: NL contract strings that would mitigate risks.
    """

    score: int
    grade: str
    agent_name: str = "anonymous"
    timestamp: str = ""
    deductions: List[Deduction] = field(default_factory=list)
    suggested_contracts: List[str] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"ScoringReport(score={self.score}, grade={self.grade!r})"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "grade": self.grade,
            "agent_name": self.agent_name,
            "timestamp": self.timestamp,
            "deductions": [d.to_dict() for d in self.deductions],
            "suggested_contracts": self.suggested_contracts,
        }

    def to_badge_url(self) -> str:
        return badge_url(self.grade, self.score)


# ---------------------------------------------------------------------------
# Regex patterns for classification
# ---------------------------------------------------------------------------

_WRITE_RE = re.compile(
    r"write|delete|send|post|execute|create|update|remove|insert|drop|put|push",
    re.IGNORECASE,
)
_READ_RE = re.compile(
    r"read|get|check|verify|validate|review|fetch|query|list|describe|search|lookup",
    re.IGNORECASE,
)
_EXTERNAL_RE = re.compile(
    r"email|slack|send|webhook|notify|message|sms|http|post_to|publish",
    re.IGNORECASE,
)
_CONFIRM_RE = re.compile(
    r"confirm|approve|review|authorize|consent",
    re.IGNORECASE,
)
_SENSITIVE_READ_RE = re.compile(
    r"user|customer|patient|account|payment|employee|record|profile|credential",
    re.IGNORECASE,
)
_DATA_SOURCE_RE = re.compile(
    r"db|database|query|fetch|read|table|store|select",
    re.IGNORECASE,
)
_PRIVILEGED_RE = re.compile(
    r"admin|transfer|delete|deploy|root|sudo|migrate|escalat",
    re.IGNORECASE,
)
_AUTH_RE = re.compile(
    r"auth|permission|role|verify_identity|login|token|session|credential|acl",
    re.IGNORECASE,
)
_FINANCIAL_RE = re.compile(
    r"pay|transfer|charge|refund|invoice|debit|credit|withdraw|deposit|bill",
    re.IGNORECASE,
)

# Domain keywords — used to group tools by the resource they operate on.
_DOMAIN_KEYWORDS: Dict[str, "re.Pattern[str]"] = {
    "user": re.compile(r"user|customer|profile|account|member", re.IGNORECASE),
    "file": re.compile(r"file|document|upload|download|attachment|blob", re.IGNORECASE),
    "database": re.compile(r"db|database|table|record|row|query|sql", re.IGNORECASE),
    "email": re.compile(r"email|mail|inbox|outbox", re.IGNORECASE),
    "payment": re.compile(
        r"pay|invoice|charge|refund|billing|transaction", re.IGNORECASE
    ),
    "deploy": re.compile(r"deploy|release|build|pipeline|ci|cd", re.IGNORECASE),
    "message": re.compile(r"message|chat|slack|notification|sms", re.IGNORECASE),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text(tool: ToolDef) -> str:
    """Combine name + description for pattern matching."""
    return f"{tool.name} {tool.description}"


def _domains(tool: ToolDef) -> Set[str]:
    """Infer resource domains from a tool's name and description."""
    text = _text(tool)
    return {name for name, pat in _DOMAIN_KEYWORDS.items() if pat.search(text)}


def _is_write(tool: ToolDef) -> bool:
    return bool(_WRITE_RE.search(_text(tool)))


def _is_read(tool: ToolDef) -> bool:
    return bool(_READ_RE.search(_text(tool)))


def _is_external(tool: ToolDef) -> bool:
    return bool(_EXTERNAL_RE.search(_text(tool)))


def _is_confirm(tool: ToolDef) -> bool:
    return bool(_CONFIRM_RE.search(_text(tool)))


def _is_sensitive_read(tool: ToolDef) -> bool:
    text = _text(tool)
    return bool(_DATA_SOURCE_RE.search(text) and _SENSITIVE_READ_RE.search(text))


def _is_privileged(tool: ToolDef) -> bool:
    return bool(_PRIVILEGED_RE.search(_text(tool)))


def _is_auth(tool: ToolDef) -> bool:
    return bool(_AUTH_RE.search(_text(tool)))


def _is_financial(tool: ToolDef) -> bool:
    return bool(_FINANCIAL_RE.search(_text(tool)))


def _grade_for(score: int) -> str:
    if score >= 100:
        return "A+"
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


# ---------------------------------------------------------------------------
# Category 1: Configuration Risk Checks
# ---------------------------------------------------------------------------


def _check_unguarded_write(tools: Sequence[ToolDef]) -> List[Deduction]:
    """[-15] Write tools with no read/verify counterpart in the same domain."""
    writes = [t for t in tools if _is_write(t)]
    reads = [t for t in tools if _is_read(t)]
    if not writes:
        return []

    read_domains: Set[str] = set()
    for r in reads:
        read_domains |= _domains(r)
    # Tools with no domain still count as reads for the "general" domain.
    has_general_read = any(not _domains(r) for r in reads)

    unguarded: List[str] = []
    for w in writes:
        w_domains = _domains(w)
        if not w_domains:
            # No specific domain — guarded if any general read exists.
            if not has_general_read and not reads:
                unguarded.append(w.name)
        else:
            # Guarded if at least one domain has a read counterpart.
            if not (w_domains & read_domains):
                unguarded.append(w.name)

    if not unguarded:
        return []
    return [
        Deduction(
            check_id="UNGUARDED_WRITE",
            points_lost=15,
            description=(
                "Write/mutation tools with no corresponding read/verify tool: "
                + ", ".join(unguarded)
            ),
            affected_tools=unguarded,
            suggested_contract=(
                "Add a read or verification tool for each write domain, "
                "e.g. `get_<resource>` must precede `update_<resource>`."
            ),
        )
    ]


def _check_external_comm_ungated(tools: Sequence[ToolDef]) -> List[Deduction]:
    """[-10] External communication with no confirmation/approval tool."""
    externals = [t for t in tools if _is_external(t)]
    confirms = [t for t in tools if _is_confirm(t)]
    if not externals or confirms:
        return []
    return [
        Deduction(
            check_id="EXTERNAL_COMM_UNGATED",
            points_lost=10,
            description=(
                "External communication tools with no confirmation gate: "
                + ", ".join(t.name for t in externals)
            ),
            affected_tools=[t.name for t in externals],
            suggested_contract=(
                "Add a confirmation step: `confirm_send` must precede "
                "any external communication tool."
            ),
        )
    ]


def _check_sensitive_data_exposed(tools: Sequence[ToolDef]) -> List[Deduction]:
    """[-10] Sensitive data readers + external senders with no gating."""
    readers = [t for t in tools if _is_sensitive_read(t)]
    senders = [t for t in tools if _is_external(t)]
    confirms = [t for t in tools if _is_confirm(t)]
    if not readers or not senders or confirms:
        return []
    return [
        Deduction(
            check_id="SENSITIVE_DATA_EXPOSED",
            points_lost=10,
            description=(
                "Sensitive data can flow from "
                + ", ".join(t.name for t in readers)
                + " to "
                + ", ".join(t.name for t in senders)
                + " with no intervention."
            ),
            affected_tools=[t.name for t in readers] + [t.name for t in senders],
            suggested_contract=(
                "Data from sensitive sources must not flow to external sinks "
                "without review. Apply `no_data_leak` between reader and sender."
            ),
        )
    ]


def _check_no_rate_limit(tools: Sequence[ToolDef]) -> List[Deduction]:
    """[-3/5/8] Write/mutation tools with no apparent rate limiting.

    Scales by write count: 1 write = -3, 2 writes = -5, 3+ writes = -8.
    """
    writes = [t for t in tools if _is_write(t)]
    if not writes:
        return []
    n = len(writes)
    points = 3 if n == 1 else 5 if n == 2 else 8
    return [
        Deduction(
            check_id="NO_RATE_LIMIT_ON_WRITES",
            points_lost=points,
            description=(
                "Write/mutation tools have no apparent rate limiting: "
                + ", ".join(t.name for t in writes)
            ),
            affected_tools=[t.name for t in writes],
            suggested_contract=(
                "Apply `rate_limit` or `idempotent` contracts to mutation tools."
            ),
        )
    ]


def _check_missing_auth(tools: Sequence[ToolDef]) -> List[Deduction]:
    """[-7] Privileged operations with no auth tool in the set."""
    privileged = [t for t in tools if _is_privileged(t)]
    auths = [t for t in tools if _is_auth(t)]
    if not privileged or auths:
        return []
    return [
        Deduction(
            check_id="MISSING_AUTH_CHECK",
            points_lost=7,
            description=(
                "Privileged operations with no authentication/authorization tool: "
                + ", ".join(t.name for t in privileged)
            ),
            affected_tools=[t.name for t in privileged],
            suggested_contract=(
                "Add an auth verification tool and apply "
                "`requires_permission` to privileged operations."
            ),
        )
    ]


def _check_over_privileged(tools: Sequence[ToolDef]) -> List[Deduction]:
    """[-5] More than 8 tools — over-privileged surface area."""
    if len(tools) <= 8:
        return []
    return [
        Deduction(
            check_id="SINGLE_AGENT_FULL_ACCESS",
            points_lost=5,
            description=(
                f"Agent has {len(tools)} tools connected (threshold: 8). "
                "Consider splitting into focused sub-agents."
            ),
            affected_tools=[t.name for t in tools],
            suggested_contract=(
                "Split tools across multiple agents with least-privilege scoping."
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Category 2: Contract Compliance Checks (reachability analysis)
# ---------------------------------------------------------------------------


def _check_must_precede_gap(tools: Sequence[ToolDef]) -> List[Deduction]:
    """[-15] Write tools that could be called without a prior read/verify."""
    writes = [t for t in tools if _is_write(t)]
    reads = [t for t in tools if _is_read(t)]
    if not writes:
        return []

    read_domains: Set[str] = set()
    for r in reads:
        read_domains |= _domains(r)

    # A write is vulnerable if a read for its domain EXISTS but nothing
    # enforces ordering — the agent could skip the read.
    vulnerable: List[str] = []
    for w in writes:
        w_domains = _domains(w)
        # Only flag if a read exists (otherwise UNGUARDED_WRITE covers it).
        if w_domains & read_domains:
            vulnerable.append(w.name)
        elif not w_domains and reads:
            vulnerable.append(w.name)

    if not vulnerable:
        return []
    return [
        Deduction(
            check_id="MUST_PRECEDE_GAP",
            points_lost=15,
            description=(
                "Write tools could be called without a preceding read/verify: "
                + ", ".join(vulnerable)
                + ". A read tool exists but no ordering contract enforces it."
            ),
            affected_tools=vulnerable,
            suggested_contract=(
                "Apply `must_precede` contracts, e.g. "
                "`get_<resource>` must precede `update_<resource>`."
            ),
        )
    ]


def _check_data_leak_path(tools: Sequence[ToolDef]) -> List[Deduction]:
    """[-10] Data could flow from sensitive reader to external sender.

    Only fires when a confirm/approve tool exists (so Cat 1's
    SENSITIVE_DATA_EXPOSED was skipped) but no formal ``no_data_leak``
    contract enforces the flow.  Without confirms, Cat 1 already covers it.
    """
    readers = [t for t in tools if _is_sensitive_read(t)]
    senders = [t for t in tools if _is_external(t)]
    confirms = [t for t in tools if _is_confirm(t)]
    if not readers or not senders or not confirms:
        return []
    return [
        Deduction(
            check_id="NO_DATA_LEAK_GAP",
            points_lost=10,
            description=(
                "No `no_data_leak` contract prevents data flow from "
                + ", ".join(t.name for t in readers)
                + " to "
                + ", ".join(t.name for t in senders)
                + "."
            ),
            affected_tools=[t.name for t in readers] + [t.name for t in senders],
            suggested_contract=(
                "Apply `no_data_leak` between each sensitive-read and "
                "external-send tool pair."
            ),
        )
    ]


def _check_idempotency_gap(tools: Sequence[ToolDef]) -> List[Deduction]:
    """[-5] Financial/write tools that could be called more than once."""
    targets = [t for t in tools if _is_financial(t) or _is_write(t)]
    if not targets:
        return []
    financial = [t for t in targets if _is_financial(t)]
    if not financial:
        return []
    return [
        Deduction(
            check_id="IDEMPOTENCY_GAP",
            points_lost=5,
            description=(
                "Financial tools could be called multiple times without "
                "idempotency control: " + ", ".join(t.name for t in financial)
            ),
            affected_tools=[t.name for t in financial],
            suggested_contract=("Apply `idempotent` contract to financial operations."),
        )
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_CATEGORY_1_CHECKS = [
    _check_unguarded_write,
    _check_external_comm_ungated,
    _check_sensitive_data_exposed,
    _check_no_rate_limit,
    _check_missing_auth,
    _check_over_privileged,
]

_CATEGORY_2_CHECKS = [
    _check_must_precede_gap,
    _check_data_leak_path,
    _check_idempotency_gap,
]


def score_tools(
    tools: Sequence[ToolDef],
    agent_name: str = "anonymous",
) -> ScoringReport:
    """Score a set of tool definitions for safety risks.

    Runs two categories of checks:
      1. **Configuration risk** — static red flags in the tool set itself.
      2. **Contract compliance** — whether the agent could reach unsafe states
         given the tools, absent explicit contracts.

    Args:
        tools: Tool definitions to evaluate.
        agent_name: Optional name for the agent being scored.

    Returns:
        A ``ScoringReport`` with score, grade, deductions, and fix suggestions.

    Examples:
        >>> report = score_tools([
        ...     ToolDef("get_user", "Fetch user profile", {"id": "str"}),
        ...     ToolDef("update_user", "Update user record", {"id": "str"}),
        ... ])
        >>> report.grade
        'A'
    """
    deductions: List[Deduction] = []
    for check_fn in _CATEGORY_1_CHECKS + _CATEGORY_2_CHECKS:
        deductions.extend(check_fn(tools))

    total_lost = sum(d.points_lost for d in deductions)
    score = max(0, 100 - total_lost)

    return ScoringReport(
        score=score,
        grade=_grade_for(score),
        agent_name=agent_name,
        timestamp=datetime.now(timezone.utc).isoformat(),
        deductions=deductions,
        suggested_contracts=[d.suggested_contract for d in deductions],
    )
