"""OpenAI SDK integration — auto-enforce contracts on tool_calls.

Generic fallback for users who use the OpenAI SDK directly without
an agent framework like LangGraph or CrewAI.

Usage::

    from sponsio.integrations.openai import patch_openai

    guard = patch_openai(contracts=[
        "tool `check_policy` must precede `issue_refund`",
        "tool `issue_refund` must not be called more than once",
    ])

    # All tool_calls are now auto-monitored
    client = openai.OpenAI()
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[...],
        tools=[...],
    )
    # If a tool_call violates a contract, it is marked in guard.violations

You can also check results programmatically::

    guard.violations       # list of all violations
    guard.last_check       # CheckResult from the most recent response
    guard.summary()        # human-readable summary

To restore the original behavior::

    from sponsio.integrations.openai import unpatch_openai
    unpatch_openai()
"""

from __future__ import annotations

import json
from typing import Any

from sponsio.integrations.base import BaseGuard, CheckResult
from sponsio.models.contract import Contract
from sponsio.models.system import System
from sponsio.runtime.evaluators import StoEvaluator
from sponsio.runtime.strategies import EnforcementStrategy

_original_create: Any = None
_original_async_create: Any = None
_active_guard: OpenAIGuard | None = None


class OpenAIGuard(BaseGuard):
    """Contract guard for OpenAI SDK tool_calls.

    Wraps ``openai.chat.completions.create`` to intercept tool_calls
    in the response and run them through the contract enforcement pipeline.

    Unlike LangGraph integration (which blocks before execution), this
    integration checks tool_calls as they appear in the model response.
    The tool has not been executed yet — the guard validates whether the
    model's *intent* to call a tool violates any contract.

    Attributes:
        last_check: The CheckResult from the most recent response.
        on_violation: Optional callback invoked on each violation.
    """

    def __init__(
        self,
        agent_id: str = "agent",
        contracts: list[dict | Contract | str] | None = None,
        system: System | None = None,
        policy: dict[str, EnforcementStrategy] | None = None,
        sto_evaluator: StoEvaluator | None = None,
        on_violation: Any | None = None,
        store: Any | None = None,
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
        self.on_violation = on_violation

    def check_response(self, response: Any) -> list[CheckResult]:
        """Check all tool_calls in an OpenAI ChatCompletion response.

        Args:
            response: The ChatCompletion response object.

        Returns:
            A list of CheckResult objects, one per tool_call.
        """
        results: list[CheckResult] = []

        for choice in response.choices:
            message = choice.message

            # Observe LLM response content (enables llm_said, token_count)
            content = getattr(message, "content", None)
            usage = getattr(response, "usage", None)
            self.observe_llm_call(
                response=content or "",
                input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
                output_tokens=getattr(usage, "completion_tokens", None)
                if usage
                else None,
            )

            if not hasattr(message, "tool_calls") or not message.tool_calls:
                continue

            for tc in message.tool_calls:
                tool_name = tc.function.name

                # Parse arguments
                try:
                    args = (
                        json.loads(tc.function.arguments)
                        if tc.function.arguments
                        else {}
                    )
                except (json.JSONDecodeError, TypeError):
                    args = {}

                check = self.guard_before(tool_name, args)
                results.append(check)

                if check.blocked and self.on_violation:
                    self.on_violation(tool_name, args, check)

        self.last_check = results[-1] if results else None
        return results

    def _filter_blocked_calls(self, response: Any, results: list[CheckResult]) -> Any:
        """Remove blocked tool_calls from response so they won't be executed.

        For each blocked tool_call, injects an assistant message indicating
        the block, so the agent loop can see why the call was rejected.
        """
        import copy

        response = copy.deepcopy(response)

        # Build set of blocked tool_call indices
        blocked_indices: set[int] = set()
        blocked_messages: list[str] = []
        tc_idx = 0
        for choice in response.choices:
            message = choice.message
            if not hasattr(message, "tool_calls") or not message.tool_calls:
                continue
            kept = []
            for tc in message.tool_calls:
                if tc_idx < len(results) and results[tc_idx].blocked:
                    blocked_indices.add(tc_idx)
                    msg = (
                        results[tc_idx].det_violations[0].message
                        if results[tc_idx].det_violations
                        else "Contract violation"
                    )
                    blocked_messages.append(f"[BLOCKED] {tc.function.name}: {msg}")
                else:
                    kept.append(tc)
                tc_idx += 1
            message.tool_calls = kept if kept else None

        # If all calls were blocked, set content to explain why
        if blocked_messages and response.choices:
            msg = response.choices[0].message
            if not msg.tool_calls:
                msg.content = (msg.content or "") + "\n".join(blocked_messages)

        return response


def patch_openai(
    agent_id: str = "agent",
    contracts: list[str | Contract] | None = None,
    system: System | None = None,
    policy: dict[str, EnforcementStrategy] | None = None,
    sto_evaluator: StoEvaluator | None = None,
    on_violation: Any | None = None,
) -> OpenAIGuard:
    """Monkey-patch the OpenAI SDK to auto-enforce contracts on tool_calls.

    After calling this, every ``client.chat.completions.create()`` call
    will automatically check tool_calls against the provided contracts.

    Args:
        agent_id: Logical agent identifier for trace/monitor.
        contracts: List of NL constraint strings or Contract objects.
        system: Pre-built System (alternative to contracts list).
        policy: Per-constraint enforcement strategy overrides.
        sto_evaluator: Optional StoEvaluator for sto constraints.
        on_violation: Optional callback ``(tool_name, args, check_result) -> None``
            invoked on each violation.

    Returns:
        The OpenAIGuard instance. Use ``guard.violations`` or
        ``guard.last_check`` to inspect results.

    Raises:
        ImportError: If ``openai`` is not installed.
    """
    global _original_create, _original_async_create, _active_guard

    try:
        import openai
    except ImportError:
        raise ImportError("openai is required. Install with: pip install openai")

    guard = OpenAIGuard(
        agent_id=agent_id,
        contracts=contracts,
        system=system,
        policy=policy,
        sto_evaluator=sto_evaluator,
        on_violation=on_violation,
    )
    _active_guard = guard

    # Save originals (only on first patch)
    if _original_create is None:
        _original_create = openai.resources.chat.completions.Completions.create

    if _original_async_create is None:
        _original_async_create = (
            openai.resources.chat.completions.AsyncCompletions.create
        )

    # --- Sync wrapper ---
    def patched_create(self_completions: Any, *args: Any, **kwargs: Any) -> Any:
        response = _original_create(self_completions, *args, **kwargs)
        results = guard.check_response(response)
        if any(r.blocked for r in results):
            return guard._filter_blocked_calls(response, results)
        return response

    # --- Async wrapper ---
    async def patched_async_create(
        self_completions: Any, *args: Any, **kwargs: Any
    ) -> Any:
        response = await _original_async_create(self_completions, *args, **kwargs)
        results = guard.check_response(response)
        if any(r.blocked for r in results):
            return guard._filter_blocked_calls(response, results)
        return response

    openai.resources.chat.completions.Completions.create = patched_create  # type: ignore[assignment]
    openai.resources.chat.completions.AsyncCompletions.create = patched_async_create  # type: ignore[assignment]

    return guard


def unpatch_openai() -> None:
    """Restore the original OpenAI SDK behavior.

    Safe to call even if ``patch_openai()`` was never called.
    """
    global _original_create, _original_async_create, _active_guard

    if _original_create is None:
        return

    try:
        import openai
    except ImportError:
        return

    openai.resources.chat.completions.Completions.create = _original_create  # type: ignore[assignment]
    openai.resources.chat.completions.AsyncCompletions.create = _original_async_create  # type: ignore[assignment]

    _original_create = None
    _original_async_create = None
    _active_guard = None


def get_active_guard() -> OpenAIGuard | None:
    """Return the currently active OpenAIGuard, or None if not patched."""
    return _active_guard
