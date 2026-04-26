"""OpenAI Agents SDK integration — enforce contracts on function tools.

Wraps ``@function_tool`` decorated tools with contract enforcement,
using the SDK's native tool execution flow.

Usage::

    from sponsio import contract
    from sponsio.agents import Sponsio

    guard = Sponsio(contracts=[
        contract("policy gate before refund")
            .assume("called `issue_refund`")
            .enforce("must call `check_policy` before `issue_refund`"),
        contract("refund rate limit")
            .enforce("tool `issue_refund` at most 1 times"),
    ])

    # Wrap tools — contract enforcement is transparent
    agent = Agent(
        name="support_bot",
        tools=guard.wrap([check_policy, issue_refund]),
    )

    result = Runner.run_sync(agent, input="process my refund")

    # Check results
    guard.violations       # all violations
    guard.last_check       # most recent CheckResult
    guard.summary()        # human-readable summary

When a tool call violates a contract, the wrapped tool raises a
``ToolCallBlocked`` error which the SDK surfaces to the model,
allowing it to self-correct.
"""

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable

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


class AgentsSDKGuard(BaseGuard):
    """Contract guard for the OpenAI Agents SDK.

    Wraps ``@function_tool`` decorated tools so that every invocation
    is checked against contracts before and after execution.

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

    def wrap_tool(self, tool: Any) -> Any:
        """Wrap a single ``@function_tool`` with contract enforcement.

        The returned tool has the same name, description, and schema
        as the original, but runs guard_before before execution and
        guard_after after.

        Args:
            tool: A ``FunctionTool`` object (from ``@function_tool``).

        Returns:
            A new ``FunctionTool`` with contract enforcement.

        Raises:
            ImportError: If ``openai-agents`` is not installed.
        """
        try:
            from agents import function_tool
        except ImportError:
            raise ImportError(
                "openai-agents is required. Install with: pip install openai-agents"
            )

        guard = self
        tool_name = getattr(tool, "name", getattr(tool, "__name__", str(tool)))
        # SDK kwarg name moved from ``name`` → ``name_override`` mid-2024.
        # Pick whichever the installed SDK accepts so users on either
        # side of the rename keep working.
        _name_kw = _function_tool_name_kw(function_tool)

        # Get the original callable
        original_fn = _extract_function(tool)

        if inspect.iscoroutinefunction(original_fn):

            @functools.wraps(original_fn)
            async def guarded_async(*args: Any, **kwargs: Any) -> Any:
                check = guard.guard_before(tool_name, kwargs)
                guard.last_check = check
                if check.blocked:
                    msg = select_agent_message(
                        check.det_violations, fallback="Contract violation"
                    )
                    raise ToolCallBlocked(tool_name, msg, f"BLOCKED by contract: {msg}")

                result = await original_fn(*args, **kwargs)

                post = guard.guard_after(tool_name, str(result))
                if post.needs_retry and post.feedback:
                    return format_sto_retry_message(post.feedback, result)

                return result

            return function_tool(**{_name_kw: tool_name})(guarded_async)
        else:

            @functools.wraps(original_fn)
            def guarded_sync(*args: Any, **kwargs: Any) -> Any:
                check = guard.guard_before(tool_name, kwargs)
                guard.last_check = check
                if check.blocked:
                    msg = select_agent_message(
                        check.det_violations, fallback="Contract violation"
                    )
                    raise ToolCallBlocked(tool_name, msg, f"BLOCKED by contract: {msg}")

                result = original_fn(*args, **kwargs)

                post = guard.guard_after(tool_name, str(result))
                if post.needs_retry and post.feedback:
                    return format_sto_retry_message(post.feedback, result)

                return result

            return function_tool(**{_name_kw: tool_name})(guarded_sync)

    def wrap(self, tools: list[Any]) -> list[Any]:
        """Wrap tools with contract enforcement for OpenAI Agents SDK.

        Example::

            from sponsio.agents import Sponsio

            guard = Sponsio(config="sponsio.yaml")
            agent = Agent(tools=guard.wrap(tools), instructions=...)

        Args:
            tools: List of ``FunctionTool`` objects.

        Returns:
            List of wrapped tools with contract enforcement.
        """
        return [self.wrap_tool(t) for t in tools]

    def wrap_tools(self, *args, **kwargs):
        """Deprecated: use ``wrap()`` instead."""
        return self.wrap(*args, **kwargs)

    def tools(self, *args, **kwargs):
        """Deprecated: use ``wrap()`` instead."""
        return self.wrap(*args, **kwargs)

    def check_tool_call(self, tool_name: str, args: dict | None = None) -> CheckResult:
        """Manually check a tool call without wrapping.

        Useful for custom execution flows or testing.

        Args:
            tool_name: Name of the tool being called.
            args: Tool arguments.

        Returns:
            CheckResult with allowed/blocked status.
        """
        check = self.guard_before(tool_name, args)
        self.last_check = check
        return check


def _function_tool_name_kw(function_tool: Callable) -> str:
    """Return the kwarg name :func:`agents.function_tool` uses for the tool name.

    The OpenAI Agents SDK renamed this kwarg from ``name`` →
    ``name_override`` mid-2024.  We inspect the signature so users on
    either side of the rename keep working without us hard-pinning a
    minimum SDK version (which would block adoption on older
    deployments).

    Falls back to ``"name_override"`` (the post-rename spelling)
    when the signature is unintrospectable, since that's the version
    we recommend in the README and the older SDK is mostly retired.
    """
    try:
        sig = inspect.signature(function_tool)
    except (TypeError, ValueError):
        return "name_override"
    if "name_override" in sig.parameters:
        return "name_override"
    if "name" in sig.parameters:
        return "name"
    return "name_override"


def _extract_function(tool: Any) -> Callable:
    """Extract the underlying callable from a FunctionTool or decorated function.

    Args:
        tool: A FunctionTool object, decorated function, or plain callable.

    Returns:
        The underlying callable.
    """
    # FunctionTool objects store the function in various attributes
    for attr in ("fn", "_fn", "func", "_func", "on_invoke_tool"):
        if hasattr(tool, attr):
            fn = getattr(tool, attr)
            if callable(fn):
                return fn

    # If it's already a callable (plain function), return it
    if callable(tool):
        return tool

    raise TypeError(f"Cannot extract function from {type(tool)}: {tool}")


# Backward compatibility alias (deprecated)
AgentsGuard = AgentsSDKGuard
