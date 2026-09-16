"""OpenAI Agents SDK integration. enforce contracts on function tools.

Wraps ``@function_tool`` decorated tools (and plain callables) with
contract enforcement, using the SDK's native tool execution flow.

Usage::

    from sponsio import contract
    from sponsio.agents import Sponsio

    guard = Sponsio(contracts=[
        contract("policy gate before refund")
            .assume("called `issue_refund`")
            .guarantees("must call `check_policy` before `issue_refund`"),
        contract("refund rate limit")
            .guarantees("tool `issue_refund` at most 1 times"),
    ])

    # Wrap tools. contract enforcement is transparent
    agent = Agent(
        name="support_bot",
        tools=guard.wrap([check_policy, issue_refund]),
    )

    result = Runner.run_sync(agent, input="process my refund")

    # Check results
    guard.violations       # all violations
    guard.last_check       # most recent CheckResult
    guard.summary()        # human-readable summary

When a tool call violates a contract the model sees a
``BLOCKED by contract: ...`` tool result instead of the tool's output,
so it can self-correct. A ``FunctionTool`` (the object ``@function_tool``
returns) is wrapped at its ``on_invoke_tool`` entry point, where the SDK
hands over the model's arguments as JSON; a plain callable is wrapped
and then decorated, with the SDK's positional argument passing bound
back to parameter names so argument contracts see the real values.
"""

from __future__ import annotations

import copy
import dataclasses
import functools
import inspect
import json
from typing import Any, Callable

from sponsio.integrations.base import (
    BaseGuard,
    CheckResult,
    format_sto_retry_message,
    select_agent_message,
)
from sponsio.models.system import System
from sponsio.protocols.sto import StoEvaluator
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
        """Wrap a single tool with contract enforcement.

        Accepts the ``FunctionTool`` object ``@function_tool`` returns,
        or a plain callable. The returned tool has the same name,
        description, and schema as the original, but runs guard_before
        before execution and guard_after after.

        Args:
            tool: A ``FunctionTool`` object (from ``@function_tool``) or a
                plain callable.

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

        if _is_function_tool(tool):
            return self._wrap_function_tool(tool)

        guard = self
        tool_name = getattr(tool, "name", getattr(tool, "__name__", str(tool)))
        # SDK kwarg name moved from ``name`` → ``name_override`` mid-2024.
        # Pick whichever the installed SDK accepts so users on either
        # side of the rename keep working.
        _name_kw = _function_tool_name_kw(function_tool)

        # Get the original callable
        original_fn = _extract_function(tool)

        # The SDK parses the model's JSON into the function's parameters
        # and calls the function with them *positionally*
        # (``FuncSchema.to_call_args``), so ``kwargs`` alone would be
        # empty and every argument contract would pass vacuously. Bind
        # whatever arrives back to the parameter names first.
        def _guard_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict:
            return _call_args(original_fn, args, kwargs)

        if inspect.iscoroutinefunction(original_fn):

            @functools.wraps(original_fn)
            async def guarded_async(*args: Any, **kwargs: Any) -> Any:
                check = guard.guard_before(tool_name, _guard_args(args, kwargs))
                guard.last_check = check
                # ``stop_original`` folds in ``redirected``: this adapter
                # does not implement transparent tool substitution, so a
                # ``redirect_to_safe`` redirect fails closed (refuse)
                # rather than running the unsafe call.
                if check.stop_original:
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
                check = guard.guard_before(tool_name, _guard_args(args, kwargs))
                guard.last_check = check
                # ``stop_original`` folds in ``redirected``: this adapter
                # does not implement transparent tool substitution, so a
                # ``redirect_to_safe`` redirect fails closed (refuse)
                # rather than running the unsafe call.
                if check.stop_original:
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

    def _wrap_function_tool(self, tool: Any) -> Any:
        """Wrap a ready-made ``FunctionTool`` at its ``on_invoke_tool``.

        ``@function_tool`` does not keep the user's function on the tool
        object; what it exposes is ``on_invoke_tool(ctx, input_json)``,
        the SDK's own invoker. Re-decorating that (what this adapter did
        before) built a schema from the invoker's signature and failed at
        wrap time. Wrapping the invoker instead keeps the tool's name,
        description and JSON schema exactly as the SDK built them and
        checks the model's arguments before the invoker parses them.

        A stopping verdict returns the block message as the tool result.
        Raising here would escape the SDK's per-tool failure handling
        (which sits inside the original invoker) and abort the whole run.
        """
        guard = self
        tool_name = getattr(tool, "name", "tool")
        original_invoke = tool.on_invoke_tool

        async def guarded_invoke(ctx: Any, input_json: str) -> Any:
            args = _decode_json_args(input_json)
            check = guard.guard_before(tool_name, args)
            guard.last_check = check
            if check.stop_original:
                msg = select_agent_message(
                    check.det_violations, fallback="Contract violation"
                )
                return f"BLOCKED by contract: {msg}"

            result = original_invoke(ctx, input_json)
            if inspect.isawaitable(result):
                result = await result

            post = guard.guard_after(tool_name, str(result))
            if post.needs_retry and post.feedback:
                return format_sto_retry_message(post.feedback, result)
            return result

        if dataclasses.is_dataclass(tool):
            try:
                return dataclasses.replace(tool, on_invoke_tool=guarded_invoke)
            except (TypeError, ValueError):
                pass
        clone = copy.copy(tool)
        clone.on_invoke_tool = guarded_invoke
        return clone

    def wrap(self, tools: list[Any]) -> list[Any]:
        """Wrap tools with contract enforcement for OpenAI Agents SDK.

        Example::

            from sponsio.agents import Sponsio

            guard = Sponsio(config="sponsio.yaml")
            agent = Agent(tools=guard.wrap(tools), instructions=...)

        Args:
            tools: List of ``FunctionTool`` objects or plain callables.

        Returns:
            List of wrapped tools with contract enforcement.
        """
        # v0.2 enforcement: proactive: strip denied tools at wrap
        # time before the Agents SDK ever binds them. Mirrors the
        # name-extraction in ``wrap_tool`` so the same tool object
        # reports the same name in both paths. Empty fallback so a
        # nameless tool never accidentally matches an approved entry
        # (``str(t)`` would render to ``<function foo at 0x...>``,
        # never useful for matching but also never empty, which felt
        # inconsistent across adapters).
        tools = self._proactive_filter_tools(
            list(tools),
            name_fn=lambda t: getattr(t, "name", None) or getattr(t, "__name__", ""),
        )
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


def _is_function_tool(tool: Any) -> bool:
    """True for the SDK's ``FunctionTool`` (or anything shaped like it)."""
    return callable(getattr(tool, "on_invoke_tool", None)) and hasattr(
        tool, "params_json_schema"
    )


def _is_run_context(value: Any) -> bool:
    """True for the SDK's ``RunContextWrapper`` / ``ToolContext`` objects.

    Duck-typed on the class name so no SDK import is needed at check
    time; both classes carry ``context`` and ``usage`` attributes.
    """
    name = type(value).__name__
    if name in ("RunContextWrapper", "ToolContext"):
        return True
    return (
        hasattr(value, "context")
        and hasattr(value, "usage")
        and hasattr(value, "tool_name")
    )


def _call_args(
    fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> dict[str, Any]:
    """Bind a call's positional and keyword arguments to ``fn``'s parameters.

    The SDK's context object (passed first when the tool takes one) is
    dropped: it is runtime plumbing, not something the model chose.
    """
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        merged: dict[str, Any] = dict(bound.arguments)
    except (TypeError, ValueError):
        merged = dict(kwargs)
        if args:
            merged["args"] = list(args)
    return {k: v for k, v in merged.items() if not _is_run_context(v)}


def _decode_json_args(input_json: Any) -> dict[str, Any]:
    """Decode the JSON arguments the SDK hands to ``on_invoke_tool``.

    Malformed JSON keeps the raw text under ``_raw_arguments`` (with a
    ``_sponsio_unparseable`` marker) so coarse ``arg_has`` regexes can
    still match; the same shape ``sponsio.integrations.openai`` uses.
    """
    if input_json is None or input_json == "":
        return {}
    if isinstance(input_json, dict):
        return input_json
    text = (
        input_json.decode()
        if isinstance(input_json, (bytes, bytearray))
        else str(input_json)
    )
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {"_sponsio_unparseable": True, "_raw_arguments": text}
    if not isinstance(parsed, dict):
        return {"_raw_arguments": parsed}
    return parsed


def _extract_function(tool: Any) -> Callable:
    """Extract the underlying callable from a decorated function or tool-like object.

    ``FunctionTool`` objects never reach here (see :func:`_is_function_tool`);
    this handles plain callables and wrappers that keep the function under
    a ``fn`` / ``func`` style attribute.

    Args:
        tool: A decorated function, plain callable, or object holding one.

    Returns:
        The underlying callable.
    """
    for attr in ("fn", "_fn", "func", "_func"):
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
