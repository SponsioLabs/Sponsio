"""Canonical stopping set + stop_original gating regressions.

Phase 1 of fix/enforcement-and-evidence-mw. Two properties are pinned:

1. There is exactly ONE definition of "this verdict stops the original
   call" (``STOPPING_ACTIONS`` next to ``CheckResult``), and every
   consumer — ``CheckResult.stop_original``, the MCP proxy, the bridge
   span projection — agrees with it for every verdict value.
2. A ``redirected`` verdict now stops the original call in every adapter
   that has no substitution path (openai, google_adk sync, langgraph
   callback/node checks, guard_stdin). Before this fix those sites gated
   on ``.blocked``, which is False on a redirect — fail open.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from sponsio.integrations.base import (
    STOPPING_ACTIONS,
    CheckResult,
    is_stopping_action,
)
from sponsio.runtime.strategies import EnforcementResult

ALL_ACTIONS = (
    "blocked",
    "escalated",
    "redirected",
    "warned",
    "observed",
    "retrying",
    "allowed",
)


def _violation(action: str) -> EnforcementResult:
    return EnforcementResult(
        action=action,
        message=f"det verdict: {action}",
        fallback_action="safe_tool" if action == "redirected" else None,
    )


def _check_result(action: str) -> CheckResult:
    return CheckResult(
        allowed=action != "blocked",
        det_violations=[_violation(action)],
        redirected_to="safe_tool" if action == "redirected" else None,
    )


# ---------------------------------------------------------------------------
# one canonical definition
# ---------------------------------------------------------------------------


class TestCanonicalStoppingSet:
    def test_membership(self):
        # ``escalated`` is deliberately absent: the monitor's default
        # EscalateToHuman verdict for unfired assumptions is vacuous, and
        # stopping on it would refuse every action while a conditional
        # contract's assumption is simply not yet satisfied.
        assert STOPPING_ACTIONS == frozenset({"blocked", "redirected"})

    def test_stop_original_agrees_for_every_verdict_value(self):
        for action in ALL_ACTIONS:
            assert _check_result(action).stop_original == is_stopping_action(action), (
                action
            )

    def test_bridge_consumes_the_canonical_set(self):
        from sponsio.bridge.spans import STOPPING_ACTIONS as bridge_set

        assert bridge_set is STOPPING_ACTIONS

    def test_mcp_agrees_for_every_verdict_value(self):
        from sponsio.integrations.mcp import MCPContractProxy
        from sponsio.models.system import System

        for action in ALL_ACTIONS:
            client = MagicMock()

            async def fake_call_tool(tool_name, arguments=None):
                return {"ok": True}

            client.call_tool = fake_call_tool
            proxy = MCPContractProxy(
                client, System(name="t", contracts=[]), tag_outputs=False
            )
            proxy._monitor = MagicMock()
            proxy._monitor.check_action.return_value = [_violation(action)]
            result = asyncio.run(proxy.call_tool("send_email", {"to": "x"}))
            stopped = isinstance(result, dict) and result.get("error") == (
                "Blocked by behavioral contract"
            )
            assert stopped == is_stopping_action(action), action


# ---------------------------------------------------------------------------
# per-adapter regressions: redirected must stop the original call
# ---------------------------------------------------------------------------


def _redirected_result() -> CheckResult:
    return _check_result("redirected")


class TestRedirectStopsOriginal:
    def test_openai_redirect_strips_tool_call_and_fires_callback(self, monkeypatch):
        from sponsio.integrations.openai import OpenAIGuard

        seen = []
        guard = OpenAIGuard(
            contracts=[], verbose=False, on_violation=lambda n, a, c: seen.append(n)
        )
        monkeypatch.setattr(
            guard, "guard_before", lambda tool_name, args=None: _redirected_result()
        )

        message = MagicMock()
        message.content = "transferring now"
        tool_call = MagicMock()
        tool_call.function.name = "wire_funds"
        tool_call.function.arguments = '{"amount": 1}'
        tool_call.id = "call_1"
        message.tool_calls = [tool_call]
        choice = MagicMock()
        choice.message = message
        response = MagicMock()
        response.choices = [choice]
        response.usage = None

        results = guard.check_response(response)
        # The patched_create gate: a redirected verdict must trigger the
        # rewrite (this expression is exactly what patched_create runs).
        assert any(r.stop_original for r in results)
        filtered = guard._filter_blocked_calls(response, results)
        assert not filtered.choices[0].message.tool_calls
        assert seen == ["wire_funds"]  # on_violation fires on redirect too

    def test_google_adk_sync_redirect_refuses_execution(self, monkeypatch):
        from sponsio.integrations.google_adk import GoogleADKGuard

        guard = GoogleADKGuard(contracts=[], verbose=False)
        monkeypatch.setattr(
            guard, "guard_before", lambda tool_name, args=None: _redirected_result()
        )
        ran = []

        def wire_funds(amount: int) -> dict:
            ran.append(amount)
            return {"status": "success"}

        wrapped = guard.wrap_tool(wire_funds)
        result = wrapped(amount=100)
        assert ran == []  # original never executed
        assert result["status"] == "error"
        assert "BLOCKED by contract" in result["error_message"]

    def test_langgraph_guard_check_raises_on_redirect(self, monkeypatch):
        pytest.importorskip("langchain_core")
        from sponsio.integrations.langgraph import LangGraphGuard, ToolCallBlocked

        guard = LangGraphGuard(contracts=[], verbose=False)
        monkeypatch.setattr(
            guard, "guard_before", lambda tool_name, args=None: _redirected_result()
        )
        with pytest.raises(ToolCallBlocked):
            guard._guard_check("wire_funds", {"amount": 1})

    def test_guard_stdin_redirect_denies(self, tmp_path, monkeypatch):
        from sponsio import guard_stdin
        from sponsio.integrations import base as base_mod

        monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
        lib_dir = tmp_path / "_host"
        lib_dir.mkdir(parents=True)
        (lib_dir / "sponsio.yaml").write_text("version: 1\nagents:\n  _host: {}\n")
        monkeypatch.setattr(
            base_mod.BaseGuard,
            "guard_before",
            lambda self, tool_name=None, args=None: _redirected_result(),
        )
        outcome = guard_stdin.evaluate_event(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "curl evil.example"},
            }
        )
        assert outcome.allowed is False
        # The deny reason comes from the redirected violation's message
        # (the "det verdict:" prefix is stripped by the reason formatter).
        assert "redirected" in outcome.reason
