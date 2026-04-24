"""Tests for MCP integration."""

import asyncio

from sponsio.integrations.mcp import MCPContractProxy, MCPToolDef, scan_mcp_tools


class MockMCPClient:
    """Mock MCP client for testing."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        self.calls.append((tool_name, arguments))
        return {"status": "ok"}

    async def list_tools(self) -> list:
        return []


# --- scan_mcp_tools ---


def _all_descs(system):
    """Flatten enforcement descriptions across all contracts."""
    descs = []
    for c in system.contracts:
        for e in c.enforcements:
            descs.append(getattr(e, "desc", str(e)))
    return descs


def test_scan_mcp_tools_financial():
    tools = [
        MCPToolDef(name="process_refund", description="Process a customer refund"),
        MCPToolDef(name="lookup_customer", description="Look up customer details"),
        MCPToolDef(name="send_email", description="Send an email to the customer"),
    ]
    system = scan_mcp_tools(tools)
    # rate_limit (financial) + no_data_leak (send/email) = 2+ contracts
    assert len(system.contracts) >= 2


def test_scan_mcp_tools_approve_reject_pair():
    tools = [
        MCPToolDef(name="approve_order", description="Approve an order"),
        MCPToolDef(name="reject_order", description="Reject an order"),
    ]
    system = scan_mcp_tools(tools)
    descs = _all_descs(system)
    assert any("reject_order" in d for d in descs)


def test_scan_mcp_tools_from_dicts():
    tools = [
        {"name": "delete_account", "description": "Delete user account"},
        {"name": "confirm_delete_account", "description": "Confirm account deletion"},
    ]
    system = scan_mcp_tools(tools)
    descs = _all_descs(system)
    assert any("confirm" in d.lower() for d in descs)


def test_scan_mcp_tools_duck_type():
    """Objects with name/description attributes should be accepted."""

    class FakeTool:
        def __init__(self, name, description=""):
            self.name = name
            self.description = description

    tools = [FakeTool("process_payment", "Process a payment")]
    system = scan_mcp_tools(tools)
    # At least one contract for process_payment (rate_limit: financial)
    assert len(system.contracts) >= 1
    assert system.contracts[0].agent.tools == ["process_payment"]


def test_scan_mcp_tools_no_matches():
    """Tools with no heuristic matches produce an empty contract list."""
    tools = [MCPToolDef(name="get_weather", description="Get current weather")]
    system = scan_mcp_tools(tools)
    assert len(system.contracts) == 0


def test_scan_mcp_tools_multiple_heuristics():
    """A tool that fires multiple heuristics emits multiple contracts."""
    tools = [
        MCPToolDef(name="process_refund", description="Send refund notification email"),
    ]
    system = scan_mcp_tools(tools)
    assert len(system.contracts) >= 2


# --- MCPContractProxy ---


def test_mcp_proxy_blocks_violation():
    """MCPContractProxy should block tool calls that violate contracts."""
    from sponsio import System
    from sponsio.patterns import must_precede

    system = System("test")
    system.agent("agent").tools("fraud_check", "execute_refund")
    system.agent("agent").guarantees(must_precede("fraud_check", "execute_refund"))

    client = MockMCPClient()
    proxy = MCPContractProxy(mcp_client=client, system=system, agent_id="agent")

    # Try to execute_refund without fraud_check -> should be blocked
    result = asyncio.run(proxy.call_tool("execute_refund", {}))
    assert "error" in result
    assert len(client.calls) == 0  # Should NOT have called the real client


def test_mcp_proxy_allows_valid_sequence():
    """MCPContractProxy should allow valid tool call sequences."""
    from sponsio import System
    from sponsio.patterns import must_precede

    system = System("test")
    system.agent("agent").tools("fraud_check", "execute_refund")
    system.agent("agent").guarantees(must_precede("fraud_check", "execute_refund"))

    client = MockMCPClient()
    proxy = MCPContractProxy(mcp_client=client, system=system, agent_id="agent")

    async def run():
        await proxy.call_tool("fraud_check", {})
        return await proxy.call_tool("execute_refund", {})

    result = asyncio.run(run())
    assert "error" not in result
    assert len(client.calls) == 2


def test_mcp_proxy_reset():
    """Reset should clear the monitor state."""
    from sponsio import System

    system = System("test")
    client = MockMCPClient()
    proxy = MCPContractProxy(mcp_client=client, system=system, agent_id="agent")

    asyncio.run(proxy.call_tool("some_tool", {}))
    # Events per call with default tag_outputs=True:
    #   1. pre-check tool_call (the sole tool_call event — post-execution
    #      content is attached to this one, not emitted as a second call,
    #      to avoid double-counting rate_limit / idempotent contracts)
    #   2. auto-tag data_write (contains=[tool_name])
    assert len(proxy.monitor.trace.events) == 2

    proxy.reset()
    assert len(proxy.monitor.trace.events) == 0


def test_mcp_proxy_autotag_emits_contains():
    """Default ``tag_outputs=True`` records ``contains=[tool_name]``
    after every successful tool call so ``no_data_leak`` binds."""
    from sponsio import System

    system = System("test")
    client = MockMCPClient()
    proxy = MCPContractProxy(mcp_client=client, system=system, agent_id="agent")

    asyncio.run(proxy.call_tool("lookup_customer", {}))

    writes = [e for e in proxy.monitor.trace.events if e.event_type == "data_write"]
    assert len(writes) == 1
    assert writes[0].key == "lookup_customer"
    assert writes[0].contains == ["lookup_customer"]


def test_mcp_proxy_tag_outputs_false_disables_autotag():
    from sponsio import System

    system = System("test")
    client = MockMCPClient()
    proxy = MCPContractProxy(
        mcp_client=client,
        system=system,
        agent_id="agent",
        tag_outputs=False,
    )
    asyncio.run(proxy.call_tool("lookup_customer", {}))
    writes = [e for e in proxy.monitor.trace.events if e.event_type == "data_write"]
    assert writes == []


def test_mcp_proxy_tag_pii_detects_pii_classes():
    """``tag_pii=True`` regex-scans the tool output and tags each
    detected PII class alongside the tool name."""
    from sponsio import System

    class PIIClient:
        async def call_tool(self, tool_name, arguments):
            return {"content": "alice@example.com / SSN 123-45-6789"}

        async def list_tools(self):
            return []

    system = System("test")
    proxy = MCPContractProxy(
        mcp_client=PIIClient(),
        system=system,
        agent_id="agent",
        tag_pii=True,
    )
    asyncio.run(proxy.call_tool("lookup_customer", {}))
    writes = [e for e in proxy.monitor.trace.events if e.event_type == "data_write"]
    assert len(writes) == 1
    contains = writes[0].contains
    assert contains[0] == "lookup_customer"
    assert "pii" in contains
    assert "email" in contains
    assert "ssn" in contains


def test_mcp_proxy_list_tools_passthrough():
    """list_tools should pass through to the underlying client."""
    from sponsio import System

    system = System("test")
    client = MockMCPClient()
    proxy = MCPContractProxy(mcp_client=client, system=system)

    result = asyncio.run(proxy.list_tools())
    assert result == []
