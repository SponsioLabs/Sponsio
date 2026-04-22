"""Cross-language test runner (Python side).

Runs the same canonical scenarios as the TypeScript test, verifying
that both languages produce identical block/allow decisions.
"""

import json
from pathlib import Path

import pytest

SCENARIOS = json.loads((Path(__file__).parent / "scenarios.json").read_text())[
    "scenarios"
]


class TestCrossLanguageScenarios:
    """Each scenario is a sequence of tool calls with expected block/allow."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        import sponsio

        self._sponsio = sponsio

    @pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["name"] for s in SCENARIOS])
    def test_scenario_guard_before(self, scenario):
        """Test guard_before decisions match expected values."""
        guard = self._sponsio.Sponsio(
            agent_id="xtest",
            contracts=scenario["contracts"],
            verbose=False,
        )

        for i, step in enumerate(scenario["steps"]):
            result = guard.guard_before(step["tool"], step.get("args", {}))

            assert result.blocked == step["expect_blocked"], (
                f"Scenario '{scenario['name']}' step {i} "
                f"(tool={step['tool']}): "
                f"expected blocked={step['expect_blocked']}, "
                f"got blocked={result.blocked}. "
                f"Reason: {step['reason']}"
            )

            if not result.blocked:
                guard.guard_after(step["tool"], "ok")


try:
    from claude_agent_sdk import HookMatcher as _HM  # noqa: F401

    _HAS_CLAUDE_SDK = True
except ImportError:
    _HAS_CLAUDE_SDK = False


@pytest.mark.skipif(not _HAS_CLAUDE_SDK, reason="claude-agent-sdk not installed")
class TestCrossLanguageClaudeAgentHooks:
    """Verify Claude Agent SDK hooks produce same results."""

    @pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["name"] for s in SCENARIOS])
    def test_hooks_match_guard(self, scenario):
        """Claude Agent hooks must produce same block/allow as guard_before."""
        import asyncio
        from sponsio.integrations.claude_agent import ClaudeAgentGuard

        guard = ClaudeAgentGuard(
            agent_id="xtest_hooks",
            contracts=scenario["contracts"],
            verbose=False,
        )
        hooks = guard.hooks()
        pre_hook = hooks["PreToolUse"][0].hooks[0]
        post_hook = hooks["PostToolUse"][0].hooks[0]

        loop = asyncio.new_event_loop()

        for i, step in enumerate(scenario["steps"]):
            input_data = {
                "hook_event_name": "PreToolUse",
                "tool_name": step["tool"],
                "tool_input": step.get("args", {}),
                "session_id": "test",
                "cwd": "/tmp",
                "tool_use_id": f"id_{i}",
                "agent_id": "xtest",
                "agent_type": "main",
            }

            result = loop.run_until_complete(pre_hook(input_data, f"id_{i}", None))

            is_denied = (
                result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
            )

            assert is_denied == step["expect_blocked"], (
                f"Scenario '{scenario['name']}' step {i} "
                f"(tool={step['tool']}): "
                f"expected blocked={step['expect_blocked']}, "
                f"got denied={is_denied}. "
                f"Reason: {step['reason']}"
            )

            if not is_denied:
                post_data = {
                    "hook_event_name": "PostToolUse",
                    "tool_name": step["tool"],
                    "tool_result": "ok",
                    "session_id": "test",
                    "cwd": "/tmp",
                    "tool_use_id": f"id_{i}",
                    "agent_id": "xtest",
                    "agent_type": "main",
                }
                loop.run_until_complete(post_hook(post_data, f"id_{i}", None))

        loop.close()


class TestCrossLanguageOpenAI:
    """Verify OpenAI guard produces same results."""

    @pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["name"] for s in SCENARIOS])
    def test_openai_guard(self, scenario):
        """OpenAI guard_before must match expected block/allow."""
        from sponsio.integrations.openai import OpenAIGuard

        guard = OpenAIGuard(
            agent_id="xtest_openai",
            contracts=scenario["contracts"],
            verbose=False,
        )

        for i, step in enumerate(scenario["steps"]):
            result = guard.guard_before(step["tool"], step.get("args", {}))

            assert result.blocked == step["expect_blocked"], (
                f"Scenario '{scenario['name']}' step {i} "
                f"(tool={step['tool']}): "
                f"expected blocked={step['expect_blocked']}, "
                f"got blocked={result.blocked}"
            )

            if not result.blocked:
                guard.guard_after(step["tool"], "ok")
