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

    def test_blocked_returns_false(self):
        """``False`` is the only return value CrewAI treats as a refusal.

        The adapter used to return an error dict, which CrewAI's
        ``before_tool_call_reducer`` does not recognise: the tool ran.
        """
        guard = CrewAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        ctx = MockToolCallHookContext(tool_name="issue_refund", tool_input={})
        result = guard.before_hook(ctx)
        assert result is False

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
        assert result is False

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


# ---------------------------------------------------------------------------
# CrewAI's real hook protocol (verified against crewai 1.15 sources)
# ---------------------------------------------------------------------------


@dataclass
class MockAfterContext:
    """``ToolCallHookContext`` as the after-hook sees it: output on the context."""

    tool_name: str
    tool_input: dict
    tool_result: str = ""
    agent: MockAgent | None = None


CREWAI_BLOCKED_MESSAGE = "Tool execution blocked by hook. Tool: issue_refund"


class TestCrewAIProtocol:
    def test_after_hook_takes_context_only(self):
        """CrewAI calls ``hook(context)``; a two-parameter signature raised a
        TypeError that CrewAI swallowed, so the after-check never ran."""
        guard = CrewAIGuard(contracts=["tool `A` must precede `B`"])
        ctx = MockAfterContext(tool_name="A", tool_input={}, tool_result="ok")
        assert guard.on_tool_end(ctx) is None

    def test_after_hook_replaces_crewai_generic_block_with_the_reason(self):
        """CrewAI runs the after-hooks on a refused call with its own generic
        message; returning a string replaces it so the agent learns why."""
        guard = CrewAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        assert (
            guard.on_tool_start(
                MockToolCallHookContext(tool_name="issue_refund", tool_input={})
            )
            is False
        )
        replaced = guard.on_tool_end(
            MockAfterContext(
                tool_name="issue_refund",
                tool_input={},
                tool_result=CREWAI_BLOCKED_MESSAGE,
            )
        )
        assert replaced is not None
        assert replaced.startswith("BLOCKED by contract:")
        assert "check_policy" in replaced

    def test_block_reason_never_overwrites_a_real_result(self):
        """A stale block reason must not replace the output of a later call
        of the same tool that really ran."""
        guard = CrewAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        guard.on_tool_start(
            MockToolCallHookContext(tool_name="issue_refund", tool_input={})
        )
        # the after-hook for the refusal never fired; a real call follows
        guard.on_tool_start(
            MockToolCallHookContext(tool_name="check_policy", tool_input={})
        )
        guard.on_tool_start(
            MockToolCallHookContext(tool_name="issue_refund", tool_input={})
        )
        kept = guard.on_tool_end(
            MockAfterContext(
                tool_name="issue_refund", tool_input={}, tool_result="refunded"
            )
        )
        assert kept is None

    def test_guard_error_refuses_instead_of_running(self, monkeypatch):
        """CrewAI swallows hook exceptions and runs the tool. A guard that
        cannot evaluate must return ``False``, not raise."""
        guard = CrewAIGuard(contracts=["tool `A` must precede `B`"])

        def boom(*args, **kwargs):
            raise RuntimeError("evaluator exploded")

        monkeypatch.setattr(guard, "guard_before", boom)
        ctx = MockToolCallHookContext(tool_name="B", tool_input={})
        assert guard.on_tool_start(ctx) is False
        replaced = guard.on_tool_end(
            MockAfterContext(
                tool_name="B",
                tool_input={},
                tool_result="Tool execution blocked by hook. Tool: B",
            )
        )
        assert replaced is not None and "evaluator exploded" in replaced

    def test_register_global_hooks_uses_crewai_hooks_module(self, monkeypatch):
        """The decorators live in ``crewai.hooks``; importing them from
        ``crewai.tools`` raised ImportError even with crewai installed."""
        import sys
        import types

        registered: dict[str, object] = {}
        hooks_mod = types.ModuleType("crewai.hooks")

        def before_tool_call(fn):
            registered["before"] = fn
            return fn

        def after_tool_call(fn):
            registered["after"] = fn
            return fn

        hooks_mod.before_tool_call = before_tool_call  # type: ignore[attr-defined]
        hooks_mod.after_tool_call = after_tool_call  # type: ignore[attr-defined]
        crewai_mod = types.ModuleType("crewai")
        crewai_mod.hooks = hooks_mod  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "crewai", crewai_mod)
        monkeypatch.setitem(sys.modules, "crewai.hooks", hooks_mod)

        guard = CrewAIGuard(contracts=["tool `A` must precede `B`"])
        guard.register_global_hooks()
        assert registered["before"] == guard.on_tool_start
        assert registered["after"] == guard.on_tool_end
