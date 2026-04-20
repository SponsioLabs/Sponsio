"""Tests for sponsio.tracer.otel_consumer."""

from __future__ import annotations

from sponsio.tracer.otel_consumer import otel_to_trace


def _make_otel(spans, agent="test_agent"):
    """Build minimal OTLP JSON with given spans."""
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": agent}}
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "test"},
                        "spans": spans,
                    }
                ],
            }
        ]
    }


def _tool_span(name, start_ns, attrs=None):
    span = {
        "name": name,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(start_ns + 1000),
    }
    if attrs:
        span["attributes"] = attrs
    return span


def _llm_span(name, start_ns, model="gpt-4", completion="Hello"):
    return {
        "name": name,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(start_ns + 1000),
        "attributes": [
            {"key": "gen_ai.system", "value": {"stringValue": "openai"}},
            {"key": "gen_ai.request.model", "value": {"stringValue": model}},
            {
                "key": "gen_ai.completion.0.content",
                "value": {"stringValue": completion},
            },
            {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "100"}},
            {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "50"}},
        ],
    }


class TestOtelToTrace:
    def test_basic_tool_calls(self):
        data = _make_otel(
            [
                _tool_span("check_policy", 1000),
                _tool_span("issue_refund", 2000),
            ]
        )
        trace = otel_to_trace(data)
        assert len(trace.events) == 2
        assert trace.events[0].tool == "check_policy"
        assert trace.events[1].tool == "issue_refund"
        assert trace.events[0].event_type == "tool_call"

    def test_sorted_by_start_time(self):
        data = _make_otel(
            [
                _tool_span("second", 2000),
                _tool_span("first", 1000),
            ]
        )
        trace = otel_to_trace(data)
        assert trace.events[0].tool == "first"
        assert trace.events[1].tool == "second"

    def test_agent_from_resource(self):
        data = _make_otel(
            [_tool_span("test", 1000)],
            agent="my_bot",
        )
        trace = otel_to_trace(data)
        assert trace.events[0].agent == "my_bot"

    def test_llm_span_classified(self):
        data = _make_otel(
            [
                _tool_span("lookup", 1000),
                _llm_span("chat", 2000, model="gemini-2.0-flash", completion="OK"),
            ]
        )
        trace = otel_to_trace(data)
        assert trace.events[0].event_type == "tool_call"
        assert trace.events[1].event_type == "llm_response"
        assert trace.events[1].content == "OK"
        assert trace.events[1].args["model"] == "gemini-2.0-flash"
        assert trace.events[1].args["input_tokens"] == 100

    def test_tool_with_output(self):
        data = _make_otel(
            [
                _tool_span(
                    "query",
                    1000,
                    attrs=[
                        {"key": "tool.output", "value": {"stringValue": "42 rows"}},
                    ],
                ),
            ]
        )
        trace = otel_to_trace(data)
        assert trace.events[0].content == "42 rows"

    def test_tool_with_args(self):
        data = _make_otel(
            [
                _tool_span(
                    "search",
                    1000,
                    attrs=[
                        {"key": "tool.input.query", "value": {"stringValue": "hello"}},
                        {"key": "tool.input.limit", "value": {"intValue": "10"}},
                    ],
                ),
            ]
        )
        trace = otel_to_trace(data)
        assert trace.events[0].args == {"query": "hello", "limit": 10}

    def test_empty_trace(self):
        trace = otel_to_trace({"resourceSpans": []})
        assert len(trace.events) == 0

    def test_existing_trace_files(self):
        """Verify the example trace files still parse correctly."""
        import json

        with open("examples/traces/good_trace.json") as f:
            data = json.load(f)
        trace = otel_to_trace(data)
        assert len(trace.events) == 2
        assert trace.events[0].tool == "check_policy"
        assert trace.events[1].tool == "issue_refund"
