"""Google ADK integration - enforce contracts on ADK function tools.

Google's Agent Development Kit (ADK) turns Python callables in an
``Agent(tools=[...])`` list into function tools. This adapter keeps
that native flow: wrap your tool functions before passing them to ADK
and Sponsio checks each invocation before and after the function body.

Usage::

    from google.adk.agents.llm_agent import Agent
    from sponsio import contract
    from sponsio.google_adk import Sponsio

    guard = Sponsio(contracts=[
        contract("must search before booking")
            .assume("called `book_flight`")
            .enforce("must call `search_flights` before `book_flight`"),
    ])

    root_agent = Agent(
        name="travel_agent",
        model="gemini-flash-latest",
        tools=guard.wrap([search_flights, book_flight]),
    )

Blocked calls return an ADK-friendly error dict instead of executing
the underlying function, so the model sees a normal tool result and
can self-correct.
"""

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable

from sponsio.integrations.base import BaseGuard, CheckResult, format_sto_retry_message
from sponsio.models.system import System
from sponsio.runtime.evaluators import StoEvaluator
from sponsio.runtime.strategies import EnforcementStrategy


class GoogleADKGuard(BaseGuard):
    """Contract guard for Google ADK Python function tools.

    Wraps plain callables before they are handed to
    ``google.adk.agents.llm_agent.Agent(tools=[...])``. ADK still
    performs its usual signature/docstring inspection on the wrapped
    function because ``functools.wraps`` preserves the original
    metadata.

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

    def wrap_tool(self, tool: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap one ADK function tool with contract enforcement."""
        if not callable(tool):
            raise TypeError("GoogleADKGuard.wrap_tool expects a callable tool")

        guard = self
        tool_name = getattr(tool, "__name__", "tool")

        if inspect.iscoroutinefunction(tool):

            @functools.wraps(tool)
            async def guarded_async(*args: Any, **kwargs: Any) -> Any:
                check = guard.guard_before(tool_name, _call_args(tool, args, kwargs))
                guard.last_check = check
                if check.blocked:
                    return _blocked_result(check)

                result = await tool(*args, **kwargs)
                post = guard.guard_after(tool_name, str(result))
                if post.needs_retry and post.feedback:
                    return _retry_result(post.feedback, result)
                return result

            return guarded_async

        @functools.wraps(tool)
        def guarded_sync(*args: Any, **kwargs: Any) -> Any:
            check = guard.guard_before(tool_name, _call_args(tool, args, kwargs))
            guard.last_check = check
            if check.blocked:
                return _blocked_result(check)

            result = tool(*args, **kwargs)
            post = guard.guard_after(tool_name, str(result))
            if post.needs_retry and post.feedback:
                return _retry_result(post.feedback, result)
            return result

        return guarded_sync

    def wrap(self, tools: list[Callable[..., Any]]) -> list[Callable[..., Any]]:
        """Wrap tools before passing them to ``Agent(tools=...)``."""
        return [self.wrap_tool(t) for t in tools]

    def tools(self, *args, **kwargs):
        """Deprecated: use ``wrap()`` instead."""
        return self.wrap(*args, **kwargs)

    def check_tool_call(
        self, tool_name: str, args: dict[str, Any] | None = None
    ) -> CheckResult:
        """Manually check a tool call without wrapping."""
        check = self.guard_before(tool_name, args or {})
        self.last_check = check
        return check


def _call_args(
    fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> dict[str, Any]:
    if kwargs:
        return dict(kwargs)
    if not args:
        return {}
    try:
        bound = inspect.signature(fn).bind_partial(*args)
    except (TypeError, ValueError):
        return {"args": list(args)}
    return dict(bound.arguments)


def _blocked_result(check: CheckResult) -> dict[str, str]:
    msg = (
        check.det_violations[0].message
        if check.det_violations
        else "Contract violation detected"
    )
    return {"status": "error", "error_message": f"BLOCKED by contract: {msg}"}


def _retry_result(feedback: str, original: Any) -> dict[str, str]:
    return {
        "status": "error",
        "error_message": format_sto_retry_message(feedback, original),
    }
