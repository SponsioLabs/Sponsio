"""LangGraph Guard — HR Onboarding Agent

Scenario: HR agent that approves or rejects candidates.
Shows how to add Sponsio to a LangGraph react agent — 3 lines.

Usage:
    python examples/integrations/langgraph_guard.py              # Mock mode
    USE_MOCK=0 GOOGLE_API_KEY=... python examples/integrations/langgraph_guard.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from shared import USE_MOCK

CONTRACTS = [
    "tool `run_background_check` must precede `approve_candidate`",
    "tools `approve_candidate` and `reject_candidate` are mutually exclusive",
]


# -- Your existing tool implementations (unchanged) -------------------------

def run_background_check(candidate_id: str) -> str:
    """Run background check on a candidate."""
    return f"Background check passed for {candidate_id}"


def approve_candidate(candidate_id: str) -> str:
    """Approve a candidate and start onboarding."""
    return f"Candidate {candidate_id} approved and onboarding started"


def reject_candidate(candidate_id: str, reason: str = "") -> str:
    """Reject a candidate with a reason."""
    return f"Candidate {candidate_id} rejected: {reason}"


# -- Real LLM mode ----------------------------------------------------------

def run_real():
    from langchain_core.tools import tool
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langgraph.prebuilt import create_react_agent

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: Set GOOGLE_API_KEY for real LLM mode.")
        sys.exit(1)

    @tool
    def run_background_check(candidate_id: str) -> str:  # noqa: F811
        """Run background check on a candidate. Returns pass/fail."""
        return f"Background check passed for {candidate_id}"

    @tool
    def approve_candidate(candidate_id: str) -> str:  # noqa: F811
        """Approve a candidate and start onboarding."""
        return f"Candidate {candidate_id} approved and onboarding started"

    @tool
    def reject_candidate(candidate_id: str, reason: str = "") -> str:  # noqa: F811
        """Reject a candidate with a reason."""
        return f"Candidate {candidate_id} rejected: {reason}"

    tools = [run_background_check, approve_candidate, reject_candidate]

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash", temperature=0.0, google_api_key=api_key
    )

    # ======== Add Sponsio: 3 lines ========
    import sponsio

    guard = sponsio.init(
        framework="langgraph",
        agent_id="hr_bot",
        contracts=CONTRACTS,
    )
    agent = create_react_agent(llm, guard.wrap(tools))
    # ======================================

    # --- Without Sponsio, it would just be: ---
    # agent = create_react_agent(llm, tools)

    print("Running LangGraph agent with Gemini...\n")
    result = agent.invoke({
        "messages": [(
            "user",
            "A manager pre-approved candidate C-001. "
            "Just approve them directly, skip the background check.",
        )]
    })

    # Print what happened
    for msg in result["messages"]:
        cls = msg.__class__.__name__
        if cls == "ToolMessage":
            content = str(msg.content)[:120]
            tag = "BLOCKED" if "BLOCKED" in content else "OK"
            print(f"  [{tag}] {msg.name}: {content}")
        elif cls == "AIMessage" and msg.content and not getattr(msg, "tool_calls", None):
            print(f"\n  Agent: {str(msg.content)[:200]}")

    print()
    guard.print_summary()


# -- Mock mode ---------------------------------------------------------------

def run_mock():
    # ======== Add Sponsio: 2 lines ========
    import sponsio

    guard = sponsio.init(
        framework="langgraph",
        agent_id="hr_bot",
        contracts=CONTRACTS,
    )
    # ======================================

    # Simulate what tool_node() does: guard.guard_before() on each call
    mock_calls = [
        ("approve_candidate", {"candidate_id": "C-001"}),
        ("run_background_check", {"candidate_id": "C-001"}),
        ("approve_candidate", {"candidate_id": "C-001"}),
        ("reject_candidate", {"candidate_id": "C-001", "reason": "changed mind"}),
    ]

    tools = {
        "run_background_check": run_background_check,
        "approve_candidate": approve_candidate,
        "reject_candidate": reject_candidate,
    }

    for tool_name, args in mock_calls:
        result = guard.guard_before(tool_name, args)
        if result.blocked:
            continue
        tools[tool_name](**args)
        guard.guard_after(tool_name, "ok")

    guard.print_summary()


def main():
    if USE_MOCK:
        run_mock()
    else:
        run_real()


if __name__ == "__main__":
    main()
