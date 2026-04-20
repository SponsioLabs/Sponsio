"""Tests for LangGraph LangGraphGuard integration."""

import pytest
from sponsio.integrations.langgraph import LangGraphGuard, ToolCallBlocked


# =============================================================================
# Direct API (on_tool_start / on_tool_end)
# =============================================================================


def test_guard_blocks_missing_precondition():
    guard = LangGraphGuard(
        agent_id="bot",
        contracts=["tool `check_policy` must precede `issue_refund`"],
    )
    with pytest.raises(ToolCallBlocked):
        guard.on_tool_start({"name": "issue_refund"}, "{}")


def test_guard_allows_correct_order():
    guard = LangGraphGuard(
        agent_id="bot",
        contracts=["tool `check_policy` must precede `issue_refund`"],
    )
    guard.on_tool_start({"name": "check_policy"}, "{}")
    guard.on_tool_end("ok")
    guard.on_tool_start({"name": "issue_refund"}, "{}")  # should NOT raise


def test_guard_no_contracts():
    guard = LangGraphGuard(agent_id="bot")
    guard.on_tool_start({"name": "anything"}, "{}")  # should not raise


def test_guard_summary():
    guard = LangGraphGuard(
        agent_id="bot",
        contracts=["tool `check_policy` must precede `issue_refund`"],
        block=False,
    )
    guard.on_tool_start({"name": "issue_refund"}, "{}")
    assert len(guard.violations) >= 1
    assert "violation" in guard.summary().lower() or "BLOCKED" in guard.summary()


# =============================================================================
# pre_check / post_check (BaseGuard API)
# =============================================================================


def test_pre_check_blocks():
    guard = LangGraphGuard(
        agent_id="bot",
        contracts=["tool `check_policy` must precede `issue_refund`"],
    )
    result = guard.pre_check("issue_refund")
    assert result.blocked
    assert len(result.det_violations) >= 1


def test_pre_check_allows_after_precondition():
    guard = LangGraphGuard(
        agent_id="bot",
        contracts=["tool `check_policy` must precede `issue_refund`"],
    )
    r1 = guard.pre_check("check_policy")
    assert r1.allowed
    r2 = guard.pre_check("issue_refund")
    assert r2.allowed


def test_pre_check_rollback():
    """Blocked events are rolled back from the trace."""
    guard = LangGraphGuard(
        agent_id="bot",
        contracts=["tool `check_policy` must precede `issue_refund`"],
    )
    result = guard.pre_check("issue_refund")
    assert result.blocked
    assert result.rollback_performed
    # Trace should not contain the blocked event
    assert len(guard.trace.events) == 0


def test_reset():
    guard = LangGraphGuard(
        agent_id="bot",
        contracts=["tool `check_policy` must precede `issue_refund`"],
        block=False,
    )
    guard.pre_check("issue_refund")
    assert len(guard.violations) >= 1
    guard.reset()
    assert len(guard.violations) == 0
    assert len(guard.trace.events) == 0


# =============================================================================
# wrap() — LangGraph native integration
# =============================================================================


def test_tool_node_creates_tool_node():
    """wrap() returns a LangGraph ToolNode."""
    pytest.importorskip("langgraph")
    from langchain_core.tools import tool

    @tool
    def my_tool(x: str) -> str:
        """A test tool."""
        return x

    guard = LangGraphGuard(
        agent_id="bot",
        contracts=["tool `check_policy` must precede `my_tool`"],
    )
    tn = guard.wrap([my_tool])

    from langgraph.prebuilt.tool_node import ToolNode

    assert isinstance(tn, ToolNode)


def test_tool_node_blocks_violation():
    """Wrapped tool raises ToolCallBlocked when contract violated."""
    pytest.importorskip("langgraph")
    from langchain_core.tools import tool

    @tool
    def check_policy(order_id: str) -> str:
        """Check policy."""
        return "ok"

    @tool
    def issue_refund(order_id: str) -> str:
        """Issue refund."""
        return "refunded"

    guard = LangGraphGuard(
        agent_id="bot",
        contracts=["tool `check_policy` must precede `issue_refund`"],
    )

    # Test the wrapped tool directly (ToolNode.invoke requires graph runtime)
    wrapped = guard._wrap_tool(issue_refund)
    with pytest.raises(ToolCallBlocked, match="BLOCKED"):
        wrapped.func(order_id="123")


def test_tool_node_allows_correct_order():
    """Wrapped tools allow calls when contract is satisfied."""
    pytest.importorskip("langgraph")
    from langchain_core.tools import tool

    @tool
    def check_policy(order_id: str) -> str:
        """Check policy."""
        return "eligible"

    @tool
    def issue_refund(order_id: str) -> str:
        """Issue refund."""
        return "refunded $50"

    guard = LangGraphGuard(
        agent_id="bot",
        contracts=["tool `check_policy` must precede `issue_refund`"],
    )

    wrapped_check = guard._wrap_tool(check_policy)
    wrapped_refund = guard._wrap_tool(issue_refund)

    # Step 1: call check_policy
    result1 = wrapped_check.func(order_id="123")
    assert result1 == "eligible"

    # Step 2: call issue_refund (should be allowed now)
    result2 = wrapped_refund.func(order_id="123")
    assert result2 == "refunded $50"
