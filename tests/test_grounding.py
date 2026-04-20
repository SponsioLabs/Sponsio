"""Unit tests for sponsio/tracer/grounding.py — trace-to-predicate conversion."""

from sponsio.models.trace import Event, Trace
from sponsio.tracer.grounding import ground


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_trace(*events: Event) -> Trace:
    return Trace(events=list(events))


def tool_event(ts: int, agent: str, tool: str) -> Event:
    return Event(ts=ts, agent=agent, event_type="tool_call", tool=tool)


def write_event(ts: int, agent: str, key: str, contains: list[str]) -> Event:
    return Event(
        ts=ts, agent=agent, event_type="data_write", key=key, contains=contains
    )


def read_event(ts: int, agent: str, key: str) -> Event:
    return Event(ts=ts, agent=agent, event_type="data_read", key=key)


def msg_event(ts: int, agent: str, to: str) -> Event:
    return Event(ts=ts, agent=agent, event_type="message", to=to)


# ---------------------------------------------------------------------------
# tool_call -> called predicate
# ---------------------------------------------------------------------------


def test_tool_call_produces_called_predicate():
    trace = make_trace(tool_event(0, "bot", "fraud_check"))
    vals = ground(trace)
    assert vals[0].get("called(fraud_check)") is True


def test_two_tool_calls_both_marked():
    trace = make_trace(
        tool_event(0, "bot", "check_policy"),
        tool_event(1, "bot", "issue_refund"),
    )
    vals = ground(trace)
    assert vals[0].get("called(check_policy)") is True
    assert vals[1].get("called(issue_refund)") is True


def test_tool_call_other_tool_not_marked():
    trace = make_trace(tool_event(0, "bot", "fraud_check"))
    vals = ground(trace)
    assert "called(other_tool)" not in vals[0]


# ---------------------------------------------------------------------------
# tool_call -> precedes predicate
# ---------------------------------------------------------------------------


def test_precedes_removed_from_grounding():
    """precedes() is no longer generated — ordering is handled by LTL Until operator."""
    trace = make_trace(
        tool_event(0, "bot", "check_policy"),
        tool_event(1, "bot", "issue_refund"),
    )
    vals = ground(trace)
    # precedes() keys should NOT be present
    for v in vals:
        assert not any("precedes" in k for k in v)


# ---------------------------------------------------------------------------
# data_write -> contains predicate
# ---------------------------------------------------------------------------


def test_data_write_contains_field():
    trace = make_trace(write_event(0, "bot", "cache", ["pii", "name"]))
    vals = ground(trace)
    assert vals[0].get("contains(pii)") is True
    assert vals[0].get("contains(name)") is True


def test_data_write_no_contains_no_predicate():
    trace = make_trace(Event(ts=0, agent="bot", event_type="data_write", key="cache"))
    vals = ground(trace)
    assert not any("contains" in k for k in vals[0])


# ---------------------------------------------------------------------------
# data_read + data_write -> flow predicate
# ---------------------------------------------------------------------------


def test_cross_agent_read_creates_flow():
    trace = make_trace(
        write_event(0, "agent_a", "cache", ["data"]),
        read_event(1, "agent_b", "cache"),
    )
    vals = ground(trace)
    assert vals[1].get("flow(agent_a, agent_b)") is True


def test_same_agent_read_no_flow():
    trace = make_trace(
        write_event(0, "bot", "cache", ["data"]),
        read_event(1, "bot", "cache"),
    )
    vals = ground(trace)
    assert "flow(bot, bot)" not in vals[1]


def test_read_from_unknown_key_no_flow():
    trace = make_trace(read_event(0, "bot", "nonexistent_key"))
    vals = ground(trace)
    assert not any("flow" in k for k in vals[0])


# ---------------------------------------------------------------------------
# message -> flow predicate
# ---------------------------------------------------------------------------


def test_message_creates_flow():
    trace = make_trace(msg_event(0, "agent_a", "agent_b"))
    vals = ground(trace)
    assert vals[0].get("flow(agent_a, agent_b)") is True


def test_message_without_to_no_flow():
    trace = make_trace(Event(ts=0, agent="bot", event_type="message"))
    vals = ground(trace)
    assert not any("flow" in k for k in vals[0])


# ---------------------------------------------------------------------------
# Flow forward-propagation
# ---------------------------------------------------------------------------


def test_flow_propagates_forward():
    trace = make_trace(
        write_event(0, "agent_a", "cache", ["data"]),
        read_event(1, "agent_b", "cache"),
        tool_event(2, "agent_b", "process"),
    )
    vals = ground(trace)
    # flow observed at ts=1 must persist at ts=2
    assert vals[2].get("flow(agent_a, agent_b)") is True


# ---------------------------------------------------------------------------
# Empty trace
# ---------------------------------------------------------------------------


def test_empty_trace_returns_empty_list():
    vals = ground(Trace())
    assert vals == []


# ---------------------------------------------------------------------------
# tool_call -> count predicate
# ---------------------------------------------------------------------------


def test_count_increments():
    trace = make_trace(
        tool_event(0, "bot", "issue_refund"),
        tool_event(1, "bot", "issue_refund"),
        tool_event(2, "bot", "issue_refund"),
    )
    vals = ground(trace)
    assert vals[0].get("count(issue_refund)") == 1
    assert vals[1].get("count(issue_refund)") == 2
    assert vals[2].get("count(issue_refund)") == 3


def test_count_per_tool():
    trace = make_trace(
        tool_event(0, "bot", "check_policy"),
        tool_event(1, "bot", "issue_refund"),
        tool_event(2, "bot", "check_policy"),
    )
    vals = ground(trace)
    assert vals[0].get("count(check_policy)") == 1
    assert vals[1].get("count(check_policy)") == 1
    assert vals[1].get("count(issue_refund)") == 1
    assert vals[2].get("count(check_policy)") == 2
    assert vals[2].get("count(issue_refund)") == 1


def test_count_propagates_to_non_call_events():
    trace = make_trace(
        tool_event(0, "bot", "issue_refund"),
        msg_event(1, "bot", "user"),  # not a tool_call
    )
    vals = ground(trace)
    # count should still be visible at step 1
    assert vals[1].get("count(issue_refund)") == 1
