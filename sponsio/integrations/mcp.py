"""MCP (Model Context Protocol) integration for Sponsio.

Provides two capabilities:
1. MCPContractProxy: wraps an MCP client to intercept tool calls and enforce contracts.
2. scan_mcp_tools: generates baseline contracts from MCP server tool definitions.

Usage:
    from sponsio.mcp import MCPContractProxy, scan_mcp_tools

    # Auto-generate contracts from MCP tool definitions
    system = scan_mcp_tools(tools_list, agent_id="my_agent")

    # Wrap MCP client for runtime enforcement
    proxy = MCPContractProxy(mcp_client=client, system=system, agent_id="my_agent")
    result = await proxy.call_tool("process_refund", {"order_id": "123"})
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from sponsio.models.agent import Agent
from sponsio.models.contract import Contract
from sponsio.models.system import System
from sponsio.patterns.library import (
    must_precede,
    no_data_leak,
    no_reversal,
    rate_limit,
)
from sponsio.runtime.monitor import RuntimeMonitor


# ---------------------------------------------------------------------------
# MCP Client Protocol (duck-typed, framework-agnostic)
# ---------------------------------------------------------------------------


@runtime_checkable
class MCPClient(Protocol):
    """Protocol for MCP clients.

    Any object that implements ``call_tool`` and ``list_tools`` as async
    methods satisfies this protocol.  This is duck-typed -- the object
    does not need to inherit from ``MCPClient``.
    """

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """Execute a tool on the MCP server."""
        ...

    async def list_tools(self) -> list[Any]:
        """List available tools from the MCP server."""
        ...


@dataclass
class MCPToolDef:
    """Simplified MCP tool definition for contract inference.

    Attributes:
        name: Tool name as exposed by the MCP server.
        description: Human-readable description of the tool.
        input_schema: Optional JSON Schema for the tool's input parameters.
    """

    name: str
    description: str = ""
    input_schema: dict | None = None


# ---------------------------------------------------------------------------
# MCPContractProxy -- runtime enforcement wrapper
# ---------------------------------------------------------------------------


class MCPContractProxy:
    """Wraps an MCP client to enforce contracts on every tool call.

    Usage::

        system = System("my_system")
        system.agent("assistant").enforces(
            must_precede("lookup_customer", "process_refund")
        )
        proxy = MCPContractProxy(mcp_client=client, system=system, agent_id="assistant")
        result = await proxy.call_tool("process_refund", {"order_id": "123"})
        # -> Blocked if lookup_customer hasn't been called yet

    Args:
        mcp_client: Any object implementing the ``MCPClient`` protocol
            (``call_tool`` and ``list_tools`` async methods).
        system: A ``System`` with contracts to enforce.
        agent_id: Logical agent identifier used in the monitor trace.
    """

    def __init__(
        self,
        mcp_client: Any,
        system: System,
        agent_id: str = "mcp_agent",
        tag_outputs: bool = True,
        tag_pii: bool = False,
    ) -> None:
        self._client = mcp_client
        self._monitor = RuntimeMonitor(system=system)
        self._agent_id = agent_id
        # Auto-tagging of tool outputs — same semantics as BaseGuard
        # but inlined here because ``MCPContractProxy`` is a standalone
        # proxy (not a BaseGuard subclass).
        self._tag_outputs = tag_outputs
        self._tag_pii = tag_pii

    async def call_tool(self, tool_name: str, arguments: dict | None = None) -> dict:
        """Call an MCP tool with contract verification.

        Checks contracts **before** execution (det constraints on args)
        and **after** execution (constraints on output content).  If a
        det violation is detected pre-execution, returns an error dict
        instead of executing the tool.

        Args:
            tool_name: Name of the MCP tool to call.
            arguments: Tool arguments (passed through to the real client).

        Returns:
            The tool result from the MCP client, or an error dict if blocked.
        """
        arguments = arguments or {}

        # Pre-execution: check det constraints on tool name + args
        results = self._monitor.check_action(
            agent_id=self._agent_id,
            action=tool_name,
            event_type="tool_call",
            metadata={"args": arguments},
        )

        # If hard block, return error without executing. Surface the
        # structured ``agent_msg`` in a separate field so MCP clients
        # that proxy this to an LLM (Claude Desktop, custom orchestrators)
        # can show the agent-tuned phrasing while keeping the legacy
        # ``violations`` array of log-formatted strings for back-compat.
        blocked = [r for r in results if r.action == "blocked"]
        if blocked:
            return {
                "error": "Blocked by behavioral contract",
                "violations": [r.message for r in blocked],
                "agent_messages": [
                    r.agent_msg for r in blocked if getattr(r, "agent_msg", "")
                ],
            }

        # Execute the actual MCP tool call
        result = await self._client.call_tool(tool_name, arguments)

        # Post-execution: enrich the trace with the tool's output so
        # content atoms (``output_has``, ``arg_field_has``) bind — but
        # do NOT emit a second ``tool_call`` event. Doing so would
        # double-count every invocation and break every ``rate_limit``,
        # ``idempotent``, ``bounded_retry``, and ``loop_detection``
        # contract (3 real calls → trace of 6 tool_call events). This
        # mirrors ``BaseGuard.observe_tool_output`` — attach content
        # to the most recent matching ``tool_call`` and re-ground for
        # the next check — without introducing a BaseGuard dependency
        # (MCPContractProxy only carries a RuntimeMonitor).
        try:
            trace_events = self._monitor.trace.events
            for ev in reversed(trace_events):
                if (
                    ev.event_type == "tool_call"
                    and ev.tool == tool_name
                    and ev.agent == self._agent_id
                ):
                    text = str(result)
                    ev.content = text if ev.content is None else (ev.content + text)
                    # Force re-ground so ``output_has`` atoms see the
                    # new content on the next check. See BaseGuard.
                    self._monitor._verifier.reset()
                    break
        except Exception:
            # Output enrichment is best-effort; never fail the tool
            # call because we couldn't attach content to the trace.
            pass

        # Auto-tag the tool output so ``contains()`` / ``no_data_leak``
        # contracts bind without manual instrumentation.
        if self._tag_outputs:
            from sponsio.integrations.base import _detect_pii_classes

            try:
                fields = [tool_name]
                if self._tag_pii:
                    fields.extend(
                        cls for cls in _detect_pii_classes(result) if cls not in fields
                    )
                self._monitor.check_action(
                    agent_id=self._agent_id,
                    action=f"<data_write:{tool_name}>",
                    event_type="data_write",
                    metadata={"key": tool_name, "contains": fields},
                )
            except Exception:
                pass

        return result

    async def list_tools(self) -> list:
        """Passthrough to the underlying MCP client's ``list_tools()``.

        Returns:
            The tool list from the wrapped MCP client.
        """
        return await self._client.list_tools()

    @property
    def monitor(self) -> RuntimeMonitor:
        """The underlying ``RuntimeMonitor`` instance.

        Useful for inspecting the accumulated trace or enforcement log
        after a sequence of tool calls.

        Returns:
            The ``RuntimeMonitor`` powering this proxy.
        """
        return self._monitor

    def reset(self) -> None:
        """Reset the monitor state (trace + enforcement log) for a new session.

        Call this between independent user sessions to avoid
        cross-session contract state leaking.
        """
        self._monitor.reset()


# ---------------------------------------------------------------------------
# MCP Tool Scanner -- auto-generate contracts from tool definitions
# ---------------------------------------------------------------------------


def scan_mcp_tools(
    tools: list[MCPToolDef] | list[dict],
    agent_id: str = "mcp_agent",
) -> System:
    """Generate baseline contracts from MCP tool definitions.

    Uses heuristics based on tool names and descriptions to infer
    safety contracts:

    - Tools with "delete"/"remove" in name -> ``must_precede("confirm_*", tool)``
    - Tools with "payment"/"refund"/"transfer" in name -> ``rate_limit``
    - Tools with "send"/"email"/"notify" in description -> ``no_data_leak``
    - Tools with "approve"/"reject" pairs -> ``no_reversal``

    Args:
        tools: List of ``MCPToolDef`` objects or dicts with ``"name"``
            and ``"description"`` fields.
        agent_id: Agent identifier for the generated contracts.

    Returns:
        A ``System`` with auto-generated contracts.
    """
    # Normalize to MCPToolDef
    tool_defs: list[MCPToolDef] = []
    for t in tools:
        if isinstance(t, dict):
            tool_defs.append(
                MCPToolDef(
                    name=t.get("name", ""),
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema") or t.get("input_schema"),
                )
            )
        elif isinstance(t, MCPToolDef):
            tool_defs.append(t)
        else:
            # Duck-type: try to extract name and description
            tool_defs.append(
                MCPToolDef(
                    name=getattr(t, "name", str(t)),
                    description=getattr(t, "description", ""),
                )
            )

    tool_names = [t.name for t in tool_defs]
    agent = Agent(id=agent_id, tools=tool_names)
    enforcements: list = []

    for tool in tool_defs:
        tn_lower = tool.name.lower()
        desc_lower = tool.description.lower()

        if any(kw in tn_lower for kw in ("delete", "remove", "drop", "destroy")):
            confirm_name = f"confirm_{tool.name}"
            if confirm_name in tool_names:
                enforcements.append(
                    must_precede(
                        confirm_name,
                        tool.name,
                        desc=f"Confirm before {tool.name} (auto-inferred: destructive action)",
                    )
                )

        if any(
            kw in tn_lower
            for kw in ("payment", "refund", "transfer", "charge", "withdraw")
        ):
            enforcements.append(
                rate_limit(
                    tool.name,
                    max_count=5,
                    desc=f"{tool.name} limited to 5 per session (auto-inferred: financial action)",
                )
            )

        if any(
            kw in desc_lower for kw in ("send", "email", "notify", "sms", "message")
        ):
            enforcements.append(
                no_data_leak(
                    "pii",
                    tool.name,
                    desc=f"No PII leak via {tool.name} (auto-inferred: external communication)",
                )
            )

    for tool in tool_defs:
        tn = tool.name
        for prefix in ("approve_", "accept_", "confirm_"):
            if tn.startswith(prefix):
                base = tn[len(prefix) :]
                for contra_prefix in ("reject_", "deny_", "cancel_"):
                    contra = contra_prefix + base
                    if contra in tool_names:
                        enforcements.append(
                            no_reversal(
                                tn,
                                contra,
                                desc=f"Cannot {contra} after {tn} (auto-inferred: approve/reject pair)",
                            )
                        )

    contracts = [Contract(agent=agent, guarantee=e) for e in enforcements]
    system = System(name=f"mcp_{agent_id}", contracts=contracts)

    return system
