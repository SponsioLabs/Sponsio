"""Sponsio semantic conventions for OpenTelemetry export.

Stable attribute keys that downstream observability platforms (Datadog,
Honeycomb, Grafana Cloud, the Sponsio-native dashboard, …) can rely on.
This module is the single source of truth — every emit-side helper in
``sponsio.tracer`` and every consumer-side parser refers to constants
defined here so renames stay in sync.

Three groups of attributes:

1. **Per-event attributes** (set on the ``sponsio.agent_turn`` root span)
   — what the agent tried to do this turn. ``sponsio.event.*`` namespace.

2. **Per-contract attributes** (set on each ``sponsio.contract_check``
   span) — which contract was evaluated, in which pipeline, with what
   verdict. ``sponsio.contract.*`` namespace.

3. **Per-constraint attributes** (set on the precondition / guarantee /
   sto_eval children) — the formal formula being checked, its result,
   evidence. ``sponsio.constraint.*`` namespace.

Plus a fourth, sparser set on violation / enforcement child spans:
``sponsio.violation.*`` and ``sponsio.enforcement.*``.

Versioning
----------

Add new keys freely. Renaming an existing key is a breaking change —
bump ``SCHEMA_VERSION`` and document the migration path in
``docs/observability.md``. Consumers should ignore unknown keys (forward
compatibility) and treat absent keys as ``None`` (not zero / empty
string) — the *absence* of an attribute is a meaningful signal in some
cards (e.g. a contract with no enforcement phase has no
``sponsio.contract.enforcement_holds``).

The schema URL below is the stable identifier observability platforms
use to detect Sponsio spans and apply the right rendering. Don't change
it without coordinating with downstream dashboards.
"""

from __future__ import annotations

# Bump on breaking renames. New keys do NOT bump the version.
SCHEMA_VERSION = "1.0.0"

# Schema URL — stable identifier for Sponsio span shape across releases.
# Observability platforms use this to detect Sponsio spans and apply
# the right rendering (e.g. the card layouts in docs/observability.md).
SCHEMA_URL = "https://sponsio.dev/schemas/observability/1.0.0"


# ---------------------------------------------------------------------------
# Span type names — emitted as the OTLP span ``name`` field.
# ---------------------------------------------------------------------------

SPAN_AGENT_TURN = "sponsio.agent_turn"
SPAN_CONTRACT_CHECK = "sponsio.contract_check"
SPAN_PRECONDITION = "sponsio.precondition"
SPAN_GUARANTEE = "sponsio.guarantee"
SPAN_VIOLATION = "sponsio.violation"
SPAN_ENFORCEMENT = "sponsio.enforcement"
SPAN_STO_CHECK = "sponsio.sto_check"
SPAN_STO_EVAL = "sponsio.sto_eval"


# ---------------------------------------------------------------------------
# Per-event attributes — set on ``sponsio.agent_turn`` (root span).
# ---------------------------------------------------------------------------

# Logical agent identifier (matches the ``agents:`` key in the yaml that
# governs this turn). Stable across IDE restarts; identifies which
# bucket fired.
ATTR_AGENT_ID = "sponsio.agent_id"

# Host runtime that emitted the event: "cursor" / "claude-code" /
# "openclaw" / "" (legacy / code-wrapped).
ATTR_HOST = "sponsio.host"

# Per-IDE conversation id (from the host's hook payload). Lets the
# dashboard group spans by user conversation.
ATTR_CONVERSATION_ID = "sponsio.conversation_id"

# What the agent was about to do.
ATTR_EVENT_TOOL = "sponsio.event.tool"
ATTR_EVENT_TYPE = "sponsio.event.type"  # "tool_call" / "llm_response" / ...
ATTR_EVENT_TS = "sponsio.event.ts"  # logical sequence number
ATTR_EVENT_TIMESTAMP_NS = "sponsio.event.timestamp_ns"  # wall-clock ns

# Tool args — single JSON-encoded string. Subject to redaction (see
# ``redact_args=`` in the writer). Truncated to ``EVENT_ARGS_MAX_BYTES``
# by default so a runaway agent calling Bash with 1MB of inline content
# doesn't blow up the dashboard.
ATTR_EVENT_TOOL_ARGS = "sponsio.event.tool_args"

# Per-event aggregates — make the "Today's blocks" card layout (A) a
# single-row scan instead of needing to walk children.
ATTR_OUTCOME_BLOCKED = "sponsio.outcome.blocked"
ATTR_OUTCOME_STATUS = "sponsio.outcome.status"  # "ok" / "violated" / "error"
ATTR_CONTRACTS_CHECKED = "sponsio.contracts_checked"
ATTR_DET_VIOLATIONS = "sponsio.det_violations"
ATTR_STO_VIOLATIONS = "sponsio.sto_violations"
ATTR_TURN_DURATION_NS = "sponsio.turn.duration_ns"


# ---------------------------------------------------------------------------
# Per-contract attributes — set on ``sponsio.contract_check`` spans.
# ---------------------------------------------------------------------------

# Human-readable description (from the yaml ``desc:`` field). This is
# what the dashboard's "rule fire heatmap" rows index by.
ATTR_CONTRACT_LABEL = "sponsio.contract.label"

# Stable id for cross-session aggregation. For pack-shipped rules this
# is the ``source:`` tag (e.g. ``"library:tier1.shell"``); for
# user-authored rules it's a hash of the desc + formula.
ATTR_CONTRACT_ID = "sponsio.contract.id"

# Which pipeline evaluated it: "det" (formal LTL) / "sto" (LLM judge).
ATTR_CONTRACT_PIPELINE = "sponsio.contract.pipeline"

# Where the contract came from: "user_policy" (NL onboard),
# "shipped_pack" (sponsio:capability/*, sponsio:incident/*),
# "agent_inferred" (sponsio scan / refresh), "manual" (hand-edited).
ATTR_CONTRACT_SOURCE = "sponsio.contract.source"

# Sto thresholds (only meaningful when pipeline=="sto").
ATTR_CONTRACT_ALPHA = "sponsio.contract.alpha"  # assumption trigger
ATTR_CONTRACT_BETA = "sponsio.contract.beta"  # enforcement pass

# When did this rule become active for this turn? Reactive contracts
# carry "first_match"; default contracts have no value.
ATTR_CONTRACT_ACTIVATE_AT = "sponsio.contract.activate_at"

# Per-phase verdicts — duplicates the precondition/guarantee child
# results so simple dashboards can group by contract without walking
# the tree.
ATTR_CONTRACT_ASSUMPTION_HOLDS = "sponsio.contract.assumption_holds"
ATTR_CONTRACT_ENFORCEMENT_HOLDS = "sponsio.contract.enforcement_holds"


# ---------------------------------------------------------------------------
# Per-constraint attributes — set on precondition / guarantee / sto_eval.
# ---------------------------------------------------------------------------

# Human-readable formula description (e.g. "must precede check_policy").
ATTR_CONSTRAINT_DESC = "sponsio.constraint.desc"

# Compact stringified LTL AST. Useful for offline regression tools that
# need to recompile the formula. Optional — emit when cheap.
ATTR_CONSTRAINT_FORMULA = "sponsio.constraint.formula"

# Verdict.
ATTR_CONSTRAINT_RESULT = "sponsio.constraint.result"  # "ok" / "violated"

# Whether the just-appended event itself caused the verdict (vs an
# already-stale violation carried forward from a prior position). Set
# only on ``sponsio.guarantee`` spans where ``result == "violated"``.
ATTR_CONSTRAINT_FRESH = "sponsio.constraint.fresh"

# Position the contract was evaluated at (0 for global semantics, k_star
# for reactive). Useful for explaining "why didn't this fire earlier".
ATTR_CONSTRAINT_EVAL_POS = "sponsio.constraint.eval_pos"

# Sto-only attributes.
ATTR_CONSTRAINT_ATOM = "sponsio.constraint.atom"  # registered atom name
ATTR_CONSTRAINT_SCORE = "sponsio.constraint.score"  # in [0, 1]
ATTR_CONSTRAINT_THRESHOLD = "sponsio.constraint.threshold"
ATTR_CONSTRAINT_PASSED = "sponsio.constraint.passed"  # score >= threshold
ATTR_CONSTRAINT_EVIDENCE = "sponsio.constraint.evidence"  # judge's one-liner
ATTR_CONSTRAINT_SUGGESTION = "sponsio.constraint.suggestion"  # retry hint
ATTR_JUDGE_MODEL = "sponsio.judge.model"  # gemini-2.5-flash / gpt-4o-mini
ATTR_JUDGE_LATENCY_MS = "sponsio.judge.latency_ms"


# ---------------------------------------------------------------------------
# Per-violation attributes — set on ``sponsio.violation`` spans.
# ---------------------------------------------------------------------------

ATTR_VIOLATION_KIND = "sponsio.violation.kind"  # assumption|guarantee|sto|liveness
ATTR_VIOLATION_SEVERITY = "sponsio.violation.severity"  # HIGH|MEDIUM|LOW
ATTR_VIOLATION_EVIDENCE = "sponsio.violation.evidence"

# Optional traceback to the user's source-of-truth: "policy.md ¶1",
# "team-handbook.md §4.2", etc. When the user authors contracts via the
# NL Skill flow, the agent should populate this from the policy
# paragraph it derived the rule from.
ATTR_VIOLATION_POLICY_REF = "sponsio.violation.policy_ref"


# ---------------------------------------------------------------------------
# Per-enforcement attributes — set on ``sponsio.enforcement`` spans.
# ---------------------------------------------------------------------------

# Strategy class name: DetBlock | EscalateToHuman | RetryWithConstraint
# | RedirectToSafe.
ATTR_ENFORCEMENT_STRATEGY = "sponsio.enforcement.strategy"

# Final action taken: blocked | escalated | retrying | redirected |
# observed (the last one only fires under mode="observe", indicating a
# would-have-blocked decision was downgraded to log-only).
ATTR_ENFORCEMENT_ACTION = "sponsio.enforcement.action"

# Sto retry-with-lesson prompt (only meaningful for RetryWithConstraint).
ATTR_ENFORCEMENT_RETRY_PROMPT = "sponsio.enforcement.retry_prompt"

# Fallback action for RedirectToSafe (e.g. "log_warning" instead of
# "transfer_funds"). Only meaningful for that strategy.
ATTR_ENFORCEMENT_FALLBACK_ACTION = "sponsio.enforcement.fallback_action"


# ---------------------------------------------------------------------------
# Truncation defaults — guard the dashboard against oversized payloads.
# ---------------------------------------------------------------------------

# Bash command lines, Edit new_string, llm_response.content can all be
# pathologically large. We truncate (not redact — different concern) at
# emit time so a single 10MB tool call doesn't make a turn span bigger
# than the rest of the day's traces combined. Users who need full
# fidelity can opt in via ``otel_exporter(truncate=False)``.
EVENT_ARGS_MAX_BYTES = 4096
CONSTRAINT_EVIDENCE_MAX_BYTES = 1024
ENFORCEMENT_RETRY_PROMPT_MAX_BYTES = 2048
