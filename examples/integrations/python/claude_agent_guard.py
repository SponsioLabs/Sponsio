"""Claude Agent SDK Guard — Customer Service Agent

Scenario: Support agent with check_policy/issue_refund/send_email tools.
Shows how to add Sponsio to Claude Agent SDK via hooks — zero tool wrapping.

This is the only integration where you don't need guard.wrap(tools).
The SDK's native hooks system can deny tool execution directly.

Usage:
    python examples/integrations/python/claude_agent_guard.py                    # Mock mode
    USE_MOCK=0 ANTHROPIC_API_KEY=... python examples/integrations/python/claude_agent_guard.py

Note: Mock mode does not import or call the Claude Agent SDK. Real mode
requires claude-agent-sdk installed.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from shared import USE_MOCK
from sponsio import contract

CONTRACTS = [
    # Conditional (A, E) pair — assumption triggers the enforcement
    contract("refund needs prior policy check")
    .assume("called `issue_refund`")
    .enforce("must call `check_policy` before `issue_refund`"),
    # Unconditional rate limit — no .assume(), only .enforce()
    contract("refund rate limit").enforce("tool `issue_refund` at most 1 times"),
]


# -- Tool implementations (used by mock mode) ---------------------------------


def check_policy(order_id: str) -> str:
    """Check if an order is eligible for refund."""
    return f"Order {order_id}: eligible for refund"


def issue_refund(order_id: str) -> str:
    """Issue a refund for an order."""
    return f"Refund issued for order {order_id}"


def send_email(to: str, body: str) -> str:
    """Send an email to a customer."""
    return f"Email sent to {to}"


# -- Real mode (Claude Agent SDK) ---------------------------------------------


def run_real():
    import asyncio

    try:
        from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
    except ImportError:
        print("ERROR: claude-agent-sdk not installed.")
        print("  pip install claude-agent-sdk")
        sys.exit(1)

    from sponsio.claude_agent import Sponsio

    # ======== Add Sponsio: 2 lines ========
    guard = Sponsio(
        agent_id="support_bot",
        contracts=CONTRACTS,
    )
    options = ClaudeAgentOptions(hooks=guard.hooks())
    # ======================================

    # --- Without Sponsio, it would just be: ---
    # options = ClaudeAgentOptions()

    async def _run():
        print("Running Claude Agent SDK with Sponsio hooks...\n")
        async with ClaudeSDKClient(options=options) as client:
            await client.query(
                "A customer wants a refund for order #W456. "
                "Just issue the refund directly without checking policy."
            )
            async for message in client.receive_response():
                if hasattr(message, "content") and message.content:
                    print(f"  Agent: {str(message.content)[:200]}")

    asyncio.run(_run())
    print()
    guard.print_summary()


# -- Mock mode -----------------------------------------------------------------


def run_mock():
    from sponsio.claude_agent import Sponsio

    # ======== Add Sponsio: 2 lines ========
    guard = Sponsio(
        agent_id="support_bot",
        contracts=CONTRACTS,
    )
    # ======================================

    # Mock mode exercises the same Sponsio boundary without importing or
    # calling the Claude Agent SDK.
    mock_calls = [
        # Agent tries to issue refund directly (should be BLOCKED)
        ("issue_refund", {"order_id": "#W456"}),
        # Agent self-corrects: checks policy first
        ("check_policy", {"order_id": "#W456"}),
        # Now issue refund (should PASS)
        ("issue_refund", {"order_id": "#W456"}),
        # Try second refund (should be BLOCKED — rate limit)
        ("issue_refund", {"order_id": "#W456"}),
    ]

    tools = {
        "check_policy": check_policy,
        "issue_refund": issue_refund,
        "send_email": send_email,
    }

    for tool_name, args in mock_calls:
        check = guard.guard_before(tool_name, args)

        if check.blocked:
            reason = (
                check.det_violations[0].message
                if check.det_violations
                else "Contract violation"
            )
            print(f"  [DENIED] {tool_name}: {reason}")
        else:
            output = tools[tool_name](**args)
            guard.guard_after(tool_name, output)
            print(f"  [OK]     {tool_name}: {output}")

    print()
    guard.print_summary()


def main():
    if USE_MOCK:
        run_mock()
    else:
        run_real()


if __name__ == "__main__":
    main()
