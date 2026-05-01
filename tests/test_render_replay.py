"""Tests for the session-log replay pipeline.

Two layers:
  * ``find_session_file`` / ``list_sessions`` — path resolution against
    a ``sessions_dir`` we control via ``tmp_path``.
  * ``reconstruct_turn_spans`` — flat MonitorEvent records →
    AgentTurnSpan tree shaped exactly like ``RuntimeMonitor.turn_spans``,
    so the existing ``render_session`` consumes it without any
    special-casing for replay vs live.
"""

from __future__ import annotations

import json

import pytest

from sponsio.render.derive import short_session_id
from sponsio.render.replay import (
    find_session_file,
    list_sessions,
    load_replay,
    reconstruct_turn_spans,
)


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


@pytest.fixture
def populated_sessions(tmp_path):
    sessions = tmp_path / "sessions"
    _write_jsonl(
        sessions / "support_bot" / "20260501_120000_111.jsonl",
        [
            {
                "ts": 1.0,
                "agent_id": "support_bot",
                "action": "issue_refund",
                "pipeline": "det",
                "constraint": "rate limit",
                "result": {"action": "blocked", "message": "limit hit"},
            }
        ],
    )
    _write_jsonl(
        sessions / "data_pipeline" / "20260501_130000_222.jsonl",
        [
            {
                "ts": 5.0,
                "agent_id": "data_pipeline",
                "action": "execute_sql",
                "pipeline": "det",
                "constraint": "no destructive SQL",
                "result": {"action": "allowed"},
            }
        ],
    )
    return sessions


# ---------------------------------------------------------------------------
# find_session_file.
# ---------------------------------------------------------------------------


def test_find_by_short_id(populated_sessions):
    expected_id = short_session_id("20260501_120000_111")
    path, agent = find_session_file(expected_id, sessions_dir=populated_sessions)
    assert path is not None
    assert path.name == "20260501_120000_111.jsonl"
    assert agent == "support_bot"


def test_find_by_filename_stem(populated_sessions):
    path, agent = find_session_file(
        "20260501_120000_111", sessions_dir=populated_sessions
    )
    assert path is not None
    assert agent == "support_bot"


def test_find_by_direct_path(populated_sessions):
    target = populated_sessions / "support_bot" / "20260501_120000_111.jsonl"
    path, agent = find_session_file(str(target), sessions_dir=populated_sessions)
    assert path == target
    assert agent == "support_bot"


def test_find_unknown_returns_pair_of_nones(populated_sessions):
    assert find_session_file("sess_nope", sessions_dir=populated_sessions) == (
        None,
        None,
    )


def test_find_handles_missing_root(tmp_path):
    assert find_session_file("sess_anything", sessions_dir=tmp_path / "absent") == (
        None,
        None,
    )


def test_find_empty_query_returns_none(populated_sessions):
    assert find_session_file("", sessions_dir=populated_sessions) == (None, None)


# ---------------------------------------------------------------------------
# list_sessions.
# ---------------------------------------------------------------------------


def test_list_sessions_returns_per_file_metadata(populated_sessions):
    rows = list_sessions(sessions_dir=populated_sessions)
    assert len(rows) == 2
    by_agent = {r["agent_id"]: r for r in rows}
    assert "support_bot" in by_agent
    assert "data_pipeline" in by_agent
    # Short IDs are deterministic.
    assert by_agent["support_bot"]["session_id"] == short_session_id(
        "20260501_120000_111"
    )


def test_list_sessions_sorted_newest_first(populated_sessions):
    rows = list_sessions(sessions_dir=populated_sessions)
    assert rows[0]["mtime"] >= rows[-1]["mtime"]


def test_list_sessions_handles_missing_root(tmp_path):
    assert list_sessions(sessions_dir=tmp_path / "absent") == []


# ---------------------------------------------------------------------------
# reconstruct_turn_spans — the main reconstruction logic.
# ---------------------------------------------------------------------------


def test_reconstruct_groups_consecutive_same_action_into_one_turn():
    events = [
        {
            "ts": 1.000,
            "agent_id": "bot",
            "action": "execute_sql",
            "constraint": "rule_x",
            "result": {"action": "blocked"},
        },
        {
            "ts": 1.001,  # within window
            "agent_id": "bot",
            "action": "execute_sql",
            "constraint": "rule_y",
            "result": {"action": "blocked"},
        },
    ]
    turns = reconstruct_turn_spans(events)
    assert len(turns) == 1
    assert turns[0].action == "execute_sql"
    assert len(turns[0].children) == 2  # two checks under one turn


def test_reconstruct_splits_when_action_changes():
    events = [
        {"ts": 1.0, "action": "a", "constraint": "x", "result": {"action": "allowed"}},
        {
            "ts": 1.001,
            "action": "b",
            "constraint": "x",
            "result": {"action": "allowed"},
        },
    ]
    turns = reconstruct_turn_spans(events)
    assert len(turns) == 2
    assert turns[0].action == "a"
    assert turns[1].action == "b"


def test_reconstruct_splits_on_large_time_gap():
    """Same action separated by > the grouping window starts a new turn."""
    events = [
        {"ts": 1.0, "action": "a", "constraint": "x", "result": {"action": "allowed"}},
        {
            "ts": 5.0,  # >> 50ms window
            "action": "a",
            "constraint": "x",
            "result": {"action": "allowed"},
        },
    ]
    turns = reconstruct_turn_spans(events)
    assert len(turns) == 2


def test_reconstruct_assumption_satisfied_creates_precondition_with_result_true():
    events = [
        {
            "ts": 1.0,
            "action": "tool_x",
            "pipeline": "det",
            "constraint": "assumption: freeze declared",
            "result": {"action": "allowed"},
        }
    ]
    turns = reconstruct_turn_spans(events)
    check = turns[0].children[0]
    pre = check.children[0]
    assert pre.span_type == "sponsio.precondition"
    assert pre.formula_desc == "freeze declared"  # prefix stripped
    assert pre.result is True


def test_reconstruct_assumption_unsatisfied_creates_precondition_with_result_false():
    events = [
        {
            "ts": 1.0,
            "action": "tool_x",
            "constraint": "assumption: not yet",
            "result": {"action": "escalated"},
        }
    ]
    turns = reconstruct_turn_spans(events)
    pre = turns[0].children[0].children[0]
    assert pre.result is False


def test_reconstruct_blocked_event_creates_violation_and_enforcement_children():
    events = [
        {
            "ts": 1.0,
            "action": "execute_sql",
            "constraint": "no destructive SQL",
            "result": {"action": "blocked", "message": "DROP detected"},
        }
    ]
    turns = reconstruct_turn_spans(events)
    guar = turns[0].children[0].children[0]
    assert guar.span_type == "sponsio.guarantee"
    assert guar.result is False
    kinds = [c.span_type for c in guar.children]
    assert "sponsio.violation" in kinds
    assert "sponsio.enforcement" in kinds
    enf = next(c for c in guar.children if c.span_type == "sponsio.enforcement")
    assert enf.result_action == "blocked"


def test_reconstruct_allowed_event_does_not_emit_violation_children():
    """A passed enforce should NOT spawn ViolationSpan/EnforcementSpan."""
    events = [
        {
            "ts": 1.0,
            "action": "safe_action",
            "constraint": "rate limit",
            "result": {"action": "allowed"},
        }
    ]
    turns = reconstruct_turn_spans(events)
    guar = turns[0].children[0].children[0]
    assert guar.result is True
    assert guar.children == []  # no violation/enforcement attached


def test_reconstruct_observed_action_is_a_violation():
    """Shadow-mode observed events should render as violations in
    the tree (matches the live verdict + verdict-banner logic)."""
    events = [
        {
            "ts": 1.0,
            "action": "send_email",
            "constraint": "pii",
            "result": {"action": "observed", "message": "pii leak"},
        }
    ]
    turns = reconstruct_turn_spans(events)
    guar = turns[0].children[0].children[0]
    assert guar.result is False
    enf = next(c for c in guar.children if c.span_type == "sponsio.enforcement")
    assert enf.result_action == "observed"


def test_reconstruct_pipeline_label_mapping():
    events = [
        {
            "ts": 1.0,
            "action": "a",
            "pipeline": "det",
            "constraint": "x",
            "result": {"action": "allowed"},
        },
        {
            "ts": 2.0,
            "action": "b",
            "pipeline": "sto",
            "constraint": "y",
            "result": {"action": "allowed"},
        },
    ]
    turns = reconstruct_turn_spans(events)
    pipelines = [t.children[0].pipeline for t in turns]
    assert pipelines == ["hard", "sto"]


def test_reconstruct_empty_input_returns_empty_list():
    assert reconstruct_turn_spans([]) == []


def test_load_replay_returns_spans_and_agent(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text(
        json.dumps(
            {
                "ts": 1.0,
                "agent_id": "support_bot",
                "action": "issue_refund",
                "constraint": "rate limit",
                "result": {"action": "blocked"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    spans, agent = load_replay(p)
    assert agent == "support_bot"
    assert len(spans) == 1
    assert spans[0].action == "issue_refund"
