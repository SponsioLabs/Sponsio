"""Tests for ``BaseGuard.filter_tools`` — the v0.2 proactive primitive.

``filter_tools`` is the API that lets adapters pre-strip a tool menu
before the model sees it. The contract these tests pin down:

* Pure probe: returning a list does not mutate the trace, append a
  MonitorEvent, fire callbacks, or sample perf.
* Catches tool-name-level det rules: ``tool_allowlist``,
  ``must_precede`` (state-dependent), ``count_at_most``.
* Preserves candidate order (so adapters can rely on stable layout).
* Mode-independent: in observe mode a candidate that *would* be
  blocked is still filtered out — the user's intent is "what's legal
  right now", which doesn't depend on whether enforcement is on.
* Args-level rules don't filter (probe has no args to test against);
  those still apply via ``guard_before``. The test for this just
  documents the carve-out — it doesn't claim the probe catches them.
"""

from __future__ import annotations

from sponsio.core import Sponsio


class TestFilterToolsBasics:
    def test_empty_candidates_returns_empty(self) -> None:
        guard = Sponsio(agent_id="bot", verbose=False)
        assert guard.filter_tools([]) == []

    def test_no_contracts_returns_all_candidates(self) -> None:
        """Guard with no det contracts should pass everything through —
        the probe finds no rule that fires, so every candidate is
        legal."""
        guard = Sponsio(agent_id="bot", verbose=False)
        assert guard.filter_tools(["a", "b", "c"]) == ["a", "b", "c"]

    def test_preserves_candidate_order(self) -> None:
        guard = Sponsio(
            agent_id="bot",
            tool_policy={"default": "deny", "approved": ["c", "a", "b"]},
            verbose=False,
        )
        # Input order is preserved in output, NOT approved-list order.
        assert guard.filter_tools(["a", "b", "c"]) == ["a", "b", "c"]


class TestFilterToolsWithToolPolicy:
    def test_default_deny_filters_unapproved(self) -> None:
        guard = Sponsio(
            agent_id="bot",
            tool_policy={"default": "deny", "approved": ["search", "read_file"]},
            verbose=False,
        )
        result = guard.filter_tools(["search", "delete_db", "read_file", "rm_rf"])
        assert result == ["search", "read_file"]

    def test_default_allow_filters_nothing(self) -> None:
        guard = Sponsio(
            agent_id="bot",
            tool_policy={"default": "allow"},
            verbose=False,
        )
        # ``allow`` injects no contract; probe finds nothing to block.
        assert guard.filter_tools(["search", "delete_db"]) == ["search", "delete_db"]

    def test_empty_approved_under_deny_blocks_everything(self) -> None:
        guard = Sponsio(
            agent_id="bot",
            tool_policy={"default": "deny", "approved": []},
            verbose=False,
        )
        assert guard.filter_tools(["search", "delete_db"]) == []


class TestFilterToolsWithCustomContracts:
    def test_must_precede_blocks_dependent_tool_until_precondition_fires(
        self,
    ) -> None:
        """``must call A before B`` should block B from the menu when A
        hasn't been called yet, and let it through once A has."""
        guard = Sponsio(
            agent_id="bot",
            contracts=["must call `check_policy` before `issue_refund`"],
            verbose=False,
        )
        # Before any call: refund is blocked, check_policy is legal.
        before = guard.filter_tools(["check_policy", "issue_refund"])
        assert before == ["check_policy"]

        # After calling check_policy: refund opens up.
        guard.guard_before("check_policy", {})
        after = guard.filter_tools(["check_policy", "issue_refund"])
        assert after == ["check_policy", "issue_refund"]

    def test_count_at_most_blocks_after_limit(self) -> None:
        guard = Sponsio(
            agent_id="bot",
            contracts=["tool `execute` at most 2 times"],
            verbose=False,
        )
        # Probe before any call: legal.
        assert guard.filter_tools(["execute"]) == ["execute"]
        # Burn the budget.
        guard.guard_before("execute", {})
        guard.guard_before("execute", {})
        # Third call would breach — filter strips it.
        assert guard.filter_tools(["execute"]) == []


class TestFilterToolsIsPure:
    def test_does_not_mutate_trace(self) -> None:
        """The probe appends + rolls back per candidate. After the call
        the trace length and event list should be unchanged."""
        guard = Sponsio(
            agent_id="bot",
            tool_policy={"default": "deny", "approved": ["search"]},
            verbose=False,
        )
        before_len = len(guard._monitor._trace.events)
        guard.filter_tools(["search", "rm_rf", "delete_db"])
        assert len(guard._monitor._trace.events) == before_len

    def test_does_not_emit_to_monitor_log(self) -> None:
        """A real call appends MonitorEvent records; a probe must not.
        Otherwise reporters / dashboards would drown in synthetic
        events."""
        guard = Sponsio(
            agent_id="bot",
            tool_policy={"default": "deny", "approved": ["search"]},
            verbose=False,
        )
        before_log = len(guard._monitor.log)
        guard.filter_tools(["search", "rm_rf"])
        assert len(guard._monitor.log) == before_log

    def test_does_not_record_turn_spans(self) -> None:
        guard = Sponsio(
            agent_id="bot",
            tool_policy={"default": "deny", "approved": ["search"]},
            verbose=False,
        )
        before_spans = len(guard._monitor.turn_spans)
        guard.filter_tools(["search", "rm_rf"])
        assert len(guard._monitor.turn_spans) == before_spans

    def test_does_not_fire_callbacks(self) -> None:
        guard = Sponsio(
            agent_id="bot",
            tool_policy={"default": "deny", "approved": ["search"]},
            verbose=False,
        )
        seen: list[str] = []
        guard._monitor.register_callback(
            lambda ev: seen.append(getattr(ev, "kind", "?"))
        )
        guard.filter_tools(["search", "rm_rf", "delete_db"])
        assert seen == []

    def test_does_not_pollute_perf_samples(self) -> None:
        """Per-turn probes (one per candidate × per contract) would
        otherwise dominate the perf histogram and break p50/p99
        latency tracking on real calls. Probe must skip sampling."""
        guard = Sponsio(
            agent_id="bot",
            tool_policy={"default": "deny", "approved": ["search"]},
            verbose=False,
        )
        before = guard._monitor.performance_tracker.summarize().total_checks
        guard.filter_tools(["search", "rm_rf", "delete_db", "rm", "drop"])
        after = guard._monitor.performance_tracker.summarize().total_checks
        assert after == before


class TestFilterToolsObserveMode:
    def test_observe_mode_still_filters(self) -> None:
        """Observe mode disables enforcement but ``filter_tools`` answers
        a different question: "is this tool legal right now?" — that
        is a pure-logic question whose answer should not change with
        the enforcement posture. The probe must bypass observe-mode
        downgrade so adapters get the same menu regardless of mode."""
        guard = Sponsio(
            agent_id="bot",
            tool_policy={"default": "deny", "approved": ["search"]},
            mode="observe",
            verbose=False,
        )
        assert guard.filter_tools(["search", "rm_rf"]) == ["search"]
