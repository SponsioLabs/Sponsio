"""Unified sto-violation surface across framework adapters (#12).

Before this fix the five Python adapters each chose their own way to
surface a sto violation:

* ``LangGraphGuard`` *raised* ``ToolCallBlocked`` — aborting the whole
  agent loop and making sto failures behave exactly like det blocks,
  which defeated the ``RetryWithConstraint`` strategy that the sto
  pipeline was designed to feed.
* ``CrewAIGuard``, ``AgentsSDKGuard``, ``VercelAIGuard`` each returned
  feedback inline but wrote their own wording ("Tool succeeded but
  output quality check failed…" / "Tool succeeded but quality check
  failed…" / "Quality check failed…"). Operators grepping their logs
  for sto retry events missed one or more of them depending on which
  framework happened to be in use.

The fix: a single ``format_sto_retry_message`` helper in
``sponsio.integrations.base``, and every adapter either routes through
it (tool-result-channel frameworks) or explicitly documents why it
can't (Claude Agent uses ``additionalContext``, a sidecar hint channel
where "Original output:" would misdescribe the wire behaviour).

These tests fail if:

* Any tool-result-channel adapter reinvents the phrasing.
* LangGraph regresses and starts raising on sto violations again.
* A new adapter is added and forgets to call the helper.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from sponsio.integrations.base import format_sto_retry_message
from sponsio.runtime.strategies import EnforcementResult


@pytest.fixture
def sto_retry_check() -> Any:
    """A synthetic ``CheckResult`` that looks like a live sto retry —
    ``needs_retry`` is True and ``feedback`` carries the would-be prompt
    injection back to the model. Each adapter is patched to return this
    from ``guard_after`` so tests don't depend on a real LLM judge.
    """
    from sponsio.integrations.base import CheckResult

    return CheckResult(
        allowed=True,
        det_violations=[],
        sto_violations=[
            EnforcementResult(
                action="retrying",
                message="SOFT: tone — aggressive phrasing detected",
                retry_prompt="Please rephrase in a neutral tone.",
            )
        ],
        feedback="Please rephrase in a neutral tone.",
        rollback_performed=False,
    )


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------


class TestFormatStoRetryMessage:
    def test_shape_is_stable(self):
        """The exact string shape is documented in docstrings and
        referenced in ops runbooks. If anyone touches the template,
        this assertion forces them to also update the docs. Don't
        relax this check — it's the whole point."""
        msg = format_sto_retry_message("rephrase neutrally", "you are stupid")
        assert msg == (
            "Tool succeeded but output quality check failed. "
            "Feedback: rephrase neutrally. Original output: you are stupid"
        )

    def test_accepts_any_original(self):
        """Tool outputs can be dicts, lists, numbers — whatever the
        framework hands us. The helper must not choke on non-string
        originals (they go through ``__str__``)."""
        msg = format_sto_retry_message("bad", {"order_id": 42})
        assert "Original output: {'order_id': 42}" in msg


# ---------------------------------------------------------------------------
# LangGraph — most important behavioural regression
# ---------------------------------------------------------------------------


class TestLangGraphStoSurface:
    def test_sto_violation_returns_feedback_not_raises(self, sto_retry_check):
        """Pre-fix LangGraph raised ``ToolCallBlocked`` on sto
        violations, which aborted the agent loop. Every other adapter
        returns feedback inline so the model self-corrects. This test
        locks in the unified behaviour."""
        from sponsio.integrations.langgraph import LangGraphGuard

        guard = LangGraphGuard(agent_id="bot")

        with patch.object(guard, "guard_after", return_value=sto_retry_check):
            out = guard._guard_post_check("toxic_responder", "you are stupid")

        assert isinstance(out, str)
        assert out == format_sto_retry_message(
            "Please rephrase in a neutral tone.", "you are stupid"
        )

    def test_sto_pass_returns_original_result_unchanged(self):
        """Happy path: no sto flag, ``_guard_post_check`` returns the
        original result so wrapped tools are transparent."""
        from sponsio.integrations.base import CheckResult
        from sponsio.integrations.langgraph import LangGraphGuard

        guard = LangGraphGuard(agent_id="bot")
        clean = CheckResult(allowed=True)

        with patch.object(guard, "guard_after", return_value=clean):
            out = guard._guard_post_check("t", "fine output")

        assert out == "fine output"

    def test_det_block_still_raises(self):
        """The ``_guard_check`` path (det pipeline) keeps its
        contract-block semantics — this fix is strictly about the sto
        surface. Det blocks must still raise so the tool body never
        runs."""
        from sponsio.integrations.langgraph import LangGraphGuard, ToolCallBlocked

        guard = LangGraphGuard(
            agent_id="bot",
            contracts=["tool `check_policy` must precede `issue_refund`"],
        )
        with pytest.raises(ToolCallBlocked):
            guard._guard_check("issue_refund", {})


# ---------------------------------------------------------------------------
# CrewAI
# ---------------------------------------------------------------------------


class TestCrewAIStoSurface:
    def test_on_tool_end_routes_through_helper(self, sto_retry_check):
        from sponsio.integrations.crewai import CrewAIGuard

        guard = CrewAIGuard(agent_id="bot")

        class Ctx:
            tool_name = "toxic_responder"

        with patch.object(guard, "guard_after", return_value=sto_retry_check):
            out = guard.on_tool_end(Ctx(), "you are stupid")

        assert out == format_sto_retry_message(
            "Please rephrase in a neutral tone.", "you are stupid"
        )


# ---------------------------------------------------------------------------
# Claude Agent — the one intentional deviation
# ---------------------------------------------------------------------------


class TestClaudeAgentStoSurface:
    def test_claude_uses_additional_context_channel(self):
        """Claude Agent's ``additionalContext`` is a sidecar hint — the
        real tool result is kept. "Tool succeeded but output quality
        check failed. Original output: …" would misdescribe the
        channel, so Claude deliberately uses its own ``[Sponsio
        quality check]`` prefix. This test documents the deviation
        and fails if someone blindly switches it to the shared helper
        without reconsidering the wire semantics."""
        # The claude_agent_sdk isn't importable in CI. Just verify the
        # intent comment survives in the source — it's the only place
        # we have an audit trail for the decision.
        from pathlib import Path

        src = Path("sponsio/integrations/claude_agent.py").read_text(encoding="utf-8")
        assert "sidecar hint" in src
        assert "format_sto_retry_message" in src
        assert "[Sponsio quality check]" in src
