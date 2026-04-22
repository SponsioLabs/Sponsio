"""Vercel AI SDK Guard — Content Publishing Agent

Scenario: Publishing agent with review/publish/notify tools.
Shows how to add Sponsio to the Vercel AI SDK via middleware — 1 line.

Usage:
    python examples/integrations/vercel_ai_guard.py                               # Mock mode
    USE_MOCK=0 OPENAI_API_KEY=... python examples/integrations/vercel_ai_guard.py  # Real mode

Note: Real mode requires Python 3.12+ and vercel-ai-sdk.
"""

from __future__ import annotations

import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from shared import USE_MOCK  # noqa: E402  (path hack above)
from sponsio import contract  # noqa: E402

CONTRACTS = [
    # Conditional (A, E) pair — assumption triggers the enforcement
    contract("review before publish")
    .assume("called `publish_post`")
    .enforce("must call `review_content` before `publish_post`"),
    # Unconditional rate limit — no .assume(), only .enforce()
    contract("newsletter rate limit").enforce("tool `send_newsletter` at most 1 times"),
]


# -- Tool implementations ---------------------------------------------------


def _review_content(post_id: str) -> str:
    return f"Content {post_id} reviewed — no issues found"


def _publish_post(post_id: str) -> str:
    return f"Post {post_id} published to production"


def _send_newsletter(subject: str) -> str:
    return f"Newsletter '{subject}' sent to all subscribers"


TOOLS = {
    "review_content": _review_content,
    "publish_post": _publish_post,
    "send_newsletter": _send_newsletter,
}


# -- Mock mode ---------------------------------------------------------------


def run_mock(guard):
    mock_calls = [
        ("publish_post", {"post_id": "blog-42"}),  # blocked: no review
        ("review_content", {"post_id": "blog-42"}),  # ok
        ("publish_post", {"post_id": "blog-42"}),  # ok: review done
        ("send_newsletter", {"subject": "New post!"}),  # ok
        ("send_newsletter", {"subject": "Reminder!"}),  # blocked: rate limit
    ]
    for tool_name, args in mock_calls:
        result = guard.guard_before(tool_name, args)
        if result.blocked:
            continue
        TOOLS[tool_name](**args)
        guard.guard_after(tool_name, "ok")


# -- Real mode: Vercel AI SDK -----------------------------------------------


async def run_real(guard):
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: Set OPENAI_API_KEY or GOOGLE_API_KEY for real mode.")
        sys.exit(1)

    try:
        import ai
    except ImportError:
        print("ERROR: pip install vercel-ai-sdk (requires Python 3.12+).")
        sys.exit(1)

    @ai.tool
    async def review_content(post_id: str) -> str:
        """Review content for issues before publishing."""
        return _review_content(post_id)

    @ai.tool
    async def publish_post(post_id: str) -> str:
        """Publish a post to production. Requires prior review."""
        return _publish_post(post_id)

    @ai.tool
    async def send_newsletter(subject: str) -> str:
        """Send newsletter to all subscribers. Use sparingly."""
        return _send_newsletter(subject)

    agent = ai.agent(tools=[review_content, publish_post, send_newsletter])

    # Use Gemini via OpenAI-compatible endpoint if GOOGLE_API_KEY is set
    if os.environ.get("GOOGLE_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        client = ai.Client(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=os.environ["GOOGLE_API_KEY"],
        )
        model = ai.openai("gemini-2.0-flash", client=client)
    else:
        model = ai.openai("gpt-4o")

    messages = [
        ai.system_message(
            "You are a content publishing assistant. "
            "Always execute tool calls immediately without asking. "
            "Never ask for confirmation — just do it."
        ),
        ai.user_message(
            "Do these steps in order: "
            "1. publish blog-42, "
            "2. review blog-42, "
            "3. publish blog-42 again, "
            "4. send a newsletter 'New post!', "
            "5. send another newsletter 'Reminder!'"
        ),
    ]

    # ======== Add Sponsio: 1 line — pass middleware ========
    async for msg in agent.run(model, messages, middleware=[guard.wrap()]):
        if msg.text_delta:
            print(msg.text_delta, end="", flush=True)
    # =======================================================

    print()


# -- Main --------------------------------------------------------------------


def main():
    # ======== Add Sponsio: 2 lines ========
    from sponsio.vercel_ai import Sponsio

    guard = Sponsio(
        agent_id="publish_bot",
        contracts=CONTRACTS,
    )
    # ======================================

    if USE_MOCK:
        run_mock(guard)
    else:
        import asyncio

        asyncio.run(run_real(guard))

    print()
    guard.print_summary()


if __name__ == "__main__":
    main()
