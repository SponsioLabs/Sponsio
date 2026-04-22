"""Tests for P2 response-content det patterns: max_length, no_pii, no_keywords.

These were previously sto (closure-wrapped evaluators) but were reclassified
to det in P2 — they're precisely computable from the response content, no
LLM judging needed. See .claude/commands/sto-refactor.md § P2.
"""

from __future__ import annotations

import pytest

from sponsio.formulas.evaluator import evaluate
from sponsio.generation.nl_to_contract import parse_nl_unified
from sponsio.models.trace import Event, Trace
from sponsio.patterns.library import max_length, no_keywords, no_pii
from sponsio.tracer.grounding import collect_content_atoms, ground


def _resp(content: str) -> Trace:
    return Trace(
        events=[Event(ts=0, agent="bot", event_type="llm_response", content=content)]
    )


def _eval(formula, trace):
    """Ground + evaluate a det formula against a trace with response content."""
    cat = collect_content_atoms([formula])
    valuations = ground(trace, content_atoms=cat)
    return evaluate(
        formula.formula if hasattr(formula, "formula") else formula, valuations
    )


# ---------------------------------------------------------------------------
# max_length
# ---------------------------------------------------------------------------


class TestMaxLength:
    def test_within_word_limit(self):
        f = max_length(max_words=5)
        assert _eval(f, _resp("one two three four five")) is True

    def test_exceeds_word_limit(self):
        f = max_length(max_words=5)
        assert _eval(f, _resp("one two three four five six seven")) is False

    def test_within_char_limit(self):
        f = max_length(max_chars=20)
        assert _eval(f, _resp("hello world")) is True

    def test_exceeds_char_limit(self):
        f = max_length(max_chars=10)
        assert _eval(f, _resp("this is longer than ten")) is False

    def test_both_limits_must_hold(self):
        f = max_length(max_words=5, max_chars=100)
        # 6 words → violates word limit even though char limit is fine
        assert _eval(f, _resp("one two three four five six")) is False

    def test_requires_at_least_one_limit(self):
        with pytest.raises(ValueError):
            max_length()

    def test_vacuous_on_non_response_event(self):
        # No llm_response events → response_words defaults to 0, formula holds
        f = max_length(max_words=5)
        trace = Trace(
            events=[Event(ts=0, agent="bot", event_type="tool_call", tool="foo")]
        )
        assert _eval(f, trace) is True


# ---------------------------------------------------------------------------
# no_pii
# ---------------------------------------------------------------------------


class TestNoPII:
    def test_clean_response_passes(self):
        f = no_pii()
        assert _eval(f, _resp("Your account is secure.")) is True

    def test_ssn_leak_caught(self):
        f = no_pii()
        assert _eval(f, _resp("Your SSN is 123-45-6789.")) is False

    def test_credit_card_leak_caught(self):
        f = no_pii()
        assert _eval(f, _resp("Charge 4532 1234 5678 9010 today.")) is False

    def test_email_leak_caught(self):
        f = no_pii()
        assert _eval(f, _resp("Contact me at test@example.com.")) is False

    def test_phone_leak_caught(self):
        f = no_pii()
        assert _eval(f, _resp("Call 555-123-4567 for help.")) is False

    def test_subset_of_fields_only_checks_those(self):
        f = no_pii(["ssn"])
        # SSN caught
        assert _eval(f, _resp("Your SSN is 123-45-6789.")) is False
        # Email is ignored when only checking SSN
        assert _eval(f, _resp("Contact test@example.com.")) is True

    def test_unknown_field_raises(self):
        with pytest.raises(ValueError, match="unknown PII field"):
            no_pii(["passport"])


# ---------------------------------------------------------------------------
# no_keywords
# ---------------------------------------------------------------------------


class TestNoKeywords:
    def test_clean_response_passes(self):
        f = no_keywords(["password", "secret"])
        assert _eval(f, _resp("Hello world")) is True

    def test_keyword_match_caught(self):
        f = no_keywords(["password", "secret"])
        assert _eval(f, _resp("Your password is abc123")) is False

    def test_case_insensitive(self):
        f = no_keywords(["PASSWORD"])
        assert _eval(f, _resp("Your password is abc123")) is False

    def test_word_boundary_respected(self):
        # "passwordless" contains "password" as substring but not as whole word
        f = no_keywords(["password"])
        assert _eval(f, _resp("Use passwordless authentication.")) is True

    def test_empty_list_raises(self):
        with pytest.raises(ValueError):
            no_keywords([])


# ---------------------------------------------------------------------------
# NL parser routing
# ---------------------------------------------------------------------------


class TestNLRouting:
    def test_response_length_words_routes_to_det(self):
        r = parse_nl_unified("response under 200 words")
        assert r.is_det
        assert r.hard.pattern_name == "max_length"

    def test_response_length_chars_routes_to_det(self):
        r = parse_nl_unified("output at most 500 chars")
        assert r.is_det
        assert r.hard.pattern_name == "max_length"

    def test_response_pii_routes_to_det(self):
        r = parse_nl_unified("response must not contain PII")
        assert r.is_det
        assert r.hard.pattern_name == "no_pii"

    def test_output_pii_routes_to_det(self):
        r = parse_nl_unified("output must not contain personal information")
        assert r.is_det
        assert r.hard.pattern_name == "no_pii"

    def test_response_keywords_routes_to_det(self):
        r = parse_nl_unified("response must not mention the words `password, secret`")
        assert r.is_det
        assert r.hard.pattern_name == "no_keywords"

    def test_tone_still_routes_to_sto(self):
        # Sanity: P2 migration must NOT affect genuine sto rules.
        r = parse_nl_unified("response must be empathetic")
        assert r.is_sto
        assert r.sto.category == "tone"

    def test_relevance_still_routes_to_sto(self):
        r = parse_nl_unified("response must be relevant to the topic")
        assert r.is_sto
        assert r.sto.category == "relevance"

    def test_must_precede_still_routes_to_det(self):
        # Sanity: non-response NL should still match its original det pattern.
        r = parse_nl_unified("tool `A` must precede `B`")
        assert r.is_det
        assert r.hard.pattern_name == "must_precede"
