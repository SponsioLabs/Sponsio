"""Tests for the unified LLM extraction layer.

Tests compilation, validation, and suggestion logic WITHOUT requiring
an actual LLM call (all LLM responses are mocked).
"""

from __future__ import annotations

import json

from sponsio.generation.llm_extraction import (
    UnifiedExtractor,
    _build_atom_vocabulary,
    clear_custom_atoms,
    compile_extraction,
    get_custom_atoms,
    register_atom,
    register_atoms,
    _suggest_pattern,
)
from sponsio.patterns.library import DetFormula
from sponsio.patterns.sto import StoFormula


# ---------------------------------------------------------------------------
# compile_extraction: det constraints
# ---------------------------------------------------------------------------


class TestCompileHard:
    """Test compilation of det constraint items from LLM JSON output."""

    def test_must_precede(self):
        item = {
            "type": "det",
            "pattern": "must_precede",
            "args": ["check_policy", "issue_refund"],
            "nl": "Must check policy before issuing refund",
            "confidence": 0.95,
        }
        result = compile_extraction(item)
        assert result.ok
        assert isinstance(result.compiled, DetFormula)
        assert result.compiled.pattern_name == "must_precede"
        assert result.confidence == 0.95

    def test_rate_limit(self):
        item = {
            "type": "det",
            "pattern": "rate_limit",
            "args": ["issue_refund", 3],
            "nl": "At most 3 refunds per session",
            "confidence": 0.9,
        }
        result = compile_extraction(item)
        assert result.ok
        assert result.compiled.pattern_name == "rate_limit"

    def test_rate_limit_string_count(self):
        """Numeric args passed as strings should be auto-converted."""
        item = {
            "type": "det",
            "pattern": "rate_limit",
            "args": ["issue_refund", "3"],
            "nl": "At most 3 refunds",
            "confidence": 0.8,
        }
        result = compile_extraction(item)
        assert result.ok
        assert result.compiled.pattern_name == "rate_limit"

    def test_mutual_exclusion(self):
        item = {
            "type": "det",
            "pattern": "mutual_exclusion",
            "args": ["approve", "reject"],
            "nl": "Approve and reject are mutually exclusive",
            "confidence": 0.9,
        }
        result = compile_extraction(item)
        assert result.ok
        assert result.compiled.pattern_name == "mutual_exclusion"

    def test_idempotent(self):
        item = {
            "type": "det",
            "pattern": "idempotent",
            "args": ["deploy"],
            "nl": "Deploy must be idempotent",
            "confidence": 0.85,
        }
        result = compile_extraction(item)
        assert result.ok

    def test_no_reversal(self):
        item = {
            "type": "det",
            "pattern": "no_reversal",
            "args": ["approve_refund", "deny_refund"],
            "nl": "Cannot deny after approving",
            "confidence": 0.9,
        }
        result = compile_extraction(item)
        assert result.ok

    def test_cooldown(self):
        item = {
            "type": "det",
            "pattern": "cooldown",
            "args": ["send_email", 3],
            "nl": "Cooldown of 3 steps between emails",
            "confidence": 0.7,
        }
        result = compile_extraction(item)
        assert result.ok

    def test_deadline(self):
        item = {
            "type": "det",
            "pattern": "deadline",
            "args": ["receive_complaint", "acknowledge", 5],
            "nl": "Acknowledge within 5 steps of complaint",
            "confidence": 0.8,
        }
        result = compile_extraction(item)
        assert result.ok

    def test_bounded_retry(self):
        item = {
            "type": "det",
            "pattern": "bounded_retry",
            "args": ["api_call", 5],
            "nl": "At most 5 retries for API call",
            "confidence": 0.85,
        }
        result = compile_extraction(item)
        assert result.ok

    def test_segregation_of_duty(self):
        item = {
            "type": "det",
            "pattern": "segregation_of_duty",
            "args": ["review", "approve"],
            "nl": "Reviewer and approver must be different",
            "confidence": 0.9,
        }
        result = compile_extraction(item)
        assert result.ok

    def test_requires_permission(self):
        item = {
            "type": "det",
            "pattern": "requires_permission",
            "args": ["delete_user", "admin"],
            "nl": "delete_user requires admin permission",
            "confidence": 0.95,
        }
        result = compile_extraction(item)
        assert result.ok

    def test_unknown_pattern_fails(self):
        item = {
            "type": "det",
            "pattern": "nonexistent_pattern",
            "args": ["a", "b"],
            "nl": "Something unknown",
            "confidence": 0.5,
        }
        result = compile_extraction(item)
        assert not result.ok
        assert "Unknown pattern" in result.error
        assert "nonexistent_pattern" in result.error

    def test_wrong_args_count_fails(self):
        """Pattern called with wrong number of args should fail gracefully."""
        item = {
            "type": "det",
            "pattern": "must_precede",
            "args": ["only_one_arg"],
            "nl": "Incomplete constraint",
            "confidence": 0.5,
        }
        result = compile_extraction(item)
        assert not result.ok
        assert "Compilation failed" in result.error

    def test_empty_args(self):
        item = {
            "type": "det",
            "pattern": "must_precede",
            "args": [],
            "nl": "No args provided",
            "confidence": 0.3,
        }
        result = compile_extraction(item)
        assert not result.ok


# ---------------------------------------------------------------------------
# compile_extraction: sto constraints
# ---------------------------------------------------------------------------


class TestCompileSoft:
    """Test compilation of sto constraint items from LLM JSON output."""

    def test_pii(self):
        item = {
            "type": "sto",
            "category": "pii",
            "params": {},
            "nl": "Response must not contain PII",
            "confidence": 0.95,
        }
        result = compile_extraction(item)
        assert result.ok
        assert isinstance(result.compiled, StoFormula)
        assert result.compiled.category == "pii"
        assert not result.compiled.requires_llm

    def test_pii_with_fields(self):
        item = {
            "type": "sto",
            "category": "pii",
            "params": {"fields": ["ssn", "email"]},
            "nl": "No SSN or email in response",
            "confidence": 0.9,
        }
        result = compile_extraction(item)
        assert result.ok
        assert result.compiled.category == "pii"

    def test_length(self):
        item = {
            "type": "sto",
            "category": "length",
            "params": {"max_words": 100},
            "nl": "Response under 100 words",
            "confidence": 0.85,
        }
        result = compile_extraction(item)
        assert result.ok
        assert result.compiled.category == "length"

    def test_format_json(self):
        item = {
            "type": "sto",
            "category": "format",
            "params": {"format": "json"},
            "nl": "Output must be valid JSON",
            "confidence": 0.9,
        }
        result = compile_extraction(item)
        assert result.ok
        assert result.compiled.category == "format"

    def test_tone(self):
        item = {
            "type": "sto",
            "category": "tone",
            "params": {"desired_tone": "empathetic"},
            "nl": "Response must be empathetic",
            "confidence": 0.7,
        }
        result = compile_extraction(item)
        assert result.ok
        assert result.compiled.category == "tone"
        assert result.compiled.requires_llm

    def test_relevance(self):
        item = {
            "type": "sto",
            "category": "relevance",
            "params": {"topic": "customer service"},
            "nl": "Response must be relevant to customer service",
            "confidence": 0.75,
        }
        result = compile_extraction(item)
        assert result.ok
        assert result.compiled.requires_llm

    def test_content_prohibition(self):
        item = {
            "type": "sto",
            "category": "content_prohibition",
            "params": {"prohibited": "competitor"},
            "nl": "Do not mention competitors",
            "confidence": 0.85,
        }
        result = compile_extraction(item)
        assert result.ok

    def test_content_prohibition_missing_param(self):
        item = {
            "type": "sto",
            "category": "content_prohibition",
            "params": {},
            "nl": "Don't mention something",
            "confidence": 0.5,
        }
        result = compile_extraction(item)
        assert not result.ok
        assert "prohibited" in result.error

    def test_unknown_category_falls_back_to_custom(self):
        item = {
            "type": "sto",
            "category": "unknown_category",
            "params": {},
            "nl": "Some custom constraint",
            "confidence": 0.5,
        }
        result = compile_extraction(item)
        assert result.ok
        assert result.compiled.category == "custom"
        assert result.compiled.requires_llm

    def test_default_type_is_hard(self):
        """Items without a 'type' field default to hard."""
        item = {
            "pattern": "idempotent",
            "args": ["deploy"],
            "nl": "Deploy once",
            "confidence": 0.8,
        }
        result = compile_extraction(item)
        assert result.ok
        assert result.constraint_type == "det"


# ---------------------------------------------------------------------------
# Suggestion engine
# ---------------------------------------------------------------------------


class TestSuggestion:
    def test_suggests_known_patterns(self):
        suggestion = _suggest_pattern("must precede something", "wrong arguments")
        assert "must_precede" in suggestion or "args" in suggestion.lower()

    def test_suggests_pattern_list_for_unknown(self):
        suggestion = _suggest_pattern("do something weird", "generic error")
        assert "must_precede" in suggestion
        assert "rate_limit" in suggestion


# ---------------------------------------------------------------------------
# UnifiedExtractor with mock LLM
# ---------------------------------------------------------------------------


class MockMessage:
    def __init__(self, content: str):
        self.content = content


class MockChoice:
    def __init__(self, content: str):
        self.message = MockMessage(content)


class MockCompletion:
    def __init__(self, content: str):
        self.choices = [MockChoice(content)]


class MockChatCompletions:
    def __init__(self, response_data: dict):
        self._response = json.dumps(response_data)

    def create(self, **kwargs):
        return MockCompletion(self._response)


class MockChat:
    def __init__(self, response_data: dict):
        self.completions = MockChatCompletions(response_data)


class MockOpenAIClient:
    def __init__(self, response_data: dict):
        self.chat = MockChat(response_data)


class TestUnifiedExtractorNL:
    """Test UnifiedExtractor.extract_from_nl with mocked LLM."""

    def test_single_hard_constraint(self):
        mock_response = {
            "constraints": [
                {
                    "type": "det",
                    "pattern": "must_precede",
                    "args": ["check_policy", "issue_refund"],
                    "nl": "Must check policy before refund",
                    "confidence": 0.95,
                    "source_quote": "",
                }
            ]
        }
        client = MockOpenAIClient(mock_response)
        extractor = UnifiedExtractor(client=client)
        results = extractor.extract_from_nl("check policy before refund")
        assert len(results) == 1
        assert results[0].ok
        assert results[0].compiled.pattern_name == "must_precede"

    def test_mixed_hard_and_soft(self):
        mock_response = {
            "constraints": [
                {
                    "type": "det",
                    "pattern": "rate_limit",
                    "args": ["issue_refund", 3],
                    "nl": "At most 3 refunds",
                    "confidence": 0.9,
                    "source_quote": "",
                },
                {
                    "type": "sto",
                    "category": "pii",
                    "params": {},
                    "nl": "No PII in response",
                    "confidence": 0.85,
                    "source_quote": "",
                },
            ]
        }
        client = MockOpenAIClient(mock_response)
        extractor = UnifiedExtractor(client=client)
        results = extractor.extract_from_nl("at most 3 refunds, no PII")
        assert len(results) == 2
        hard = [r for r in results if r.constraint_type == "det"]
        sto_list = [r for r in results if r.constraint_type == "sto"]
        assert len(hard) == 1
        assert len(sto_list) == 1

    def test_empty_input_returns_empty(self):
        client = MockOpenAIClient({"constraints": []})
        extractor = UnifiedExtractor(client=client)
        results = extractor.extract_from_nl("")
        assert results == []

    def test_confidence_filtering(self):
        mock_response = {
            "constraints": [
                {
                    "type": "det",
                    "pattern": "idempotent",
                    "args": ["deploy"],
                    "nl": "Deploy once",
                    "confidence": 0.3,
                    "source_quote": "",
                },
                {
                    "type": "det",
                    "pattern": "must_precede",
                    "args": ["a", "b"],
                    "nl": "A before B",
                    "confidence": 0.9,
                    "source_quote": "",
                },
            ]
        }
        client = MockOpenAIClient(mock_response)
        extractor = UnifiedExtractor(client=client)
        # With min_confidence=0.5, the low-confidence one should be filtered
        results = extractor._extract("nl", "test", min_confidence=0.5)
        assert len(results) == 1
        assert results[0].confidence == 0.9


class TestUnifiedExtractorDocument:
    """Test UnifiedExtractor.extract_from_document with mocked LLM."""

    def test_document_extraction(self):
        mock_response = {
            "constraints": [
                {
                    "type": "det",
                    "pattern": "must_precede",
                    "args": ["verify_identity", "transfer_funds"],
                    "nl": "Must verify identity before transferring funds",
                    "confidence": 0.95,
                    "source_quote": "All transfers require identity verification.",
                },
                {
                    "type": "sto",
                    "category": "content_prohibition",
                    "params": {"prohibited": "competitor"},
                    "nl": "Do not mention competitors",
                    "confidence": 0.8,
                    "source_quote": "Agents must not reference competing products.",
                },
            ]
        }
        client = MockOpenAIClient(mock_response)
        extractor = UnifiedExtractor(client=client)
        results = extractor.extract_from_document("policy document text...")
        assert len(results) == 2
        assert results[0].ok
        assert results[1].ok
        assert results[0].source_quote == "All transfers require identity verification."

    def test_with_tool_inventory(self):
        """Tool inventory should be included in the prompt context."""
        mock_response = {
            "constraints": [
                {
                    "type": "det",
                    "pattern": "must_precede",
                    "args": ["check_policy", "issue_refund"],
                    "nl": "Check policy first",
                    "confidence": 0.95,
                    "source_quote": "",
                }
            ]
        }
        client = MockOpenAIClient(mock_response)
        extractor = UnifiedExtractor(client=client)
        results = extractor.extract_from_document(
            "Refunds require policy check.",
            tool_inventory=[
                {"name": "check_policy", "docstring": "Check eligibility"},
                {"name": "issue_refund", "docstring": "Process refund"},
            ],
        )
        assert len(results) == 1
        assert results[0].ok


class TestUnifiedExtractorCode:
    """Test UnifiedExtractor.extract_from_code with mocked LLM."""

    def test_code_extraction(self):
        mock_response = {
            "constraints": [
                {
                    "type": "det",
                    "pattern": "must_precede",
                    "args": ["validate_input", "execute_query"],
                    "nl": "Must validate input before executing query",
                    "confidence": 0.85,
                    "source_quote": "",
                },
                {
                    "type": "det",
                    "pattern": "rate_limit",
                    "args": ["execute_query", 10],
                    "nl": "Limit queries to 10 per session",
                    "confidence": 0.6,
                    "source_quote": "",
                },
            ]
        }
        client = MockOpenAIClient(mock_response)
        extractor = UnifiedExtractor(client=client)
        results = extractor.extract_from_code(
            tool_inventory=[
                {"name": "validate_input", "docstring": "Validate user input"},
                {"name": "execute_query", "docstring": "Run database query"},
            ],
            source_snippet="def execute_query(sql): ...",
        )
        assert len(results) == 2
        assert all(r.ok for r in results)

    def test_extract_compiled_convenience(self):
        mock_response = {
            "constraints": [
                {
                    "type": "det",
                    "pattern": "idempotent",
                    "args": ["deploy"],
                    "nl": "Deploy once",
                    "confidence": 0.9,
                    "source_quote": "",
                },
                {
                    "type": "sto",
                    "category": "pii",
                    "params": {},
                    "nl": "No PII",
                    "confidence": 0.8,
                    "source_quote": "",
                },
            ]
        }
        client = MockOpenAIClient(mock_response)
        extractor = UnifiedExtractor(client=client)
        hard, sto = extractor.extract_compiled("nl", "test")
        assert len(hard) == 1
        assert len(sto) == 1
        assert isinstance(hard[0], DetFormula)
        assert isinstance(sto[0], StoFormula)


# ---------------------------------------------------------------------------
# LLM error resilience
# ---------------------------------------------------------------------------


class MockErrorCompletions:
    def create(self, **kwargs):
        raise RuntimeError("API rate limited")


class MockErrorChat:
    completions = MockErrorCompletions()


class MockErrorClient:
    chat = MockErrorChat()


class TestLLMErrorResilience:
    def test_llm_failure_returns_empty(self):
        extractor = UnifiedExtractor(client=MockErrorClient())
        results = extractor.extract_from_nl("some constraint")
        assert results == []

    def test_llm_invalid_json_returns_empty(self):
        class BadJSONMessage:
            content = "not valid json at all"

        class BadJSONChoice:
            message = BadJSONMessage()

        class BadJSONCompletion:
            choices = [BadJSONChoice()]

        class BadJSONCompletions:
            def create(self, **kwargs):
                return BadJSONCompletion()

        class BadJSONChat:
            completions = BadJSONCompletions()

        class BadJSONClient:
            chat = BadJSONChat()

        extractor = UnifiedExtractor(client=BadJSONClient())
        results = extractor.extract_from_nl("some constraint")
        assert results == []


# ---------------------------------------------------------------------------
# Extensible Atom vocabulary
# ---------------------------------------------------------------------------


class TestAtomRegistry:
    """Test custom atom registration and prompt inclusion."""

    def setup_method(self):
        clear_custom_atoms()

    def teardown_method(self):
        clear_custom_atoms()

    def test_register_single_atom(self):
        register_atom("latency(tool_name)", "response latency in ms")
        atoms = get_custom_atoms()
        assert len(atoms) == 1
        assert atoms[0] == ("latency(tool_name)", "response latency in ms")

    def test_register_atoms_dict(self):
        register_atoms(
            {
                "latency(tool_name)": "response latency in ms",
                "user_role(role)": "current user's role string",
            }
        )
        atoms = get_custom_atoms()
        assert len(atoms) == 2

    def test_register_atoms_list(self):
        register_atoms(
            [
                ("latency(tool_name)", "response latency in ms"),
                ("user_role(role)", "current user's role string"),
            ]
        )
        atoms = get_custom_atoms()
        assert len(atoms) == 2

    def test_clear_custom_atoms(self):
        register_atom("latency(tool_name)", "test")
        assert len(get_custom_atoms()) == 1
        clear_custom_atoms()
        assert len(get_custom_atoms()) == 0

    def test_custom_atoms_in_prompt(self):
        register_atom("latency(tool_name)", "response latency in ms")
        vocab = _build_atom_vocabulary()
        assert "latency(tool_name)" in vocab
        assert "response latency in ms" in vocab
        assert "Custom atoms" in vocab

    def test_no_custom_section_when_empty(self):
        vocab = _build_atom_vocabulary()
        assert "Custom atoms" not in vocab
        # Built-in atoms should still be present
        assert "called(tool_name)" in vocab

    def test_custom_atoms_accumulate(self):
        register_atom("a()", "first")
        register_atom("b()", "second")
        register_atoms({"c()": "third"})
        assert len(get_custom_atoms()) == 3


# ---------------------------------------------------------------------------
# LLM fallback in parse_nl_unified
# ---------------------------------------------------------------------------


class TestParseNLUnifiedLLMFallback:
    """Test that parse_nl_unified falls back to LLM when rule-based fails."""

    def test_rule_based_success_skips_llm(self):
        """When rule-based parsing succeeds, LLM is never called."""
        from sponsio.generation.nl_to_contract import parse_nl_unified

        # This matches the keyword rule for must_precede
        result = parse_nl_unified(
            "tool `check_policy` must precede `issue_refund`",
            llm_extractor="should_not_be_called",  # not a real extractor
        )
        assert result.is_det
        assert result.hard.pattern_name == "must_precede"

    def test_llm_fallback_hard(self):
        """When rule-based fails, LLM fallback produces a det constraint."""
        from sponsio.generation.nl_to_contract import parse_nl_unified

        mock_response = {
            "constraints": [
                {
                    "type": "det",
                    "pattern": "must_precede",
                    "args": ["validate", "execute"],
                    "nl": "validate before execute",
                    "confidence": 0.9,
                    "source_quote": "",
                }
            ]
        }
        client = MockOpenAIClient(mock_response)
        extractor = UnifiedExtractor(client=client)

        # NL text that does NOT match any keyword rule
        result = parse_nl_unified(
            "the validate step is a prerequisite for execute",
            llm_extractor=extractor,
        )
        assert result.is_det
        assert result.hard.pattern_name == "must_precede"

    def test_llm_fallback_soft(self):
        """When rule-based fails and LLM returns sto, we get a sto result."""
        from sponsio.generation.nl_to_contract import parse_nl_unified

        mock_response = {
            "constraints": [
                {
                    "type": "sto",
                    "category": "tone",
                    "params": {"desired_tone": "empathetic"},
                    "nl": "be kind in responses",
                    "confidence": 0.8,
                    "source_quote": "",
                }
            ]
        }
        client = MockOpenAIClient(mock_response)
        extractor = UnifiedExtractor(client=client)

        # Text that doesn't match hard keywords but DOES match sto keywords.
        # However the LLM should take priority over sto keyword fallback.
        result = parse_nl_unified(
            "be kind in responses",
            llm_extractor=extractor,
        )
        assert result.is_sto
        # LLM classified as tone with empathetic
        assert result.sto.category == "tone"

    def test_llm_failure_falls_through_to_soft_keywords(self):
        """When LLM fails, we still fall through to sto keyword classification."""
        from sponsio.generation.nl_to_contract import parse_nl_unified

        extractor = UnifiedExtractor(client=MockErrorClient())

        # Text that fails both rule-based hard AND LLM, but matches sto keywords
        result = parse_nl_unified(
            "response must not contain PII",
            llm_extractor=extractor,
        )
        assert result.is_sto
        assert result.sto.category == "pii"

    def test_llm_no_results_falls_through_to_soft_keywords(self):
        """When LLM returns empty constraints, fall through to sto."""
        from sponsio.generation.nl_to_contract import parse_nl_unified

        mock_response = {"constraints": []}
        client = MockOpenAIClient(mock_response)
        extractor = UnifiedExtractor(client=client)

        result = parse_nl_unified(
            "response must be in json format",
            llm_extractor=extractor,
        )
        assert result.is_sto
        assert result.sto.category == "format"


# ---------------------------------------------------------------------------
# config_to_system with LLM extractor
# ---------------------------------------------------------------------------


class TestConfigToSystemLLMFallback:
    """Test that config_to_system passes llm_extractor through."""

    def test_config_with_llm_extractor(self, tmp_path):
        from sponsio.config import config_to_system, load_config

        f = tmp_path / "sponsio.yaml"
        f.write_text(
            """
version: "1"
agents:
  bot:
    contracts:
      - E: "tool `A` must precede `B`"
      - E: "some complex constraint LLM should handle"
"""
        )
        config = load_config(f)

        # Mock extractor that handles the second line
        mock_response = {
            "constraints": [
                {
                    "type": "det",
                    "pattern": "rate_limit",
                    "args": ["query", 5],
                    "nl": "some complex constraint",
                    "confidence": 0.9,
                    "source_quote": "",
                }
            ]
        }
        client = MockOpenAIClient(mock_response)
        extractor = UnifiedExtractor(client=client)

        system = config_to_system(config, llm_extractor=extractor)
        # Each YAML contract entry becomes one Contract, so there are 2.
        assert len(system.contracts) == 2
