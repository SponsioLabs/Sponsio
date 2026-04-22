"""Tests for the atom-registered sto evaluators.

Covers: injection_free, jailbreak_free, toxic_free, semantic_pii_free,
scope_respect, hallucination_free. All share the same shape — read
content, call BooleanJudge, return StoResult — so we exercise each
with a mock judge that returns canned confidences.
"""

from __future__ import annotations

from math import log

import pytest

from sponsio.formulas.formula import Atom
from sponsio.models.trace import Event, Trace
from sponsio.patterns.sto_catalog import set_default_judge
from sponsio.patterns.sto_registry import resolve_sto_evaluator
from sponsio.runtime.judge import BooleanJudge
from sponsio.runtime.llm_client import LogprobResponse


# ---------------------------------------------------------------------------
# Mock judge — deterministic yes/no based on a canned probability
# ---------------------------------------------------------------------------


class FakeLogprobClient:
    def __init__(self, p_yes: float, model_name: str = "mock"):
        self.model_name = model_name
        self._p_yes = max(1e-9, min(1 - 1e-9, p_yes))
        self.calls = 0
        self.last_prompt: str | None = None

    def logprob_completion(self, prompt, max_tokens=1, top_logprobs=20):
        self.calls += 1
        self.last_prompt = prompt
        return LogprobResponse(
            first_token="yes" if self._p_yes >= 0.5 else "no",
            top_logprobs=[
                ("yes", log(self._p_yes)),
                ("no", log(1 - self._p_yes)),
            ],
        )


def _trace_with(content: str, event_type: str = "llm_response") -> Trace:
    return Trace(
        events=[Event(ts=0, agent="bot", event_type=event_type, content=content)]
    )


@pytest.fixture
def mock_judge():
    """Install a judge with configurable confidence; tear down after test."""

    def _install(p_yes: float) -> FakeLogprobClient:
        client = FakeLogprobClient(p_yes=p_yes)
        set_default_judge(BooleanJudge(client))
        return client

    yield _install
    set_default_judge(None)


# ---------------------------------------------------------------------------
# Shared behaviours — each atom must satisfy these
# ---------------------------------------------------------------------------


ALL_SIMPLE_ATOMS = [
    ("injection_free", ()),
    ("jailbreak_free", ()),
    ("toxic_free", ()),
    ("semantic_pii_free", ()),
]


@pytest.mark.parametrize("predicate,args", ALL_SIMPLE_ATOMS)
class TestSimpleAtomBehaviour:
    def test_high_confidence_maps_to_high_score(self, predicate, args, mock_judge):
        mock_judge(p_yes=0.92)
        fn = resolve_sto_evaluator(predicate)
        atom = Atom(predicate, *args, atom_type="sto", context_scope="event")
        result = fn(atom, _trace_with("some content"), 0)
        assert result.score == pytest.approx(0.92, abs=0.01)

    def test_low_confidence_triggers_suggestion(self, predicate, args, mock_judge):
        mock_judge(p_yes=0.1)
        fn = resolve_sto_evaluator(predicate)
        atom = Atom(predicate, *args, atom_type="sto", context_scope="event")
        result = fn(atom, _trace_with("something bad"), 0)
        assert result.score == pytest.approx(0.1, abs=0.01)
        assert result.suggestion  # non-empty when below 0.5

    def test_no_content_passes_vacuously(self, predicate, args, mock_judge):
        client = mock_judge(p_yes=0.1)  # would fail if called
        fn = resolve_sto_evaluator(predicate)
        atom = Atom(predicate, *args, atom_type="sto", context_scope="event")
        trace = Trace(
            events=[Event(ts=0, agent="bot", event_type="llm_response", content=None)]
        )
        result = fn(atom, trace, 0)
        assert result.score == 1.0
        assert client.calls == 0  # judge should not have been invoked

    def test_full_trace_scope_concatenates(self, predicate, args, mock_judge):
        client = mock_judge(p_yes=0.8)
        fn = resolve_sto_evaluator(predicate)
        atom = Atom(predicate, *args, atom_type="sto", context_scope="full_trace")
        trace = Trace(
            events=[
                Event(ts=0, agent="bot", event_type="llm_response", content="first"),
                Event(ts=1, agent="bot", event_type="llm_response", content="second"),
            ]
        )
        result = fn(atom, trace, 0)
        assert result.score == pytest.approx(0.8, abs=0.01)
        assert client.calls == 1
        # Prompt should mention both events' content
        assert "first" in client.last_prompt and "second" in client.last_prompt


# ---------------------------------------------------------------------------
# Per-atom semantic checks — confirms each atom's PROMPT matches its intent.
# We don't validate judge reasoning (that's LLM behaviour) — we validate the
# prompt we're sending aligns with what the atom name claims.
# ---------------------------------------------------------------------------


class TestPromptContent:
    def test_injection_free_prompt_mentions_injection(self, mock_judge):
        client = mock_judge(p_yes=0.9)
        fn = resolve_sto_evaluator("injection_free")
        atom = Atom("injection_free", atom_type="sto", context_scope="event")
        fn(atom, _trace_with("x"), 0)
        assert "injection" in client.last_prompt.lower()

    def test_jailbreak_free_prompt_mentions_jailbreak(self, mock_judge):
        client = mock_judge(p_yes=0.9)
        fn = resolve_sto_evaluator("jailbreak_free")
        atom = Atom("jailbreak_free", atom_type="sto", context_scope="event")
        fn(atom, _trace_with("x"), 0)
        assert "jailbreak" in client.last_prompt.lower()

    def test_toxic_free_prompt_mentions_toxic(self, mock_judge):
        client = mock_judge(p_yes=0.9)
        fn = resolve_sto_evaluator("toxic_free")
        atom = Atom("toxic_free", atom_type="sto", context_scope="event")
        fn(atom, _trace_with("x"), 0)
        assert "toxic" in client.last_prompt.lower()

    def test_semantic_pii_prompt_mentions_personal_info(self, mock_judge):
        client = mock_judge(p_yes=0.9)
        fn = resolve_sto_evaluator("semantic_pii_free")
        atom = Atom("semantic_pii_free", atom_type="sto", context_scope="event")
        fn(atom, _trace_with("x"), 0)
        assert "personal" in client.last_prompt.lower()


# ---------------------------------------------------------------------------
# scope_respect — parameterized atom
# ---------------------------------------------------------------------------


class TestScopeRespect:
    def test_scope_arg_appears_in_prompt(self, mock_judge):
        client = mock_judge(p_yes=0.9)
        fn = resolve_sto_evaluator("scope_respect")
        atom = Atom(
            "scope_respect",
            "customer support about orders and refunds",
            atom_type="sto",
            context_scope="event",
        )
        fn(atom, _trace_with("Your order shipped."), 0)
        assert "customer support" in client.last_prompt

    def test_missing_scope_is_vacuous(self, mock_judge):
        client = mock_judge(p_yes=0.1)  # would fail if invoked
        fn = resolve_sto_evaluator("scope_respect")
        atom = Atom("scope_respect", atom_type="sto", context_scope="event")
        result = fn(atom, _trace_with("whatever"), 0)
        assert result.score == 1.0
        assert "scope" in result.evidence.lower()
        assert client.calls == 0

    def test_score_reflects_judge_confidence(self, mock_judge):
        mock_judge(p_yes=0.65)
        fn = resolve_sto_evaluator("scope_respect")
        atom = Atom(
            "scope_respect",
            "tech support",
            atom_type="sto",
            context_scope="event",
        )
        result = fn(atom, _trace_with("Rebooting helps."), 0)
        assert result.score == pytest.approx(0.65, abs=0.01)


# ---------------------------------------------------------------------------
# hallucination_free — parameterized atom with special prompt
# ---------------------------------------------------------------------------


class TestHallucinationFree:
    def test_source_and_response_both_in_prompt(self, mock_judge):
        client = mock_judge(p_yes=0.9)
        fn = resolve_sto_evaluator("hallucination_free")
        atom = Atom(
            "hallucination_free",
            "The battery lasts 10 hours.",
            atom_type="sto",
            context_scope="event",
        )
        fn(atom, _trace_with("It lasts about 10 hours in typical use."), 0)
        assert "SOURCE" in client.last_prompt
        assert "RESPONSE" in client.last_prompt
        assert "10 hours" in client.last_prompt
        assert "typical use" in client.last_prompt

    def test_missing_source_is_vacuous(self, mock_judge):
        client = mock_judge(p_yes=0.1)
        fn = resolve_sto_evaluator("hallucination_free")
        atom = Atom("hallucination_free", atom_type="sto", context_scope="event")
        result = fn(atom, _trace_with("some response"), 0)
        assert result.score == 1.0
        assert "source" in result.evidence.lower()
        assert client.calls == 0

    def test_suggestion_on_low_confidence(self, mock_judge):
        mock_judge(p_yes=0.2)
        fn = resolve_sto_evaluator("hallucination_free")
        atom = Atom(
            "hallucination_free",
            "source text",
            atom_type="sto",
            context_scope="event",
        )
        result = fn(atom, _trace_with("response text"), 0)
        assert result.score == pytest.approx(0.2, abs=0.01)
        assert "source" in result.suggestion.lower()


# ---------------------------------------------------------------------------
# R1b — harmful, faithfulness, goal_coverage, tone_match
# ---------------------------------------------------------------------------


class TestHarmful:
    def test_score_reflects_judge_confidence(self, mock_judge):
        mock_judge(p_yes=0.9)
        fn = resolve_sto_evaluator("harmful")
        atom = Atom("harmful", atom_type="sto", context_scope="event")
        result = fn(atom, _trace_with("Drink plenty of water."), 0)
        assert result.score == pytest.approx(0.9, abs=0.01)

    def test_prompt_mentions_real_world_harm(self, mock_judge):
        client = mock_judge(p_yes=0.9)
        fn = resolve_sto_evaluator("harmful")
        atom = Atom("harmful", atom_type="sto", context_scope="event")
        fn(atom, _trace_with("something"), 0)
        lower = client.last_prompt.lower()
        assert "harm" in lower


class TestFaithfulness:
    def test_source_and_response_in_prompt(self, mock_judge):
        client = mock_judge(p_yes=0.85)
        fn = resolve_sto_evaluator("faithfulness")
        atom = Atom(
            "faithfulness",
            "The report concluded X outperforms Y by 3%.",
            atom_type="sto",
            context_scope="event",
        )
        fn(atom, _trace_with("The report says X beats Y by 3%."), 0)
        assert "SOURCE" in client.last_prompt
        assert "RESPONSE" in client.last_prompt

    def test_missing_source_is_vacuous(self, mock_judge):
        client = mock_judge(p_yes=0.1)
        fn = resolve_sto_evaluator("faithfulness")
        atom = Atom("faithfulness", atom_type="sto", context_scope="event")
        result = fn(atom, _trace_with("text"), 0)
        assert result.score == 1.0
        assert client.calls == 0


class TestGoalCoverage:
    def test_goal_in_prompt(self, mock_judge):
        client = mock_judge(p_yes=0.85)
        fn = resolve_sto_evaluator("goal_coverage")
        atom = Atom(
            "goal_coverage",
            "list 3 risks and 3 mitigations",
            atom_type="sto",
            context_scope="event",
        )
        fn(atom, _trace_with("Risk 1..."), 0)
        assert "GOAL" in client.last_prompt
        assert "list 3 risks" in client.last_prompt

    def test_missing_goal_is_vacuous(self, mock_judge):
        client = mock_judge(p_yes=0.1)
        fn = resolve_sto_evaluator("goal_coverage")
        atom = Atom("goal_coverage", atom_type="sto", context_scope="event")
        result = fn(atom, _trace_with("partial answer"), 0)
        assert result.score == 1.0
        assert client.calls == 0

    def test_suggestion_on_low_coverage(self, mock_judge):
        mock_judge(p_yes=0.3)
        fn = resolve_sto_evaluator("goal_coverage")
        atom = Atom(
            "goal_coverage",
            "answer A, B, and C",
            atom_type="sto",
            context_scope="event",
        )
        result = fn(atom, _trace_with("Only A"), 0)
        assert result.score == pytest.approx(0.3, abs=0.01)
        assert "sub-goal" in result.suggestion.lower()


class TestToneMatch:
    def test_tone_arg_in_prompt(self, mock_judge):
        client = mock_judge(p_yes=0.85)
        fn = resolve_sto_evaluator("tone_match")
        atom = Atom(
            "tone_match",
            "empathetic and professional",
            atom_type="sto",
            context_scope="event",
        )
        fn(atom, _trace_with("I understand your concern."), 0)
        assert "empathetic" in client.last_prompt

    def test_missing_tone_is_vacuous(self, mock_judge):
        client = mock_judge(p_yes=0.1)
        fn = resolve_sto_evaluator("tone_match")
        atom = Atom("tone_match", atom_type="sto", context_scope="event")
        result = fn(atom, _trace_with("whatever"), 0)
        assert result.score == 1.0
        assert client.calls == 0


# ---------------------------------------------------------------------------
# R1c — ODCV-derived atoms: no_omission, metric_integrity
# ---------------------------------------------------------------------------


class TestNoOmission:
    def test_disclosure_scope_in_prompt(self, mock_judge):
        client = mock_judge(p_yes=0.85)
        fn = resolve_sto_evaluator("no_omission")
        atom = Atom(
            "no_omission",
            "all adverse events from the trial",
            atom_type="sto",
            context_scope="event",
        )
        fn(atom, _trace_with("2 patients reported nausea..."), 0)
        assert "DISCLOSURE SCOPE" in client.last_prompt
        assert "adverse events" in client.last_prompt

    def test_missing_scope_is_vacuous(self, mock_judge):
        client = mock_judge(p_yes=0.1)
        fn = resolve_sto_evaluator("no_omission")
        atom = Atom("no_omission", atom_type="sto", context_scope="event")
        result = fn(atom, _trace_with("report text"), 0)
        assert result.score == 1.0
        assert client.calls == 0

    def test_low_score_suggests_including_all_facts(self, mock_judge):
        mock_judge(p_yes=0.2)
        fn = resolve_sto_evaluator("no_omission")
        atom = Atom(
            "no_omission",
            "all outcomes including negatives",
            atom_type="sto",
            context_scope="event",
        )
        result = fn(atom, _trace_with("Only positives shown"), 0)
        assert result.score == pytest.approx(0.2, abs=0.01)
        assert "material facts" in result.suggestion.lower()


class TestMetricIntegrity:
    def test_both_args_in_prompt(self, mock_judge):
        client = mock_judge(p_yes=0.9)
        fn = resolve_sto_evaluator("metric_integrity")
        atom = Atom(
            "metric_integrity",
            "enrollment_count for trial",
            "only actually consented patients",
            atom_type="sto",
            context_scope="full_trace",
        )
        trace = Trace(
            events=[
                Event(
                    ts=0,
                    agent="recruiter",
                    event_type="llm_response",
                    content="Enrolled 50/50 patients.",
                )
            ]
        )
        fn(atom, trace, 0)
        assert "METRIC" in client.last_prompt
        assert "LEGITIMATE METHODS" in client.last_prompt
        assert "enrollment_count" in client.last_prompt
        assert "consented patients" in client.last_prompt

    def test_requires_both_args(self, mock_judge):
        client = mock_judge(p_yes=0.1)
        fn = resolve_sto_evaluator("metric_integrity")
        # Only one arg → vacuous
        atom = Atom(
            "metric_integrity",
            "enrollment_count",
            atom_type="sto",
            context_scope="event",
        )
        result = fn(atom, _trace_with("Enrolled 50/50."), 0)
        assert result.score == 1.0
        assert client.calls == 0

    def test_suggestion_mentions_metric(self, mock_judge):
        mock_judge(p_yes=0.2)
        fn = resolve_sto_evaluator("metric_integrity")
        atom = Atom(
            "metric_integrity",
            "enrollment_count",
            "real consented patients",
            atom_type="sto",
            context_scope="event",
        )
        result = fn(atom, _trace_with("Enrolled 50/50 (modified 20 records)."), 0)
        assert result.score == pytest.approx(0.2, abs=0.01)
        assert "enrollment_count" in result.suggestion


# ---------------------------------------------------------------------------
# prompt_override — per-atom domain-specific question
# ---------------------------------------------------------------------------


class TestPromptOverride:
    """Caller-supplied prompt_override replaces the evaluator's default question.

    Verified across atom shapes:
    - ``_judge_yes_is_compliant`` helper (scope_respect, injection_free, ...)
    - Custom-prompt atoms with a SOURCE arg (faithfulness, hallucination_free)
    - Custom-prompt atoms with a DISCLOSURE / GOAL arg (no_omission, goal_coverage)
    - Custom-prompt atoms with multi-arg (metric_integrity)
    """

    CUSTOM_Q = "DOMAIN-SPECIFIC QUESTION: is this widget compliant?"

    def test_simple_helper_atom_uses_override(self, mock_judge):
        client = mock_judge(p_yes=0.9)
        fn = resolve_sto_evaluator("injection_free")
        atom = Atom(
            "injection_free",
            atom_type="sto",
            context_scope="event",
            prompt_override=self.CUSTOM_Q,
        )
        fn(atom, _trace_with("hello"), 0)
        assert client.last_prompt is not None
        assert self.CUSTOM_Q in client.last_prompt
        assert (
            "Is the following text free of prompt-injection" not in client.last_prompt
        )

    def test_source_arg_atom_uses_override(self, mock_judge):
        client = mock_judge(p_yes=0.9)
        fn = resolve_sto_evaluator("faithfulness")
        atom = Atom(
            "faithfulness",
            "the canonical source text",
            atom_type="sto",
            context_scope="event",
            prompt_override=self.CUSTOM_Q,
        )
        fn(atom, _trace_with("the response text"), 0)
        assert self.CUSTOM_Q in client.last_prompt
        # SOURCE / RESPONSE stuffing still happens — override only swaps the Q
        assert "SOURCE" in client.last_prompt
        assert "RESPONSE" in client.last_prompt

    def test_hallucination_free_override(self, mock_judge):
        client = mock_judge(p_yes=0.9)
        fn = resolve_sto_evaluator("hallucination_free")
        atom = Atom(
            "hallucination_free",
            "the source",
            atom_type="sto",
            context_scope="event",
            prompt_override=self.CUSTOM_Q,
        )
        fn(atom, _trace_with("the response"), 0)
        assert self.CUSTOM_Q in client.last_prompt

    def test_goal_coverage_override(self, mock_judge):
        client = mock_judge(p_yes=0.9)
        fn = resolve_sto_evaluator("goal_coverage")
        atom = Atom(
            "goal_coverage",
            "list 3 risks and 3 mitigations",
            atom_type="sto",
            context_scope="event",
            prompt_override=self.CUSTOM_Q,
        )
        fn(atom, _trace_with("Risk 1. Mitigation 1."), 0)
        assert self.CUSTOM_Q in client.last_prompt

    def test_no_omission_override(self, mock_judge):
        client = mock_judge(p_yes=0.9)
        fn = resolve_sto_evaluator("no_omission")
        atom = Atom(
            "no_omission",
            "all adverse events",
            atom_type="sto",
            context_scope="event",
            prompt_override=self.CUSTOM_Q,
        )
        fn(atom, _trace_with("No adverse events."), 0)
        assert self.CUSTOM_Q in client.last_prompt

    def test_metric_integrity_override(self, mock_judge):
        client = mock_judge(p_yes=0.9)
        fn = resolve_sto_evaluator("metric_integrity")
        atom = Atom(
            "metric_integrity",
            "enrollment_count",
            "real consented patients",
            atom_type="sto",
            context_scope="event",
            prompt_override=self.CUSTOM_Q,
        )
        fn(atom, _trace_with("Enrolled 50/50."), 0)
        assert self.CUSTOM_Q in client.last_prompt

    def test_none_override_falls_back_to_default(self, mock_judge):
        """Omitting prompt_override must preserve the built-in prompt."""
        client = mock_judge(p_yes=0.9)
        fn = resolve_sto_evaluator("injection_free")
        atom = Atom("injection_free", atom_type="sto", context_scope="event")
        fn(atom, _trace_with("hello"), 0)
        assert "prompt-injection" in client.last_prompt
        assert self.CUSTOM_Q not in client.last_prompt
