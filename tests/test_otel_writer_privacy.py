"""What the OTLP exporter is allowed to put in a span.

It put every tool argument, the whole prompt and the whole completion
into span attributes, and read no privacy setting at all. An operator who
set SPONSIO_PRIVACY=tool_calls and exported OTLP sent all of it anyway.
The point of that setting is that the data does not leave the machine.
"""

from __future__ import annotations

import pytest

from sponsio.models.trace import Event, Trace
from sponsio.tracer.otel_writer import trace_to_otlp


def trace_with_secrets() -> Trace:
    t = Trace()
    t.events.append(
        Event(ts=0, event_type="tool_call", tool="lookup_case", agent="a",
              args={"ssn": "999-88-7777", "case": 12}, content="found Jane Roe")
    )
    t.events.append(
        Event(ts=1, event_type="llm_request", agent="a",
              args={"model": "gpt-x"}, content="the patient's ssn is 999-88-7777")
    )
    t.events.append(
        Event(ts=2, event_type="llm_response", agent="a",
              args={"model": "gpt-x"}, content="I will not repeat it")
    )
    return t


def rendered(level: str | None, monkeypatch) -> str:
    import json

    monkeypatch.delenv("SPONSIO_PRIVACY", raising=False)
    return json.dumps(trace_to_otlp(trace_with_secrets(), privacy=level))


def test_full_is_unchanged(monkeypatch):
    """Nothing changes for anyone who has not asked for it."""
    out = rendered("full", monkeypatch)
    assert "999-88-7777" in out and "found Jane Roe" in out


@pytest.mark.parametrize("level", ["metadata", "tool_calls"])
def test_contents_never_leave_the_machine(level, monkeypatch):
    out = rendered(level, monkeypatch)
    assert "999-88-7777" not in out
    assert "found Jane Roe" not in out
    assert "the patient's ssn" not in out
    # the call itself still does
    assert "lookup_case" in out


def test_the_argument_names_survive_below_full(monkeypatch):
    """"args.ssn was set" is a debugging fact; its value is not."""
    out = rendered("shape", monkeypatch)
    assert "args.ssn" in out and "999-88-7777" not in out


def test_hashed_keeps_repeats_comparable(monkeypatch):
    import json

    monkeypatch.delenv("SPONSIO_PRIVACY", raising=False)
    one = json.dumps(trace_to_otlp(trace_with_secrets(), privacy="hashed"))
    two = json.dumps(trace_to_otlp(trace_with_secrets(), privacy="hashed"))
    assert one == two and "999-88-7777" not in one


def test_tool_calls_drops_the_model_turn_whole(monkeypatch):
    out = rendered("tool_calls", monkeypatch)
    assert "llm_call" not in out
    assert "lookup_case" in out


def test_the_environment_beats_the_argument(monkeypatch):
    """The person who decides what may leave a machine is its operator,
    not the author of the agent running on it."""
    import json

    monkeypatch.setenv("SPONSIO_PRIVACY", "tool_calls")
    out = json.dumps(trace_to_otlp(trace_with_secrets(), privacy="full"))
    assert "999-88-7777" not in out
