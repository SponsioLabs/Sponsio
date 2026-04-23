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
        with pytest.warns(UserWarning, match="not valid JSON"):
            results = guard.check_response(response)
        assert len(results) == 1


class TestMalformedToolArguments:
    """Issue #11: malformed ``tool_call.function.arguments`` must NOT silently
    decode to ``{}``. Field-level content contracts (``arg_blacklist``,
    ``arg_field_has``, ``arg_value_range``) would then vacuously pass on a
    payload an attacker carefully crafted to be unparseable. The fix preserves
    the raw bytes under ``_raw_arguments`` so coarse regex contracts still see
    them, and emits a UserWarning so operators know their field-level guards
    are momentarily blind.
    """

    def test_unparseable_args_emit_warning_and_preserve_raw(self):
        """The raw payload survives so ``arg_has``-style regex guards work."""
        from sponsio.integrations.openai import _coerce_tool_arguments

        raw = "{not valid json -- rm -rf /}"
        with pytest.warns(UserWarning, match="not valid JSON"):
            args = _coerce_tool_arguments(raw, tool_name="bash")
        assert args["_sponsio_unparseable"] is True
        assert args["_raw_arguments"] == raw

    def test_strict_mode_raises_on_malformed(self, monkeypatch):
        """Operators can opt into hard-fail via env var."""
        from sponsio.integrations.openai import _coerce_tool_arguments

        monkeypatch.setenv("SPONSIO_OPENAI_STRICT_TOOL_ARGS", "1")
        with pytest.raises(ValueError, match="not valid JSON"):
            _coerce_tool_arguments("{not json}", tool_name="bash")

    def test_empty_or_none_arguments_become_empty_dict(self):
        """Legitimate "no arguments" case must not warn or sentinel-wrap."""
        from sponsio.integrations.openai import _coerce_tool_arguments
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert _coerce_tool_arguments(None, tool_name="t") == {}
            assert _coerce_tool_arguments("", tool_name="t") == {}

    def test_already_dict_passthrough(self):
        """Some SDK versions hand back an already-parsed dict; don't re-encode."""
        from sponsio.integrations.openai import _coerce_tool_arguments

        d = {"command": "echo hi"}
        assert _coerce_tool_arguments(d, tool_name="bash") is d

    def test_non_object_json_wrapped(self):
        """JSON arrays / scalars decode but are wrapped so the field is a dict."""
        from sponsio.integrations.openai import _coerce_tool_arguments

        assert _coerce_tool_arguments("[1, 2]", tool_name="t") == {
            "_raw_arguments": [1, 2]
        }
        assert _coerce_tool_arguments("42", tool_name="t") == {"_raw_arguments": 42}

    def test_coarse_regex_guard_still_matches_unparseable_payload(self):
        """End-to-end: ``arg_has`` over the raw blob keeps catching attacks
        even when the JSON parser refuses the payload. Regression for the
        silent ``{}`` failure mode."""
        from sponsio.models.agent import Agent
        from sponsio.models.contract import Contract
        from sponsio.patterns.library import arg_blacklist

        # arg_blacklist on the (synthetic) field name "_raw_arguments"
        # — the field where unparseable bytes are stashed. A coarse regex
        # guard applied here catches the attack string even when the
        # original field structure was lost.
        guard = OpenAIGuard(
            agent_id="t",
            contracts=[
                Contract(
                    agent=Agent(id="t"),
                    enforcement=arg_blacklist("bash", "_raw_arguments", ["rm -rf"]),
                )
            ],
        )
        tool_calls = [
            MockToolCall(
                id="call_0",
                function=MockFunction(
                    name="bash",
                    arguments="{not json: 'rm -rf /'}",  # unparseable + dangerous
                ),
            ),
        ]
        response = MockCompletion(
            choices=[MockChoice(message=MockMessage(tool_calls=tool_calls))]
        )
        with pytest.warns(UserWarning, match="not valid JSON"):
            results = guard.check_response(response)
        assert len(results) == 1
        assert results[0].blocked, (
            "arg_blacklist on the raw-arguments fallback field must still "
            "catch attack patterns when JSON parsing fails."
        )


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


# ---------------------------------------------------------------------------
# guard_after: observe_tool_result + auto-scan of tool messages
# ---------------------------------------------------------------------------


def _data_writes(guard: OpenAIGuard):
    return [e for e in guard._monitor.trace.events if e.event_type == "data_write"]


class TestObserveToolResult:
    """The OpenAI integration used to only cover ``guard_before`` (i.e.
    the model's *intent* to call a tool).  These tests lock in the new
    ``guard_after`` path so ``no_data_leak`` and BaseGuard auto-tag
    work for OpenAI users at parity with LangGraph / CrewAI."""

    def test_check_response_captures_tool_call_ids(self):
        guard = OpenAIGuard(contracts=["tool `A` must precede `B`"])
        response = make_response("lookup_customer")
        guard.check_response(response)
        # The id from ``make_response`` is ``call_0``.
        assert guard._pending_tool_calls == {"call_0": "lookup_customer"}

    def test_observe_tool_result_emits_contains(self):
        guard = OpenAIGuard(agent_id="bot")
        guard.check_response(make_response("lookup_customer"))
        guard.observe_tool_result("call_0", "customer record for 42")

        writes = _data_writes(guard)
        assert len(writes) == 1
        assert writes[0].contains == ["lookup_customer"]

    def test_observe_tool_result_explicit_name_bypasses_pending(self):
        """Explicit ``tool_name`` wins — useful when the user builds
        the trace by hand without going through ``check_response``."""
        guard = OpenAIGuard(agent_id="bot")
        guard.observe_tool_result(
            tool_call_id="manual_1",
            output="pong",
            tool_name="ping",
        )
        writes = _data_writes(guard)
        assert len(writes) == 1
        assert writes[0].contains == ["ping"]

    def test_observe_tool_result_is_idempotent(self):
        """Calling twice for the same id should no-op — prevents the
        auto-scan path from re-firing on every subsequent turn."""
        guard = OpenAIGuard(agent_id="bot")
        guard.check_response(make_response("lookup_customer"))

        guard.observe_tool_result("call_0", "first")
        guard.observe_tool_result("call_0", "second")  # should no-op

        assert len(_data_writes(guard)) == 1

    def test_observe_tool_result_without_known_tool_name_noops(self):
        """If no id→name binding exists AND no explicit name is given,
        we silently produce no data_write rather than crashing or
        emitting an empty-name event."""
        guard = OpenAIGuard(agent_id="bot")
        res = guard.observe_tool_result("unknown_id", "output")
        assert res.allowed is True
        assert _data_writes(guard) == []


class TestAutoScanToolMessages:
    """The OpenAI tool-calling loop feeds results back into the next
    ``chat.completions.create`` as ``{"role": "tool", ...}`` messages.
    The patch scans those on the outbound path so
    ``BaseGuard.guard_after`` runs without the user lifting a finger.
    """

    def test_dict_message_triggers_guard_after(self):
        guard = OpenAIGuard(agent_id="bot")
        guard.check_response(make_response("lookup_customer"))

        messages = [
            {"role": "user", "content": "Look up user 42"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "call_0", "function": {"name": "lookup_customer"}}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_0",
                "content": "customer record",
            },
        ]
        guard._auto_observe_tool_messages(messages)
        writes = _data_writes(guard)
        assert len(writes) == 1
        assert writes[0].contains == ["lookup_customer"]

    def test_list_of_parts_content_is_flattened(self):
        """OpenAI allows ``content: [{type: text, text: "..."}, ...]``.
        The PII detector needs a plain string, so the scanner must
        flatten parts before handing to ``guard_after``."""
        guard = OpenAIGuard(agent_id="bot", tag_pii=True)
        guard.check_response(make_response("lookup_customer"))

        messages = [
            {
                "role": "tool",
                "tool_call_id": "call_0",
                "content": [
                    {"type": "text", "text": "Alice, SSN 123-45-6789,"},
                    {"type": "text", "text": " alice@example.com"},
                ],
            },
        ]
        guard._auto_observe_tool_messages(messages)
        writes = _data_writes(guard)
        assert len(writes) == 1
        contains = writes[0].contains
        assert "ssn" in contains
        assert "email" in contains
        assert "pii" in contains

    def test_already_observed_tool_call_is_skipped(self):
        """Message history replayed across turns must not double-fire."""
        guard = OpenAIGuard(agent_id="bot")
        guard.check_response(make_response("lookup_customer"))

        msgs = [{"role": "tool", "tool_call_id": "call_0", "content": "result"}]
        guard._auto_observe_tool_messages(msgs)
        # Simulate a second turn: the user rebuilds ``messages`` with
        # the same historical tool message still in it.
        guard._auto_observe_tool_messages(msgs)

        assert len(_data_writes(guard)) == 1

    def test_missing_or_malformed_messages_does_not_crash(self):
        guard = OpenAIGuard(agent_id="bot")
        # None
        guard._auto_observe_tool_messages(None)
        # Not a list
        guard._auto_observe_tool_messages("oops")
        # Tool message missing id
        guard._auto_observe_tool_messages([{"role": "tool", "content": "orphan"}])
        assert _data_writes(guard) == []

    def test_pydantic_like_message_objects_work(self):
        """Users sometimes pass the pydantic ``ChatCompletionMessage``
        objects straight from a previous response — auto-scan must
        tolerate attribute access as well as dict access."""

        class Msg:
            def __init__(self, role, tool_call_id=None, content=None):
                self.role = role
                self.tool_call_id = tool_call_id
                self.content = content

        guard = OpenAIGuard(agent_id="bot")
        guard.check_response(make_response("lookup_customer"))

        guard._auto_observe_tool_messages(
            [Msg(role="tool", tool_call_id="call_0", content="pydantic-shape")]
        )
        writes = _data_writes(guard)
        assert len(writes) == 1
        assert writes[0].contains == ["lookup_customer"]
