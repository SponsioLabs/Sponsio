"""OTEL trace writer — convert a Sponsio ``Trace`` to OTLP JSON.

Dual of :mod:`sponsio.tracer.otel_consumer`: serialises a live
(or loaded) ``Trace`` into the exact OTLP JSON shape that
``otel_to_trace`` consumes, so a runtime-captured trace can be
replayed by ``sponsio eval`` without any shape mismatch.

Why a dedicated writer and not a quick one-liner?  Three reasons:

1. **Round-trip guarantee**: downstream code (``eval``, tests,
   anyone doing regression replay) already assumes the consumer's
   shape.  Handrolling OTLP in N callsites means N subtly-different
   shapes; a single writer gives us one place to keep them in sync.
2. **Attribute encoding is finicky**: OTLP uses a tagged-union
   style (``{"stringValue": ...}`` vs ``{"intValue": ...}``) that
   every ad-hoc emitter gets wrong in at least one edge case.
3. **Future OTel exporters**: if we ever add real OTel SDK export,
   this module is where the "canonical Sponsio span shape" lives.

Intentionally minimal — we emit only the attributes the consumer
reads, not the full OTel semantic-conventions surface.  Anything
the evaluator doesn't care about is dead bytes and a landmine for
drift.
"""

from __future__ import annotations

from typing import Any

from sponsio.models.trace import Event, Trace


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
