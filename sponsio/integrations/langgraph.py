"""LangGraph integration — enforce contracts on tool calls.

Two integration patterns:

1. **guard.wrap(tools)** — get a guarded ToolNode (recommended):

        guard = LangGraphGuard(contracts=[
            "tool `check_policy` must precede `issue_refund`",
        ])
        agent = create_react_agent(model, guard.wrap(tools))
        result = agent.invoke({"messages": [("user", input)]})

2. **Direct API** — for manual control or non-LangGraph use:

        result = guard.guard_before("issue_refund", {"order_id": "123"})
        if result.blocked: ...

Wraps each tool to enforce contracts before/after execution.
Thread-safe: parallel tool calls are serialized via lock in BaseGuard.
"""

from __future__ import annotations

from typing import Any, Dict
from uuid import UUID

from sponsio.integrations.base import BaseGuard, CheckResult
from sponsio.models.contract import Contract
from sponsio.models.system import System
from sponsio.runtime.evaluators import StoEvaluator
from sponsio.runtime.strategies import EnforcementStrategy


class ToolCallBlocked(Exception):
    """Raised when a tool call violates a hard contract."""

    def __init__(self, tool_name: str, constraint: str, message: str):
        self.tool_name = tool_name
        self.constraint = constraint
        super().__init__(message)


class LangGraphGuard(BaseGuard):
    """LangGraph contract guard — enforces hard + sto constraints on tool calls.

    Usage::

        guard = LangGraphGuard(
            contracts=[
                "tool `check_policy` must precede `issue_refund`",
                "tool `issue_refund` must not be called more than once",
            ],
        )

        # Recommended: guarded ToolNode
        agent = create_react_agent(model, guard.wrap(tools))
        result = agent.invoke({"messages": [...]})

        # Or: direct check (non-LangGraph)
        result = guard.guard_before("issue_refund", {"order_id": "123"})
    """

    def __init__(
        self,
        agent_id: str = "agent",
        contracts: list[dict | Contract | str] | None = None,
        system: System | None = None,
        policy: dict[str, EnforcementStrategy] | None = None,
        sto_evaluator: StoEvaluator | None = None,
        block: bool = True,
        store: Any = None,
        **kwargs: Any,
    ):
        super().__init__(
            agent_id=agent_id,
            contracts=contracts,
            system=system,
            policy=policy,
            sto_evaluator=sto_evaluator,
            store=store,
            **kwargs,
        )
        self._block = block

    # -----------------------------------------------------------------
    # LangGraph native integration
    # -----------------------------------------------------------------

    def wrap_graph(self, graph: Any) -> Any:
        """Wrap a compiled LangGraph with contract enforcement and dashboard streaming.

        Uses this guard's contracts and dashboard URL to monitor every node
        invocation in the graph.

        Usage::

            import sponsio

            guard = sponsio.init(
                framework="langgraph",
                agent_id="earnings_pipeline",
                contracts=["tool `parser` must precede `forecaster`"],
                dashboard="http://localhost:8000",
            )
            monitored = guard.wrap_graph(graph)
            result = monitored.invoke(state)

        Args:
            graph: A compiled LangGraph (result of ``builder.compile()``).

        Returns:
            A wrapper with the same ``.invoke()`` / ``.stream()`` interface.
        """
        url = self._dashboard_url or "http://localhost:8000"
        return _build_monitored_graph(graph, url, self.agent_id, self)

    def wrap(self, tools: list | Any) -> Any:
        """Wrap tools with contract enforcement for LangGraph.

        Returns a ``ToolNode`` where every tool call is checked against
        the loaded contracts before execution. Blocked calls return an
        error ``ToolMessage`` so the agent can self-correct.

        Example::

            guard = sponsio.init(config="sponsio.yaml", framework="langgraph")
            agent = create_react_agent(model, guard.wrap(tools))

        Args:
            tools: List of LangChain tools or callables.

        Returns:
            A ``ToolNode`` with contract enforcement on every call.
        """
        try:
            from langgraph.prebuilt.tool_node import ToolNode
        except ImportError:
            raise ImportError(
                "langgraph is required. Install with: pip install langgraph"
            )

        wrapped = [self._wrap_tool(t) for t in tools]
        return ToolNode(wrapped, handle_tool_errors=True)

    def tool_node(self, *args, **kwargs):
        """Deprecated: use ``wrap()`` instead."""
        return self.wrap(*args, **kwargs)

    def _guard_check(self, tool_name: str, kwargs: dict):
        """Run guard_before and raise if blocked."""
        check = self.guard_before(tool_name, kwargs)
        if check.blocked:
            msg = (
                check.det_violations[0].message
                if check.det_violations
                else "contract violated"
            )
            raise ToolCallBlocked(
                tool_name=tool_name,
                constraint=msg,
                message=f"BLOCKED by contract: {msg}",
            )

    def _guard_post_check(self, tool_name: str, result: Any):
        """Run guard_after and raise if sto constraint fails."""
        post = self.guard_after(tool_name, str(result))
        if post.needs_retry and post.feedback:
            raise ToolCallBlocked(
                tool_name=tool_name,
                constraint="sto constraint",
                message=f"Tool succeeded but output quality check failed. "
                f"Feedback: {post.feedback}. Original output: {result}",
            )

    def _wrap_tool(self, tool: Any) -> Any:
        """Wrap a single LangChain tool with contract enforcement."""
        from langchain_core.tools import StructuredTool

        guard = self
        original_func = tool.func
        original_coro = getattr(tool, "coroutine", None)

        def guarded_func(**kwargs):
            guard._guard_check(tool.name, kwargs)
            result = original_func(**kwargs)
            guard._guard_post_check(tool.name, result)
            return result

        async def guarded_coro(**kwargs):
            guard._guard_check(tool.name, kwargs)
            result = await original_coro(**kwargs)
            guard._guard_post_check(tool.name, result)
            return result

        return StructuredTool(
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
            func=guarded_func,
            coroutine=guarded_coro if original_coro else None,
        )

    # -----------------------------------------------------------------
    # Direct API (for manual use or non-LangGraph frameworks)
    # -----------------------------------------------------------------

    def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input_str: str,
        *,
        run_id: UUID | None = None,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict | None = None,
        inputs: dict | None = None,
        **kwargs: Any,
    ) -> None:
        """Called BEFORE a tool executes. Enforces hard contracts.

        For manual use or non-LangGraph frameworks.
        Prefer ``wrap()`` for LangGraph.
        """
        tool_name = serialized.get("name", "") or str(serialized.get("id", "unknown"))

        result = self.guard_before(tool_name, {"input": input_str})

        if result.blocked and self._block:
            msg = (
                result.det_violations[0].message
                if result.det_violations
                else "contract violated"
            )
            raise ToolCallBlocked(
                tool_name=tool_name,
                constraint=msg,
                message=f"\u25e1\u25e0 BLOCKED: {msg}",
            )

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID | None = None,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> CheckResult:
        """Called AFTER a tool executes. Checks sto constraints."""
        return self.guard_after("", output)

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID | None = None,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when a tool errors."""
        pass


# ---------------------------------------------------------------------------
# monitor_graph — zero-config monitoring for any LangGraph StateGraph
# ---------------------------------------------------------------------------


def monitor_graph(
    graph: Any,
    *,
    dashboard_url: str = "http://localhost:8000",
    agent_id: str = "agent",
    contracts: list[str] | None = None,
) -> Any:
    """Wrap a compiled LangGraph to enforce contracts and stream to the dashboard.

    Works with any ``StateGraph`` — react agents, sequential pipelines,
    branching graphs.

    Without ``contracts``: monitoring only — pushes events for visibility.
    With ``contracts``: full enforcement — each node goes through
    ``BaseGuard.guard_before()``, producing span trees, blocking violations,
    and streaming everything to the dashboard.

    Usage::

        from sponsio.integrations.langgraph import monitor_graph

        # Monitor only
        graph = monitor_graph(graph, dashboard_url="http://localhost:8000")

        # Monitor + enforce
        graph = monitor_graph(graph,
            dashboard_url="http://localhost:8000",
            contracts=["tool `parser` must precede `forecaster`"],
        )
        result = graph.invoke(state)

    Args:
        graph: A compiled LangGraph (result of ``builder.compile()``).
        dashboard_url: Sponsio API base URL.
        agent_id: Agent identifier for the trace events.
        contracts: Optional list of NL contract strings. When provided,
            enforcement is active and span trees are generated.

    Returns:
        A wrapper with the same ``.invoke()`` / ``.stream()`` interface.
    """
    # Build guard if contracts provided
    guard: BaseGuard | None = None
    if contracts:
        guard = BaseGuard(
            agent_id=agent_id,
            contracts=contracts,
            dashboard_url=dashboard_url,
        )

    return _build_monitored_graph(graph, dashboard_url, agent_id, guard)


def _build_monitored_graph(
    graph: Any,
    dashboard_url: str,
    agent_id: str,
    guard: BaseGuard | None,
) -> Any:
    """Build a monitored graph wrapper (shared by monitor_graph and LangGraphGuard.monitor)."""

    class _MonitoredGraph:
        def __init__(self, inner: Any, url: str, aid: str, g: BaseGuard | None) -> None:
            self._inner = inner
            self._url = url
            self._aid = aid
            self._guard = g

        def _push(
            self, event_type: str, tool: str | None = None, content: str | None = None
        ) -> None:
            try:
                import json
                import urllib.request as _ur

                data = json.dumps(
                    {
                        "agent": self._aid,
                        "type": event_type,
                        "tool": tool,
                        "content": content,
                    }
                ).encode()
                req = _ur.Request(
                    f"{self._url}/api/monitor/push",
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                _ur.urlopen(req, timeout=2)
            except Exception:
                pass

        def _push_span(self, span_dict: dict) -> None:
            """Push a span tree to the dashboard for SpanTree rendering."""
            try:
                import json
                import urllib.request as _ur

                data = json.dumps(span_dict).encode()
                req = _ur.Request(
                    f"{self._url}/api/monitor/push-span",
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                _ur.urlopen(req, timeout=2)
            except Exception:
                pass

        @staticmethod
        def _summarize_output(output: Any) -> str:
            """Extract a human-readable summary from a node's output."""
            if output is None:
                return ""
            if not isinstance(output, dict):
                return str(output)[:120]
            parts = []
            for k, v in output.items():
                if v is None:
                    continue
                try:
                    if isinstance(v, list):
                        parts.append(f"{k}: {len(v)} items")
                    elif isinstance(v, dict):
                        parts.append(f"{k}: dict({len(v)} keys)")
                    elif isinstance(v, (int, float)):
                        parts.append(f"{k}={v}")
                    elif isinstance(v, str):
                        parts.append(f"{k}={v[:50]}" if len(v) > 50 else f"{k}={v}")
                    elif isinstance(v, bool):
                        parts.append(f"{k}={v}")
                    else:
                        # Pydantic / dataclass / any object
                        type_name = type(v).__name__
                        # Try model_dump (pydantic v2), dict (v1), then __dict__
                        if hasattr(v, "model_dump"):
                            d = v.model_dump()
                            preview = ", ".join(f"{fk}" for fk in list(d.keys())[:4])
                            parts.append(f"{k}: {type_name}({preview})")
                        elif hasattr(v, "dict"):
                            d = v.dict()
                            preview = ", ".join(f"{fk}" for fk in list(d.keys())[:4])
                            parts.append(f"{k}: {type_name}({preview})")
                        else:
                            parts.append(f"{k}: {type_name}")
                except Exception:
                    parts.append(f"{k}: {type(v).__name__}")
            return "; ".join(parts[:5]) if parts else "output: dict"

        def _check_node(self, node_name: str) -> bool:
            """Run enforcement for a node. Returns True if allowed."""
            if not self._guard:
                self._push("tool_call", tool=node_name)
                return True

            # guard_before already pushes a tool_call event via _push_to_dashboard
            result = self._guard.guard_before(node_name)
            # Push the span tree
            span = self._guard.last_check_span
            if span:
                self._push_span(span.to_dict())

            return not result.blocked

        def invoke(self, state: Any, *, config: Any = None, **kwargs: Any) -> Any:
            """Invoke the graph, enforcing contracts on each node."""
            cfg = config or {}
            last_state = state
            for chunk in self._inner.stream(
                state, config=cfg, stream_mode="updates", **kwargs
            ):
                for node_name, node_output in chunk.items():
                    self._check_node(node_name)
                    summary = self._summarize_output(node_output)
                    if summary:
                        self._push("data_write", tool=node_name, content=summary)
                    last_state = node_output
            return (
                self._inner.get_state(cfg).values if last_state is not state else state
            )

        def stream(self, state: Any, *, config: Any = None, **kwargs: Any):
            """Stream the graph, enforcing contracts on each node."""
            cfg = config or {}
            for chunk in self._inner.stream(state, config=cfg, **kwargs):
                if isinstance(chunk, dict):
                    for node_name, node_output in chunk.items():
                        self._check_node(node_name)
                        summary = self._summarize_output(node_output)
                        if summary:
                            self._push("data_write", tool=node_name, content=summary)
                yield chunk

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    return _MonitoredGraph(graph, dashboard_url, agent_id, guard)


# Backward compatibility alias (deprecated)
ContractGuard = LangGraphGuard
