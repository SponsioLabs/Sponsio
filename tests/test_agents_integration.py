"""Unit tests for sponsio/integrations/agents.py — OpenAI Agents SDK integration."""

from __future__ import annotations

import builtins
import sys

import pytest

from sponsio.integrations.agents import (
    AgentsSDKGuard,
    ToolCallBlocked,
    _extract_function,
    _function_tool_name_kw,
)


# ---------------------------------------------------------------------------
# Test AgentsSDKGuard core logic (without openai-agents dependency)
# ---------------------------------------------------------------------------


class TestCheckToolCall:
    def test_allowed(self):
        guard = AgentsSDKGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        result = guard.check_tool_call("check_policy")
        assert result.blocked is False

    def test_blocked(self):
        guard = AgentsSDKGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        result = guard.check_tool_call("issue_refund")
        assert result.blocked is True

    def test_correct_order(self):
        guard = AgentsSDKGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        guard.check_tool_call("check_policy")
        result = guard.check_tool_call("issue_refund")
        assert result.blocked is False

    def test_mutual_exclusion(self):
        guard = AgentsSDKGuard(
            contracts=["tools `approve` and `reject` are mutually exclusive"]
        )
        guard.check_tool_call("approve")
        result = guard.check_tool_call("reject")
        assert result.blocked is True

    def test_unrelated_tool_allowed(self):
        guard = AgentsSDKGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        result = guard.check_tool_call("lookup_customer")
        assert result.blocked is False

    def test_last_check_updated(self):
        guard = AgentsSDKGuard(contracts=["tool `A` must precede `B`"])
        guard.check_tool_call("A")
        assert guard.last_check is not None
        assert guard.last_check.blocked is False

    def test_violations_recorded(self):
        guard = AgentsSDKGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        guard.check_tool_call("issue_refund")
        assert len(guard.violations) > 0

    def test_no_contracts_allows_everything(self):
        guard = AgentsSDKGuard()
        result = guard.check_tool_call("anything")
        assert result.blocked is False


# ---------------------------------------------------------------------------
# Test _extract_function
# ---------------------------------------------------------------------------


class TestExtractFunction:
    def test_plain_callable(self):
        def my_fn():
            pass

        assert _extract_function(my_fn) is my_fn

    def test_object_with_fn_attr(self):
        class MockTool:
            def fn(self):
                pass

        tool = MockTool()
        assert _extract_function(tool) == tool.fn

    def test_object_with_func_attr(self):
        class MockTool:
            def func(self):
                pass

        tool = MockTool()
        assert _extract_function(tool) == tool.func

    def test_non_callable_raises(self):
        with pytest.raises(TypeError):
            _extract_function(42)  # type: ignore


# ---------------------------------------------------------------------------
# Test ToolCallBlocked exception
# ---------------------------------------------------------------------------


class TestToolCallBlocked:
    def test_exception_attrs(self):
        exc = ToolCallBlocked("issue_refund", "must_precede", "blocked message")
        assert exc.tool_name == "issue_refund"
        assert exc.constraint == "must_precede"
        assert "blocked message" in str(exc)


# ---------------------------------------------------------------------------
# Test wrap_tool (requires openai-agents)
# ---------------------------------------------------------------------------


class TestWrapTool:
    def test_wrap_tool_requires_agents(self, monkeypatch):
        # Force the import inside ``wrap_tool`` to fail regardless of
        # whether ``openai-agents`` is installed in the test env.
        # We patch ``builtins.__import__`` so the relative-from import
        # (``from agents import function_tool``) raises just like in a
        # bare environment, without disturbing other imports the test
        # suite needs.
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "agents":
                raise ImportError("No module named 'agents'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        # Drop any cached module entry too so a previously-imported
        # ``agents`` package can't shadow the simulated ImportError.
        monkeypatch.setitem(sys.modules, "agents", None)

        guard = AgentsSDKGuard(contracts=["tool `A` must precede `B`"])

        def dummy_tool():
            return "ok"

        with pytest.raises(ImportError, match="openai-agents is required"):
            guard.wrap_tool(dummy_tool)


class TestFunctionToolNameKw:
    """`_function_tool_name_kw` picks the kwarg name accepted by the
    installed Agents SDK.  Pre-rename SDKs took ``name=``;
    post-rename SDKs take ``name_override=``.  We pin both branches
    so a future SDK swap can't silently regress."""

    def test_post_rename_sdk(self):
        def fake(name_override=None):  # mimic new SDK signature
            return name_override

        assert _function_tool_name_kw(fake) == "name_override"

    def test_pre_rename_sdk(self):
        def fake(name=None):  # mimic old SDK signature
            return name

        assert _function_tool_name_kw(fake) == "name"

    def test_unintrospectable_falls_back_to_override(self):
        # C-implemented callables sometimes refuse signature
        # inspection; we default to the post-rename spelling since
        # that's what the README documents.
        assert _function_tool_name_kw(len) == "name_override"


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


class TestStateManagement:
    def test_reset_clears_state(self):
        guard = AgentsSDKGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        guard.check_tool_call("issue_refund")
        assert len(guard.violations) > 0

        guard.reset()
        assert len(guard.violations) == 0

    def test_summary(self):
        guard = AgentsSDKGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        assert "No violations" in guard.summary()

        guard.check_tool_call("issue_refund")
        assert "violation" in guard.summary().lower()
