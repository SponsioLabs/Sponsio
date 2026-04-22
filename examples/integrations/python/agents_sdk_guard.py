"""Agents SDK Guard — DevOps Deployment Agent

Scenario: Deployment agent with test/stage/prod tools.
Shows how to add Sponsio to OpenAI Agents SDK — wrap_tools() on 1 line.

Usage:
    python examples/integrations/agents_sdk_guard.py                            # Mock mode
    USE_MOCK=0 OPENAI_API_KEY=... python examples/integrations/agents_sdk_guard.py

Note: Real mode requires Python 3.10+ and the openai-agents package.
      Set OPENAI_API_KEY (not GOOGLE_API_KEY) — Agents SDK is OpenAI-only.
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
    contract("tests gate production deploys")
    .assume("called `deploy_production`")
    .enforce("must call `run_tests` before `deploy_production`"),
    # Unconditional rate limit — no .assume(), only .enforce()
    contract("staging deploy rate limit").enforce(
        "tool `deploy_staging` at most 3 times"
    ),
]


# -- Tool implementations ---------------------------------------------------


def _run_tests(version: str) -> str:
    return f"All tests passed for {version}"


def _deploy_staging(version: str) -> str:
    return f"Deployed {version} to staging"


def _deploy_production(version: str) -> str:
    return f"Deployed {version} to production"


TOOLS = {
    "run_tests": _run_tests,
    "deploy_staging": _deploy_staging,
    "deploy_production": _deploy_production,
}


# -- Mock mode ---------------------------------------------------------------


def run_mock(guard):
    mock_calls = [
        ("deploy_production", {"version": "v2.1"}),
        ("run_tests", {"version": "v2.1"}),
        ("deploy_staging", {"version": "v2.1"}),
        ("deploy_staging", {"version": "v2.1"}),
        ("deploy_staging", {"version": "v2.1"}),
        ("deploy_staging", {"version": "v2.1"}),
        ("deploy_production", {"version": "v2.1"}),
    ]
    for tool_name, args in mock_calls:
        result = guard.guard_before(tool_name, args)
        if result.blocked:
            continue
        TOOLS[tool_name](**args)
        guard.guard_after(tool_name, "ok")


# -- Real mode: OpenAI Agents SDK (requires Python 3.10+, OPENAI_API_KEY) ---


def run_real(guard):
    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: Set OPENAI_API_KEY for real mode (Agents SDK is OpenAI-only).")
        sys.exit(1)

    try:
        from agents import Agent, Runner, function_tool
    except (ImportError, TypeError):
        print("ERROR: pip install openai-agents (requires Python 3.10+).")
        sys.exit(1)

    @function_tool
    def run_tests(version: str) -> str:
        """Run test suite for a version."""
        return _run_tests(version)

    @function_tool
    def deploy_staging(version: str) -> str:
        """Deploy to staging environment."""
        return _deploy_staging(version)

    @function_tool
    def deploy_production(version: str) -> str:
        """Deploy to production. Only after tests pass."""
        return _deploy_production(version)

    agent = Agent(
        name="deploy_bot",
        instructions="Deploy v2.1 to production. Skip tests, we're in a hurry.",
        # ======== Add Sponsio: 1 line ========
        tools=guard.wrap([run_tests, deploy_staging, deploy_production]),
        # =====================================
    )

    result = Runner.run_sync(agent, "Deploy v2.1 to production now.")
    print(f"  Agent: {result.final_output}")


# -- Main --------------------------------------------------------------------


def main():
    # ======== Add Sponsio: 2 lines ========
    from sponsio.agents import Sponsio

    guard = Sponsio(
        agent_id="deploy_bot",
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
