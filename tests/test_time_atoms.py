"""Tests for the event-clock time atoms (now / time_since) and the
Phase-1 args conventions (data_write scope, llm_response segment) and
the ``observe_approval`` BaseGuard helper.

These are the only new public surfaces this round — keep the suite
focused on the contracts they exposed (recency gating, approval gating,
internal-state writes that don't trip ``flow``, segment-scoped checks).
"""

from sponsio.formulas.evaluator import evaluate
from sponsio.models.trace import Event, Trace
from sponsio.patterns.library import approval_active, time_since
from sponsio.tracer.grounding import collect_content_atoms, ground


# ---------------------------------------------------------------------------
# now atom
# ---------------------------------------------------------------------------


def test_now_advances_with_event_ts():
    trace = Trace(
        events=[
            Event(ts=0, agent="a", event_type="tool_call", tool="t"),
            Event(ts=10, agent="a", event_type="tool_call", tool="t"),
            Event(ts=42, agent="a", event_type="tool_call", tool="t"),
        ]
    )
    vals = ground(trace)
    assert vals[0]["now"] == 0.0
    assert vals[1]["now"] == 10.0
    assert vals[2]["now"] == 42.0


# ---------------------------------------------------------------------------
# time_since — fresh-only semantics
# ---------------------------------------------------------------------------


def test_time_since_called_measures_since_last_call():
    pat = time_since("called(refund)", 5)
    content_atoms = collect_content_atoms([pat])
    trace = Trace(
        events=[
            Event(ts=0, agent="a", event_type="tool_call", tool="refund"),
            Event(ts=3, agent="a", event_type="tool_call", tool="other"),
            Event(ts=10, agent="a", event_type="tool_call", tool="other"),
        ]
    )
    vals = ground(trace, content_atoms=content_atoms)
    # event 0: refund just called → time_since = 0
    assert vals[0]["time_since(called\\(refund\\))"] == 0.0
    # event 1: 3 ticks since refund
    assert vals[1]["time_since(called\\(refund\\))"] == 3.0
    # event 2: 10 ticks since refund
    assert vals[2]["time_since(called\\(refund\\))"] == 10.0


def test_time_since_never_seen_returns_sentinel():
    pat = time_since("called(refund)", 5)
    content_atoms = collect_content_atoms([pat])
    trace = Trace(
        events=[
            Event(ts=0, agent="a", event_type="tool_call", tool="other"),
        ]
    )
    vals = ground(trace, content_atoms=content_atoms)
    # Sentinel: very large number so Le(time_since, N) fails — the
    # constraint "P happened within last N seconds" must not pass when
    # P has never happened.
    assert vals[0]["time_since(called\\(refund\\))"] >= 1e17
    # End-to-end: the contract should violate.
    assert evaluate(pat.formula, vals) is False


def test_time_since_passes_when_within_window():
    pat = time_since("called(refund)", 5)
    content_atoms = collect_content_atoms([pat])
    trace = Trace(
        events=[
            Event(ts=0, agent="a", event_type="tool_call", tool="refund"),
            Event(ts=3, agent="a", event_type="tool_call", tool="other"),
        ]
    )
    vals = ground(trace, content_atoms=content_atoms)
    assert evaluate(pat.formula, vals) is True


def test_time_since_violates_when_outside_window():
    pat = time_since("called(refund)", 5)
    content_atoms = collect_content_atoms([pat])
    trace = Trace(
        events=[
            Event(ts=0, agent="a", event_type="tool_call", tool="refund"),
            Event(ts=10, agent="a", event_type="tool_call", tool="other"),
        ]
    )
    vals = ground(trace, content_atoms=content_atoms)
    assert evaluate(pat.formula, vals) is False


# ---------------------------------------------------------------------------
# Sustained predicates: time_since must not refresh on re-emission
# ---------------------------------------------------------------------------


def test_time_since_ctx_does_not_refresh_while_sustained():
    """ctx(approval.role, alice) is re-emitted every event after the
    push, but ``last_ts`` should stay at the push event — otherwise
    "time since approval was granted" collapses to a useless 0.
    """
    pat = time_since("ctx(approval.role, alice)", 100)
    content_atoms = collect_content_atoms([pat])
    trace = Trace(
        events=[
            Event(
                ts=0,
                agent="a",
                event_type="context_update",
                args={"approval.role": "alice"},
            ),
            Event(ts=5, agent="a", event_type="tool_call", tool="t"),
            Event(ts=20, agent="a", event_type="tool_call", tool="t"),
        ]
    )
    vals = ground(trace, content_atoms=content_atoms)
    # On event 0 the ctx is freshly true → time_since = 0
    assert vals[0]["time_since(ctx\\(approval.role\\,\\ alice\\))"] == 0.0
    # On event 1 the ctx is sustained (still true, was true last
    # event) → last_ts not refreshed → time_since = 5
    assert vals[1]["time_since(ctx\\(approval.role\\,\\ alice\\))"] == 5.0
    # On event 2 still sustained → time_since = 20
    assert vals[2]["time_since(ctx\\(approval.role\\,\\ alice\\))"] == 20.0


def test_time_since_flow_does_not_refresh_under_propagation():
    """A flow predicate, once true, is forward-propagated forever. Its
    time_since should measure since first appearance, not 0.
    """
    pat = time_since("flow(writer, reader)", 100)
    content_atoms = collect_content_atoms([pat])
    trace = Trace(
        events=[
            Event(
                ts=0,
                agent="writer",
                event_type="data_write",
                key="store",
                contains=["x"],
            ),
            Event(ts=4, agent="reader", event_type="data_read", key="store"),
            Event(ts=15, agent="other", event_type="tool_call", tool="t"),
        ]
    )
    vals = ground(trace, content_atoms=content_atoms)
    # flow first appears on event 1
    assert vals[1]["time_since(flow\\(writer\\,\\ reader\\))"] == 0.0
    # On event 2 the flow is propagated, NOT fresh → last_ts stays at 4
    assert vals[2]["time_since(flow\\(writer\\,\\ reader\\))"] == 11.0


# ---------------------------------------------------------------------------
# data_write scope convention
# ---------------------------------------------------------------------------


def test_external_data_write_creates_flow():
    """Default scope (or scope=external) must continue to register in
    data_stores so a cross-agent read produces flow().
    """
    trace = Trace(
        events=[
            Event(
                ts=0,
                agent="writer",
                event_type="data_write",
                key="store",
                contains=["x"],
                args={"scope": "external"},
            ),
            Event(ts=1, agent="reader", event_type="data_read", key="store"),
        ]
    )
    vals = ground(trace)
    assert vals[1].get("flow(writer, reader)") is True


def test_internal_data_write_does_not_create_flow():
    """``scope=internal`` flags scratchpad writes — they should not
    register as a cross-agent flow source. ``contains()`` still emits
    so PII detectors keep working on scratchpad payloads.
    """
    trace = Trace(
        events=[
            Event(
                ts=0,
                agent="writer",
                event_type="data_write",
                key="scratchpad",
                contains=["x"],
                args={"scope": "internal"},
            ),
            Event(ts=1, agent="reader", event_type="data_read", key="scratchpad"),
        ]
    )
    vals = ground(trace)
    assert "flow(writer, reader)" not in vals[1]
    # contains still propagates
    assert vals[0].get("contains(x)") is True


def test_data_write_scope_defaults_to_external():
    """Backwards compatibility — pre-existing data_write events without
    args["scope"] must keep behaving as external writes.
    """
    trace = Trace(
        events=[
            Event(
                ts=0,
                agent="writer",
                event_type="data_write",
                key="store",
                contains=["x"],
            ),
            Event(ts=1, agent="reader", event_type="data_read", key="store"),
        ]
    )
    vals = ground(trace)
    assert vals[1].get("flow(writer, reader)") is True


# ---------------------------------------------------------------------------
# llm_response segment convention
# ---------------------------------------------------------------------------


def test_llm_response_emits_segment_atom_when_tagged():
    trace = Trace(
        events=[
            Event(
                ts=0,
                agent="a",
                event_type="llm_response",
                content="thinking out loud",
                args={"segment": "thinking"},
            ),
            Event(
                ts=1,
                agent="a",
                event_type="llm_response",
                content="final answer",
                args={"segment": "answer"},
            ),
        ]
    )
    vals = ground(trace)
    assert vals[0].get("segment(thinking)") is True
    assert vals[1].get("segment(answer)") is True
    assert "segment(answer)" not in vals[0]


def test_llm_response_without_segment_emits_nothing():
    trace = Trace(
        events=[
            Event(
                ts=0,
                agent="a",
                event_type="llm_response",
                content="hi",
            )
        ]
    )
    vals = ground(trace)
    assert not any(k.startswith("segment(") for k in vals[0])


# ---------------------------------------------------------------------------
# approval_active end-to-end
# ---------------------------------------------------------------------------


def test_approval_active_passes_inside_window():
    pat = approval_active("issue_refund", "senior_eng", max_seconds=100)
    content_atoms = collect_content_atoms([pat])
    trace = Trace(
        events=[
            Event(
                ts=0,
                agent="a",
                event_type="context_update",
                args={"approval.role": "senior_eng", "approval.decision": "allow"},
            ),
            Event(ts=50, agent="a", event_type="tool_call", tool="issue_refund"),
        ]
    )
    vals = ground(trace, content_atoms=content_atoms)
    assert evaluate(pat.formula, vals) is True


def test_approval_active_violates_outside_window():
    pat = approval_active("issue_refund", "senior_eng", max_seconds=10)
    content_atoms = collect_content_atoms([pat])
    trace = Trace(
        events=[
            Event(
                ts=0,
                agent="a",
                event_type="context_update",
                args={"approval.role": "senior_eng", "approval.decision": "allow"},
            ),
            Event(ts=50, agent="a", event_type="tool_call", tool="issue_refund"),
        ]
    )
    vals = ground(trace, content_atoms=content_atoms)
    assert evaluate(pat.formula, vals) is False


def test_approval_active_violates_when_no_approval():
    pat = approval_active("issue_refund", "senior_eng", max_seconds=100)
    content_atoms = collect_content_atoms([pat])
    trace = Trace(
        events=[
            Event(ts=0, agent="a", event_type="tool_call", tool="issue_refund"),
        ]
    )
    vals = ground(trace, content_atoms=content_atoms)
    assert evaluate(pat.formula, vals) is False


def test_approval_active_violates_on_deny_decision():
    pat = approval_active("issue_refund", "senior_eng", max_seconds=100)
    content_atoms = collect_content_atoms([pat])
    trace = Trace(
        events=[
            Event(
                ts=0,
                agent="a",
                event_type="context_update",
                args={"approval.role": "senior_eng", "approval.decision": "deny"},
            ),
            Event(ts=10, agent="a", event_type="tool_call", tool="issue_refund"),
        ]
    )
    vals = ground(trace, content_atoms=content_atoms)
    assert evaluate(pat.formula, vals) is False
