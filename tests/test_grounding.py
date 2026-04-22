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
# Contains forward-propagation
# ---------------------------------------------------------------------------


def test_contains_propagates_forward():
    """contains(field) must stay True after the data_write event.

    Without this, ``no_data_leak`` —
    ``G(contains(x) -> !flow(x, ext))`` — would never fire because
    the read/send happens on a *later* timestep than the write, so
    the implication's antecedent would be false everywhere except the
    write timestep.
    """
    trace = make_trace(
        write_event(0, "agent_a", "cache", ["pii"]),
        tool_event(1, "agent_a", "do_stuff"),
        read_event(2, "agent_b", "cache"),
        tool_event(3, "agent_b", "post_to_slack"),
    )
    vals = ground(trace)
    # contains(pii) was set at ts=0 and must stay True at every later ts
    assert vals[0].get("contains(pii)") is True
    assert vals[1].get("contains(pii)") is True
    assert vals[2].get("contains(pii)") is True
    assert vals[3].get("contains(pii)") is True
    # flow only becomes true at ts=2, but it sticks too
    assert vals[2].get("flow(agent_a, agent_b)") is True
    assert vals[3].get("flow(agent_a, agent_b)") is True


def test_no_data_leak_actually_fires_on_late_exfil():
    """End-to-end: ``no_data_leak`` violation when write precedes exfil.

    This is the scenario that motivated the contains-stickiness fix.
    Before the fix, the formula evaluated to True at every timestep
    because ``contains(agent_a)`` was only True at ts=0 and
    ``flow(agent_a, agent_b)`` was only True at ts=1 — they never
    overlapped, so the implication's antecedent was false everywhere
    and the formula trivially passed.

    Convention: the ``no_data_leak(source, external)`` formula uses
    ``contains(source)`` AND ``flow(source, external)``, so ``source``
    must match both — the canonical usage is to tag a write with the
    writer agent's name (``contains=[agent_a]``) and then ``source``
    is that agent name.
    """
    from sponsio.formulas.evaluator import evaluate
    from sponsio.patterns.library import no_data_leak

    trace = make_trace(
        write_event(0, "agent_a", "cache", ["agent_a"]),
        read_event(1, "agent_b", "cache"),
        tool_event(2, "agent_b", "exfil"),
    )
    contract = no_data_leak("agent_a", "agent_b")
    vals = ground(trace)

    verdict = evaluate(contract.formula, vals)
    assert verdict is False, (
        "no_data_leak should detect the late exfil now that contains() "
        "is forward-propagated; got verdict=True (the old buggy result)."
    )


def test_no_data_leak_passes_when_no_exfil_happens():
    """Sanity: writing PII without exfiltrating it must NOT violate."""
    from sponsio.formulas.evaluator import evaluate
    from sponsio.patterns.library import no_data_leak

    trace = make_trace(
        write_event(0, "agent_a", "cache", ["agent_a"]),
        tool_event(1, "agent_a", "process_locally"),
    )
    contract = no_data_leak("agent_a", "agent_b")
    vals = ground(trace)
    assert evaluate(contract.formula, vals) is True


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
