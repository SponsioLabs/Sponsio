"""CrewAI integration — enforce contracts via tool call hooks.

Uses CrewAI's native ``before_tool_call`` / ``after_tool_call`` hooks.
No monkey-patching, no tool wrapping.

Usage::

    from sponsio.integrations.crewai import CrewAIGuard

    guard = CrewAIGuard(contracts=[
        "tool `check_policy` must precede `issue_refund`",
        "tool `issue_refund` must not be called more than once",
    ])

    crew = Crew(
        agents=[agent],
        tasks=[task],
        before_tool_call=guard.before_hook,
        after_tool_call=guard.after_hook,
    )
    result = crew.kickoff()

    # Or register globally:
    guard.register_global_hooks()

When a tool call violates a hard contract, ``before_hook`` returns a
dict with an error message, which CrewAI surfaces to the agent as the
tool result. The agent then self-corrects.
"""

from __future__ import annotations

import functools
from typing import Any

from sponsio.integrations.base import BaseGuard, CheckResult
from sponsio.models.contract import Contract
from sponsio.models.system import System
from sponsio.runtime.evaluators import StoEvaluator
from sponsio.runtime.strategies import EnforcementStrategy


class CrewAIGuard(BaseGuard):
    """Contract guard for CrewAI tool call hooks.

    Provides ``before_hook`` and ``after_hook`` callables that plug
    directly into CrewAI's ``Crew(before_tool_call=..., after_tool_call=...)``
    or the global ``@before_tool_call`` / ``@after_tool_call`` decorators.

    Attributes:
        last_check: The CheckResult from the most recent tool call.
    """

    def __init__(
        self,
        agent_id: str = "agent",
        contracts: list[dict | Contract | str] | None = None,
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

    def on_tool_start(self, context: Any) -> Any:
        """Hook for ``Crew(before_tool_call=guard.on_tool_start)``.

        Called before every tool execution. Runs det constraint checks.

        Args:
            context: CrewAI ``ToolCallHookContext`` with ``tool_name``,
                ``tool_input``, ``agent``, ``task``, etc.

        Returns:
            - ``None`` if the tool call is allowed (execution proceeds).
            - A ``dict`` with an error message if blocked (returned to
              the agent as the tool result, skipping actual execution).
        """
        tool_name = getattr(context, "tool_name", str(context))
        tool_input = getattr(context, "tool_input", {})

        check = self.guard_before(
            tool_name, tool_input if isinstance(tool_input, dict) else {}
        )
        self.last_check = check

        if check.blocked:
            msg = (
                check.det_violations[0].message
                if check.det_violations
                else "Contract violation detected"
            )
            # Returning a dict tells CrewAI to use this as the tool result
            # instead of executing the tool
            return {"error": f"BLOCKED by contract: {msg}"}

        return None  # Allow execution

    def on_tool_end(self, context: Any, result: str) -> str | None:
        """Hook for ``Crew(after_tool_call=guard.on_tool_end)``.

        Called after every tool execution. Runs sto constraint checks.

        Args:
            context: CrewAI ``ToolCallHookContext``.
            result: The tool's output string.

        Returns:
            - ``None`` to keep the original result.
            - A modified string if sto constraints require feedback.
        """
        tool_name = getattr(context, "tool_name", str(context))

        check = self.guard_after(tool_name, result)

        if check.needs_retry and check.feedback:
            return (
                f"Tool succeeded but output quality check failed. "
                f"Feedback: {check.feedback}. "
                f"Original output: {result}"
            )

        return None  # Keep original result

    def wrap(self, tools: list[Any]) -> list[Any]:
        """Wrap plain functions as CrewAI Tools with contract enforcement.

        Each function is turned into a CrewAI Tool via ``@crewai.tools.tool``.
        The wrapper runs ``guard_before`` before the call and ``guard_after``
        after the call, surfacing violations as the tool result so the agent
        can self-correct.

        Args:
            tools: List of plain callables (or CrewAI Tool objects).

        Returns:
            List of CrewAI Tool objects. Each has a ``.name`` attribute
            matching the original function name.

        Raises:
            ImportError: If ``crewai`` is not installed.
        """
        try:
            from crewai.tools import tool as crewai_tool_decorator
        except (ImportError, TypeError) as e:
            raise ImportError(
                "crewai is required. Install with: pip install crewai"
            ) from e

        guard = self
        wrapped: list[Any] = []
        for fn in tools:
            # Already a CrewAI tool? Unwrap to the underlying function.
            original = getattr(fn, "func", fn)
            tool_name = getattr(fn, "name", None) or getattr(
                original, "__name__", "tool"
            )

            def make_guarded(orig: Any, name: str):
                @functools.wraps(orig)
                def guarded(*args: Any, **kwargs: Any) -> Any:
                    call_args = kwargs if kwargs else {"args": list(args)}
                    check = guard.guard_before(name, call_args)
                    if check.blocked:
                        msg = (
                            check.det_violations[0].message
                            if check.det_violations
                            else "contract violated"
                        )
                        return f"BLOCKED by contract: {msg}"
                    result = orig(*args, **kwargs)
                    post = guard.guard_after(name, str(result))
                    if post.needs_retry and post.feedback:
                        return (
                            f"Tool succeeded but output quality check failed. "
                            f"Feedback: {post.feedback}. Original output: {result}"
                        )
                    return result

                guarded.__name__ = name
                return guarded

            guarded_fn = make_guarded(original, tool_name)
            wrapped.append(crewai_tool_decorator(guarded_fn))

        return wrapped

    def tool_node(self, *args, **kwargs):
        """Deprecated: use ``wrap()`` instead."""
        return self.wrap(*args, **kwargs)

    def tools(self, *args, **kwargs):
        """Deprecated: use ``wrap()`` instead."""
        return self.wrap(*args, **kwargs)

    def register_global_hooks(self) -> None:
        """Register before/after hooks globally with CrewAI.

        After calling this, ALL crews will have contract enforcement.

        Raises:
            ImportError: If ``crewai`` is not installed.
        """
        try:
            from crewai.tools import before_tool_call, after_tool_call
        except (ImportError, TypeError):
            raise ImportError("crewai is required. Install with: pip install crewai")

        before_tool_call(self.on_tool_start)
        after_tool_call(self.on_tool_end)

    # Backward-compatible aliases (deprecated)
    def before_hook(self, context: Any) -> Any:
        """Deprecated: use ``on_tool_start`` instead."""
        return self.on_tool_start(context)

    def after_hook(self, context: Any, result: str) -> str | None:
        """Deprecated: use ``on_tool_end`` instead."""
        return self.on_tool_end(context, result)
