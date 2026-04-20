"""CrewAI Guard — Content Moderation Pipeline

Scenario: Moderation agent with flag/delete/notify tools.
Shows how to add Sponsio to CrewAI — pass 2 hook args to Crew().

Usage:
    python examples/integrations/crewai_guard.py                            # Mock mode
    USE_MOCK=0 GOOGLE_API_KEY=... python examples/integrations/crewai_guard.py   # Real Gemini
"""

from __future__ import annotations

import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from shared import USE_MOCK

CONTRACTS = [
    "tools `flag_content` and `delete_content` must never be called together",
    "tool `delete_content` requires permission `admin_permission`",
]


# -- Tool implementations ---------------------------------------------------

def flag_content(post_id: str) -> str:
    return f"Post {post_id} flagged for review"


def delete_content(post_id: str) -> str:
    return f"Post {post_id} deleted"


def notify_user(user_id: str, msg: str) -> str:
    return f"Notified {user_id}: {msg}"


TOOLS = {
    "flag_content": flag_content,
    "delete_content": delete_content,
    "notify_user": notify_user,
}


# -- Mock mode ---------------------------------------------------------------

def run_mock(guard):
    mock_calls = [
        ("flag_content", {"post_id": "post-123"}),
        ("delete_content", {"post_id": "post-123"}),
        ("notify_user", {"user_id": "u-456", "msg": "flagged"}),
    ]
    for tool_name, args in mock_calls:
        result = guard.guard_before(tool_name, args)
        if result.blocked:
            continue
        TOOLS[tool_name](**args)
        guard.guard_after(tool_name, "ok")


# -- Real mode: CrewAI with Gemini via LangChain ----------------------------

def run_real(guard):
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: Set GOOGLE_API_KEY for real mode.")
        sys.exit(1)

    try:
        from crewai import Agent, Crew, Task
        from crewai.tools import tool as crewai_tool
    except (ImportError, TypeError):
        print("ERROR: pip install crewai for real mode (requires Python 3.10+).")
        sys.exit(1)

    # CrewAI tools
    @crewai_tool
    def flag_content(post_id: str) -> str:
        """Flag a post for review by moderators."""
        return f"Post {post_id} flagged for review"

    @crewai_tool
    def delete_content(post_id: str) -> str:
        """Delete a post permanently. Requires admin permission."""
        return f"Post {post_id} deleted"

    @crewai_tool
    def notify_user(user_id: str, msg: str) -> str:
        """Notify a user about a moderation action."""
        return f"Notified {user_id}: {msg}"

    agent = Agent(
        role="Content Moderator",
        goal="Flag inappropriate posts and notify users",
        backstory="You are a content moderator. Flag spam posts and notify the author.",
        tools=[flag_content, delete_content, notify_user],
        llm="gemini/gemini-2.0-flash",
    )

    task = Task(
        description="Post post-123 is spam. Flag it, delete it, and notify user u-456.",
        expected_output="Summary of moderation actions taken",
        agent=agent,
    )

    crew = Crew(
        agents=[agent],
        tasks=[task],
        verbose=False,
        # ======== Add Sponsio: 2 args ========
        before_tool_call=guard.on_tool_start,
        after_tool_call=guard.on_tool_end,
        # =====================================
    )

    result = crew.kickoff()
    print(f"  Agent: {str(result)[:200]}")


# -- Main --------------------------------------------------------------------

def main():
    # ======== Add Sponsio: 2 lines ========
    import sponsio

    guard = sponsio.init(
        framework="crewai",
        agent_id="moderator",
        contracts=CONTRACTS,
    )
    # ======================================

    if USE_MOCK:
        run_mock(guard)
    else:
        run_real(guard)

    print()
    guard.print_summary()


if __name__ == "__main__":
    main()
