"""OTEL trace consumer — convert OTLP JSON to Sponsio Trace.

Parses OpenTelemetry trace exports (OTLP JSON format) and produces
Sponsio ``Trace`` objects ready for grounding and evaluation.

Classifies spans into event types:
- **LLM spans** (gen_ai.* / openinference LLM attributes) → ``llm_*`` events
- **Tool spans** (everything else) → ``tool_call`` events

Recognised semantic conventions:

**OTel Gen AI** (the W3C-bound vocabulary)::

    gen_ai.system                    → LLM provider ("openai", "anthropic")
    gen_ai.request.model             → model name
    gen_ai.prompt.{i}.content        → prompt text
    gen_ai.completion.{i}.content    → completion text
    gen_ai.usage.input_tokens        → token count
    gen_ai.usage.output_tokens       → token count

**OpenInference** (used by Arize Phoenix, MLflow, Langfuse; a
superset of Gen AI for AI-specific workloads)::

    openinference.span.kind          → "LLM" | "TOOL" | "CHAIN" | ...
    llm.model_name                   → model name
    llm.input_messages.{i}.message.content   → prompt text
    llm.output_messages.{i}.message.content  → completion text
    llm.token_count.prompt           → token count
    llm.token_count.completion       → token count
    tool.name                        → tool name (override span.name)
    tool.parameters                  → tool args (JSON string)
    input.value / output.value       → free-form Phoenix fallbacks

Usage::

    from sponsio.tracer.otel_consumer import otel_to_trace

    with open("trace.json") as f:
        data = json.load(f)
    trace = otel_to_trace(data)
"""

from __future__ import annotations

from typing import Any

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


_OPENINFERENCE_LLM_KINDS = frozenset({"LLM", "EMBEDDING", "CHAT_MODEL"})


def _is_llm_span(attrs: dict) -> bool:
    """Check if span attributes indicate an LLM call.

    Accepts both conventions:

    * **OTel Gen AI** — ``gen_ai.system`` / ``gen_ai.request.model``.
    * **OpenInference** — ``openinference.span.kind in {LLM, ...}`` or
      any ``llm.*`` attribute (most common: ``llm.model_name``).
    """
    if "gen_ai.system" in attrs or "gen_ai.request.model" in attrs:
        return True
    kind = attrs.get("openinference.span.kind")
    if isinstance(kind, str) and kind.upper() in _OPENINFERENCE_LLM_KINDS:
        return True
    return any(k.startswith("llm.") for k in attrs)


def _first(attrs: dict, *keys: str, default: Any = None) -> Any:
    """Return the first non-empty value in ``attrs`` among ``keys``.

    Empty string is treated as missing (Phoenix/Langfuse exports
    sometimes emit placeholder ``""`` attributes).
    """
    for k in keys:
        v = attrs.get(k)
        if v is not None and v != "":
            return v
    return default


def _parse_tool_args(attrs: dict) -> dict | None:
    """Extract tool arguments from span attributes.

    Supports three conventions in order of preference:

    1. **OpenInference** — ``tool.parameters`` (JSON string) is parsed
       when present; ``input.value`` is a last-resort free-form fallback.
    2. **OTel Gen AI + ad-hoc** — any ``tool.input.*`` / ``input.*`` /
       ``args.*`` attribute becomes a named argument.
    """
    # OpenInference: tool.parameters is typically a JSON-encoded dict.
    raw = attrs.get("tool.parameters")
    if isinstance(raw, str) and raw.strip().startswith(("{", "[")):
        try:
            import json as _json

            parsed = _json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            pass

    args: dict = {}
    for key, val in attrs.items():
        # Common patterns: tool.input.*, input.*, args.*
        for prefix in ("tool.input.", "input.", "args."):
            if key.startswith(prefix):
                args[key[len(prefix) :]] = val
    if args:
        return args

    # Last-resort: Phoenix's generic ``input.value`` (free-form).
    inp = attrs.get("input.value")
    if isinstance(inp, str) and inp:
        return {"value": inp}
    return None


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
            # Fallback chains accept OTel Gen AI *and* OpenInference
            # attribute names so exports from Phoenix / Langfuse /
            # MLflow work without translation.

            prompt = _first(
                span_attrs,
                "gen_ai.prompt.0.content",
                "gen_ai.prompt",
                "llm.input_messages.0.message.content",
                "llm.prompts",
                "input.value",
                default="",
            )
            completion = _first(
                span_attrs,
                "gen_ai.completion.0.content",
                "gen_ai.completion",
                "llm.output_messages.0.message.content",
                "output.value",
                default="",
            )
            input_tokens = _first(
                span_attrs,
                "gen_ai.usage.input_tokens",
                "llm.token_count.prompt",
            )
            output_tokens = _first(
                span_attrs,
                "gen_ai.usage.output_tokens",
                "llm.token_count.completion",
            )
            total_tokens = None
            if input_tokens is not None and output_tokens is not None:
                try:
                    total_tokens = int(input_tokens) + int(output_tokens)
                except (ValueError, TypeError):
                    pass

            # Has a system prompt?  OpenInference uses
            # ``llm.input_messages.{i}.message.role`` so check both
            # role slots and the OTel system-instruction key.
            system_prompt_present = bool(
                span_attrs.get("gen_ai.prompt.0.role") == "system"
                or span_attrs.get("llm.input_messages.0.message.role") == "system"
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
                "model": _first(
                    span_attrs,
                    "gen_ai.request.model",
                    "llm.model_name",
                ),
                "system": _first(
                    span_attrs,
                    "gen_ai.system",
                    "llm.provider",
                ),
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
            # Tool call span — prefer OpenInference's explicit
            # ``tool.name`` over the span name (which is sometimes a
            # framework-generic label like ``langchain.tool``).
            tool_name = _first(span_attrs, "tool.name") or span.get("name", "")
            tool_output = _first(
                span_attrs,
                "tool.output",
                "output.value",
                "output",
            )
            events.append(
                Event(
                    ts=len(events),
                    agent=agent,
                    event_type="tool_call",
                    tool=tool_name,
                    args=_parse_tool_args(span_attrs),
                    content=tool_output,
                )
            )

    return Trace(events=events)
