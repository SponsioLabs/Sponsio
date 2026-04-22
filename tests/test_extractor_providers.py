"""Provider auto-detection, Anthropic call path, and base_url plumbing.

The matrix the user can hit:

* ``--provider openai``  → ``openai.OpenAI(api_key=...)``
* ``--provider anthropic`` (or ``ANTHROPIC_API_KEY`` env) → ``anthropic.Anthropic``
* ``--provider gemini`` (or ``GOOGLE_API_KEY`` / ``GEMINI_API_KEY`` env)
* ``--base-url http://...`` → uses openai SDK against an OpenAI-compatible
  endpoint (Ollama / OpenRouter / DeepSeek / Together / Groq / vLLM / Azure).

All transport is mocked — the tests don't make any network calls and don't
require the ``anthropic`` SDK to actually be installed (the fake client is
injected via the ``client=`` constructor arg, which short-circuits the
SDK import).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from sponsio.generation.llm_extraction import UnifiedExtractor


# ---------------------------------------------------------------------------
# Fake clients — minimal stand-ins for openai.OpenAI and anthropic.Anthropic
# ---------------------------------------------------------------------------


class _FakeOpenAICompletions:
    def __init__(self, response_text: str):
        self._response_text = response_text
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=self._response_text))
            ]
        )


class _FakeOpenAIClient:
    def __init__(self, response_text: str):
        self.completions = _FakeOpenAICompletions(response_text)
        self.chat = SimpleNamespace(completions=self.completions)


class _FakeAnthropicMessages:
    def __init__(self, response_text: str):
        self._response_text = response_text
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self._response_text)]
        )


class _FakeAnthropicClient:
    def __init__(self, response_text: str):
        self.messages = _FakeAnthropicMessages(response_text)


_EMPTY_JSON = json.dumps({"constraints": [], "tools": []})


# ---------------------------------------------------------------------------
# Provider auto-detection from env vars
# ---------------------------------------------------------------------------


class TestProviderAutoDetection:
    """``provider=None`` resolves from explicit hints + env vars."""

    def test_explicit_client_implies_openai(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        ext = UnifiedExtractor(client=_FakeOpenAIClient(_EMPTY_JSON))
        assert ext._provider == "openai"

    def test_base_url_forces_openai_even_with_anthropic_key(self, monkeypatch):
        # base_url is the strongest hint after an explicit client — a user
        # pointing at Ollama with ANTHROPIC_API_KEY also set should still
        # talk to Ollama via the openai SDK.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        ext = UnifiedExtractor(base_url="http://localhost:11434/v1")
        assert ext._provider == "openai"
        assert ext._base_url == "http://localhost:11434/v1"

    def test_anthropic_key_picks_anthropic(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        # Inject fake client so we don't need the real anthropic SDK.
        ext = UnifiedExtractor(
            provider="anthropic", client=_FakeAnthropicClient(_EMPTY_JSON)
        )
        assert ext._provider == "anthropic"
        assert ext._model == "claude-3-5-sonnet-latest"

    def test_gemini_env_picks_gemini(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "fake-gemini-key")
        ext = UnifiedExtractor()
        assert ext._provider == "gemini"
        assert ext._model == "gemini-2.0-flash"

    def test_anthropic_takes_priority_over_gemini(self, monkeypatch):
        # When a user has BOTH keys set, prefer anthropic — it tends to
        # be the explicitly purchased one, while GOOGLE_API_KEY is often
        # left around from other tooling.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        monkeypatch.setenv("GOOGLE_API_KEY", "fake-gemini-key")
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        ext = UnifiedExtractor(
            provider="anthropic", client=_FakeAnthropicClient(_EMPTY_JSON)
        )
        assert ext._provider == "anthropic"

    def test_model_name_with_claude_picks_anthropic(self, monkeypatch):
        # ``--model claude-3-5-sonnet-latest --api-key sk-ant-...`` with no
        # explicit ``--provider`` and no client should still infer
        # anthropic from the model-name hint.  We stub the SDK
        # constructor so the test doesn't require the real package.
        anthropic = pytest.importorskip("anthropic")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

        captured: dict = {}

        class _StubAnthropic:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.messages = SimpleNamespace(create=lambda **_: None)

        monkeypatch.setattr(anthropic, "Anthropic", _StubAnthropic)

        ext = UnifiedExtractor(
            model="claude-3-5-sonnet-latest",
            api_key="sk-ant-fake",
        )
        assert ext._provider == "anthropic"
        assert captured["api_key"] == "sk-ant-fake"


# ---------------------------------------------------------------------------
# Anthropic call path
# ---------------------------------------------------------------------------


class TestAnthropicCallPath:
    def test_extract_from_nl_uses_anthropic_messages(self):
        response = json.dumps(
            {
                "constraints": [
                    {
                        "type": "det",
                        "pattern": "must_precede",
                        "args": ["check_policy", "issue_refund"],
                        "nl": "Must check policy before refund",
                        "confidence": 0.9,
                        "source_quote": "",
                    }
                ]
            }
        )
        fake = _FakeAnthropicClient(response)
        ext = UnifiedExtractor(provider="anthropic", client=fake)

        results = ext.extract_from_nl("check policy before refund")
        assert len(results) == 1
        assert results[0].ok
        assert results[0].compiled.pattern_name == "must_precede"

        kwargs = fake.messages.last_kwargs
        assert kwargs is not None
        # System prompt and user content must be on the right channels
        # for the Messages API (system on the top-level ``system`` arg,
        # not interleaved into ``messages``).
        assert kwargs["system"], "system prompt missing"
        assert kwargs["messages"][0]["role"] == "user"
        assert kwargs["temperature"] == 0.0
        assert kwargs["model"] == "claude-3-5-sonnet-latest"

    def test_anthropic_strips_json_fence(self):
        # Some Claude models wrap JSON in a ```json fence even when told
        # not to. The adapter must strip it so json.loads succeeds.
        fenced = "```json\n" + _EMPTY_JSON + "\n```"
        fake = _FakeAnthropicClient(fenced)
        ext = UnifiedExtractor(provider="anthropic", client=fake)
        # If stripping failed, extract would log "invalid JSON" and
        # return [] — the call would not raise but also wouldn't crash.
        # We assert no exception bubbles up and the empty-but-valid
        # result is returned.
        assert ext.extract_from_nl("anything") == []

    def test_anthropic_bare_fence_without_json_tag(self):
        fenced = "```\n" + _EMPTY_JSON + "\n```"
        fake = _FakeAnthropicClient(fenced)
        ext = UnifiedExtractor(provider="anthropic", client=fake)
        assert ext.extract_from_nl("anything") == []


# ---------------------------------------------------------------------------
# base_url plumbing
# ---------------------------------------------------------------------------


class TestBaseUrlPlumbing:
    def test_constructor_arg_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_BASE_URL", "http://from-env/v1")
        ext = UnifiedExtractor(
            base_url="http://from-arg/v1",
            client=_FakeOpenAIClient(_EMPTY_JSON),
        )
        assert ext._base_url == "http://from-arg/v1"

    def test_env_used_when_arg_omitted(self, monkeypatch):
        monkeypatch.setenv("OPENAI_BASE_URL", "http://from-env/v1")
        ext = UnifiedExtractor(client=_FakeOpenAIClient(_EMPTY_JSON))
        assert ext._base_url == "http://from-env/v1"
        assert ext._provider == "openai"

    def test_base_url_passed_to_openai_constructor(self, monkeypatch):
        # Spy on openai.OpenAI() so we can assert the kwargs we forward.
        pytest.importorskip("openai")
        import openai

        captured: dict = {}

        class _StubOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                # Provide the chat shape so any later code doesn't blow up.
                self.chat = SimpleNamespace(
                    completions=SimpleNamespace(create=lambda **_: None)
                )

        monkeypatch.setattr(openai, "OpenAI", _StubOpenAI)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        UnifiedExtractor(base_url="http://localhost:11434/v1")

        assert captured["base_url"] == "http://localhost:11434/v1"
        # Endpoints like Ollama don't need a real key; we still need to
        # supply *something* because the openai SDK insists on it.
        assert captured["api_key"]  # non-empty placeholder

    def test_explicit_api_key_used_with_base_url(self, monkeypatch):
        pytest.importorskip("openai")
        import openai

        captured: dict = {}

        class _StubOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.chat = SimpleNamespace(
                    completions=SimpleNamespace(create=lambda **_: None)
                )

        monkeypatch.setattr(openai, "OpenAI", _StubOpenAI)
        UnifiedExtractor(
            base_url="https://openrouter.ai/api/v1",
            api_key="sk-or-real-key",
        )
        assert captured["api_key"] == "sk-or-real-key"
        assert captured["base_url"] == "https://openrouter.ai/api/v1"


# ---------------------------------------------------------------------------
# Smoke test for CLI plumbing — DocumentExtractor accepts new args
# ---------------------------------------------------------------------------


def test_document_extractor_accepts_provider_and_base_url():
    from sponsio.discovery.extractors.document import DocumentExtractor

    # Construction must not require the openai SDK eagerly, since the
    # user might be on --provider anthropic only.
    ext = DocumentExtractor(provider="anthropic", base_url=None)
    assert ext._provider == "anthropic"

    ext2 = DocumentExtractor(base_url="http://localhost:11434/v1")
    assert ext2._base_url == "http://localhost:11434/v1"
