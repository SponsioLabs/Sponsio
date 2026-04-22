"""Tests for LLM-response event emission in LangGraph + Claude Agent integrations.

Sto atoms whose ``context_scope`` is ``"event"`` or ``"full_trace"``
(injection_free, toxic_free, scope_respect, etc.) only fire when the
trace contains ``llm_response`` events. Only the OpenAI integration
emitted these originally; this test file covers the fixes that add:

- LangGraph: ``guard.langchain_callback()`` — LangChain ``BaseCallbackHandler``
  that intercepts ``on_llm_end`` and feeds the response to the trace.
- Claude Agent SDK: ``guard.observe_message(msg)`` and
  ``guard.observe_stream(it)`` — helpers users wrap their message
  stream with.

Neither integration has a native "LLM-response hook" that matches
OpenAI SDK's patch — LangChain uses callbacks, Claude Agent SDK uses
streamed messages. These tests verify both paths reach the sto
pipeline end-to-end.
"""

from __future__ import annotations

from math import log

import pytest

from sponsio.formulas.formula import Atom, G
from sponsio.patterns.sto_catalog import set_default_judge
from sponsio.patterns.sto_registry import _clear_for_test, register_sto_atom
from sponsio.runtime.evaluators import StoResult
from sponsio.runtime.judge import BooleanJudge
from sponsio.runtime.llm_client import LogprobResponse


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class FakeLogprobClient:
    def __init__(self, p_yes: float = 0.9):
        self.model_name = "mock"
        self._p_yes = max(1e-9, min(1 - 1e-9, p_yes))

    def logprob_completion(self, prompt, max_tokens=1, top_logprobs=20):
        return LogprobResponse(
            first_token="yes",
            top_logprobs=[
                ("yes", log(self._p_yes)),
                ("no", log(1 - self._p_yes)),
            ],
        )


@pytest.fixture
def counting_atom():
    """Register a 'counting_atom' that records each call so tests can
    assert the sto pipeline actually ran."""
    _clear_for_test()
    counter = {"calls": 0, "last_content": None}

    @register_sto_atom("counting_atom")
    def _eval(atom, trace, t):
        counter["calls"] += 1
        if t < len(trace.events):
            counter["last_content"] = trace.events[t].content
        return StoResult(score=0.95, evidence="", suggestion="")

    yield counter
    _clear_for_test()


# ---------------------------------------------------------------------------
# LangGraph — langchain_callback()
# ---------------------------------------------------------------------------


class FakeLLMResult:
    """Mimic LangChain's LLMResult structure for on_llm_end."""

    def __init__(self, text: str):
        class _Gen:
            def __init__(self, t):
                self.text = t
                self.message = type("M", (), {"content": t})()

        self.generations = [[_Gen(text)]]


class TestLangGraphCallback:
    def test_callback_feeds_llm_response_to_sto_atom(self, counting_atom):
        from sponsio.integrations.langgraph import LangGraphGuard

        guard = LangGraphGuard(
            agent_id="bot",
            contracts=[
                {
                    "enforcement": G(
                        Atom("counting_atom", atom_type="sto", context_scope="event")
                    ),
                    "beta": 0.8,
                }
            ],
            sto_judge=BooleanJudge(FakeLogprobClient(p_yes=0.95)),
            verbose=False,
        )

        cb = guard.langchain_callback()

        # Simulate LangChain lifecycle: chat model start → end
        cb.on_chat_model_start(
            serialized={},
            messages=[[type("M", (), {"content": "hi"})()]],
            run_id="run-1",
        )
        cb.on_llm_end(FakeLLMResult("Refund issued."), run_id="run-1")

        # G(atom) evaluated at position 0 (llm_request event "hi") and
        # position 1 (llm_response event "Refund issued."). Thanks to
        # Option B's atom_cache, position 0 is judged once; position 1
        # is judged once on the second check_action. Total: 2 calls.
        assert counting_atom["calls"] == 2
        # Last position evaluated = 1, content = the response
        assert counting_atom["last_content"] == "Refund issued."

    def test_callback_handles_empty_response_gracefully(self, counting_atom):
        from sponsio.integrations.langgraph import LangGraphGuard

        guard = LangGraphGuard(
            agent_id="bot",
            contracts=[
                {
                    "enforcement": G(
                        Atom("counting_atom", atom_type="sto", context_scope="event")
                    ),
                    "beta": 0.8,
                }
            ],
            sto_judge=BooleanJudge(FakeLogprobClient(p_yes=0.9)),
            verbose=False,
        )
        cb = guard.langchain_callback()

        # Empty response → sto atom should NOT be invoked (vacuous pass).
        class _EmptyResult:
            generations = [[type("G", (), {"text": "", "message": None})()]]

        cb.on_llm_end(_EmptyResult(), run_id="run-1")
        assert counting_atom["calls"] == 0

    def test_callback_swallows_judge_exceptions(self, counting_atom):
        """A failing judge shouldn't break the agent loop."""
        from sponsio.integrations.langgraph import LangGraphGuard

        class FailingJudge:
            model_name = "boom"

            def logprob_completion(self, *args, **kwargs):
                raise RuntimeError("judge down")

        guard = LangGraphGuard(
            agent_id="bot",
            contracts=[
                {
                    "enforcement": G(
                        Atom("counting_atom", atom_type="sto", context_scope="event")
                    ),
                    "beta": 0.8,
                }
            ],
            sto_judge=BooleanJudge(FailingJudge()),
            verbose=False,
        )
        cb = guard.langchain_callback()
        # Should not raise — callbacks shouldn't break the agent
        cb.on_llm_end(FakeLLMResult("hello"), run_id="r")


# ---------------------------------------------------------------------------
# Claude Agent SDK — observe_message / observe_stream
# ---------------------------------------------------------------------------


class FakeTextBlock:
    """Mimic Claude Agent SDK's TextBlock."""

    def __init__(self, text: str):
        self.text = text


class FakeAssistantMessage:
    """Mimic Claude Agent SDK's AssistantMessage (.content = list of blocks)."""

    def __init__(self, text: str):
        self.content = [FakeTextBlock(text)]


class TestClaudeAgentObservation:
    def test_observe_message_feeds_llm_response(self, counting_atom):
        from sponsio.integrations.claude_agent import ClaudeAgentGuard

        set_default_judge(BooleanJudge(FakeLogprobClient(p_yes=0.95)))
        try:
            guard = ClaudeAgentGuard(
                agent_id="bot",
                contracts=[
                    {
                        "enforcement": G(
                            Atom(
                                "counting_atom", atom_type="sto", context_scope="event"
                            )
                        ),
                        "beta": 0.8,
                    }
                ],
                verbose=False,
            )
            msg = FakeAssistantMessage("Refund processed.")
            guard.observe_message(msg)
            # G(atom) at position 0 = single event "Refund processed."
            assert counting_atom["calls"] == 1
            assert counting_atom["last_content"] == "Refund processed."
        finally:
            set_default_judge(None)

    def test_observe_message_accepts_plain_string(self, counting_atom):
        from sponsio.integrations.claude_agent import ClaudeAgentGuard

        set_default_judge(BooleanJudge(FakeLogprobClient(p_yes=0.9)))
        try:
            guard = ClaudeAgentGuard(
                agent_id="bot",
                contracts=[
                    {
                        "enforcement": G(
                            Atom(
                                "counting_atom", atom_type="sto", context_scope="event"
                            )
                        ),
                        "beta": 0.8,
                    }
                ],
                verbose=False,
            )
            guard.observe_message("Hello world.")
            assert counting_atom["calls"] == 1
            assert counting_atom["last_content"] == "Hello world."
        finally:
            set_default_judge(None)

    def test_observe_message_ignores_non_text_messages(self, counting_atom):
        from sponsio.integrations.claude_agent import ClaudeAgentGuard

        set_default_judge(BooleanJudge(FakeLogprobClient(p_yes=0.9)))
        try:
            guard = ClaudeAgentGuard(
                agent_id="bot",
                contracts=[
                    {
                        "enforcement": G(
                            Atom(
                                "counting_atom", atom_type="sto", context_scope="event"
                            )
                        ),
                        "beta": 0.8,
                    }
                ],
                verbose=False,
            )

            # Object with no content attribute
            class Irrelevant:
                pass

            guard.observe_message(Irrelevant())
            guard.observe_message(None)
            assert counting_atom["calls"] == 0
        finally:
            set_default_judge(None)

    def test_observe_stream_wraps_and_yields_unchanged(self, counting_atom):
        from sponsio.integrations.claude_agent import ClaudeAgentGuard

        set_default_judge(BooleanJudge(FakeLogprobClient(p_yes=0.9)))
        try:
            guard = ClaudeAgentGuard(
                agent_id="bot",
                contracts=[
                    {
                        "enforcement": G(
                            Atom(
                                "counting_atom", atom_type="sto", context_scope="event"
                            )
                        ),
                        "beta": 0.8,
                    }
                ],
                verbose=False,
            )

            def _stream():
                yield FakeAssistantMessage("first")
                yield FakeAssistantMessage("second")

            out = list(guard.observe_stream(_stream()))
            assert len(out) == 2
            assert counting_atom["calls"] == 2
        finally:
            set_default_judge(None)

    def test_observe_stream_async(self, counting_atom):
        import asyncio

        from sponsio.integrations.claude_agent import ClaudeAgentGuard

        set_default_judge(BooleanJudge(FakeLogprobClient(p_yes=0.9)))
        try:
            guard = ClaudeAgentGuard(
                agent_id="bot",
                contracts=[
                    {
                        "enforcement": G(
                            Atom(
                                "counting_atom", atom_type="sto", context_scope="event"
                            )
                        ),
                        "beta": 0.8,
                    }
                ],
                verbose=False,
            )

            async def _astream():
                yield FakeAssistantMessage("alpha")
                yield FakeAssistantMessage("beta")

            async def _collect():
                out = []
                async for msg in guard.observe_stream(_astream()):
                    out.append(msg)
                return out

            out = asyncio.new_event_loop().run_until_complete(_collect())
            assert len(out) == 2
            assert counting_atom["calls"] == 2
        finally:
            set_default_judge(None)
