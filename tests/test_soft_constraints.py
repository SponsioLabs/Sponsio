"""Tests for sto constraint system: StoFormula, catalog, auto-detection, integration."""

from __future__ import annotations

from sponsio.generation.nl_to_contract import classify_sto, parse_nl_unified
from sponsio.models.trace import Event, Trace
from sponsio.patterns.sto import StoFormula
from sponsio.patterns.sto_catalog import (
    content_prohibition_evaluator,
    format_evaluator,
    length_evaluator,
    pii_evaluator,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trace_with_content(content: str) -> Trace:
    return Trace(
        events=[Event(ts=0, agent="bot", event_type="message", content=content)]
    )


# ---------------------------------------------------------------------------
# PII evaluator
# ---------------------------------------------------------------------------


class TestPIIEvaluator:
    def test_detects_ssn(self):
        fn = pii_evaluator()
        result = fn(_trace_with_content("My SSN is 123-45-6789"))
        assert result.score == 0.0
        assert "ssn" in result.evidence.lower()

    def test_detects_credit_card(self):
        fn = pii_evaluator()
        result = fn(_trace_with_content("Card: 4111-1111-1111-1111"))
        assert result.score == 0.0
        assert "credit_card" in result.evidence.lower()

    def test_detects_email(self):
        fn = pii_evaluator()
        result = fn(_trace_with_content("Email me at john@example.com"))
        assert result.score == 0.0
        assert "email" in result.evidence.lower()

    def test_clean_text(self):
        fn = pii_evaluator()
        result = fn(_trace_with_content("The weather is nice today."))
        assert result.score == 1.0

    def test_empty_content(self):
        fn = pii_evaluator()
        result = fn(Trace(events=[]))
        assert result.score == 1.0

    def test_specific_fields(self):
        fn = pii_evaluator(fields=["ssn"])
        # Has email but we only check SSN
        result = fn(_trace_with_content("Email: john@example.com"))
        assert result.score == 1.0


# ---------------------------------------------------------------------------
# Length evaluator
# ---------------------------------------------------------------------------


class TestLengthEvaluator:
    def test_within_word_limit(self):
        fn = length_evaluator(max_words=10)
        result = fn(_trace_with_content("Short response"))
        assert result.score == 1.0

    def test_exceeds_word_limit(self):
        fn = length_evaluator(max_words=3)
        result = fn(_trace_with_content("This is a longer response than allowed"))
        assert result.score == 0.0

    def test_within_char_limit(self):
        fn = length_evaluator(max_chars=100)
        result = fn(_trace_with_content("Short"))
        assert result.score == 1.0

    def test_exceeds_char_limit(self):
        fn = length_evaluator(max_chars=5)
        result = fn(_trace_with_content("Too long"))
        assert result.score == 0.0


# ---------------------------------------------------------------------------
# Format evaluator
# ---------------------------------------------------------------------------


class TestFormatEvaluator:
    def test_valid_json(self):
        fn = format_evaluator("json")
        result = fn(_trace_with_content('{"key": "value"}'))
        assert result.score == 1.0

    def test_invalid_json(self):
        fn = format_evaluator("json")
        result = fn(_trace_with_content("not json at all"))
        assert result.score == 0.0

    def test_markdown_detected(self):
        fn = format_evaluator("markdown")
        result = fn(_trace_with_content("# Heading\n\n* bullet point"))
        assert result.score == 1.0

    def test_bullet_points(self):
        fn = format_evaluator("bullet_points")
        result = fn(_trace_with_content("- item 1\n- item 2\n- item 3"))
        assert result.score > 0.5


# ---------------------------------------------------------------------------
# Content prohibition evaluator
# ---------------------------------------------------------------------------


class TestContentProhibitionEvaluator:
    def test_prohibited_found(self):
        fn = content_prohibition_evaluator("competitor")
        result = fn(_trace_with_content("You should try our competitor's product"))
        assert result.score == 0.0

    def test_prohibited_not_found(self):
        fn = content_prohibition_evaluator("competitor")
        result = fn(_trace_with_content("Our product is the best"))
        assert result.score == 1.0

    def test_case_insensitive(self):
        fn = content_prohibition_evaluator("SECRET")
        result = fn(_trace_with_content("This is a secret message"))
        assert result.score == 0.0


# ---------------------------------------------------------------------------
# classify_sto
# ---------------------------------------------------------------------------


class TestClassifySoft:
    def test_pii_keywords(self):
        sc = classify_sto("response must not contain PII")
        assert isinstance(sc, StoFormula)
        assert sc.category == "pii"
        assert sc.requires_llm is False

    def test_length_keywords(self):
        sc = classify_sto("response must be under 100 words")
        assert sc.category == "length"
        assert sc.requires_llm is False

    def test_format_json(self):
        sc = classify_sto("output must be in JSON format")
        assert sc.category == "format"
        assert sc.requires_llm is False

    def test_tone_keywords(self):
        sc = classify_sto("response must be empathetic")
        assert sc.category == "tone"
        assert sc.requires_llm is True

    def test_relevance_keywords(self):
        sc = classify_sto("response must be relevant to financial services")
        assert sc.category == "relevance"
        assert sc.requires_llm is True

    def test_content_prohibition(self):
        sc = classify_sto("response must not contain pricing information")
        assert sc.category == "content_prohibition"
        assert sc.requires_llm is False

    def test_unknown_no_llm_returns_stub(self):
        sc = classify_sto("response must be creative and inspiring")
        assert sc.category == "custom"
        assert sc.requires_llm is True
        # Stub evaluator should return 0.5
        result = sc.evaluator_fn(Trace(events=[]))
        assert result.score == 0.5


# ---------------------------------------------------------------------------
# parse_nl_unified
# ---------------------------------------------------------------------------


class TestParseNLUnified:
    def test_hard_pattern_detected(self):
        result = parse_nl_unified("tool `check_policy` must precede `issue_refund`")
        assert result.is_det
        assert not result.is_sto
        assert result.ok

    def test_soft_pattern_detected(self):
        result = parse_nl_unified("response must be empathetic and professional")
        assert result.is_sto
        assert not result.is_det
        assert result.ok

    def test_pii_soft(self):
        result = parse_nl_unified(
            "response must not contain PII or personal information"
        )
        assert result.is_sto
        assert result.sto.category == "pii"

    def test_length_soft(self):
        result = parse_nl_unified("response must be under 50 words")
        assert result.is_sto
        assert result.sto.category == "length"


# ---------------------------------------------------------------------------
# Integration: LangGraphGuard with mixed det + sto
# ---------------------------------------------------------------------------


class TestMixedConstraints:
    def test_hard_and_soft_coexist(self):
        from sponsio.integrations.langgraph import LangGraphGuard

        guard = LangGraphGuard(
            contracts=[
                "tool `check_policy` must precede `issue_refund`",  # hard
                "response must not contain PII",  # sto
            ],
        )

        r = guard.pre_check("issue_refund")
        assert r.blocked

    def test_assumption_gates_enforcement(self):
        """An assumption paired with an enforcement gates only its own pair,
        not other contracts on the same agent."""
        from sponsio.integrations.langgraph import LangGraphGuard

        guard = LangGraphGuard(
            contracts=[
                {
                    "assumption": "tool `verify_identity` must precede `transfer_funds`",
                    "enforcement": "tool `check_balance` must precede `transfer_funds`",
                },
                {"enforcement": "response must not contain personal information"},
            ],
        )

        # Assumption not met → the paired enforcement is skipped, but
        # the escalation from the assumption failure is still reported.
        r = guard.pre_check("transfer_funds")
        assert len(r.all_violations) > 0

    def test_bare_string_in_contracts_kwarg(self):
        from sponsio.integrations.langgraph import LangGraphGuard

        # Bare strings inside contracts[] are treated as unconditional.
        guard = LangGraphGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"],
        )
        r = guard.pre_check("issue_refund")
        assert r.blocked

    def test_only_soft_no_crash(self):
        from sponsio.integrations.langgraph import LangGraphGuard

        guard = LangGraphGuard(
            contracts=[
                "response must be empathetic",
                "response must not contain PII",
            ],
        )

        r = guard.pre_check("some_tool")
        assert not r.blocked
