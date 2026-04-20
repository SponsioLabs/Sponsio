"""Unit tests for sponsio/integrations/openai.py — OpenAI SDK monkey-patch."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from sponsio.integrations.openai import OpenAIGuard, patch_openai, unpatch_openai


# ---------------------------------------------------------------------------
# Mock OpenAI response objects
# ---------------------------------------------------------------------------


@dataclass
class MockFunction:
    name: str
    arguments: str = "{}"


@dataclass
class MockToolCall:
    id: str
    type: str = "function"
    function: MockFunction = field(default_factory=lambda: MockFunction(name="test"))


@dataclass
class MockMessage:
    role: str = "assistant"
    content: str | None = None
    tool_calls: list[MockToolCall] | None = None


@dataclass
class MockChoice:
    index: int = 0
    message: MockMessage = field(default_factory=MockMessage)
    finish_reason: str = "tool_calls"


@dataclass
class MockCompletion:
    choices: list[MockChoice] = field(default_factory=list)


def make_response(*tool_names: str) -> MockCompletion:
    """Build a mock ChatCompletion with the given tool_call names."""
    tool_calls = [
        MockToolCall(id=f"call_{i}", function=MockFunction(name=name))
        for i, name in enumerate(tool_names)
    ]
    return MockCompletion(
        choices=[MockChoice(message=MockMessage(tool_calls=tool_calls))]
    )


def make_response_no_tools() -> MockCompletion:
    """Build a mock ChatCompletion with no tool_calls."""
    return MockCompletion(
        choices=[
            MockChoice(message=MockMessage(content="Hello!"), finish_reason="stop")
        ]
    )


# ---------------------------------------------------------------------------
# OpenAIGuard.check_response
# ---------------------------------------------------------------------------


class TestOpenAIGuard:
    def test_no_tool_calls_no_violations(self):
        guard = OpenAIGuard(contracts=["tool `A` must precede `B`"])
        response = make_response_no_tools()
        results = guard.check_response(response)
        assert results == []
        assert guard.last_check is None

    def test_allowed_tool_call(self):
        guard = OpenAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        response = make_response("check_policy")
        results = guard.check_response(response)
        assert len(results) == 1
        assert results[0].allowed is True
        assert results[0].blocked is False

    def test_blocked_tool_call(self):
        guard = OpenAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        response = make_response("issue_refund")
        results = guard.check_response(response)
        assert len(results) == 1
        assert results[0].blocked is True
        assert len(guard.violations) > 0

    def test_correct_order_allowed(self):
        guard = OpenAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )

        # First call: check_policy
        r1 = guard.check_response(make_response("check_policy"))
        assert r1[0].blocked is False

        # Second call: issue_refund (now allowed because check_policy was seen)
        r2 = guard.check_response(make_response("issue_refund"))
        assert r2[0].blocked is False

    def test_mutual_exclusion_enforced(self):
        guard = OpenAIGuard(
            contracts=["tools `approve` and `reject` are mutually exclusive"]
        )

        # First call: approve — allowed
        r1 = guard.check_response(make_response("approve"))
        assert r1[0].blocked is False

        # Second call: reject — blocked (already approved)
        r2 = guard.check_response(make_response("reject"))
        assert r2[0].blocked is True

    def test_multiple_tool_calls_in_one_response(self):
        guard = OpenAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )

        # Both tools in one response — check_policy first is OK
        tool_calls = [
            MockToolCall(id="call_0", function=MockFunction(name="check_policy")),
            MockToolCall(id="call_1", function=MockFunction(name="issue_refund")),
        ]
        response = MockCompletion(
            choices=[MockChoice(message=MockMessage(tool_calls=tool_calls))]
        )
        results = guard.check_response(response)
        assert len(results) == 2
        # check_policy should be allowed
        assert results[0].blocked is False
        # issue_refund should also be allowed (check_policy preceded it)
        assert results[1].blocked is False

    def test_last_check_updated(self):
        guard = OpenAIGuard(contracts=["tool `A` must precede `B`"])
        guard.check_response(make_response("A"))
        assert guard.last_check is not None
        assert guard.last_check.blocked is False

    def test_on_violation_callback(self):
        violations_seen: list[str] = []

        def on_violation(tool_name: str, args: dict, check: Any):
            violations_seen.append(tool_name)

        guard = OpenAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"],
            on_violation=on_violation,
        )
        guard.check_response(make_response("issue_refund"))
        assert "issue_refund" in violations_seen

    def test_on_violation_not_called_when_allowed(self):
        violations_seen: list[str] = []

        def on_violation(tool_name: str, args: dict, check: Any):
            violations_seen.append(tool_name)

        guard = OpenAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"],
            on_violation=on_violation,
        )
        guard.check_response(make_response("check_policy"))
        assert violations_seen == []

    def test_reset_clears_state(self):
        guard = OpenAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        guard.check_response(make_response("issue_refund"))
        assert len(guard.violations) > 0

        guard.reset()
        assert len(guard.violations) == 0

        # After reset, issue_refund without check_policy is still blocked
        r = guard.check_response(make_response("issue_refund"))
        assert r[0].blocked is True

    def test_summary(self):
        guard = OpenAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        assert "No violations" in guard.summary()

        guard.check_response(make_response("issue_refund"))
        summary = guard.summary()
        assert "violation" in summary.lower()

    def test_malformed_arguments_handled(self):
        guard = OpenAIGuard(contracts=["tool `A` must precede `B`"])
        tool_calls = [
            MockToolCall(
                id="call_0", function=MockFunction(name="A", arguments="not-json")
            ),
        ]
        response = MockCompletion(
            choices=[MockChoice(message=MockMessage(tool_calls=tool_calls))]
        )
        # Should not raise
        results = guard.check_response(response)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# patch_openai / unpatch_openai
# ---------------------------------------------------------------------------


class TestPatchUnpatch:
    def test_unpatch_safe_without_patch(self):
        """unpatch_openai should not raise if never patched."""
        unpatch_openai()

    def test_patch_returns_guard(self):
        """patch_openai returns an OpenAIGuard when openai is available."""
        try:
            import openai  # noqa: F401
        except ImportError:
            pytest.skip("openai not installed")

        guard = patch_openai(contracts=["tool `A` must precede `B`"])
        try:
            assert isinstance(guard, OpenAIGuard)
        finally:
            unpatch_openai()

    def test_patch_requires_openai(self):
        """patch_openai raises ImportError if openai is missing."""
        import sys

        # Temporarily hide the openai module
        openai_module = sys.modules.get("openai")
        sys.modules["openai"] = None  # type: ignore[assignment]
        try:
            with pytest.raises(ImportError, match="openai is required"):
                patch_openai(contracts=["tool `A` must precede `B`"])
        finally:
            if openai_module is not None:
                sys.modules["openai"] = openai_module
            else:
                sys.modules.pop("openai", None)
