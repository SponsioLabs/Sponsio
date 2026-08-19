"""Evidence middleware — claim verification at the observe_llm_call chokepoint.

Pure logic between a parsed assistant response and the cloud evidence
API (``sponsio/cloud/evidence.py``): extract configured claims from
structured output, verify them in ONE ``verify_batch`` call, feed each
verdict into the trace as an ``evidence`` event through the guard's
normal event path, and report whether the response must stop.

Everything here follows the thin-client rule: no comparators, no verdict
computation — the server's verdict/action words are consumed verbatim.
No printing either; terminal output goes through the existing
``TerminalReporter.report_evidence`` from the guard layer.

Opt-in and fail closed:

* A guard without an ``evidence=`` config never reaches this module —
  zero behavior change.
* ``on_error: block`` (the default) turns an :class:`EvidenceError`
  (network, auth, bad response) into a stop; ``allow`` releases but the
  error is still recorded on the CheckResult.
* Claims are extracted ONLY from structured output: assistant message
  content that parses as a JSON object, plus decoded tool_call argument
  dicts. A configured ``claim_field`` absent from the output is simply
  not a claim. No free-text regex extraction here.
* Input source tags are sent exactly as declared (e.g. ``model_output``).
  If the server's taint allowlist rejects the source, that rejection is
  the correct outcome and surfaces as the server's error — the
  middleware never spoofs source tags.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

from sponsio.cloud.evidence import EvidenceClient, EvidenceError, EvidenceResult

if TYPE_CHECKING:
    from sponsio.integrations.base import BaseGuard

# ---------------------------------------------------------------------------
# Notice templates — the single place adapters take their user-facing
# rewrite text from, so wording can be customized later without touching
# each integration.
# ---------------------------------------------------------------------------

BLOCK_NOTICE_TEMPLATE = (
    "[BLOCKED] claim '{claim_field}'={value} failed verification "
    "({predicate}: {verdict})"
)
BLOCK_NOTICE_CORRECTION_SUFFIX = "; evidence says: {correction}"
BLOCK_NOTICE_ERROR_TEMPLATE = (
    "[BLOCKED] claim verification unavailable ({error}); "
    "failing closed per on_error=block"
)
CLARIFY_NOTICE_TEMPLATE = (
    "Before I can confirm '{claim_field}'={value}: the evidence for "
    "{predicate} is ambiguous. Did you mean one of: {candidates}?"
)
CLARIFY_CANDIDATE_LIMIT = 5


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class EvidenceConfigError(ValueError):
    """The evidence config is malformed. Raised at guard construction."""


@dataclass(frozen=True)
class EvidenceInputSpec:
    """One resolver input: which output field feeds it, tagged how."""

    field: str
    source: str


@dataclass(frozen=True)
class EvidenceClaimSpec:
    """One configured claim: predicate + the output field that carries it."""

    predicate: str
    claim_field: str
    inputs: Mapping[str, EvidenceInputSpec] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceConfig:
    """Guard-level evidence configuration (see BaseGuard ``evidence=``)."""

    client: EvidenceClient
    claims: tuple[EvidenceClaimSpec, ...]
    on_error: str = "block"

    @classmethod
    def from_value(cls, value: Any) -> "EvidenceConfig":
        """Normalize the construction-time value into a validated config.

        Accepts an :class:`EvidenceConfig` (passed through) or a mapping
        shaped like::

            {"client": <EvidenceClient | {"api_key":..., "url":...} | None>,
             "on_error": "block" | "allow",
             "claims": [{"predicate": ..., "claim_field": ...,
                         "inputs": {name: {"field":..., "source":...}}}]}

        ``client: None`` builds a default CloudClient (env/credentials).
        """
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise EvidenceConfigError(
                f"evidence config must be a mapping or EvidenceConfig, "
                f"got {type(value).__name__}"
            )

        on_error = value.get("on_error", "block")
        if on_error not in ("block", "allow"):
            raise EvidenceConfigError(
                f"evidence.on_error must be 'block' or 'allow', got {on_error!r}"
            )

        raw_client = value.get("client")
        if isinstance(raw_client, EvidenceClient):
            client = raw_client
        elif raw_client is None or isinstance(raw_client, Mapping):
            from sponsio.cloud.client import CloudClient

            kwargs = dict(raw_client or {})
            client = EvidenceClient(
                CloudClient(api_key=kwargs.get("api_key"), url=kwargs.get("url"))
            )
        else:
            raise EvidenceConfigError(
                "evidence.client must be an EvidenceClient, a mapping "
                "with api_key/url, or None for the default credentials path"
            )

        raw_claims = value.get("claims") or []
        if not isinstance(raw_claims, (list, tuple)):
            raise EvidenceConfigError("evidence.claims must be a list")
        claims: list[EvidenceClaimSpec] = []
        for raw in raw_claims:
            if not isinstance(raw, Mapping) or not raw.get("predicate"):
                raise EvidenceConfigError(
                    f"each evidence claim needs a 'predicate', got {raw!r}"
                )
            if not raw.get("claim_field"):
                raise EvidenceConfigError(
                    f"claim {raw.get('predicate')!r} needs a 'claim_field'"
                )
            inputs: dict[str, EvidenceInputSpec] = {}
            for name, spec in (raw.get("inputs") or {}).items():
                if (
                    not isinstance(spec, Mapping)
                    or not spec.get("field")
                    or not spec.get("source")
                ):
                    raise EvidenceConfigError(
                        f"claim {raw['predicate']!r} input {name!r} needs "
                        "'field' and 'source'"
                    )
                inputs[str(name)] = EvidenceInputSpec(
                    field=str(spec["field"]), source=str(spec["source"])
                )
            claims.append(
                EvidenceClaimSpec(
                    predicate=str(raw["predicate"]),
                    claim_field=str(raw["claim_field"]),
                    inputs=inputs,
                )
            )
        return cls(client=client, claims=tuple(claims), on_error=on_error)


# ---------------------------------------------------------------------------
# Extraction — structured output only
# ---------------------------------------------------------------------------


def structured_sources(
    content: str | None, tool_call_args: list[Mapping[str, Any]] | None
) -> list[Mapping[str, Any]]:
    """The field-lookup sources, in precedence order.

    Message content is a source only when it parses as a JSON object;
    tool_call argument dicts follow in call order. First source holding
    a field wins.
    """
    sources: list[Mapping[str, Any]] = []
    if content:
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            sources.append(parsed)
    for args in tool_call_args or []:
        if isinstance(args, Mapping):
            sources.append(args)
    return sources


def _lookup(sources: list[Mapping[str, Any]], field_name: str) -> tuple[bool, Any]:
    for source in sources:
        if field_name in source:
            return True, source[field_name]
    return False, None


@dataclass(frozen=True)
class ExtractedClaim:
    spec: EvidenceClaimSpec
    value: Any
    wire: dict[str, Any]  # verify_batch item


def extract_claims(
    config: EvidenceConfig, sources: list[Mapping[str, Any]]
) -> list[ExtractedClaim]:
    """Configured claims present in the structured output.

    A missing ``claim_field`` means no claim (silently skipped, per the
    design). A missing input field sends the claim without that input —
    the resolver answers UNAVAILABLE and the verdict lattice does the
    fail-closed work; inventing a value here would be worse.
    """
    extracted: list[ExtractedClaim] = []
    for spec in config.claims:
        present, value = _lookup(sources, spec.claim_field)
        if not present:
            continue
        inputs: dict[str, Any] = {}
        for name, input_spec in spec.inputs.items():
            found, input_value = _lookup(sources, input_spec.field)
            if found:
                inputs[name] = (input_value, input_spec.source)
        extracted.append(
            ExtractedClaim(
                spec=spec,
                value=value,
                wire={
                    "predicate": spec.predicate,
                    "value": value,
                    "inputs": inputs,
                },
            )
        )
    return extracted


# ---------------------------------------------------------------------------
# Verification run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifiedClaim:
    """One claim paired with the server's verdict."""

    spec: EvidenceClaimSpec
    value: Any
    result: EvidenceResult

    @property
    def blocked(self) -> bool:
        return self.result.action == "block"

    @property
    def needs_clarification(self) -> bool:
        return self.result.action == "clarify"


@dataclass(frozen=True)
class EvidenceOutcome:
    """What one response's evidence run produced."""

    claims: tuple[VerifiedClaim, ...] = ()
    error: EvidenceError | None = None

    @property
    def blocked_claims(self) -> tuple[VerifiedClaim, ...]:
        return tuple(c for c in self.claims if c.blocked)

    @property
    def clarify_claims(self) -> tuple[VerifiedClaim, ...]:
        return tuple(c for c in self.claims if c.needs_clarification)

    def stops(self, on_error: str) -> bool:
        """Must the response be withheld? (Phase-1 canonical semantics —
        this feeds a synthesized ``blocked`` det violation, so
        ``CheckResult.stop_original`` reflects it.)"""
        if self.blocked_claims:
            return True
        return self.error is not None and on_error == "block"


def run_evidence(
    guard: "BaseGuard",
    config: EvidenceConfig,
    *,
    content: str | None,
    tool_call_args: list[Mapping[str, Any]] | None = None,
    session_id: str | None = None,
) -> EvidenceOutcome:
    """Extract, verify (one batch call), feed the trace, summarize.

    Never raises: an :class:`EvidenceError` is captured on the outcome
    (callers decide via ``on_error``). Events enter the trace through
    ``RuntimeMonitor.check_action`` — the guard's normal event path — so
    evidence-referencing det contracts (``claim_requires_evidence``,
    ``underdetermined_must_clarify``) evaluate on the same step.
    """
    extracted = extract_claims(config, structured_sources(content, tool_call_args))
    if not extracted:
        return EvidenceOutcome()

    try:
        results = config.client.verify_batch(
            [claim.wire for claim in extracted], session_id=session_id
        )
    except EvidenceError as exc:
        return EvidenceOutcome(error=exc)

    verified: list[VerifiedClaim] = []
    for claim, result in zip(extracted, results):
        verified.append(
            VerifiedClaim(spec=claim.spec, value=claim.value, result=result)
        )
        # Normal event path: the verdict becomes an ``evidence`` event in
        # the trace (same shape as EvidenceResult.to_event), evaluated
        # under the monitor's lock like every other event.
        guard._monitor.check_action(
            agent_id=guard.agent_id,
            action=f"<evidence:{result.predicate}>",
            event_type="evidence",
            metadata={
                "key": result.predicate,
                "args": {"verdict": result.verdict, "action": result.action},
            },
        )
    return EvidenceOutcome(claims=tuple(verified))


# ---------------------------------------------------------------------------
# Notice formatting (used by adapters; templates above are the source)
# ---------------------------------------------------------------------------


def format_block_notice(
    blocked: tuple[VerifiedClaim, ...] | list[VerifiedClaim],
    error: str | None = None,
) -> str:
    """One line per blocked claim; the error line when the run failed."""
    lines = []
    for claim in blocked:
        line = BLOCK_NOTICE_TEMPLATE.format(
            claim_field=claim.spec.claim_field,
            value=claim.value,
            predicate=claim.result.predicate,
            verdict=claim.result.verdict,
        )
        if claim.result.correction is not None:
            line += BLOCK_NOTICE_CORRECTION_SUFFIX.format(
                correction=claim.result.correction
            )
        lines.append(line)
    if error:
        lines.append(BLOCK_NOTICE_ERROR_TEMPLATE.format(error=error))
    return "\n".join(lines)


def format_clarify_notice(
    clarify: tuple[VerifiedClaim, ...] | list[VerifiedClaim],
) -> str:
    lines = []
    for claim in clarify:
        candidates = [str(c) for c in claim.result.clarify_on]
        shown = ", ".join(candidates[:CLARIFY_CANDIDATE_LIMIT])
        remaining = len(candidates) - CLARIFY_CANDIDATE_LIMIT
        if remaining > 0:
            shown += f" (+{remaining} more)"
        lines.append(
            CLARIFY_NOTICE_TEMPLATE.format(
                claim_field=claim.spec.claim_field,
                value=claim.value,
                predicate=claim.result.predicate,
                candidates=shown,
            )
        )
    return "\n".join(lines)
