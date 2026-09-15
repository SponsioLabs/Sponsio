"""CrewAI integration. enforce contracts via tool call hooks.

Uses CrewAI's native ``before_tool_call`` / ``after_tool_call`` hooks.
No monkey-patching, no tool wrapping.

Usage::

    from sponsio import contract
    from sponsio.crewai import Sponsio

    guard = Sponsio(contracts=[
        contract("policy gate before refund")
            .assume("called `issue_refund`")
            .guarantees("must call `check_policy` before `issue_refund`"),
        contract("refund rate limit")
            .guarantees("tool `issue_refund` at most 1 times"),
    ])

    # Register the hooks with CrewAI (every crew in the process):
    guard.register_global_hooks()

    # or with the decorators yourself:
    from crewai.hooks import after_tool_call, before_tool_call
    before_tool_call(guard.on_tool_start)
    after_tool_call(guard.on_tool_end)

    crew = Crew(agents=[agent], tasks=[task])
    result = crew.kickoff()

    # or wrap the tools instead of hooking the crew:
    agent = Agent(..., tools=guard.wrap([check_policy, issue_refund]))

CrewAI's hook protocol: a ``before_tool_call`` hook that returns ``False``
blocks the call; every other return value lets the tool run. CrewAI
then still runs the ``after_tool_call`` hooks with its own generic
"blocked by hook" result, and an ``after_tool_call`` hook that returns
a string replaces the tool result. ``on_tool_start`` therefore returns
``False`` on a stopping verdict and ``on_tool_end`` swaps CrewAI's
generic message for the contract violation so the agent learns why
and self-corrects.
"""

from __future__ import annotations

import functools
import sys
from typing import Any

from sponsio.integrations.base import (
    BaseGuard,
    CheckResult,
    format_sto_retry_message,
    select_agent_message,
)
from sponsio.models.system import System
from sponsio.protocols.sto import StoEvaluator
from sponsio.runtime.strategies import EnforcementStrategy

# CrewAI's own tool result for a call a before-hook refused. The after-hook
# only replaces this exact shape, so a lingering block reason can never
# overwrite the output of a later call that really ran.
_CREWAI_BLOCKED_PREFIX = "Tool execution blocked by hook"


class CrewAIGuard(BaseGuard):
    """Contract guard for CrewAI tool call hooks.

    Provides ``on_tool_start`` and ``on_tool_end`` callables that plug
    into CrewAI's ``@before_tool_call`` / ``@after_tool_call`` hook
    registry (see :meth:`register_global_hooks`), plus :meth:`wrap` for
    wrapping the tools themselves.

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
        # tool name -> reason for the block ``on_tool_start`` just issued,
        # consumed by ``on_tool_end`` when CrewAI reports the refusal.
        self._pending_blocks: dict[str, str] = {}

    def on_tool_start(self, context: Any) -> bool | None:
        """Hook for CrewAI's ``before_tool_call``.

        Called before every tool execution. Runs det constraint checks.

        Args:
            context: CrewAI ``ToolCallHookContext`` with ``tool_name``,
                ``tool_input``, ``agent``, ``task``, etc.

        Returns:
            - ``None`` if the tool call is allowed (execution proceeds).
            - ``False`` if blocked. This is the only return value CrewAI
              treats as a refusal; anything else (including a dict with
              an error message, which earlier releases returned) lets the
              tool run.
        """
        tool_name = getattr(context, "tool_name", str(context))
        tool_input = getattr(context, "tool_input", {})

        try:
            check = self.guard_before(
                tool_name, tool_input if isinstance(tool_input, dict) else {}
            )
        except Exception as exc:  # noqa: BLE001 - fail closed, see below
            # CrewAI swallows exceptions raised by a hook and runs the
            # tool anyway. A guard that cannot evaluate must refuse, not
            # wave the call through.
            sys.stderr.write(
                f"sponsio: CrewAI guard could not evaluate `{tool_name}` "
                f"({exc}); refusing the call.\n"
            )
            self._pending_blocks[tool_name] = (
                f"BLOCKED by Sponsio: the contract guard failed to evaluate "
                f"`{tool_name}` ({exc})."
            )
            return False
        self.last_check = check

        # ``stop_original`` folds in ``redirected``: CrewAI's adapter has
        # no transparent-substitution path, so a redirect fails closed
        # (refuses the call) rather than executing the unsafe tool.
        if check.stop_original:
            msg = select_agent_message(
                check.det_violations, fallback="Contract violation detected"
            )
            # The "BLOCKED by contract:" prefix is preserved so CrewAI
            # agents trained on this template still recognise the
            # rejection pattern.
            self._pending_blocks[tool_name] = f"BLOCKED by contract: {msg}"
            return False

        return None  # Allow execution

    def on_tool_end(self, context: Any, result: Any = None) -> str | None:
        """Hook for CrewAI's ``after_tool_call``.

        Called after every tool execution, and after every refusal too
        (CrewAI runs the after-hooks on a blocked call with its generic
        "blocked by hook" result). Runs sto constraint checks.

        Args:
            context: CrewAI ``ToolCallHookContext``; the tool output is
                ``context.tool_result``.
            result: The tool's output. Optional; CrewAI passes only the
                context, older callers passed the result explicitly.

        Returns:
            - ``None`` to keep the original result.
            - A string to replace the result: the contract violation for
              a call ``on_tool_start`` refused, or sto feedback.
        """
        tool_name = getattr(context, "tool_name", str(context))
        if result is None:
            result = getattr(context, "tool_result", "")
        result_text = "" if result is None else str(result)

        reason = self._pending_blocks.pop(tool_name, None)
        if reason is not None and result_text.startswith(_CREWAI_BLOCKED_PREFIX):
            return reason

        check = self.guard_after(tool_name, result_text)

        if check.needs_retry and check.feedback:
            return format_sto_retry_message(check.feedback, result)

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

        # v0.2 enforcement: proactive. strip denied tools at wrap
        # time. Accepts both plain callables (name from ``__name__``)
        # and pre-built CrewAI Tool objects (``.name``).
        tools = self._proactive_filter_tools(
            list(tools),
            name_fn=lambda t: (
                getattr(t, "name", None)
                or getattr(getattr(t, "func", t), "__name__", "")
            ),
        )

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
                    # Fail closed on redirect too (no substitution path here).
                    if check.stop_original:
                        msg = select_agent_message(
                            check.det_violations, fallback="contract violated"
                        )
                        return f"BLOCKED by contract: {msg}"
                    result = orig(*args, **kwargs)
                    post = guard.guard_after(name, str(result))
                    if post.needs_retry and post.feedback:
                        return format_sto_retry_message(post.feedback, result)
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

        After calling this, ALL crews in the process have contract
        enforcement. The decorators live in ``crewai.hooks``; the older
        ``crewai.tools`` location is tried second for releases that
        exported them there.

        Raises:
            ImportError: If ``crewai`` is not installed.
        """
        try:
            from crewai.hooks import after_tool_call, before_tool_call
        except (ImportError, TypeError):
            try:
                from crewai.tools import after_tool_call, before_tool_call
            except (ImportError, TypeError) as e:
                raise ImportError(
                    "crewai is required. Install with: pip install crewai"
                ) from e

        before_tool_call(self.on_tool_start)
        after_tool_call(self.on_tool_end)

    # Backward-compatible aliases (deprecated)
    def before_hook(self, context: Any) -> bool | None:
        """Deprecated: use ``on_tool_start`` instead."""
        return self.on_tool_start(context)

    def after_hook(self, context: Any, result: Any = None) -> str | None:
        """Deprecated: use ``on_tool_end`` instead."""
        return self.on_tool_end(context, result)
