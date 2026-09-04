"""Thin client for the cloud evidence verification API.

The runtime ships NO comparators, NO resolvers, NO predicate catalog and
computes NO verdicts: this module sends a claim to the service's
``/v1/evidence/verify`` endpoints and hands back the server's answer as
an :class:`EvidenceResult`. All judgment is server-side — the seven-value
verdict lattice (PASS / MISMATCH / UNDERDETERMINED / NO_EVIDENCE / STALE
/ SOURCE_UNAVAILABLE / EXTRACTION_AMBIGUOUS) and the policy action
(release / block / clarify / re_resolve) arrive already decided.

Failure policy — explicit and conservative: a network problem, auth
rejection, or malformed response raises :class:`EvidenceError` (a
:class:`~sponsio.cloud.client.CloudError`). The client never fabricates
a verdict for a check that did not run. Callers that want fail-closed
behavior should treat the exception as a block::

    try:
        result = client.evidence.verify("order_status", value="shipped",
                                        inputs={"order_id": (oid, "customer_db")})
        release = result.passed
    except CloudError:
        release = False   # fail closed: unverified claims do not ship

Inputs carry taint tags. The ergonomic form is a ``(value, source)``
tuple per input — ``inputs={"city": ("Berkeley", "user_utterance")}`` —
normalized here to the wire shape ``{"value": ..., "source": ...}``.
The server rejects sources outside the predicate's allowlist.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

from sponsio.cloud.client import CloudClient, CloudError

if TYPE_CHECKING:
    from sponsio.models.trace import Event


class EvidenceError(CloudError):
    """An evidence call did not produce a server verdict.

    Raised on auth rejection, transport failure surfaced by the client,
    non-2xx responses, and unparseable bodies. Carries ``status`` when
    an HTTP status was involved. Never raised for a blocking verdict —
    MISMATCH is an answer, not an error.
    """


@dataclass(frozen=True)
class EvidenceResult:
    """One server verdict, as returned by /v1/evidence/verify.

    ``verdict`` and ``action`` are the server's words verbatim; the
    convenience properties cover the three decisions callers branch on.
    """

    predicate: str
    verdict: str
    action: str
    values: tuple[Any, ...] = ()
    evidence_ts: float | None = None
    source: str | None = None
    table_version: str | None = None
    correction: Any = None
    clarify_on: tuple[Any, ...] = field(default_factory=tuple)
    attestation_id: str | None = None

    @property
    def passed(self) -> bool:
        """Claim matched fresh evidence; the server says release."""
        return self.verdict == "PASS"

    @property
    def blocked(self) -> bool:
        """The server's policy action is a hard block."""
        return self.action == "block"

    @property
    def needs_clarification(self) -> bool:
        """Evidence was ambiguous; ``clarify_on`` holds the candidates."""
        return self.action == "clarify"

    def to_event(self, ts: int | float, agent: str = "assistant") -> "Event":
        """This verdict as a trace event, for the DFA contract layer.

        Feeding the returned event through the normal trace path grounds
        the ``claim_emitted(pred)`` / ``evidence_verdict(pred, V)`` /
        ``evidence_action(pred, a)`` atoms, so contracts built from
        :func:`sponsio.patterns.library.claim_requires_evidence` and
        :func:`~sponsio.patterns.library.underdetermined_must_clarify`
        can reference the verdict.
        """
        from sponsio.models.trace import Event

        return Event(
            ts=ts,
            agent=agent,
            event_type="evidence",
            key=self.predicate,
            args={"verdict": self.verdict, "action": self.action},
        )


def _normalize_inputs(inputs: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Ergonomic input forms -> the wire's tagged shape.

    Accepted per input: ``(value, source)`` tuple/list where ``source``
    is a string, or an already wire-shaped
    ``{"value": ..., "source": ...}`` mapping. Anything without a source
    tag is rejected here — the server would refuse it anyway (taint
    rule), and a local error names the input.

    The source must be a string for the pair form to apply. Without that
    check, a two-element list of data was read as a pair:
    ``{"items": [40, 60]}`` became ``value=40, source="60"``, and the
    server then refused source ``"60"`` as untrusted — an error naming
    nothing the caller had written. A list value now needs the explicit
    form, ``([40, 60], "submitted_output")``.
    """
    wire: dict[str, dict[str, Any]] = {}
    for name, spec in (inputs or {}).items():
        if isinstance(spec, Mapping) and "source" in spec:
            wire[name] = {"value": spec.get("value"), "source": str(spec["source"])}
        elif (
            isinstance(spec, (tuple, list))
            and len(spec) == 2
            and isinstance(spec[1], str)
        ):
            value, source = spec
            wire[name] = {"value": value, "source": source}
        else:
            raise ValueError(
                f"input {name!r} needs a source tag: pass (value, source) "
                'or {"value": ..., "source": ...} — the server rejects '
                "untagged inputs (taint rule)"
            )
    return wire


def _parse_result(data: Mapping[str, Any]) -> EvidenceResult:
    evidence = data.get("evidence") or {}
    return EvidenceResult(
        predicate=str(data.get("predicate", "")),
        verdict=str(data.get("verdict", "")),
        action=str(data.get("action", "")),
        values=tuple(evidence.get("values") or ()),
        evidence_ts=evidence.get("ts"),
        source=evidence.get("source"),
        table_version=evidence.get("table_version"),
        correction=data.get("correction"),
        clarify_on=tuple(data.get("clarify_on") or ()),
        attestation_id=data.get("attestation_id"),
    )


class EvidenceClient:
    """Namespace object reached as ``CloudClient.evidence``."""

    def __init__(self, client: CloudClient) -> None:
        self._client = client

    def _post(self, path: str, body: dict) -> dict:
        try:
            status, payload, _ = self._client._request(
                "POST",
                path,
                body=json.dumps(body).encode(),
                content_type="application/json",
            )
        except EvidenceError:
            raise
        except CloudError as exc:
            # Re-type transport failures so callers can catch one class,
            # keeping the original message (and cause) intact.
            raise EvidenceError(str(exc)) from exc
        if status in (401, 403):
            raise EvidenceError("key rejected", status=status)
        if not 200 <= status < 300:
            raise EvidenceError(
                self._client._detail(payload, f"evidence call failed ({status})"),
                status=status,
            )
        try:
            return json.loads(payload.decode())
        except (ValueError, UnicodeDecodeError) as exc:
            raise EvidenceError("evidence call returned a non-JSON body") from exc

    def verify(
        self,
        predicate: str,
        *,
        value: Any = None,
        text: str | None = None,
        inputs: Mapping[str, Any] | None = None,
        session_id: str | None = None,
    ) -> EvidenceResult:
        """Verify one claim against fresh evidence, server-side.

        Args:
            predicate: Catalog predicate name (``GET /v1/evidence/predicates``
                lists them).
            value: The claimed value (structured-output path; recommended).
            text: Free-text claim (server support pending; sent verbatim).
            inputs: Resolver inputs with taint tags — see module docstring.
            session_id: Optional session correlation id for attestations.

        Raises:
            EvidenceError: The check did not run to a verdict. Decide
                fail-open/fail-closed at the call site; fail closed
                means treating this as a block.
        """
        body: dict[str, Any] = {
            "predicate": predicate,
            "claim": {"value": value, "text": text},
            "inputs": _normalize_inputs(inputs),
        }
        if session_id:
            body["session_id"] = session_id
        return _parse_result(self._post("/v1/evidence/verify", body))

    def verify_batch(
        self,
        claims: list[Mapping[str, Any]],
        *,
        session_id: str | None = None,
    ) -> list[EvidenceResult]:
        """Verify several claims in one call (one attestation each).

        Each claim is a mapping with ``predicate`` and ``value`` (or
        ``text``), plus optional ``inputs`` in any form
        :func:`_normalize_inputs` accepts. The server is strict: one bad
        claim (unknown predicate, taint violation) fails the whole batch.
        """
        wire_claims = []
        for claim in claims:
            wire_claims.append(
                {
                    "predicate": claim["predicate"],
                    "claim": {
                        "value": claim.get("value"),
                        "text": claim.get("text"),
                    },
                    "inputs": _normalize_inputs(claim.get("inputs")),
                }
            )
        body: dict[str, Any] = {"claims": wire_claims}
        if session_id:
            body["session_id"] = session_id
        data = self._post("/v1/evidence/verify_batch", body)
        return [_parse_result(item) for item in data.get("results") or []]
