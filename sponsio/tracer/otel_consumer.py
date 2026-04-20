"""OTEL trace consumer — convert OTLP JSON to Sponsio Trace.

Parses OpenTelemetry trace exports (OTLP JSON format) and produces
Sponsio ``Trace`` objects ready for grounding and evaluation.

Classifies spans into event types:
- **LLM spans** (gen_ai.* attributes) → ``llm_response`` events
- **Tool spans** (everything else) → ``tool_call`` events

Extracts rich attributes following OTEL Gen AI semantic conventions:

    gen_ai.system                    → LLM provider ("openai", "anthropic")
    gen_ai.request.model             → model name
    gen_ai.prompt.{i}.content        → prompt text
    gen_ai.completion.{i}.content    → completion text
    gen_ai.usage.input_tokens        → token count
    gen_ai.usage.output_tokens       → token count

Usage::

    from sponsio.tracer.otel_consumer import otel_to_trace

    with open("trace.json") as f:
        data = json.load(f)
    trace = otel_to_trace(data)
"""

from __future__ import annotations

from sponsio.models.trace import Event, Trace


def _extract_attrs(span: dict) -> dict:
    """Extract span attributes into a flat dict."""
    attrs = {}
    for attr in span.get("attributes", []):
        key = attr.get("key", "")
        val = attr.get("value", {})
        if "stringValue" in val:
            attrs[key] = val["stringValue"]
        elif "intValue" in val:
            attrs[key] = int(val["intValue"])
        elif "doubleValue" in val:
            attrs[key] = float(val["doubleValue"])
        elif "boolValue" in val:
            attrs[key] = val["boolValue"]
    return attrs


def _flatten_spans(data: dict) -> list[dict]:
    """Flatten OTLP resourceSpans → list of (span, resource_attrs) tuples sorted by time."""
    flat = []
    for rs in data.get("resourceSpans", []):
        resource_attrs = {}
        for attr in rs.get("resource", {}).get("attributes", []):
            key = attr.get("key", "")
            val = attr.get("value", {})
            if "stringValue" in val:
                resource_attrs[key] = val["stringValue"]

        agent = resource_attrs.get("service.name", "agent")

        for ss in rs.get("scopeSpans", []):
            for span in ss.get("spans", []):
                start_ns = int(span.get("startTimeUnixNano", "0"))
                flat.append(
                    {
                        "span": span,
                        "agent": agent,
                        "start_ns": start_ns,
                        "resource_attrs": resource_attrs,
                    }
                )

    flat.sort(key=lambda s: s["start_ns"])
    return flat


def _is_llm_span(attrs: dict) -> bool:
    """Check if span attributes indicate an LLM call."""
    return "gen_ai.system" in attrs or "gen_ai.request.model" in attrs


def _parse_tool_args(attrs: dict) -> dict | None:
    """Extract tool arguments from span attributes."""
    args = {}
    for key, val in attrs.items():
        # Common patterns: tool.input.*, input.*, args.*
        for prefix in ("tool.input.", "input.", "args."):
            if key.startswith(prefix):
                args[key[len(prefix) :]] = val
    return args if args else None


def otel_to_trace(data: dict) -> Trace:
    """Convert OTEL JSON to Sponsio Trace with rich event extraction.

    Supports OTLP format (``resourceSpans``). Classifies spans as LLM
    or tool call based on ``gen_ai.*`` attributes.

    Args:
        data: Parsed OTLP JSON (dict with ``resourceSpans`` key).

    Returns:
        A ``Trace`` with events ordered by span start time.
    """
    events = []
    for item in _flatten_spans(data):
        span = item["span"]
        agent = item["agent"]
        span_attrs = _extract_attrs(span)

        if _is_llm_span(span_attrs):
            # LLM span — extract prompt/completion/token info.
            # Emit TWO events: llm_request (prompt) + llm_response (completion)
            # so both prompt_contains and llm_said atoms can be grounded.

            prompt = span_attrs.get(
                "gen_ai.prompt.0.content",
                span_attrs.get("gen_ai.prompt", ""),
            )
            completion = span_attrs.get(
                "gen_ai.completion.0.content",
                span_attrs.get("gen_ai.completion", ""),
            )
            input_tokens = span_attrs.get("gen_ai.usage.input_tokens")
            output_tokens = span_attrs.get("gen_ai.usage.output_tokens")
            total_tokens = None
            if input_tokens is not None and output_tokens is not None:
                try:
                    total_tokens = int(input_tokens) + int(output_tokens)
                except (ValueError, TypeError):
                    pass

            # Has a system prompt?
            system_prompt_present = bool(
                span_attrs.get("gen_ai.prompt.0.role") == "system"
                or span_attrs.get("gen_ai.system_instruction")
            )

            # llm_request event (for prompt_contains, token_count, etc.)
            if prompt:
                req_args: dict = {}
                if system_prompt_present:
                    req_args["system_prompt_present"] = True
                if input_tokens is not None:
                    req_args["char_count"] = len(str(prompt))
                events.append(
                    Event(
                        ts=len(events),
                        agent=agent,
                        event_type="llm_request",
                        content=prompt or None,
                        args=req_args or None,
                    )
                )

            # llm_response event (for llm_said, token_count, etc.)
            resp_args: dict = {}
            for k, v in {
                "model": span_attrs.get("gen_ai.request.model"),
                "system": span_attrs.get("gen_ai.system"),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "tokens": total_tokens,
            }.items():
                if v is not None:
                    resp_args[k] = v

            events.append(
                Event(
                    ts=len(events),
                    agent=agent,
                    event_type="llm_response",
                    content=completion or None,
                    args=resp_args or None,
                )
            )
        else:
            # Tool call span
            tool_output = span_attrs.get(
                "tool.output",
                span_attrs.get("output", None),
            )
            events.append(
                Event(
                    ts=len(events),
                    agent=agent,
                    event_type="tool_call",
                    tool=span.get("name", ""),
                    args=_parse_tool_args(span_attrs),
                    content=tool_output,
                )
            )

    return Trace(events=events)
