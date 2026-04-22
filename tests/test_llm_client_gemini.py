"""Tests for ``GeminiLogprobClient`` (google-genai SDK adapter).

The adapter itself just shapes the generate_content config and parses
logprobs_result. We mock the SDK client so the tests don't need network
access or an API key.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sponsio.runtime.llm_client import GeminiLogprobClient


class _FakeGenaiClient:
    """Mimics ``genai.Client``: only ``.models.generate_content`` is called."""

    def __init__(self, response):
        self._response = response
        self.last_kwargs: dict | None = None
        self.models = self  # so ``client.models.generate_content`` works

    def generate_content(self, **kwargs):
        self.last_kwargs = kwargs
        return self._response


def _make_resp(top_pairs: list[tuple[str, float]] | None):
    """Build a fake genai response matching what logprob_completion parses."""
    if top_pairs is None:
        return SimpleNamespace(candidates=[])

    cand_objs = [SimpleNamespace(token=t, log_probability=lp) for t, lp in top_pairs]
    top0 = SimpleNamespace(candidates=cand_objs)
    lp_result = SimpleNamespace(top_candidates=[top0])
    cand = SimpleNamespace(logprobs_result=lp_result)
    return SimpleNamespace(candidates=[cand])


class TestGeminiLogprobClient:
    def test_happy_path_parses_top_candidates(self):
        pytest.importorskip("google.genai")
        resp = _make_resp([("yes", -0.05), ("no", -3.0)])
        fake = _FakeGenaiClient(resp)
        client = GeminiLogprobClient(fake, model_name="gemini-2.0-flash")

        result = client.logprob_completion("Is water wet?")
        assert result is not None
        assert result.first_token == "yes"
        assert result.top_logprobs == [("yes", -0.05), ("no", -3.0)]

    def test_caps_top_logprobs_at_2(self):
        """k > 2 hits Gemini's per-call quota; client must clamp."""
        pytest.importorskip("google.genai")
        resp = _make_resp([("yes", -0.1), ("no", -2.3)])
        fake = _FakeGenaiClient(resp)
        client = GeminiLogprobClient(fake, model_name="gemini-2.0-flash")

        client.logprob_completion("?", top_logprobs=20)
        # Inspect the config we sent
        cfg = fake.last_kwargs["config"]
        assert cfg.logprobs == 2
        assert cfg.response_logprobs is True

    def test_empty_candidates_returns_none(self):
        pytest.importorskip("google.genai")
        fake = _FakeGenaiClient(_make_resp(None))
        client = GeminiLogprobClient(fake, model_name="gemini-2.0-flash")
        assert client.logprob_completion("?") is None

    def test_missing_logprobs_result_returns_none(self):
        """Model returned content but no logprobs section — callers fall back."""
        pytest.importorskip("google.genai")
        cand = SimpleNamespace(logprobs_result=None)
        resp = SimpleNamespace(candidates=[cand])
        fake = _FakeGenaiClient(resp)
        client = GeminiLogprobClient(fake, model_name="gemini-2.0-flash")
        assert client.logprob_completion("?") is None

    def test_empty_top_candidates_returns_none(self):
        pytest.importorskip("google.genai")
        lp_result = SimpleNamespace(top_candidates=[])
        cand = SimpleNamespace(logprobs_result=lp_result)
        resp = SimpleNamespace(candidates=[cand])
        fake = _FakeGenaiClient(resp)
        client = GeminiLogprobClient(fake, model_name="gemini-2.0-flash")
        assert client.logprob_completion("?") is None

    def test_config_carries_temperature_and_max_tokens(self):
        pytest.importorskip("google.genai")
        resp = _make_resp([("yes", -0.1), ("no", -2.3)])
        fake = _FakeGenaiClient(resp)
        client = GeminiLogprobClient(fake, model_name="gemini-2.0-flash")
        client.logprob_completion("?", max_tokens=3)
        cfg = fake.last_kwargs["config"]
        assert cfg.max_output_tokens == 3
        assert cfg.temperature == 0.0
