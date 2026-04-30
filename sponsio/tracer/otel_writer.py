"""OTEL trace writer — convert Sponsio traces and span trees to OTLP JSON.

Two flavors live in this module:

1. :func:`trace_to_otlp` — serialises a flat :class:`~sponsio.models.trace.Trace`
   (the round-trippable event log) into the OTLP shape that
   :mod:`sponsio.tracer.otel_consumer` reads back. Used by ``sponsio eval``
   for regression replay; the round-trip invariant is load-bearing.

2. :func:`span_tree_to_otlp` — serialises a contract-check
   :class:`~sponsio.models.spans.AgentTurnSpan` tree (with all
   ``contract_check`` / ``precondition`` / ``guarantee`` /
   ``violation`` / ``enforcement`` / ``sto_eval`` children) into an
   OTLP span hierarchy stamped with the
   :mod:`sponsio.tracer.semconv` attribute keys. Used by observability
   exports to the Sponsio dashboard, Datadog, Honeycomb, Grafana
   Cloud, and anywhere else that speaks OTLP.

The two are *not* round-trippable into each other on purpose. Trace
events and span trees describe different layers — what the agent did
vs how Sponsio judged it — and conflating them would force every
consumer to re-derive one from the other.

Why a dedicated writer and not a quick one-liner? Three reasons:

1. **Round-trip guarantee** for ``trace_to_otlp``: downstream code
   (``eval``, tests, regression replay) already assumes the consumer's
   shape. Handrolling OTLP in N callsites means N subtly-different
   shapes; a single writer gives us one place to keep them in sync.
2. **Attribute encoding is finicky**: OTLP uses a tagged-union style
   (``{"stringValue": ...}`` vs ``{"intValue": ...}``) that every
   ad-hoc emitter gets wrong in at least one edge case.
3. **Stable schema URL** for ``span_tree_to_otlp``: every observability
   platform that wants to render Sponsio spans natively keys off the
   ``schemaUrl`` we stamp on the resource. Centralising that here
   means a renamed attribute is a single-file change, not a hunt.

The semantic-convention key constants live in
:mod:`sponsio.tracer.semconv` — refer to those rather than hardcoding
``"sponsio.contract.label"`` etc. anywhere.
"""

from __future__ import annotations

import json
from typing import Any

from sponsio.models.trace import Event, Trace
from sponsio.tracer import semconv


# The consumer reads these attributes; we emit exactly this set
# plus any tool args.  Keep in sync with ``otel_consumer._is_llm_span``
# and the attribute lookups in ``otel_to_trace``.
_LLM_REQUEST_PROMPT_KEY = "gen_ai.prompt.0.content"
_LLM_RESPONSE_COMPLETION_KEY = "gen_ai.completion.0.content"
_LLM_INPUT_TOKENS_KEY = "gen_ai.usage.input_tokens"
_LLM_OUTPUT_TOKENS_KEY = "gen_ai.usage.output_tokens"
_LLM_SYSTEM_KEY = "gen_ai.system"
_LLM_MODEL_KEY = "gen_ai.request.model"


def _attr(key: str, value: Any) -> dict:
    """One OTLP attribute entry, tagging the value type correctly.

    The consumer handles string/int/double/bool; everything else
    we fall back to string-encoded so no data is silently lost.
    """
    if isinstance(value, bool):  # bool before int (bool IS-A int in Python)
        v: dict = {"boolValue": value}
    elif isinstance(value, int):
        v = {"intValue": str(value)}  # OTLP uses string for int64
    elif isinstance(value, float):
        v = {"doubleValue": value}
    elif isinstance(value, str):
        v = {"stringValue": value}
    else:
        v = {"stringValue": str(value)}
    return {"key": key, "value": v}


def _span_time_ns(event: Event) -> int:
    """Synthesize a per-event timestamp from the logical clock.

    ``Event.ts`` is a monotonically-increasing int (0, 1, 2, ...),
    not a real wall-clock time.  We map it to nanoseconds so the
    consumer's ``sort(key=start_ns)`` preserves order; the absolute
    epoch doesn't matter for replay correctness, only relative order.

    Using 1-second spacing keeps debugging output human-readable
    (each span visibly "fires" a second after the previous one in
    any viewer) without any semantic meaning.
    """
    base = 1_700_000_000_000_000_000  # ~2023-11-14, arbitrary fixed epoch
    step = 1_000_000_000
    return base + event.ts * step


def _build_llm_span(
    req: Event | None,
    resp: Event | None,
) -> dict:
    """Emit ONE OTLP span for an LLM call, optionally carrying both
    the prompt (from ``req``) and completion (from ``resp``).

    Why one span and not two?  The consumer's contract is
    "one LLM span → up to two events (``llm_request`` + ``llm_response``)."
    If the writer emitted two spans per LLM call, the consumer
    would synthesize up to four events, inflating token counts and
    confusing every ``at most`` / token-budget contract.  Pairing
    on the way out is the only way to keep the round-trip
    event-count-stable.

    Either side may be ``None`` — a completion-only span (no
    prompt) is legal and still renders as a valid LLM span on
    replay (consumer simply skips emitting ``llm_request``).
    """
    anchor = req or resp
    assert anchor is not None, "both req and resp cannot be None"
    start_ns = _span_time_ns(anchor)
    end_ns = start_ns + 500_000_000

    # Prefer req args for system/model (that's where they're
    # semantically set), fall back to resp if only resp exists.
    args = (req.args if req else None) or (resp.args if resp else None) or {}
    system = args.get("system", args.get("provider", "unknown"))
    model = args.get("model", "unknown")

    attrs: list[dict] = [
        _attr(_LLM_SYSTEM_KEY, system),
        _attr(_LLM_MODEL_KEY, model),
    ]

    if req is not None and req.content:
        attrs.append(_attr(_LLM_REQUEST_PROMPT_KEY, req.content))
    if resp is not None and resp.content:
        attrs.append(_attr(_LLM_RESPONSE_COMPLETION_KEY, resp.content))

    # Token counts — pull from whichever event provided them.
    # Consumer will sum input+output into total_tokens on replay.
    req_args = (req.args if req else None) or {}
    resp_args = (resp.args if resp else None) or {}
    if "input_tokens" in req_args:
        attrs.append(_attr(_LLM_INPUT_TOKENS_KEY, req_args["input_tokens"]))
    elif "input_tokens" in resp_args:
        attrs.append(_attr(_LLM_INPUT_TOKENS_KEY, resp_args["input_tokens"]))
    if "output_tokens" in resp_args:
        attrs.append(_attr(_LLM_OUTPUT_TOKENS_KEY, resp_args["output_tokens"]))
    elif "output_tokens" in req_args:
        attrs.append(_attr(_LLM_OUTPUT_TOKENS_KEY, req_args["output_tokens"]))

    return {
        "traceId": "0" * 32,
        "spanId": f"{anchor.ts:016x}",
        "name": "llm_call",
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "status": {"code": 1},
        "attributes": attrs,
    }


def _build_tool_span(event: Event) -> dict:
    """One OTLP span per tool_call event.  Straightforward; no pairing."""
    start_ns = _span_time_ns(event)
    end_ns = start_ns + 500_000_000
    attrs: list[dict] = []
    for k, v in (event.args or {}).items():
        # ``args.<k>`` is one of the three prefixes the consumer's
        # ``_parse_tool_args`` recognises — keep the key unchanged
        # so round-trip preserves the arg name verbatim.
        attrs.append(_attr(f"args.{k}", v))
    if event.content is not None:
        attrs.append(_attr("tool.output", event.content))

    span: dict = {
        "traceId": "0" * 32,
        "spanId": f"{event.ts:016x}",
        "name": event.tool or "tool_call",
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "status": {"code": 1},
    }
    if attrs:
        span["attributes"] = attrs
    return span


def _build_fallback_span(event: Event) -> dict:
    """Degrade exotic event types (``data_read``, ``data_write``,
    ``message``) into a named non-LLM span.

    OTLP JSON has no vocabulary for these, and the consumer
    reclassifies any non-LLM span as ``tool_call``.  The best we
    can do is preserve order + agent + a Sponsio-specific
    ``sponsio.*`` attribute payload so the raw data is still
    there if someone wants to pull it back out.  This is a
    documented lossy edge — callers who need lossless round-trip
    for data events should export in Sponsio-native JSON instead.
    """
    start_ns = _span_time_ns(event)
    end_ns = start_ns + 500_000_000
    attrs: list[dict] = []
    if event.tool is not None:
        attrs.append(_attr("sponsio.tool", event.tool))
    if event.key is not None:
        attrs.append(_attr("sponsio.key", event.key))
    if event.contains is not None:
        attrs.append(_attr("sponsio.contains", ",".join(event.contains)))
    if event.to is not None:
        attrs.append(_attr("sponsio.to", event.to))
    if event.content is not None:
        attrs.append(_attr("sponsio.content", event.content))

    span: dict = {
        "traceId": "0" * 32,
        "spanId": f"{event.ts:016x}",
        "name": event.event_type,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "status": {"code": 1},
    }
    if attrs:
        span["attributes"] = attrs
    return span


def _events_to_spans(events: list[Event]) -> list[dict]:
    """Walk events and emit one span per logical call.

    The non-trivial bit is LLM pairing: when we see an
    ``llm_request`` immediately followed by a compatible
    ``llm_response`` (same agent), we fuse them into one span
    so the consumer's split-on-the-way-back yields exactly the
    original two events, not three.  Unpaired LLM events
    degrade gracefully to completion-only or prompt-only spans.
    """
    spans: list[dict] = []
    i = 0
    while i < len(events):
        ev = events[i]
        if ev.event_type == "llm_request":
            # Look ahead for a matching llm_response.  "Matching"
            # means same agent and no interleaving events between
            # them — a conservative definition that mirrors how
            # real LLM traces look (prompt → completion, no gap).
            nxt = events[i + 1] if i + 1 < len(events) else None
            if (
                nxt is not None
                and nxt.event_type == "llm_response"
                and nxt.agent == ev.agent
            ):
                spans.append(_build_llm_span(ev, nxt))
                i += 2
                continue
            spans.append(_build_llm_span(ev, None))
            i += 1
            continue
        if ev.event_type == "llm_response":
            spans.append(_build_llm_span(None, ev))
            i += 1
            continue
        if ev.event_type == "tool_call":
            spans.append(_build_tool_span(ev))
            i += 1
            continue
        spans.append(_build_fallback_span(ev))
        i += 1
    return spans


def trace_to_otlp(
    trace: Trace,
    *,
    agent_id: str | None = None,
    service_name: str | None = None,
) -> dict:
    """Convert a Sponsio ``Trace`` to OTLP JSON that round-trips.

    ``agent_id`` / ``service_name`` are interchangeable — whichever
    you pass gets stamped as ``resource.attributes["service.name"]``,
    which is what the consumer reads as the per-event ``agent``.  If
    neither is set, we fall back to the first event's ``agent`` field
    and then to ``"agent"``.

    The output dict is directly assignable to
    ``json.dumps(...)`` without any further massaging.  Round-trip
    invariant: ``otel_to_trace(trace_to_otlp(t))`` preserves event
    ordering and tool names; LLM prompts/completions and token
    counts survive; agent identity survives.  Exotic event types
    (``data_*``, ``message``) get degraded to named spans on the
    way out because OTLP has no vocabulary for them — this is a
    documented limitation, not a bug.
    """
    resolved_agent = (
        service_name or agent_id or (trace.events[0].agent if trace.events else "agent")
    )

    spans = _events_to_spans(trace.events)

    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        _attr("service.name", resolved_agent),
                    ],
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "sponsio"},
                        "spans": spans,
                    }
                ],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Span-tree exporter (observability platforms read this)
# ---------------------------------------------------------------------------


def _truncate(text: str, limit: int) -> str:
    """Cap a string at ``limit`` bytes, marking the cut visibly.

    Truncation is byte-based (not codepoint-based) so a malicious
    long-emoji payload can't slip past the limit by being narrow on
    screen. The trailing marker is human-friendly so dashboard cards
    can show "(…+1.2 KB truncated)" without re-querying upstream.
    """
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    cut = raw[:limit].decode("utf-8", errors="ignore")
    overflow_kb = (len(raw) - limit) / 1024
    return f"{cut}…(+{overflow_kb:.1f} KB truncated)"


def _redact_args(args: Any) -> Any:
    """Strip values out of common secret-shape keys.

    A defence-in-depth filter for users who opt in to ``redact_args=True``
    on the writer. Keeps key names so the dashboard still shows
    "args.api_key was set" without leaking the value. Conservative — we
    only redact based on key name patterns, never on value heuristics
    (those would risk false-positive redacting normal text).
    """
    if not isinstance(args, dict):
        return args
    redacted: dict = {}
    for k, v in args.items():
        if isinstance(k, str) and any(
            t in k.lower() for t in ("password", "token", "secret", "key", "auth")
        ):
            redacted[k] = "<redacted>"
        elif isinstance(v, dict):
            redacted[k] = _redact_args(v)
        else:
            redacted[k] = v
    return redacted


def _new_span_id(counter: list[int]) -> str:
    """Sequential 16-hex-char span id. ``counter`` is a one-element list
    used as a closure-style mutable so the caller can share it across
    nested ``_visit`` recursions without threading a return value."""
    counter[0] += 1
    return f"{counter[0]:016x}"


def _wall_time_ns(span: Any) -> tuple[int, int]:
    """Return (start_ns, end_ns) for a Span.

    ``Span.start_time`` / ``end_time`` are ``time.monotonic()`` floats —
    relative to an arbitrary process-local epoch, NOT wall clock.
    Observability platforms expect Unix ns, so we anchor on a fixed
    epoch (``trace_to_otlp`` uses the same trick). Relative ordering and
    duration are preserved, which is what dashboards actually use.
    """
    base = 1_700_000_000_000_000_000
    start = base + int(span.start_time * 1_000_000_000)
    end_time = span.end_time if span.end_time is not None else span.start_time
    end = base + int(end_time * 1_000_000_000)
    return start, end


def _status_code(span: Any) -> int:
    """OTLP StatusCode: 1=OK, 2=ERROR. Sponsio "violated" maps to ERROR
    so dashboards' "% errors" widgets show contract violations as
    operational red without any extra mapping."""
    return 2 if span.status in ("violated", "error") else 1


def span_tree_to_otlp(
    turn_span: Any,
    *,
    agent_id: str | None = None,
    host: str | None = None,
    conversation_id: str | None = None,
    event_tool: str | None = None,
    event_args: Any = None,
    event_type: str | None = None,
    event_ts: int | None = None,
    redact_args: bool = True,
    truncate: bool = True,
    service_name: str | None = None,
) -> dict:
    """Convert an :class:`AgentTurnSpan` tree into OTLP JSON for export.

    The output is the **observability** view of one turn — what the
    agent attempted, which contracts ran, what each contract decided,
    and how Sponsio enforced. The ``sponsio.*`` attributes follow the
    semantic conventions in :mod:`sponsio.tracer.semconv`; downstream
    dashboards (Sponsio's own, plus any OTLP-aware platform) key off
    those stable names. The OTLP resource carries Sponsio's
    ``schemaUrl`` (``semconv.SCHEMA_URL``) so consumers can detect
    Sponsio spans before parsing attributes.

    This is *not* round-trippable to a :class:`Trace` — span trees
    record verdicts, not the underlying event sequence. For replay,
    use :func:`trace_to_otlp` against ``guard.trace`` instead.

    Args:
        turn_span: Root :class:`AgentTurnSpan` (one per ``check_action``).
        agent_id: Override the span's ``agent_id`` attribute. Defaults
            to ``turn_span.agent_id``.
        host: Host runtime tag (``"cursor"`` / ``"claude-code"`` /
            ``"openclaw"``). Optional — when omitted the
            ``sponsio.host`` attribute is left unset, which dashboards
            should treat as "legacy / code-wrapped".
        conversation_id: Per-IDE conversation id from the hook payload.
            Lets dashboards group turns by user conversation. Optional.
        event_tool, event_args, event_type, event_ts: The tool call this
            turn evaluated. Stamped on the root span so the "Today's
            blocks" card can render a single row per turn without
            walking children. ``event_args`` is JSON-encoded; pass
            ``None`` to skip emission entirely.
        redact_args: If True (default), strip values from any
            ``event_args`` key whose name looks like a credential
            (``password`` / ``token`` / ``secret`` / ``key`` / ``auth``).
            Set False only when downstream is trusted (your own
            dashboard, your own retention policy, your own legal team).
        truncate: If True (default), cap large string fields at the
            byte budgets in :mod:`sponsio.tracer.semconv`. Operators
            with a strict no-loss requirement can disable this; expect
            individual spans to balloon to MB scale on Bash calls with
            inline payloads.
        service_name: OTLP ``resource.service.name`` value. Defaults to
            ``agent_id``. Use this when one Sponsio process governs
            multiple logical services and you want the dashboard to
            split them.

    Returns:
        OTLP JSON dict suitable for ``json.dumps(...)`` and POSTing to
        any OTLP/HTTP collector. Contains exactly one ``resourceSpans``
        entry — batch multiple turns by collecting ``resourceSpans``
        from successive calls into a list.
    """
    counter: list[int] = [0]
    spans: list[dict] = []
    trace_id = "0" * 32  # one trace per turn; per-turn isolation is the norm

    def _visit(span: Any, parent_id: str | None) -> str:
        """Recursive visit. Returns the span id we minted for ``span``
        so child spans can reference it as parent."""
        sid = _new_span_id(counter)
        start_ns, end_ns = _wall_time_ns(span)

        attrs: list[dict] = []

        # Per-span-type attribute mapping. Each branch corresponds to
        # one of the SPAN_* constants in semconv.
        if span.span_type == semconv.SPAN_AGENT_TURN:
            attrs.extend(
                _emit_agent_turn_attrs(
                    span,
                    agent_id=agent_id,
                    host=host,
                    conversation_id=conversation_id,
                    event_tool=event_tool,
                    event_args=event_args,
                    event_type=event_type,
                    event_ts=event_ts,
                    redact_args=redact_args,
                    truncate=truncate,
                )
            )
        elif span.span_type == semconv.SPAN_CONTRACT_CHECK:
            attrs.extend(_emit_contract_check_attrs(span))
        elif span.span_type in (
            semconv.SPAN_PRECONDITION,
            semconv.SPAN_GUARANTEE,
        ):
            attrs.extend(_emit_constraint_attrs(span))
        elif span.span_type == semconv.SPAN_STO_EVAL:
            attrs.extend(_emit_sto_eval_attrs(span, truncate=truncate))
        elif span.span_type == semconv.SPAN_VIOLATION:
            attrs.extend(_emit_violation_attrs(span))
        elif span.span_type == semconv.SPAN_ENFORCEMENT:
            attrs.extend(_emit_enforcement_attrs(span, truncate=truncate))

        out: dict = {
            "traceId": trace_id,
            "spanId": sid,
            "name": span.span_type,
            "startTimeUnixNano": str(start_ns),
            "endTimeUnixNano": str(end_ns),
            "status": {"code": _status_code(span)},
        }
        if parent_id is not None:
            out["parentSpanId"] = parent_id
        if attrs:
            out["attributes"] = attrs
        spans.append(out)

        for child in span.children:
            _visit(child, sid)
        return sid

    _visit(turn_span, None)

    resolved_service = (
        service_name or agent_id or getattr(turn_span, "agent_id", "agent") or "agent"
    )

    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        _attr("service.name", resolved_service),
                    ],
                },
                "scopeSpans": [
                    {
                        "scope": {
                            "name": "sponsio",
                            "version": semconv.SCHEMA_VERSION,
                        },
                        "schemaUrl": semconv.SCHEMA_URL,
                        "spans": spans,
                    }
                ],
            }
        ],
    }


def _emit_agent_turn_attrs(
    span: Any,
    *,
    agent_id: str | None,
    host: str | None,
    conversation_id: str | None,
    event_tool: str | None,
    event_args: Any,
    event_type: str | None,
    event_ts: int | None,
    redact_args: bool,
    truncate: bool,
) -> list[dict]:
    """Map :class:`AgentTurnSpan` → root-span attributes.

    Emits the ``sponsio.event.*`` and ``sponsio.outcome.*`` namespaces
    plus the per-turn aggregate counters. The aggregates (``contracts_checked``
    / ``det_violations`` / ``sto_violations``) duplicate what's
    summable from children so the dashboard's "Today's blocks" card
    can render one row per turn with a single attribute lookup."""
    attrs: list[dict] = []

    aid = agent_id or getattr(span, "agent_id", None)
    if aid:
        attrs.append(_attr(semconv.ATTR_AGENT_ID, aid))
    if host:
        attrs.append(_attr(semconv.ATTR_HOST, host))
    if conversation_id:
        attrs.append(_attr(semconv.ATTR_CONVERSATION_ID, conversation_id))

    tool = event_tool or getattr(span, "action", None)
    if tool:
        attrs.append(_attr(semconv.ATTR_EVENT_TOOL, tool))
    if event_type:
        attrs.append(_attr(semconv.ATTR_EVENT_TYPE, event_type))
    if event_ts is not None:
        attrs.append(_attr(semconv.ATTR_EVENT_TS, event_ts))

    if event_args is not None:
        rendered = _redact_args(event_args) if redact_args else event_args
        # JSON-encode regardless of redaction so dashboards always parse
        # the same shape. Compact separators reduce on-the-wire bytes.
        encoded = json.dumps(rendered, separators=(",", ":"), default=str)
        if truncate:
            encoded = _truncate(encoded, semconv.EVENT_ARGS_MAX_BYTES)
        attrs.append(_attr(semconv.ATTR_EVENT_TOOL_ARGS, encoded))

    blocked = getattr(span, "blocked", None)
    if blocked is not None:
        attrs.append(_attr(semconv.ATTR_OUTCOME_BLOCKED, bool(blocked)))
    attrs.append(_attr(semconv.ATTR_OUTCOME_STATUS, span.status))

    if hasattr(span, "total_contracts_checked"):
        attrs.append(
            _attr(semconv.ATTR_CONTRACTS_CHECKED, span.total_contracts_checked)
        )
    if hasattr(span, "det_violations"):
        attrs.append(_attr(semconv.ATTR_DET_VIOLATIONS, span.det_violations))
    if hasattr(span, "sto_violations"):
        attrs.append(_attr(semconv.ATTR_STO_VIOLATIONS, span.sto_violations))

    if span.end_time is not None:
        attrs.append(
            _attr(
                semconv.ATTR_TURN_DURATION_NS,
                int((span.end_time - span.start_time) * 1_000_000_000),
            )
        )

    return attrs


def _emit_contract_check_attrs(span: Any) -> list[dict]:
    """Map :class:`ContractCheckSpan` → ``sponsio.contract.*`` attributes."""
    attrs: list[dict] = []
    label = getattr(span, "contract_name", None)
    if label:
        attrs.append(_attr(semconv.ATTR_CONTRACT_LABEL, label))

    pipeline = getattr(span, "pipeline", None)
    if pipeline:
        # Normalise the legacy "hard" alias to the public "det" name —
        # this is the value the dashboard's pipeline filter uses, so
        # picking one canonical form matters.
        attrs.append(
            _attr(
                semconv.ATTR_CONTRACT_PIPELINE,
                "det" if pipeline == "hard" else pipeline,
            )
        )

    # Contract-level extras the runtime drops into the ``attributes``
    # bag (id, alpha/beta thresholds, source tag, activate_at). The
    # core span object doesn't have typed fields for these, so we
    # forward whatever the monitor stamped.
    bag = getattr(span, "attributes", {}) or {}
    for src_key, dst_key in (
        ("contract_id", semconv.ATTR_CONTRACT_ID),
        ("source", semconv.ATTR_CONTRACT_SOURCE),
        ("alpha", semconv.ATTR_CONTRACT_ALPHA),
        ("beta", semconv.ATTR_CONTRACT_BETA),
        ("activate_at", semconv.ATTR_CONTRACT_ACTIVATE_AT),
        ("assumption_holds", semconv.ATTR_CONTRACT_ASSUMPTION_HOLDS),
        ("enforcement_holds", semconv.ATTR_CONTRACT_ENFORCEMENT_HOLDS),
    ):
        if src_key in bag and bag[src_key] is not None:
            attrs.append(_attr(dst_key, bag[src_key]))

    return attrs


def _emit_constraint_attrs(span: Any) -> list[dict]:
    """Map :class:`PreconditionSpan` / :class:`GuaranteeSpan` →
    ``sponsio.constraint.*`` attributes."""
    attrs: list[dict] = []
    desc = getattr(span, "formula_desc", None)
    if desc:
        attrs.append(_attr(semconv.ATTR_CONSTRAINT_DESC, desc))
    result = getattr(span, "result", None)
    if result is not None:
        attrs.append(
            _attr(
                semconv.ATTR_CONSTRAINT_RESULT,
                "ok" if result else "violated",
            )
        )

    bag = getattr(span, "attributes", {}) or {}
    for src_key, dst_key in (
        ("formula", semconv.ATTR_CONSTRAINT_FORMULA),
        ("fresh", semconv.ATTR_CONSTRAINT_FRESH),
        ("eval_pos", semconv.ATTR_CONSTRAINT_EVAL_POS),
    ):
        if src_key in bag and bag[src_key] is not None:
            attrs.append(_attr(dst_key, bag[src_key]))
    return attrs


def _emit_sto_eval_attrs(span: Any, *, truncate: bool) -> list[dict]:
    """Map :class:`StoEvalSpan` → sto-flavoured constraint attributes."""
    attrs: list[dict] = []
    name = getattr(span, "constraint_name", None)
    if name:
        attrs.append(_attr(semconv.ATTR_CONSTRAINT_DESC, name))
        attrs.append(_attr(semconv.ATTR_CONSTRAINT_ATOM, name))
    score = getattr(span, "score", None)
    if score is not None:
        attrs.append(_attr(semconv.ATTR_CONSTRAINT_SCORE, float(score)))
    threshold = getattr(span, "threshold", None)
    if threshold is not None:
        attrs.append(_attr(semconv.ATTR_CONSTRAINT_THRESHOLD, float(threshold)))
    passed = getattr(span, "passed", None)
    if passed is not None:
        attrs.append(_attr(semconv.ATTR_CONSTRAINT_PASSED, bool(passed)))
        attrs.append(
            _attr(
                semconv.ATTR_CONSTRAINT_RESULT,
                "ok" if passed else "violated",
            )
        )

    bag = getattr(span, "attributes", {}) or {}
    for src_key, dst_key in (
        ("evidence", semconv.ATTR_CONSTRAINT_EVIDENCE),
        ("suggestion", semconv.ATTR_CONSTRAINT_SUGGESTION),
        ("judge_model", semconv.ATTR_JUDGE_MODEL),
        ("judge_latency_ms", semconv.ATTR_JUDGE_LATENCY_MS),
    ):
        val = bag.get(src_key)
        if val is None:
            continue
        if src_key == "evidence" and truncate and isinstance(val, str):
            val = _truncate(val, semconv.CONSTRAINT_EVIDENCE_MAX_BYTES)
        attrs.append(_attr(dst_key, val))
    return attrs


def _emit_violation_attrs(span: Any) -> list[dict]:
    """Map :class:`ViolationSpan` → ``sponsio.violation.*`` attributes."""
    attrs: list[dict] = []
    kind = getattr(span, "kind", None)
    if kind:
        attrs.append(_attr(semconv.ATTR_VIOLATION_KIND, kind))
    severity = getattr(span, "severity", None)
    if severity:
        attrs.append(_attr(semconv.ATTR_VIOLATION_SEVERITY, severity))
    evidence = getattr(span, "evidence", None)
    if evidence:
        attrs.append(_attr(semconv.ATTR_VIOLATION_EVIDENCE, evidence))

    # Optional traceability link to the user's policy source-of-truth.
    bag = getattr(span, "attributes", {}) or {}
    if bag.get("policy_ref"):
        attrs.append(_attr(semconv.ATTR_VIOLATION_POLICY_REF, bag["policy_ref"]))
    return attrs


def _emit_enforcement_attrs(span: Any, *, truncate: bool) -> list[dict]:
    """Map :class:`EnforcementSpan` → ``sponsio.enforcement.*`` attributes."""
    attrs: list[dict] = []
    strategy = getattr(span, "strategy", None)
    if strategy:
        attrs.append(_attr(semconv.ATTR_ENFORCEMENT_STRATEGY, strategy))
    action = getattr(span, "result_action", None)
    if action:
        attrs.append(_attr(semconv.ATTR_ENFORCEMENT_ACTION, action))

    bag = getattr(span, "attributes", {}) or {}
    retry = bag.get("retry_prompt")
    if retry:
        if truncate and isinstance(retry, str):
            retry = _truncate(retry, semconv.ENFORCEMENT_RETRY_PROMPT_MAX_BYTES)
        attrs.append(_attr(semconv.ATTR_ENFORCEMENT_RETRY_PROMPT, retry))
    fallback = bag.get("fallback_action")
    if fallback:
        attrs.append(_attr(semconv.ATTR_ENFORCEMENT_FALLBACK_ACTION, fallback))
    return attrs
