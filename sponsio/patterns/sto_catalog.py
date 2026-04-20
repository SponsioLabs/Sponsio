"""Built-in sto evaluator catalog.

Factory functions that produce ``(Trace) -> StoResult`` callables.
These plug into the existing StoEvaluator pipeline.

Zero-dependency evaluators:
    - pii_evaluator: detects PII via regex (SSN, credit card, email, phone)
    - length_evaluator: checks word/character count
    - format_evaluator: validates JSON, markdown, etc.
    - content_prohibition_evaluator: substring/regex check

LLM-dependent evaluators:
    - tone_evaluator: LLM judges tone/style
    - relevance_evaluator: LLM judges topic relevance
    - llm_judge_evaluator: generic LLM judge fallback
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from sponsio.models.trace import Trace
from sponsio.runtime.evaluators import StoResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_last_content(trace: Trace) -> str:
    """Extract the content/output from the most recent trace event."""
    for event in reversed(trace.events):
        if event.content:
            return event.content
        if event.args:
            # Check for 'output', 'result', 'content' keys in args
            for key in ("output", "result", "content", "response"):
                if key in event.args:
                    return str(event.args[key])
    return ""


# ---------------------------------------------------------------------------
# PII detection (no LLM)
# ---------------------------------------------------------------------------

_PII_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "phone": re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
}


def pii_evaluator(
    fields: Optional[list[str]] = None,
) -> Callable[[Trace], StoResult]:
    """Detect PII in trace output via regex.

    Args:
        fields: Specific PII types to check (e.g. ["ssn", "email"]).
            If None, checks all types.

    Returns:
        Evaluator function that scores 1.0 (clean) or 0.0 (PII found).
    """
    patterns = (
        {k: v for k, v in _PII_PATTERNS.items() if k in fields}
        if fields
        else _PII_PATTERNS
    )

    def evaluate(trace: Trace) -> StoResult:
        content = _get_last_content(trace)
        if not content:
            return StoResult(score=1.0, evidence="No content to check", suggestion="")

        found = []
        for name, pattern in patterns.items():
            if pattern.search(content):
                found.append(name)

        if found:
            return StoResult(
                score=0.0,
                evidence=f"PII detected: {', '.join(found)}",
                suggestion=f"Remove or redact {', '.join(found)} from the response",
            )
        return StoResult(score=1.0, evidence="No PII detected", suggestion="")

    return evaluate


# ---------------------------------------------------------------------------
# Length check (no LLM)
# ---------------------------------------------------------------------------


def length_evaluator(
    max_words: Optional[int] = None,
    max_chars: Optional[int] = None,
) -> Callable[[Trace], StoResult]:
    """Check output length against word/character limits.

    Args:
        max_words: Maximum number of words allowed.
        max_chars: Maximum number of characters allowed.

    Returns:
        Evaluator function that scores 1.0 (within bounds) or 0.0 (exceeded).
    """

    def evaluate(trace: Trace) -> StoResult:
        content = _get_last_content(trace)
        if not content:
            return StoResult(score=1.0, evidence="No content to check", suggestion="")

        violations = []
        if max_words is not None:
            word_count = len(content.split())
            if word_count > max_words:
                violations.append(f"{word_count} words (max {max_words})")
        if max_chars is not None:
            char_count = len(content)
            if char_count > max_chars:
                violations.append(f"{char_count} chars (max {max_chars})")

        if violations:
            return StoResult(
                score=0.0,
                evidence=f"Length exceeded: {'; '.join(violations)}",
                suggestion="Shorten the response to within the specified limits",
            )
        return StoResult(score=1.0, evidence="Length within bounds", suggestion="")

    return evaluate


# ---------------------------------------------------------------------------
# Format validation (no LLM)
# ---------------------------------------------------------------------------


def format_evaluator(
    expected_format: str,
) -> Callable[[Trace], StoResult]:
    """Validate output format (JSON, markdown, etc.).

    Args:
        expected_format: One of "json", "markdown", "bullet_points".

    Returns:
        Evaluator function that scores 1.0 (valid) or 0.0 (invalid).
    """

    def evaluate(trace: Trace) -> StoResult:
        content = _get_last_content(trace)
        if not content:
            return StoResult(score=1.0, evidence="No content to check", suggestion="")

        fmt = expected_format.lower().strip()

        if fmt == "json":
            try:
                json.loads(content)
                return StoResult(score=1.0, evidence="Valid JSON", suggestion="")
            except (json.JSONDecodeError, TypeError):
                return StoResult(
                    score=0.0,
                    evidence="Invalid JSON format",
                    suggestion="Ensure the response is valid JSON",
                )

        if fmt in ("markdown", "md"):
            # Basic check: contains markdown elements
            has_md = bool(
                re.search(
                    r"^#{1,6}\s|^\*\s|^\d+\.\s|```|\*\*|__", content, re.MULTILINE
                )
            )
            score = 1.0 if has_md else 0.3
            return StoResult(
                score=score,
                evidence="Markdown elements found"
                if has_md
                else "No markdown formatting detected",
                suggestion=""
                if has_md
                else "Format the response using markdown headings, lists, or code blocks",
            )

        if fmt in ("bullet_points", "bullets", "list"):
            lines = [
                line.strip() for line in content.strip().split("\n") if line.strip()
            ]
            bullet_lines = [
                line for line in lines if re.match(r"^[-*•]\s|^\d+[.)]\s", line)
            ]
            ratio = len(bullet_lines) / max(len(lines), 1)
            score = min(ratio * 1.5, 1.0)  # some tolerance
            return StoResult(
                score=score,
                evidence=f"{len(bullet_lines)}/{len(lines)} lines are bullet points",
                suggestion="Format the response as a bulleted or numbered list",
            )

        return StoResult(score=0.5, evidence=f"Unknown format: {fmt}", suggestion="")

    return evaluate


# ---------------------------------------------------------------------------
# Content prohibition (no LLM for basic, optional LLM for semantic)
# ---------------------------------------------------------------------------


def content_prohibition_evaluator(
    prohibited: str,
) -> Callable[[Trace], StoResult]:
    """Check that output does not contain prohibited content.

    Uses case-insensitive substring matching.

    Args:
        prohibited: Text or keyword that must not appear in output.

    Returns:
        Evaluator function.
    """

    def evaluate(trace: Trace) -> StoResult:
        content = _get_last_content(trace)
        if not content:
            return StoResult(score=1.0, evidence="No content to check", suggestion="")

        if prohibited.lower() in content.lower():
            return StoResult(
                score=0.0,
                evidence=f"Prohibited content found: '{prohibited}'",
                suggestion=f"Remove any mention of '{prohibited}' from the response",
            )
        return StoResult(
            score=1.0,
            evidence=f"No prohibited content ('{prohibited}') found",
            suggestion="",
        )

    return evaluate


# ---------------------------------------------------------------------------
# Tone evaluation (LLM required)
# ---------------------------------------------------------------------------


def tone_evaluator(
    desired_tone: str,
    client: Any = None,
    model: str = "gpt-4o-mini",
) -> Callable[[Trace], StoResult]:
    """Evaluate output tone/style via LLM.

    Args:
        desired_tone: e.g. "empathetic", "professional", "friendly".
        client: Pre-configured OpenAI client. If None, creates one.
        model: Model to use for evaluation.

    Returns:
        Evaluator function that returns a scored result.
    """

    def evaluate(trace: Trace) -> StoResult:
        content = _get_last_content(trace)
        if not content:
            return StoResult(score=1.0, evidence="No content to check", suggestion="")

        try:
            _client = client
            if _client is None:
                import openai

                _client = openai.OpenAI()

            response = _client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"Rate the following text on a scale of 0-10 for being {desired_tone}. "
                            'Respond with JSON: {"score": <number>, "evidence": "<brief reason>", "suggestion": "<how to improve>"}'
                        ),
                    },
                    {"role": "user", "content": content},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            data = json.loads(response.choices[0].message.content or "{}")
            score = float(data.get("score", 5)) / 10.0
            return StoResult(
                score=max(0.0, min(1.0, score)),
                evidence=data.get("evidence", ""),
                suggestion=data.get("suggestion", ""),
            )
        except Exception as e:
            return StoResult(
                score=0.5,
                evidence=f"LLM evaluation failed: {e}",
                suggestion=f"Ensure response is {desired_tone}",
            )

    return evaluate


# ---------------------------------------------------------------------------
# Relevance evaluation (LLM required)
# ---------------------------------------------------------------------------


def relevance_evaluator(
    topic: str,
    client: Any = None,
    model: str = "gpt-4o-mini",
) -> Callable[[Trace], StoResult]:
    """Evaluate whether output is relevant to a given topic via LLM.

    Args:
        topic: The expected topic or context.
        client: Pre-configured OpenAI client.
        model: Model to use.

    Returns:
        Evaluator function.
    """

    def evaluate(trace: Trace) -> StoResult:
        content = _get_last_content(trace)
        if not content:
            return StoResult(score=1.0, evidence="No content to check", suggestion="")

        try:
            _client = client
            if _client is None:
                import openai

                _client = openai.OpenAI()

            response = _client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"Rate the following text on a scale of 0-10 for relevance to: {topic}. "
                            'Respond with JSON: {"score": <number>, "evidence": "<brief reason>", "suggestion": "<how to improve>"}'
                        ),
                    },
                    {"role": "user", "content": content},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            data = json.loads(response.choices[0].message.content or "{}")
            score = float(data.get("score", 5)) / 10.0
            return StoResult(
                score=max(0.0, min(1.0, score)),
                evidence=data.get("evidence", ""),
                suggestion=data.get("suggestion", ""),
            )
        except Exception as e:
            return StoResult(
                score=0.5,
                evidence=f"LLM evaluation failed: {e}",
                suggestion=f"Ensure response is relevant to {topic}",
            )

    return evaluate


# ---------------------------------------------------------------------------
# Generic LLM judge (fallback)
# ---------------------------------------------------------------------------


def llm_judge_evaluator(
    constraint_text: str,
    client: Any = None,
    model: str = "gpt-4o-mini",
) -> Callable[[Trace], StoResult]:
    """Generic LLM judge for arbitrary NL constraints.

    Used as fallback when no specific category matches.

    Args:
        constraint_text: The original NL constraint text.
        client: Pre-configured OpenAI client.
        model: Model to use.

    Returns:
        Evaluator function.
    """

    def evaluate(trace: Trace) -> StoResult:
        content = _get_last_content(trace)
        if not content:
            return StoResult(score=1.0, evidence="No content to check", suggestion="")

        try:
            _client = client
            if _client is None:
                import openai

                _client = openai.OpenAI()

            response = _client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"Evaluate whether the following text satisfies this constraint: '{constraint_text}'. "
                            "Rate compliance on a scale of 0-10. "
                            'Respond with JSON: {"score": <number>, "evidence": "<brief reason>", "suggestion": "<how to improve>"}'
                        ),
                    },
                    {"role": "user", "content": content},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            data = json.loads(response.choices[0].message.content or "{}")
            score = float(data.get("score", 5)) / 10.0
            return StoResult(
                score=max(0.0, min(1.0, score)),
                evidence=data.get("evidence", ""),
                suggestion=data.get("suggestion", ""),
            )
        except Exception as e:
            return StoResult(
                score=0.5,
                evidence=f"LLM evaluation failed: {e}",
                suggestion=f"Ensure response satisfies: {constraint_text}",
            )

    return evaluate


# ---------------------------------------------------------------------------
# Sto catalog registry
# ---------------------------------------------------------------------------

_SOFT_CATALOG = {
    "pii": pii_evaluator,
    "length": length_evaluator,
    "format": format_evaluator,
    "content_prohibition": content_prohibition_evaluator,
    "tone": tone_evaluator,
    "relevance": relevance_evaluator,
    "custom": llm_judge_evaluator,
}
