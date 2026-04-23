"""OpenAI SDK integration — auto-enforce contracts on tool_calls.

Generic fallback for users who use the OpenAI SDK directly without
an agent framework like LangGraph or CrewAI.

Usage::

    from sponsio import contract
    from sponsio.openai import patch_openai

    guard = patch_openai(contracts=[
        contract("policy gate before refund")
            .assume("called `issue_refund`")
            .enforce("must call `check_policy` before `issue_refund`"),
        contract("refund rate limit")
            .enforce("tool `issue_refund` at most 1 times"),
    ])

    # All tool_calls are now auto-monitored
    client = openai.OpenAI()
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[...],
        tools=[...],
    )
    # If a tool_call violates a contract, it is marked in guard.violations

Two enforcement hooks run automatically:

* **Before** the tool executes — ``check_response`` inspects every
  ``tool_call`` in the model's output and enforces det contracts
  (``must_precede``, ``rate_limit``, ``mutual_exclusion``, …).
* **After** the tool executes — when the user feeds the tool result
  back in as a ``{"role": "tool", "tool_call_id": ..., "content": ...}``
  message on the next ``chat.completions.create``, the patch scans
  those messages and runs ``guard_after`` on each.  This powers sto
  constraints (``tone_professional``, ``injection_free``) and the
  ``BaseGuard`` auto-tag layer (``contains(tool_name)``, optional
  PII detection) without the user touching anything.

If you're not going through ``patch_openai`` — e.g. you use the raw
response plus your own executor — call ``guard.observe_tool_result``
explicitly after each tool execution.

You can also check results programmatically::

    guard.violations       # list of all violations
    guard.last_check       # CheckResult from the most recent response
    guard.summary()        # human-readable summary

To restore the original behavior::

    from sponsio.openai import unpatch_openai
    unpatch_openai()
"""

from __future__ import annotations

import json
import os
import warnings
from typing import Any

from sponsio.integrations.base import BaseGuard, CheckResult
from sponsio.models.system import System
from sponsio.runtime.evaluators import StoEvaluator
from sponsio.runtime.strategies import EnforcementStrategy

_original_create: Any = None
_original_async_create: Any = None
_active_guard: OpenAIGuard | None = None


def _coerce_tool_arguments(
    raw: Any,
    *,
    tool_name: str,
) -> dict[str, Any]:
    """Best-effort decode of an OpenAI ``tool_call.function.arguments`` payload.

    OpenAI returns ``arguments`` as a JSON-encoded *string*. When a model
    hallucinates malformed JSON (truncation, prompt-injection nudging it to
    inject control characters, function-calling format slips, …) the
    previous behavior was a silent ``args = {}`` — every content-aware
    contract (``arg_blacklist``, ``arg_field_has``, ``arg_value_range``,
    ``arg_length_exceeds``, …) then *vacuously* passes because the field
    it inspects doesn't exist. That is precisely the wrong failure mode
    for a security boundary.

    This helper:

    * Returns the parsed dict when ``raw`` is valid JSON.
    * Returns ``{}`` for empty / ``None`` arguments (the legitimate
      "no args" case).
    * Otherwise emits a ``UserWarning`` and falls back to a sentinel
      payload that **preserves the raw bytes** so coarser contracts
      (``arg_has`` regex over the serialized payload) can still match::

        {
            "_sponsio_unparseable": True,
            "_raw_arguments": <original_string>,
        }

    Operators who want a malformed-args attempt to *block* (rather than
    just warn + degrade) can set ``SPONSIO_OPENAI_STRICT_TOOL_ARGS=1``
    in the environment; the helper then raises ``ValueError`` from the
    caller's ``check_response`` site.
    """
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, (str, bytes, bytearray)):
        text = str(raw)
    else:
        text = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        if os.environ.get("SPONSIO_OPENAI_STRICT_TOOL_ARGS") == "1":
            raise ValueError(
                f"OpenAIGuard: tool_call arguments for {tool_name!r} are "
                f"not valid JSON ({exc}). Strict mode is on "
                "(SPONSIO_OPENAI_STRICT_TOOL_ARGS=1)."
            ) from exc
        warnings.warn(
            f"OpenAIGuard: tool_call {tool_name!r} arguments are not valid "
            f"JSON ({exc.msg if isinstance(exc, json.JSONDecodeError) else exc}); "
            "preserving raw payload under '_raw_arguments' so coarse "
            "regex contracts can still match. Field-level guards "
            "(arg_blacklist, arg_value_range) WILL miss because the "
            "field structure was lost.",
            UserWarning,
            stacklevel=3,
        )
        return {"_sponsio_unparseable": True, "_raw_arguments": text}

    if not isinstance(parsed, dict):
        # Decoded but not an object (e.g. ``"42"`` or ``[1, 2]``). Wrap
        # so callers continue to see a dict; field-level guards still
        # miss but ``arg_has`` over the serialized form keeps working.
        return {"_raw_arguments": parsed}
    return parsed


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
        contracts: list[Any] | None = None,
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
        # Map of ``tool_call_id`` → ``tool_name`` captured from the
        # assistant response, used so that
        # :meth:`observe_tool_result` can resolve the original tool
        # name when the user only has the OpenAI-style
        # ``{"role": "tool", "tool_call_id": ..., "content": ...}``
        # message to hand back.  Populated by :meth:`check_response`,
        # drained by :meth:`observe_tool_result`.
        self._pending_tool_calls: dict[str, str] = {}
        # Deduplication set for the auto-scan path — the user's message
        # history grows across turns and typically contains the same
        # tool messages repeatedly; without this we'd run
        # ``guard_after`` once per turn per historical result.
        self._observed_tool_call_ids: set[str] = set()

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
                args = _coerce_tool_arguments(
                    tc.function.arguments,
                    tool_name=tool_name,
                )

                check = self.guard_before(tool_name, args)
                results.append(check)

                # Remember (tool_call_id → tool_name) so the user can
                # hand back just the tool-role message later and
                # :meth:`observe_tool_result` / the auto-scan can
                # still resolve the original tool name.
                tool_call_id = getattr(tc, "id", None)
                if tool_call_id:
                    self._pending_tool_calls[tool_call_id] = tool_name

                if check.blocked and self.on_violation:
                    self.on_violation(tool_name, args, check)

        self.last_check = results[-1] if results else None
        return results

    def observe_tool_result(
        self,
        tool_call_id: str | None,
        output: Any,
        tool_name: str | None = None,
    ) -> CheckResult:
        """Record a tool result and run ``guard_after``.

        Call this after executing a tool_call returned by
        ``chat.completions.create`` so the trace has the output
        visible to sto constraints and the BaseGuard auto-tag layer
        (``contains(tool_name)``, optional PII tags).  Without this,
        ``no_data_leak`` and similar contracts can never fire on
        OpenAI-hosted agents — the OpenAI guard only sees the model's
        *intent* to call a tool, not the tool's actual output.

        Two usage patterns:

        1. **Explicit** — you already know the tool name::

             guard.observe_tool_result("call_abc", result, tool_name="lookup_customer")

        2. **Implicit** — use the id captured from the last response::

             for tc in response.choices[0].message.tool_calls:
                 result = run_tool(tc)
                 guard.observe_tool_result(tc.id, result)

        Calling this is idempotent for a given ``tool_call_id`` —
        subsequent calls with the same id are no-ops so the auto-scan
        path in the SDK patch can safely re-enter.
        """
        if tool_call_id is not None and tool_call_id in self._observed_tool_call_ids:
            return CheckResult(allowed=True)

        if tool_name is None and tool_call_id is not None:
            tool_name = self._pending_tool_calls.get(tool_call_id)
        if not tool_name:
            # Falling back to ``guard_after("", ...)`` would silently
            # skip the auto-tag (empty name is rejected there).  Return
            # a no-op CheckResult and tell the user what happened.
            return CheckResult(
                allowed=True,
                feedback=(
                    "sponsio: no tool_name resolvable for tool_call_id="
                    f"{tool_call_id!r} — skipping guard_after"
                ),
            )

        if tool_call_id is not None:
            self._observed_tool_call_ids.add(tool_call_id)
            self._pending_tool_calls.pop(tool_call_id, None)

        return self.guard_after(tool_name, output)

    def _auto_observe_tool_messages(self, messages: Any) -> None:
        """Scan an outbound ``messages`` list and observe any tool
        results we haven't already seen.

        This runs on every ``chat.completions.create`` call *before*
        the request is forwarded to OpenAI, so the auto-tag + sto
        pipeline sees tool outputs from the previous turn even when
        the user never touched ``observe_tool_result`` explicitly —
        the whole point of matching LangGraph / CrewAI ergonomics.

        Extracts content from both the string shape and the OpenAI
        list-of-parts shape (``[{"type": "text", "text": "..."}]``).
        """
        if not isinstance(messages, list):
            return
        for msg in messages:
            # Tolerate both dicts and pydantic message objects.
            if isinstance(msg, dict):
                role = msg.get("role")
                tcid = msg.get("tool_call_id")
                content = msg.get("content")
            else:
                role = getattr(msg, "role", None)
                tcid = getattr(msg, "tool_call_id", None)
                content = getattr(msg, "content", None)

            if role != "tool" or not tcid:
                continue
            if tcid in self._observed_tool_call_ids:
                continue

            # Normalise content: OpenAI allows string OR list-of-parts.
            if isinstance(content, list):
                parts: list[str] = []
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "text":
                        parts.append(str(p.get("text", "")))
                    elif isinstance(p, str):
                        parts.append(p)
                content_str = "".join(parts)
            elif content is None:
                content_str = ""
            else:
                content_str = str(content)

            try:
                self.observe_tool_result(tcid, content_str)
            except Exception:
                # Matching the defensive posture of
                # ``BaseGuard._autotag_tool_output`` — never let a
                # bookkeeping failure break the model call.
                pass

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
    contracts: list[Any] | None = None,
    system: System | None = None,
    policy: dict[str, EnforcementStrategy] | None = None,
    sto_evaluator: StoEvaluator | None = None,
    sto_judge: Any | None = None,
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
        sto_judge: Optional judge used by sto atom evaluators.
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
        sto_judge=sto_judge,
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
        # Pre-flight: scan the outbound ``messages`` for tool results
        # returning from the previous turn and run them through
        # ``guard_after`` so the trace reflects real execution.  This
        # is the OpenAI equivalent of LangGraph's post-execution hook.
        guard._auto_observe_tool_messages(kwargs.get("messages"))
        response = _original_create(self_completions, *args, **kwargs)
        results = guard.check_response(response)
        if any(r.blocked for r in results):
            return guard._filter_blocked_calls(response, results)
        return response

    # --- Async wrapper ---
    async def patched_async_create(
        self_completions: Any, *args: Any, **kwargs: Any
    ) -> Any:
        guard._auto_observe_tool_messages(kwargs.get("messages"))
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
