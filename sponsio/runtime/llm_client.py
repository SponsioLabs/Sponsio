"""LLM client adapters for the stochastic judge pipeline.

The sto pipeline needs to read **next-token log-probabilities** from an
LLM to produce calibrated confidence scores (see
``docs/cost-based-thresholds.md`` §6.1 — "token-probability extraction"
is more robust than verbalized confidence).

This module defines a lightweight :class:`LogprobClient` protocol that
lets :class:`sponsio.runtime.judge.BooleanJudge` stay provider-agnostic.
Adapters for OpenAI, Anthropic, and Gemini follow the same shape.

Providers differ in what they expose:

* **OpenAI** — full top-K logprobs on ``chat.completions`` (``logprobs=True,
  top_logprobs=N`` up to 20). First-class support.
* **Anthropic** — does NOT currently expose ``top_logprobs`` on the
  public Messages API. Adapter returns ``None`` from
  ``logprob_completion()`` so the caller falls back to
  :class:`sponsio.runtime.judge.BestOfNJudge`.
* **Gemini** — ``response_logprobs=True`` works but caps the number of
  top candidates at 5. Usable for BooleanJudge (yes/no vocab fits).

Callers should treat ``logprob_completion()`` as a best-effort method:
on ``None``, fall back to sampling-based judging.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class LogprobResponse:
    """Top-K logprobs for the first generated token.

    Attributes:
        first_token: The token the model actually sampled.
        top_logprobs: List of ``(token, logprob)`` pairs for the top-K
            candidates at that position. Natural log, not probability.
    """

    first_token: str
    top_logprobs: list[tuple[str, float]]


@runtime_checkable
class LogprobClient(Protocol):
    """Provider-agnostic interface for extracting next-token logprobs.

    ``model_name`` is used by :class:`sponsio.runtime.calibrator.ModelCalibrator`
    to key per-model calibration maps, so keep it stable across
    invocations (e.g. ``"gpt-4o-mini"``).

    Adapters that cannot supply logprobs (e.g. Anthropic today) MUST
    return ``None`` from :meth:`logprob_completion` rather than raising
    — the caller uses this signal to fall back to best-of-N sampling.
    """

    model_name: str

    def logprob_completion(
        self,
        prompt: str,
        max_tokens: int = 1,
        top_logprobs: int = 20,
    ) -> LogprobResponse | None: ...


class OpenAILogprobClient:
    """Adapter over an OpenAI-compatible ``chat.completions`` client.

    Uses ``logprobs=True, top_logprobs=N`` to extract top-K candidates
    for the first generated token. Works with the official ``openai``
    Python SDK and any drop-in compatible endpoint (Ollama, vLLM, etc.).
    """

    def __init__(self, client: Any, model_name: str):
        self._client = client
        self.model_name = model_name

    def logprob_completion(
        self,
        prompt: str,
        max_tokens: int = 1,
        top_logprobs: int = 20,
    ) -> LogprobResponse | None:
        resp = self._client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            logprobs=True,
            top_logprobs=top_logprobs,
            temperature=0.0,
        )
        choice = resp.choices[0]
        if not choice.logprobs or not choice.logprobs.content:
            return None
        first = choice.logprobs.content[0]
        return LogprobResponse(
            first_token=first.token,
            top_logprobs=[(x.token, float(x.logprob)) for x in first.top_logprobs],
        )


class AnthropicLogprobClient:
    """Adapter for the Anthropic Messages API.

    The public Anthropic API does not currently expose per-position
    top-K logprobs, so this adapter always returns ``None``. The caller
    (:class:`BooleanJudge`) treats that as a signal to fall back to
    :class:`BestOfNJudge`.

    Kept in the codebase so users can plug Anthropic in and have
    BooleanJudge's fallback path kick in automatically — no code
    changes needed when / if Anthropic ships logprobs.
    """

    def __init__(self, client: Any, model_name: str):
        self._client = client
        self.model_name = model_name

    def logprob_completion(
        self,
        prompt: str,
        max_tokens: int = 1,
        top_logprobs: int = 20,
    ) -> LogprobResponse | None:
        return None


class GeminiLogprobClient:
    """Adapter for Google's new ``google-genai`` SDK (``google.genai.Client``).

    Wraps a ``genai.Client`` so ``BooleanJudge`` can extract top-K
    logprobs from Gemini. Pass the already-configured client and the
    model name::

        from google import genai
        client = genai.Client(api_key=...)
        judge = BooleanJudge(GeminiLogprobClient(client, "gemini-2.0-flash"))

    Empirical notes:
    - ``gemini-2.0-flash`` is the only Gemini that accepts
      ``response_logprobs`` as of 2026-04. 2.5-flash/2.5-pro return
      400 "Logprobs is not enabled".
    - ``logprobs`` (top-K) defaults to 2. Yes/no BooleanJudge only needs
      the yes and no tokens in the candidate list, and k>2 frequently
      hits a per-call logprobs quota (429). Raise at your own risk.
    - Returns ``None`` when the SDK isn't installed; callers
      (:class:`BooleanJudge`) will fall back to :class:`BestOfNJudge` if
      configured, otherwise raise.
    """

    def __init__(self, client: Any, model_name: str):
        self._client = client
        self.model_name = model_name

    def logprob_completion(
        self,
        prompt: str,
        max_tokens: int = 1,
        top_logprobs: int = 2,
    ) -> LogprobResponse | None:
        try:
            from google.genai import types
        except ImportError:
            return None

        k = min(top_logprobs, 2)  # Gemini quota: k>2 hits 429 under
        # plain API key usage as of 2026-04.
        resp = self._client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                temperature=0.0,
                response_logprobs=True,
                logprobs=k,
            ),
        )
        try:
            candidates = getattr(resp, "candidates", None)
            if not candidates:
                return None
            lp_result = getattr(candidates[0], "logprobs_result", None)
            if lp_result is None:
                return None
            top = getattr(lp_result, "top_candidates", None) or []
            if not top:
                return None
            top_pos = top[0].candidates  # candidates at position 0
            if not top_pos:
                return None
            return LogprobResponse(
                first_token=top_pos[0].token,
                top_logprobs=[(c.token, float(c.log_probability)) for c in top_pos],
            )
        except Exception:
            return None
