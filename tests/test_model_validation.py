"""Regression tests for ``__post_init__`` validation (#15 + #16).

These cover data-integrity checks added to the public model dataclasses
(``Event``, ``Violation``, ``Agent``) and the span-collector stack
underflow guard. Each pre-fix failure mode was silent: the object
constructed fine, the runtime happily processed it, and the result was
a contract that looked active but evaluated to a no-op.

Map back to the review report:

* #15 — ``SpanCollector.finish_span`` silent root-return on stack
  underflow (breaks the span tree invisibly).
* #16 — ``Event``, ``Violation``, ``Agent`` missing ``__post_init__``
  (negative ``ts``, empty ``agent_id``, unknown ``event_type``,
  unknown violation ``kind``).
"""

from __future__ import annotations

import pytest

from sponsio.models.agent import Agent
from sponsio.models.result import Violation
from sponsio.models.spans import SpanCollector
from sponsio.models.trace import Event


class TestEventValidation:
    """#16 — Event dataclass."""

    def test_negative_ts_rejected(self):
        """Negative ``ts`` breaks monotonicity in the incremental backend."""
        with pytest.raises(ValueError, match=">= 0"):
            Event(ts=-1, agent="bot", event_type="tool_call")

    def test_non_numeric_ts_rejected(self):
        with pytest.raises(TypeError, match="must be int or float"):
            Event(ts="now", agent="bot", event_type="tool_call")  # type: ignore[arg-type]

    def test_bool_ts_rejected(self):
        """``bool`` is an ``int`` subclass in Python; catching it here
        prevents ``Event(ts=True, ...)`` from slipping through."""
        with pytest.raises(TypeError, match="must be int or float"):
            Event(ts=True, agent="bot", event_type="tool_call")  # type: ignore[arg-type]

    def test_empty_agent_rejected(self):
        """Empty agent id silently disables ``current_agent`` /
        ``segregation_of_duty`` atoms."""
        with pytest.raises(ValueError, match="non-empty string"):
            Event(ts=1, agent="", event_type="tool_call")

    def test_unknown_event_type_rejected(self):
        """Unknown ``event_type`` flows through ``ground_event`` and
        produces *no* atoms — every contract looking at this event is
        vacuously satisfied."""
        with pytest.raises(ValueError, match="not recognized"):
            Event(ts=1, agent="bot", event_type="tool_calll")

    def test_valid_event_accepted(self):
        """Regression: happy path must still work."""
        e = Event(ts=0, agent="bot", event_type="tool_call", tool="search")
        assert e.ts == 0
        assert e.tool == "search"

    def test_float_ts_accepted_for_wall_clock_loaders(self):
        """The session-log loader populates ``ts`` with ``monotonic()``
        floats; rejecting float would break the trace scan path."""
        e = Event(ts=1.5, agent="bot", event_type="tool_call")
        assert e.ts == 1.5


class TestViolationValidation:
    """#16 — Violation dataclass."""

    def test_empty_agent_id_rejected(self):
        with pytest.raises(ValueError, match="non-empty string"):
            Violation(agent_id="", formula=None, kind="guarantee")  # type: ignore[arg-type]

    def test_unknown_kind_rejected(self):
        """Unknown ``kind`` silently skips strategy dispatch — the
        result object looks correct but enforcement never fired."""
        with pytest.raises(ValueError, match="not recognized"):
            Violation(agent_id="bot", formula=None, kind="xyz")  # type: ignore[arg-type]

    def test_valid_violation_accepted(self):
        v = Violation(agent_id="bot", formula=None, kind="guarantee", desc="oops")  # type: ignore[arg-type]
        assert v.kind == "guarantee"


class TestAgentValidation:
    """#16 — Agent dataclass."""

    def test_empty_id_rejected(self):
        """Empty ``id`` collides with other empty-id agents in
        ``system.agents`` dict lookups — per-agent contracts land on
        the wrong object."""
        with pytest.raises(ValueError, match="non-empty string"):
            Agent(id="")

    def test_whitespace_only_id_rejected(self):
        with pytest.raises(ValueError, match="non-empty string"):
            Agent(id="   ")

    def test_non_string_tool_rejected(self):
        with pytest.raises(ValueError, match="non-empty strings"):
            Agent(id="bot", tools=["ok", ""])

    def test_non_list_tools_rejected(self):
        with pytest.raises(TypeError, match="must be a list"):
            Agent(id="bot", tools="search")  # type: ignore[arg-type]

    def test_valid_agent_accepted(self):
        a = Agent(id="bot", tools=["search"], permissions=["approver"])
        assert a.id == "bot"
        assert a.tools == ["search"]


class TestSpanCollectorUnderflow:
    """#15 — SpanCollector.finish_span stack underflow."""

    def test_underflow_raises_clearly(self):
        """Mismatched ``start_*`` / ``finish_span`` pairs used to
        silently return the root, corrupting the tree without any
        signal. The RuntimeError surfaces the caller bug."""
        with SpanCollector("a", "act") as c:
            with pytest.raises(RuntimeError, match="stack underflow"):
                c.finish_span()

    def test_extra_finish_span_after_nested_pair(self):
        """Regression: one ``start`` + two ``finish`` = caller bug, raises."""
        with SpanCollector("a", "act") as c:
            c.start_contract_check("c1")
            c.finish_span("ok")  # legitimate pair
            with pytest.raises(RuntimeError, match="stack underflow"):
                c.finish_span("ok")  # stray extra finish

    def test_paired_finish_still_works(self):
        """Happy path — valid start/finish pairs stay in working order."""
        with SpanCollector("a", "act") as c:
            c.start_contract_check("c1")
            c.start_precondition("phi")
            c.finish_span("ok")
            c.finish_span("ok")
        assert len(c.root.children) == 1
