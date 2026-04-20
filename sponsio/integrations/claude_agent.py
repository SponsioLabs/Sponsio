"""Claude Agent SDK integration — enforce contracts via hooks.

Uses the SDK's native ``PreToolUse`` / ``PostToolUse`` hooks to intercept
every tool call. This is a **true callback-only** integration — no tool
wrapping needed. The agent sees blocked calls as denied permissions with
a system message explaining why.

Usage::

    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
    from sponsio.integrations.claude_agent import ClaudeAgentGuard

    guard = ClaudeAgentGuard(config="sponsio.yaml")

    options = ClaudeAgentOptions(hooks=guard.hooks())

    async with ClaudeSDKClient(options=options) as client:
        await client.query("process my refund")
        async for message in client.receive_response():
            print(message)

Or via ``sponsio.init()``::

    import sponsio

    guard = sponsio.init(
        framework="claude_agent",
        config="sponsio.yaml",
    )
    options = ClaudeAgentOptions(hooks=guard.hooks())
"""

from __future__ import annotations

from typing import Any

from sponsio.integrations.base import BaseGuard, CheckResult
from sponsio.models.contract import Contract
from sponsio.models.system import System
from sponsio.runtime.evaluators import StoEvaluator
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
        contracts: list[dict | Contract | str] | None = None,
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

            if check.blocked:
                msg = (
                    check.det_violations[0].message
                    if check.det_violations
                    else "Contract violation"
                )
                return {
                    "systemMessage": (
                        f"[Sponsio] Tool call `{tool_name}` was blocked: {msg}. "
                        f"Please adjust your approach to comply with the policy."
                    ),
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

    def wrap(self, tools: Any = None) -> dict:
        """Return hooks dict (alias for :meth:`hooks`).

        For Claude Agent SDK, ``wrap()`` returns a hooks dict rather
        than wrapped tools, since the SDK uses native hooks for
        interception — no tool wrapping needed.

        Usage::

            options = ClaudeAgentOptions(hooks=guard.wrap())
        """
        return self.hooks()
