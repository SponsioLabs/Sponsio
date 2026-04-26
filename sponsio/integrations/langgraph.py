"""LangGraph integration — enforce contracts on tool calls.

Two integration patterns:

1. **guard.wrap(tools)** — get a guarded ToolNode (recommended):

        from sponsio import contract

        guard = LangGraphGuard(contracts=[
            contract("policy gate before refund")
                .assume("called `issue_refund`")
                .enforce("must call `check_policy` before `issue_refund`"),
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

from sponsio.integrations.base import (
    BaseGuard,
    CheckResult,
    format_sto_retry_message,
    select_agent_message,
)
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
        contracts: list[Any] | None = None,
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

            from sponsio.langgraph import Sponsio

            guard = Sponsio(
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

            from sponsio.langgraph import Sponsio

            guard = Sponsio(config="sponsio.yaml")
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
            msg = select_agent_message(
                check.det_violations, fallback="contract violated"
            )
            raise ToolCallBlocked(
                tool_name=tool_name,
                constraint=msg,
                message=f"BLOCKED by contract: {msg}",
            )

    def _guard_post_check(self, tool_name: str, result: Any) -> Any:
        """Run guard_after and, on sto failure, return feedback inline.

        Historical note (#12): this method used to *raise*
        ``ToolCallBlocked`` when the sto pipeline flagged the tool
        output. Every other framework adapter (OpenAI Agents, CrewAI,
        Vercel AI, Claude Agent) instead returns the feedback as the
        tool result so the model self-corrects on the next turn — which
        is what the sto ``RetryWithConstraint`` strategy was designed
        for. Raising aborted the whole LangGraph run, making sto
        violations functionally identical to det blocks and defeating
        the retry strategy entirely.

        Now harmonised: we return the feedback string through the tool
        result channel like every other adapter, using the shared
        :func:`format_sto_retry_message` template.

        Auto-tagging happens inside ``BaseGuard.guard_after`` when
        ``tag_outputs`` / ``tag_pii`` are set on the guard — no
        integration-specific logic needed here.
        """
        post = self.guard_after(tool_name, str(result))
        if post.needs_retry and post.feedback:
            return format_sto_retry_message(post.feedback, result)
        return result

    def _wrap_tool(self, tool: Any) -> Any:
        """Wrap a single LangChain tool with contract enforcement."""
        from langchain_core.tools import StructuredTool

        guard = self
        original_func = tool.func
        original_coro = getattr(tool, "coroutine", None)

        def guarded_func(**kwargs):
            guard._guard_check(tool.name, kwargs)
            result = original_func(**kwargs)
            # _guard_post_check returns the (possibly patched) result —
            # a plain string if the sto pipeline flagged it, otherwise
            # the original tool output unchanged.
            return guard._guard_post_check(tool.name, result)

        async def guarded_coro(**kwargs):
            guard._guard_check(tool.name, kwargs)
            result = await original_coro(**kwargs)
            return guard._guard_post_check(tool.name, result)

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
            msg = select_agent_message(
                result.det_violations, fallback="contract violated"
            )
            raise ToolCallBlocked(
                tool_name=tool_name,
                constraint=msg,
                message=f"\u25d2\u25d3 BLOCKED: {msg}",
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

    # -----------------------------------------------------------------
    # LLM observation — feed llm_response events into the trace so sto
    # atoms (injection_free, scope_respect, etc.) can evaluate actual
    # model output. Without this hook the trace only sees tool_call
    # events and llm-response-scoped atoms silently pass.
    # -----------------------------------------------------------------

    def langchain_callback(self):
        """Return a LangChain ``BaseCallbackHandler`` that feeds LLM
        responses into the guard's trace.

        Attach this to your agent config so sto atoms like
        ``injection_free``, ``toxic_free``, ``scope_respect`` actually
        fire on model output:

        .. code-block:: python

            from sponsio import contract
            from sponsio.langgraph import Sponsio

            guard = Sponsio(
                contracts=[
                    contract("response free of prompt injection")
                        .enforce(Atom("injection_free", atom_type="sto"))
                        .threshold(beta=0.9)
                ],
                sto_judge=BooleanJudge(...),
            )
            agent = create_react_agent(llm, guard.wrap(tools))
            result = agent.invoke(
                {"messages": [...]},
                config={"callbacks": [guard.langchain_callback()]},
            )

        Without this callback, the sto pipeline sees no
        ``llm_response`` events and response-scoped atoms are no-ops.
        """
        from langchain_core.callbacks import BaseCallbackHandler

        guard = self

        class _SponsioLLMCallback(BaseCallbackHandler):
            """Captures LLM request/response pairs and feeds them to the
            guard. Thread-safety is provided by BaseGuard's internal lock."""

            def on_llm_start(self, serialized, prompts, *, run_id=None, **kwargs):
                # Store the prompt keyed on run_id so on_llm_end can
                # emit it together with the response (useful for atoms
                # that read prompt_contains / context_length).
                self._prompts = getattr(self, "_prompts", {})
                if prompts:
                    self._prompts[run_id] = "\n\n".join(prompts)

            def on_chat_model_start(
                self, serialized, messages, *, run_id=None, **kwargs
            ):
                # LangChain fires this instead of on_llm_start for chat
                # models. Reconstruct a joined prompt for trace context.
                self._prompts = getattr(self, "_prompts", {})
                try:
                    flat = []
                    for msg_list in messages:
                        for m in msg_list:
                            content = getattr(m, "content", str(m))
                            flat.append(str(content))
                    self._prompts[run_id] = "\n\n".join(flat)
                except Exception:
                    self._prompts[run_id] = ""

            def on_llm_end(self, response, *, run_id=None, **kwargs):
                # LLMResult → generations is list[list[Generation]].
                # Take the first completion across all batches.
                text = ""
                try:
                    for gen_list in response.generations:
                        for gen in gen_list:
                            msg = getattr(gen, "message", None)
                            if msg is not None and getattr(msg, "content", None):
                                text = str(msg.content)
                            elif getattr(gen, "text", None):
                                text = gen.text
                            if text:
                                break
                        if text:
                            break
                except Exception:
                    pass
                if not text:
                    return
                prompt = getattr(self, "_prompts", {}).get(run_id) if run_id else None
                # observe_llm_call runs both det and sto atoms that key
                # on llm_request / llm_response events. Return value
                # (CheckResult) is not raised — callbacks shouldn't
                # raise; violations surface through guard.print_summary()
                # or the monitor's event log.
                try:
                    guard.observe_llm_call(prompt=prompt, response=text)
                except Exception:
                    # A failed sto judge shouldn't break the agent — the
                    # monitor already recorded the failure. Swallow and
                    # continue the agent loop.
                    pass

        return _SponsioLLMCallback()

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
