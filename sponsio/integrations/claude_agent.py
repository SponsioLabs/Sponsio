"""Claude Agent SDK integration — enforce contracts via hooks.

Uses the SDK's native ``PreToolUse`` / ``PostToolUse`` hooks to intercept
every tool call. This is a **true callback-only** integration — no tool
wrapping needed. The agent sees blocked calls as denied permissions with
a system message explaining why.

Usage::

    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
    from sponsio.claude_agent import Sponsio

    guard = Sponsio(config="sponsio.yaml")

    options = ClaudeAgentOptions(hooks=guard.hooks())

    async with ClaudeSDKClient(options=options) as client:
        await client.query("process my refund")
        async for message in client.receive_response():
            print(message)

Or explicitly::

    from sponsio.claude_agent import Sponsio

    guard = Sponsio(config="sponsio.yaml")
    options = ClaudeAgentOptions(hooks=guard.hooks())
"""

from __future__ import annotations

from typing import Any

from sponsio.integrations.base import BaseGuard, CheckResult, select_agent_message
from sponsio.models.system import System
from sponsio.protocols.sto import StoEvaluator
from sponsio.runtime.strategies import EnforcementStrategy


class ClaudeAgentGuard(BaseGuard):
    """Contract guard for the Claude Agent SDK.

    Provides hook callbacks that integrate with the SDK's ``PreToolUse``
    and ``PostToolUse`` events. Unlike other integrations that require
    wrapping tools, this uses the SDK's native permission system to
    block violations — ``permissionDecision: "deny"`` prevents execution
    without any tool modification.

    Example::

        guard = ClaudeAgentGuard(config="sponsio.yaml")
        options = ClaudeAgentOptions(hooks=guard.hooks())

        async with ClaudeSDKClient(options=options) as client:
            await client.query("do something")
            async for msg in client.receive_response():
                print(msg)
    """

    def __init__(
        self,
        agent_id: str = "agent",
        contracts: list[Any] | None = None,
        config: str | None = None,
        system: System | None = None,
        policy: dict[str, EnforcementStrategy] | None = None,
        sto_evaluator: StoEvaluator | None = None,
        store: Any = None,
        **kwargs: Any,
    ):
        super().__init__(
            agent_id=agent_id,
            contracts=contracts,
            config=config,
            system=system,
            policy=policy,
            sto_evaluator=sto_evaluator,
            store=store,
            **kwargs,
        )
        self.last_check: CheckResult | None = None

    def hooks(self) -> dict:
        """Return a hooks dict for ``ClaudeAgentOptions(hooks=...)``.

        Returns a dict with ``PreToolUse`` and ``PostToolUse`` entries,
        each containing a ``HookMatcher`` that fires on all tools.

        Usage::

            options = ClaudeAgentOptions(hooks=guard.hooks())

        Returns:
            Dict compatible with ``ClaudeAgentOptions.hooks``.
        """
        try:
            from claude_agent_sdk import HookMatcher
        except ImportError:
            raise ImportError(
                "claude-agent-sdk is required. "
                "Install with: pip install claude-agent-sdk"
            )

        guard = self

        async def pre_tool_hook(
            input_data: Any, tool_use_id: Any, context: Any
        ) -> dict:
            tool_name = input_data.get("tool_name", "")
            tool_input = input_data.get("tool_input", {})

            check = guard.guard_before(tool_name, tool_input)
            guard.last_check = check

            # ``stop_original`` folds in ``redirected``: this hook denies
            # via the SDK permission system and has no substitution path,
            # so a redirect fails closed (denied) rather than running the
            # unsafe call.
            if check.stop_original:
                # Prefer the structured ``agent_msg`` from OutcomeBuilder
                # — it's already phrased to steer the model toward
                # abandoning this action. Falls back to the legacy
                # ``message`` for hand-constructed EnforcementResults.
                msg = select_agent_message(
                    check.det_violations, fallback="Contract violation"
                )
                return {
                    "systemMessage": f"[Sponsio] Tool call `{tool_name}` was blocked: {msg}",
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": f"Sponsio contract violation: {msg}",
                    },
                }

            return {}

        async def post_tool_hook(
            input_data: Any, tool_use_id: Any, context: Any
        ) -> dict:
            tool_name = input_data.get("tool_name", "")
            tool_output = input_data.get("tool_result", "")

            post = guard.guard_after(tool_name, str(tool_output))

            if post.needs_retry and post.feedback:
                # NOTE (#12): other adapters surface sto feedback via the
                # tool-result channel using
                # :func:`format_sto_retry_message`. Claude Agent's
                # ``additionalContext`` is a sidecar hint — the real tool
                # output is kept intact — so the "Tool succeeded but
                # output quality check failed. Original output: ..."
                # template used elsewhere would misdescribe the channel
                # (the output isn't being replaced here). The bracketed
                # prefix tags this as Sponsio-originated and is
                # retained. This is the one *intentional* deviation from
                # the shared helper; see base.format_sto_retry_message.
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": (
                            f"[Sponsio quality check] {post.feedback}"
                        ),
                    },
                }

            return {}

        return {
            "PreToolUse": [HookMatcher(hooks=[pre_tool_hook])],
            "PostToolUse": [HookMatcher(hooks=[post_tool_hook])],
        }

    # -----------------------------------------------------------------
    # LLM response observation — needed for sto atoms like injection_free
    # whose context_scope="event" / "full_trace" expect llm_response
    # events in the trace. PreToolUse / PostToolUse hooks only cover
    # tool calls, not model-produced messages. Users must stream
    # assistant messages into the guard via observe_message().
    # -----------------------------------------------------------------

    def observe_message(self, message: Any) -> None:
        """Feed an assistant message from ``client.receive_response()`` into
        the guard's trace so sto atoms can evaluate LLM output.

        The Claude Agent SDK delivers model responses through a message
        stream, not through hooks. Call this on each ``AssistantMessage``
        you receive so sto atoms like ``injection_free`` /
        ``scope_respect`` / ``toxic_free`` actually fire:

        .. code-block:: python

            from claude_agent_sdk import AssistantMessage, ClaudeSDKClient, query

            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    guard.observe_message(msg)
                # user keeps processing msg as normal...

        Non-assistant messages and messages without text content are
        silently ignored — safe to call on every message from the stream.

        Args:
            message: An SDK message object; expected to be an
                ``AssistantMessage`` with ``.content`` (list of text
                blocks). Strings are also accepted as a shortcut for
                ad-hoc content.
        """
        # Extract text from the message. The SDK's AssistantMessage
        # carries .content as a list of TextBlock / ToolUseBlock /
        # ThinkingBlock; we take text blocks only.
        text = ""
        if isinstance(message, str):
            text = message
        else:
            content = getattr(message, "content", None)
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = []
                for block in content:
                    # Handle TextBlock-like (text attribute) or dicts
                    block_text = getattr(block, "text", None)
                    if block_text is None and isinstance(block, dict):
                        block_text = block.get("text")
                    if block_text:
                        parts.append(str(block_text))
                text = "\n\n".join(parts)
        if not text:
            return
        try:
            self.observe_llm_call(response=text)
        except Exception:
            # Swallow — sto failures shouldn't crash the agent loop.
            # Violations are already in the monitor's event log.
            pass

    def observe_stream(self, stream):
        """Wrap an assistant-message stream to transparently observe
        each message.

        Usage::

            async for msg in guard.observe_stream(client.receive_response()):
                # msg is yielded unchanged; guard has already observed it
                ...

        Works with both sync and async iterables.
        """
        import inspect

        if inspect.isasyncgen(stream) or hasattr(stream, "__anext__"):

            async def _agen():
                async for msg in stream:
                    self.observe_message(msg)
                    yield msg

            return _agen()

        def _gen():
            for msg in stream:
                self.observe_message(msg)
                yield msg

        return _gen()

    def wrap(self, tools: Any = None) -> dict:
        """Return hooks dict (alias for :meth:`hooks`).

        For Claude Agent SDK, ``wrap()`` returns a hooks dict rather
        than wrapped tools, since the SDK uses native hooks for
        interception — no tool wrapping needed.

        Usage::

            options = ClaudeAgentOptions(hooks=guard.wrap())
        """
        return self.hooks()
