"""LLM-as-judge primitives for stochastic atom evaluators.

Two judges:

* :class:`BooleanJudge` — reads the top-K next-token logprobs, sums the
  probability mass of yes-variant and no-variant tokens, and returns
  ``P(yes) / (P(yes) + P(no))``. Well-calibrated across providers that
  expose logprobs. See ``docs/cost-based-thresholds.md`` §6.1.

* :class:`BestOfNJudge` — fallback for providers without logprobs.
  Samples N independent yes/no completions at temperature=1.0 and
  returns the empirical fraction. Less accurate, more expensive, but
  works anywhere the model can generate text.

Both return ``(confidence, raw_answer)``. :class:`BooleanJudge` accepts
a :class:`~sponsio.runtime.calibrator.ModelCalibrator` that maps raw
confidence → calibrated confidence per model.
"""

from __future__ import annotations

from math import exp
from typing import Protocol

from sponsio.runtime.llm_client import LogprobClient


# Token vocabularies — includes common tokenizations with/without leading
# space, case variants, and common synonyms. These cover OpenAI BPE
# and Gemini SentencePiece reasonably well; Anthropic isn't used here
# because the adapter returns None → fallback path.
_YES_TOKENS: frozenset[str] = frozenset(
    [
        "yes",
        " yes",
        "Yes",
        " Yes",
        "YES",
        " YES",
        "y",
        " y",
        "Y",
        " Y",
        "true",
        " true",
        "True",
        " True",
    ]
)
_NO_TOKENS: frozenset[str] = frozenset(
    [
        "no",
        " no",
        "No",
        " No",
        "NO",
        " NO",
        "n",
        " n",
        "N",
        " N",
        "false",
        " false",
        "False",
        " False",
    ]
)

DEFAULT_TEMPLATE = (
    'Answer strictly with "yes" or "no".\n\nQuestion: {question}\n\nAnswer:'
)


def _render_template(template: str, question: str) -> str:
    """Substitute ``{question}`` in ``template`` *without* invoking ``str.format``.

    Why this exists
    ---------------
    ``self._template.format(question=question)`` is unsafe in two ways:

    1. ``str.format`` accepts attribute / index expressions. A user-supplied
       template containing ``{question.__class__.__mro__[1].__subclasses__()}``
       would walk Python's class hierarchy at render time — a known
       *format-string injection* sandbox-escape vector. The ``template``
       argument here flows from user code (custom judges, NL contracts
       carrying their own phrasing) and from server-side configs in the
       playground / discovery flows, so the threat is real.
    2. If *question* contains literal ``{`` / ``}`` (which user-provided
       constraint descriptions frequently do — e.g. JSON blobs, regex
       patterns), ``str.format`` raises ``KeyError`` or ``IndexError``
       and aborts the whole judge call.

    The replacement is a literal-substring swap of ``{question}`` for
    ``question``. We do NOT support format spec suffixes (``{question:s}``)
    because the only consumer is the LLM prompt, which never needs them.
    """
    if "{question}" in template:
        return template.replace("{question}", question)
    # Backwards-compat: templates without the placeholder are still
    # rendered as-is; preserves "fixed prompt" use cases.
    return template


class _TextCompletionClient(Protocol):
    """Minimal text-generation interface used by :class:`BestOfNJudge`."""

    model_name: str

    def generate(
        self,
        prompt: str,
        temperature: float = 1.0,
        max_tokens: int = 3,
    ) -> str: ...


class LogprobUnsupportedError(RuntimeError):
    """Raised when a caller demands logprob judging from a client that
    doesn't support it and no fallback is configured."""


class _Calibrator(Protocol):
    def calibrate(self, model_name: str, raw: float) -> float: ...


class BooleanJudge:
    """Logprob-based yes/no judge with optional calibration + fallback.

    Args:
        llm: A :class:`LogprobClient`. If ``logprob_completion`` returns
            ``None`` (e.g. Anthropic), we delegate to ``fallback``.
        calibrator: Optional :class:`_Calibrator` (typically
            :class:`~sponsio.runtime.calibrator.ModelCalibrator`) to map
            raw confidence → calibrated confidence per model.
        fallback: Optional :class:`BestOfNJudge` to invoke when the LLM
            doesn't expose logprobs. If ``None`` and logprobs are
            unavailable, :meth:`judge` raises
            :class:`LogprobUnsupportedError`.
        template: Format string with a ``{question}`` placeholder. Keep
            short — the model only sees a single-token response anyway.
    """

    def __init__(
        self,
        llm: LogprobClient,
        calibrator: _Calibrator | None = None,
        fallback: "BestOfNJudge | None" = None,
        template: str = DEFAULT_TEMPLATE,
    ):
        self._llm = llm
        self._calibrator = calibrator
        self._fallback = fallback
        self._template = template

    def judge(self, question: str) -> tuple[float, str]:
        """Ask the LLM a yes/no question; return ``(confidence, raw_answer)``.

        ``confidence`` is in [0, 1]: ``1.0`` means "certainly yes",
        ``0.0`` means "certainly no". Calibration (if a calibrator is
        provided) is applied before returning.
        """
        prompt = _render_template(self._template, question)
        resp = self._llm.logprob_completion(prompt, max_tokens=1, top_logprobs=20)
        if resp is None:
            if self._fallback is None:
                raise LogprobUnsupportedError(
                    f"{self._llm.model_name} does not expose logprobs and no "
                    f"fallback judge is configured. Pass fallback=BestOfNJudge(...)."
                )
            return self._fallback.judge(question)

        p_yes = sum(exp(lp) for tok, lp in resp.top_logprobs if tok in _YES_TOKENS)
        p_no = sum(exp(lp) for tok, lp in resp.top_logprobs if tok in _NO_TOKENS)
        denom = p_yes + p_no
        if denom <= 0.0:
            # Top-K didn't contain any yes/no token — the model answered
            # something unexpected. Return 0.5 to signal uncertainty.
            return 0.5, resp.first_token
        raw = p_yes / denom
        if self._calibrator is not None:
            raw = self._calibrator.calibrate(self._llm.model_name, raw)
        return float(raw), resp.first_token


class BestOfNJudge:
    """Sampling-based fallback for providers without logprobs.

    Samples ``n`` independent yes/no completions at ``temperature=1.0``
    and returns the fraction that starts with a yes-variant token. Less
    accurate than logprob extraction but works for any client that can
    generate text.

    Args:
        llm: A text-generation client exposing ``generate(prompt,
            temperature, max_tokens) -> str`` and a ``model_name``
            attribute.
        n: Number of samples. Defaults to 8; higher = lower variance,
            linearly more expensive.
        template: Same ``{question}`` format string as BooleanJudge.
    """

    def __init__(
        self,
        llm: _TextCompletionClient,
        n: int = 8,
        template: str = DEFAULT_TEMPLATE,
    ):
        if n < 1:
            raise ValueError(f"n must be >= 1, got {n}")
        self._llm = llm
        self._n = n
        self._template = template

    def judge(self, question: str) -> tuple[float, str]:
        prompt = _render_template(self._template, question)
        answers = [
            self._llm.generate(prompt, temperature=1.0, max_tokens=3).strip()
            for _ in range(self._n)
        ]

        def _is_yes(a: str) -> bool:
            low = a.lower().lstrip(" .,\t")
            return low.startswith(("yes", "y ", "y.", "true"))

        yes_count = sum(1 for a in answers if _is_yes(a))
        return float(yes_count) / float(self._n), answers[0] if answers else ""
