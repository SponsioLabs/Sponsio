"""Tests for sponsio/discovery/extractors/document.py."""

import json
from dataclasses import dataclass


from sponsio.discovery.extractors.document import DocumentExtractor


# ---------------------------------------------------------------------------
# Mock OpenAI client
# ---------------------------------------------------------------------------


@dataclass
class MockMessage:
    content: str


@dataclass
class MockChoice:
    message: MockMessage


@dataclass
class MockCompletion:
    choices: list[MockChoice]


class MockChatCompletions:
    def __init__(self, response_json: dict):
        self._response = response_json

    def create(self, **kwargs) -> MockCompletion:
        return MockCompletion(
            choices=[
                MockChoice(message=MockMessage(content=json.dumps(self._response)))
            ]
        )


class MockChat:
    def __init__(self, response_json: dict):
        self.completions = MockChatCompletions(response_json)


class MockOpenAIClient:
    def __init__(self, response_json: dict):
        self.chat = MockChat(response_json)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDocumentExtractor:
    def test_extracts_constraints_from_llm_response(self):
        response = {
            "constraints": [
                {
                    "nl": "Policy check before refund",
                    "pattern": "must_precede",
                    "args": ["check_policy", "issue_refund"],
                    "confidence": 0.95,
                    "source_quote": "All refunds require a policy check.",
                },
            ]
        }
        client = MockOpenAIClient(response)
        extractor = DocumentExtractor(client=client)
        results = extractor.extract("All refunds require a policy check.")

        assert len(results) == 1
        assert results[0].formula.pattern_name == "must_precede"
        assert results[0].confidence == 0.95
        assert results[0].provenance == "All refunds require a policy check."

    def test_multiple_constraints(self):
        response = {
            "constraints": [
                {
                    "nl": "Policy check before refund",
                    "pattern": "must_precede",
                    "args": ["check_policy", "issue_refund"],
                    "confidence": 0.9,
                    "source_quote": "check first",
                },
                {
                    "nl": "Refund at most 3 times",
                    "pattern": "rate_limit",
                    "args": ["issue_refund", 3],
                    "confidence": 0.8,
                    "source_quote": "limit refunds",
                },
            ]
        }
        client = MockOpenAIClient(response)
        extractor = DocumentExtractor(client=client)
        results = extractor.extract("Some policy document.")

        assert len(results) == 2
        patterns = {r.formula.pattern_name for r in results}
        assert patterns == {"must_precede", "rate_limit"}

    def test_unknown_pattern_is_filtered_out(self):
        """Unknown det patterns fail compilation and are filtered out.

        The unified extractor logs a warning with the list of available
        patterns, rather than silently converting to a sto constraint.
        """
        response = {
            "constraints": [
                {
                    "nl": "Something weird",
                    "pattern": "nonexistent_pattern",
                    "args": ["A", "B"],
                    "confidence": 0.5,
                    "source_quote": "...",
                },
            ]
        }
        client = MockOpenAIClient(response)
        extractor = DocumentExtractor(client=client)
        results = extractor.extract("Some doc.")
        # Unknown patterns fail compilation and are filtered out
        assert len(results) == 0

    def test_empty_document(self):
        client = MockOpenAIClient({"constraints": []})
        extractor = DocumentExtractor(client=client)
        results = extractor.extract("")
        assert results == []

    def test_llm_failure_returns_empty(self):
        class FailingClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kwargs):
                        raise RuntimeError("API down")

        extractor = DocumentExtractor(client=FailingClient())
        results = extractor.extract("Some doc.")
        assert results == []

    def test_extractor_field_set(self):
        response = {
            "constraints": [
                {
                    "nl": "A before B",
                    "pattern": "must_precede",
                    "args": ["A", "B"],
                    "confidence": 0.9,
                    "source_quote": "...",
                },
            ]
        }
        client = MockOpenAIClient(response)
        extractor = DocumentExtractor(client=client)
        results = extractor.extract("doc")
        assert results[0].extractor == "document"

    def test_new_patterns_supported(self):
        response = {
            "constraints": [
                {
                    "nl": "Transfer is idempotent",
                    "pattern": "idempotent",
                    "args": ["transfer"],
                    "confidence": 0.85,
                    "source_quote": "...",
                },
                {
                    "nl": "Cooldown on emails",
                    "pattern": "cooldown",
                    "args": ["send_email", 3],
                    "confidence": 0.75,
                    "source_quote": "...",
                },
            ]
        }
        client = MockOpenAIClient(response)
        extractor = DocumentExtractor(client=client)
        results = extractor.extract("doc")
        assert len(results) == 2
        patterns = {r.formula.pattern_name for r in results}
        assert "idempotent" in patterns
        assert "cooldown" in patterns
