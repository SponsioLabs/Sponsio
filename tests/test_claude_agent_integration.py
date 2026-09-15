"""Tests for Claude Agent SDK integration."""

import asyncio

import pytest

try:
    from claude_agent_sdk import HookMatcher  # noqa: F401

    HAS_CLAUDE_SDK = True
except ImportError:
    HAS_CLAUDE_SDK = False

from sponsio.integrations.claude_agent import ClaudeAgentGuard


@pytest.mark.skipif(not HAS_CLAUDE_SDK, reason="claude-agent-sdk not installed")
class TestClaudeAgentGuard:
    """Tests for ClaudeAgentGuard hook generation."""

    def test_hooks_returns_dict_with_pre_and_post(self):
        guard = ClaudeAgentGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"],
        )
        hooks = guard.hooks()
        assert "PreToolUse" in hooks
        assert "PostToolUse" in hooks
        assert len(hooks["PreToolUse"]) == 1
        assert len(hooks["PostToolUse"]) == 1

    def test_wrap_returns_hooks_dict(self):
        guard = ClaudeAgentGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"],
        )
        result = guard.wrap()
        assert "PreToolUse" in result
        assert "PostToolUse" in result

    def test_pre_tool_hook_blocks_violation(self):
        guard = ClaudeAgentGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"],
            verbose=False,
        )
        hooks = guard.hooks()
        pre_hook = hooks["PreToolUse"][0].hooks[0]

        # Try to call issue_refund without check_policy first
        input_data = {
            "hook_event_name": "PreToolUse",
            "tool_name": "issue_refund",
            "tool_input": {"order_id": "#123"},
            "session_id": "test",
            "cwd": "/tmp",
            "tool_use_id": "id_1",
            "agent_id": "test",
            "agent_type": "test",
        }

        result = asyncio.run(pre_hook(input_data, "id_1", None))

        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
        assert "Sponsio" in result.get("hookSpecificOutput", {}).get(
            "permissionDecisionReason", ""
        )
        assert "systemMessage" in result

    def test_pre_tool_hook_allows_compliant_call(self):
        guard = ClaudeAgentGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"],
            verbose=False,
        )
        hooks = guard.hooks()
        pre_hook = hooks["PreToolUse"][0].hooks[0]

        # Call check_policy first
        input_check = {
            "hook_event_name": "PreToolUse",
            "tool_name": "check_policy",
            "tool_input": {},
            "session_id": "test",
            "cwd": "/tmp",
            "tool_use_id": "id_1",
            "agent_id": "test",
            "agent_type": "test",
        }
        result1 = asyncio.run(pre_hook(input_check, "id_1", None))
        assert result1 == {}

        # Record that tool completed
        guard.guard_after("check_policy", "eligible")

        # Now call issue_refund — should be allowed
        input_refund = {
            "hook_event_name": "PreToolUse",
            "tool_name": "issue_refund",
            "tool_input": {"order_id": "#123"},
            "session_id": "test",
            "cwd": "/tmp",
            "tool_use_id": "id_2",
            "agent_id": "test",
            "agent_type": "test",
        }
        result2 = asyncio.run(pre_hook(input_refund, "id_2", None))
        assert result2 == {}

    def test_pre_tool_hook_rate_limit(self):
        guard = ClaudeAgentGuard(
            contracts=["tool `issue_refund` at most 1 times"],
            verbose=False,
        )
        hooks = guard.hooks()
        pre_hook = hooks["PreToolUse"][0].hooks[0]

        input_data = {
            "hook_event_name": "PreToolUse",
            "tool_name": "issue_refund",
            "tool_input": {},
            "session_id": "test",
            "cwd": "/tmp",
            "tool_use_id": "id_1",
            "agent_id": "test",
            "agent_type": "test",
        }

        # First call — allowed
        r1 = asyncio.run(pre_hook(input_data, "id_1", None))
        assert r1 == {}
        guard.guard_after("issue_refund", "done")

        # Second call — blocked
        r2 = asyncio.run(pre_hook(input_data, "id_2", None))
        assert r2.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def test_last_check_updated(self):
        guard = ClaudeAgentGuard(
            contracts=["tool `issue_refund` at most 1 times"],
            verbose=False,
        )
        assert guard.last_check is None

        hooks = guard.hooks()
        pre_hook = hooks["PreToolUse"][0].hooks[0]

        input_data = {
            "hook_event_name": "PreToolUse",
            "tool_name": "issue_refund",
            "tool_input": {},
            "session_id": "test",
            "cwd": "/tmp",
            "tool_use_id": "id_1",
            "agent_id": "test",
            "agent_type": "test",
        }

        asyncio.run(pre_hook(input_data, "id_1", None))
        assert guard.last_check is not None
        assert guard.last_check.allowed

    def test_init_via_sponsio_init(self):
        import sponsio

        guard = sponsio.Sponsio(
            framework="claude_agent",
            agent_id="test",
            contracts=["tool `A` must precede `B`"],
            verbose=False,
        )
        assert isinstance(guard, ClaudeAgentGuard)
        hooks = guard.hooks()
        assert "PreToolUse" in hooks

    def test_system_message_contains_tool_name(self):
        guard = ClaudeAgentGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"],
            verbose=False,
        )
        hooks = guard.hooks()
        pre_hook = hooks["PreToolUse"][0].hooks[0]

        input_data = {
            "hook_event_name": "PreToolUse",
            "tool_name": "issue_refund",
            "tool_input": {},
            "session_id": "test",
            "cwd": "/tmp",
            "tool_use_id": "id_1",
            "agent_id": "test",
            "agent_type": "test",
        }

        result = asyncio.run(pre_hook(input_data, "id_1", None))

        msg = result.get("systemMessage", "")
        assert "issue_refund" in msg
        assert "blocked" in msg.lower()


# ---------------------------------------------------------------------------
# Hook payload fields (no SDK needed: a stand-in HookMatcher is enough)
# ---------------------------------------------------------------------------


def _fake_sdk(monkeypatch):
    import sys
    import types

    mod = types.ModuleType("claude_agent_sdk")

    class HookMatcher:
        def __init__(self, hooks=None, matcher=None):
            self.hooks = hooks or []
            self.matcher = matcher

    mod.HookMatcher = HookMatcher  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)


def test_post_hook_reads_tool_response(monkeypatch):
    """The SDK's ``PostToolUseHookInput`` carries the output as
    ``tool_response``; reading ``tool_result`` left the after-check with an
    empty string on every call."""
    from sponsio.integrations.base import CheckResult

    _fake_sdk(monkeypatch)
    guard = ClaudeAgentGuard(contracts=["tool `A` must precede `B`"], verbose=False)
    seen: dict = {}

    def fake_guard_after(name, output):
        seen["name"] = name
        seen["output"] = output
        return CheckResult(allowed=True)

    monkeypatch.setattr(guard, "guard_after", fake_guard_after)
    post = guard.hooks()["PostToolUse"][0].hooks[0]
    asyncio.run(
        post(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "A",
                "tool_input": {},
                "tool_response": {"stdout": "hello from A"},
                "tool_use_id": "t1",
            },
            "t1",
            None,
        )
    )
    assert seen["name"] == "A"
    assert "hello from A" in seen["output"]


def test_post_hook_falls_back_to_tool_result(monkeypatch):
    from sponsio.integrations.base import CheckResult

    _fake_sdk(monkeypatch)
    guard = ClaudeAgentGuard(contracts=["tool `A` must precede `B`"], verbose=False)
    seen: dict = {}
    monkeypatch.setattr(
        guard,
        "guard_after",
        lambda name, output: seen.update(output=output) or CheckResult(allowed=True),
    )
    post = guard.hooks()["PostToolUse"][0].hooks[0]
    asyncio.run(post({"tool_name": "A", "tool_result": "legacy"}, "t1", None))
    assert seen["output"] == "legacy"
