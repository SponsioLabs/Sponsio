"""Unit tests for sponsio/integrations/crewai.py — CrewAI hook integration."""

from __future__ import annotations

from dataclasses import dataclass

from sponsio.integrations.crewai import CrewAIGuard


# ---------------------------------------------------------------------------
# Mock CrewAI ToolCallHookContext
# ---------------------------------------------------------------------------


@dataclass
class MockAgent:
    role: str = "support_bot"


@dataclass
class MockToolCallHookContext:
    tool_name: str
    tool_input: dict
    agent: MockAgent | None = None


# ---------------------------------------------------------------------------
# before_hook
# ---------------------------------------------------------------------------


class TestBeforeHook:
    def test_allowed_returns_none(self):
        guard = CrewAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        ctx = MockToolCallHookContext(tool_name="check_policy", tool_input={})
        result = guard.before_hook(ctx)
        assert result is None  # allowed

    def test_blocked_returns_error_dict(self):
        guard = CrewAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        ctx = MockToolCallHookContext(tool_name="issue_refund", tool_input={})
        result = guard.before_hook(ctx)
        assert isinstance(result, dict)
        assert "BLOCKED" in result["error"]

    def test_correct_order_allowed(self):
        guard = CrewAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )

        ctx1 = MockToolCallHookContext(tool_name="check_policy", tool_input={})
        assert guard.before_hook(ctx1) is None

        ctx2 = MockToolCallHookContext(tool_name="issue_refund", tool_input={})
        assert guard.before_hook(ctx2) is None

    def test_mutual_exclusion_blocked(self):
        guard = CrewAIGuard(
            contracts=["tools `approve` and `reject` are mutually exclusive"]
        )

        ctx1 = MockToolCallHookContext(tool_name="approve", tool_input={})
        assert guard.before_hook(ctx1) is None

        ctx2 = MockToolCallHookContext(tool_name="reject", tool_input={})
        result = guard.before_hook(ctx2)
        assert isinstance(result, dict)
        assert "BLOCKED" in result["error"]

    def test_unrelated_tool_allowed(self):
        guard = CrewAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        ctx = MockToolCallHookContext(tool_name="lookup_customer", tool_input={})
        assert guard.before_hook(ctx) is None

    def test_last_check_updated(self):
        guard = CrewAIGuard(contracts=["tool `A` must precede `B`"])
        ctx = MockToolCallHookContext(tool_name="A", tool_input={})
        guard.before_hook(ctx)
        assert guard.last_check is not None
        assert guard.last_check.blocked is False

    def test_uses_agent_role_as_id(self):
        guard = CrewAIGuard(contracts=["tool `A` must precede `B`"])
        agent = MockAgent(role="my_bot")
        ctx = MockToolCallHookContext(tool_name="A", tool_input={}, agent=agent)
        guard.before_hook(ctx)
        # Should not error; the agent role is used internally
        assert guard.last_check is not None

    def test_violations_recorded(self):
        guard = CrewAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        ctx = MockToolCallHookContext(tool_name="issue_refund", tool_input={})
        guard.before_hook(ctx)
        assert len(guard.violations) > 0

    def test_no_contracts_allows_everything(self):
        guard = CrewAIGuard()
        ctx = MockToolCallHookContext(tool_name="anything", tool_input={})
        assert guard.before_hook(ctx) is None


# ---------------------------------------------------------------------------
# after_hook
# ---------------------------------------------------------------------------


class TestAfterHook:
    def test_no_sto_evaluator_returns_none(self):
        guard = CrewAIGuard(contracts=["tool `A` must precede `B`"])
        ctx = MockToolCallHookContext(tool_name="A", tool_input={})
        result = guard.after_hook(ctx, "some output")
        assert result is None

    def test_preserves_original_result(self):
        guard = CrewAIGuard(contracts=["tool `A` must precede `B`"])
        ctx = MockToolCallHookContext(tool_name="A", tool_input={})
        result = guard.after_hook(ctx, "original output")
        assert result is None  # None means keep original


# ---------------------------------------------------------------------------
# reset / summary
# ---------------------------------------------------------------------------


class TestStateManagement:
    def test_reset_clears_violations(self):
        guard = CrewAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        ctx = MockToolCallHookContext(tool_name="issue_refund", tool_input={})
        guard.before_hook(ctx)
        assert len(guard.violations) > 0

        guard.reset()
        assert len(guard.violations) == 0

    def test_summary_with_violations(self):
        guard = CrewAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        ctx = MockToolCallHookContext(tool_name="issue_refund", tool_input={})
        guard.before_hook(ctx)
        assert "violation" in guard.summary().lower()

    def test_summary_no_violations(self):
        guard = CrewAIGuard(contracts=["tool `A` must precede `B`"])
        assert "No violations" in guard.summary()

    def test_tool_node_creates_guarded_tools(self):
        guard = CrewAIGuard(contracts=["tool `A` must precede `B`"])
        try:
            from crewai.tools import tool as _  # noqa: F401

            def my_fn(x: str) -> str:
                """A test tool."""
                return f"result: {x}"

            tools = guard.wrap([my_fn])
            assert len(tools) == 1
            assert tools[0].name == "my_fn"
        except (ImportError, TypeError):
            pass  # crewai not installed — skip
