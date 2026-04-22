"""Vercel AI SDK integration — enforce contracts via middleware.

Uses the SDK's native ``ai.Middleware`` system to intercept every tool
execution with ``wrap_tool``, running ``guard_before`` / ``guard_after``
transparently inside the agent loop.

Usage::

    from sponsio import contract
    from sponsio.vercel_ai import Sponsio

    guard = Sponsio(contracts=[
        contract("policy gate before refund")
            .assume("called `issue_refund`")
            .enforce("must call `check_policy` before `issue_refund`"),
        contract("refund rate limit")
            .enforce("tool `issue_refund` at most 1 times"),
    ])

    agent = ai.agent(tools=[check_policy, issue_refund])

    # Pass Sponsio as middleware — 1 line
    async for msg in agent.run(model, messages, middleware=[guard.wrap()]):
        print(msg.text_delta or "", end="")

Or explicitly::

    from sponsio.vercel_ai import Sponsio

    guard = Sponsio(
        agent_id="bot",
        contracts=[...],
    )
    # guard.wrap() returns the ai.Middleware instance

When a tool call violates a det contract, the middleware returns an
error tool result to the model (``is_error=True``), allowing it to
self-correct without executing the tool.
"""

from __future__ import annotations

from typing import Any

from sponsio.integrations.base import BaseGuard, CheckResult
from sponsio.models.system import System
from sponsio.runtime.evaluators import StoEvaluator
from sponsio.runtime.strategies import EnforcementStrategy


class VercelAIGuard(BaseGuard):
    """Contract guard for the Vercel AI SDK.

    Provides an ``ai.Middleware`` that intercepts tool calls via
    ``wrap_tool``. The middleware calls ``guard_before`` before execution
    and ``guard_after`` after, matching Sponsio's dual-pipeline model.

    Attributes:
        last_check: The CheckResult from the most recent tool call.
    """

    def __init__(
        self,
        agent_id: str = "agent",
        contracts: list[Any] | None = None,
        system: System | None = None,
        policy: dict[str, EnforcementStrategy] | None = None,
        sto_evaluator: StoEvaluator | None = None,
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
        self.last_check: CheckResult | None = None

    def wrap(self) -> Any:
        """Return an ``ai.Middleware`` that enforces contracts on tool calls.

        Usage::

            async for msg in agent.run(model, messages,
                                       middleware=[guard.wrap()]):
                ...

        Returns:
            An ``ai.Middleware`` instance.

        Raises:
            ImportError: If ``vercel-ai-sdk`` is not installed.
        """
        try:
            import ai
        except ImportError:
            raise ImportError(
                "vercel-ai-sdk is required. Install with: pip install vercel-ai-sdk"
            )

        guard = self

        class SponsioMiddleware(ai.Middleware):
            """Middleware that enforces Sponsio contracts on every tool call."""

            async def wrap_tool(self, call: Any, next_fn: Any) -> Any:
                tool_name = call.tool_name
                kwargs = call.kwargs

                # --- Det check (before execution) ---
                check = guard.guard_before(tool_name, kwargs)
                guard.last_check = check

                if check.blocked:
                    msg = (
                        check.det_violations[0].message
                        if check.det_violations
                        else "Contract violation"
                    )
                    return ai.Message(
                        role="tool",
                        parts=[
                            ai.ToolResultPart(
                                tool_call_id=call.tool_call_id,
                                tool_name=tool_name,
                                result=f"BLOCKED by contract: {msg}",
                                is_error=True,
                            )
                        ],
                    )

                # --- Execute tool ---
                result_msg = await next_fn(call)

                # --- Sto check (after execution) ---
                result_text = ""
                for part in getattr(result_msg, "parts", []):
                    if hasattr(part, "result"):
                        result_text = str(part.result)
                        break

                post = guard.guard_after(tool_name, result_text)
                if post.needs_retry and post.feedback:
                    patched_parts = []
                    for part in result_msg.parts:
                        if hasattr(part, "result"):
                            patched_parts.append(
                                ai.ToolResultPart(
                                    tool_call_id=part.tool_call_id,
                                    tool_name=part.tool_name,
                                    result=(
                                        f"Quality check failed. "
                                        f"Feedback: {post.feedback}. "
                                        f"Original: {part.result}"
                                    ),
                                    is_error=True,
                                )
                            )
                        else:
                            patched_parts.append(part)
                    return ai.Message(role=result_msg.role, parts=patched_parts)

                return result_msg

        return SponsioMiddleware()

    def middleware(self, *args, **kwargs):
        """Deprecated: use ``wrap()`` instead."""
        return self.wrap(*args, **kwargs)
