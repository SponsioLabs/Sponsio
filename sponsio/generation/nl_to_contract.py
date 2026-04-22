"""NL → Contract: translate natural language descriptions to pattern-based contracts.

This is the primary user-facing path for contract acquisition. Users describe
constraints in natural language; this module maps them to existing pattern
functions (must_precede, always_followed_by, never_together, etc.) and
validates the results via the formula evaluator.

The module provides both an LLM-assisted path (requires an API key) and a
rule-based fallback that uses keyword matching for common constraint patterns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

from sponsio.formulas.evaluator import evaluate
from sponsio.models.agent import Agent
from sponsio.models.contract import Contract
from sponsio.patterns.library import (
    DetFormula,
    always_followed_by,
    arg_blacklist,
    arg_length_limit,
    bounded_retry,
    arg_value_range,
    confirm_after_source,
    cooldown,
    dangerous_bash_commands,
    dangerous_sql_verbs,
    delegation_depth_limit,
    data_intact,
    deadline,
    destructive_action_gate,
    idempotent,
    irreversible_once,
    loop_detection,
    must_confirm,
    must_precede,
    mutual_exclusion,
    never_together,
    no_data_leak,
    no_reversal,
    rate_limit,
    required_steps_completion,
    requires_permission,
    scope_limit,
    segregation_of_duty,
    token_budget,
    tool_allowlist,
    untrusted_source_gate,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ParsedConstraint:
    """A single constraint parsed from natural language.

    Attributes:
        original_nl: The original natural language text.
        pattern_name: Name of the matched pattern function.
        args: Positional arguments for the pattern function.
        kwargs: Keyword arguments for the pattern function.
        formula: The compiled DetFormula (None if parsing failed).
        error: Error message if parsing or validation failed.
    """

    original_nl: str
    pattern_name: str = ""
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    formula: DetFormula | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.formula is not None and not self.error


@dataclass
class NLParseResult:
    """Result of parsing one or more NL constraint descriptions.

    Attributes:
        constraints: List of parsed constraints (one per NL line).
        formulas: Successfully compiled ``DetFormula`` objects
            (not ``Contract``s — wrap them yourself if needed).
        errors: Lines that failed to parse.
    """

    constraints: list[ParsedConstraint] = field(default_factory=list)

    @property
    def formulas(self) -> list[DetFormula]:
        return [c.formula for c in self.constraints if c.ok]

    # Backward-compatible alias: previous code called ``.contracts`` but
    # the returned items are formulas, not ``Contract`` objects. Kept to
    # avoid churn; prefer ``.formulas``.
    contracts = formulas

    @property
    def errors(self) -> list[ParsedConstraint]:
        return [c for c in self.constraints if not c.ok]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0 and len(self.constraints) > 0


# ---------------------------------------------------------------------------
# Pattern registry
# ---------------------------------------------------------------------------

_PATTERN_REGISTRY: dict[str, Callable[..., DetFormula]] = {
    "must_precede": must_precede,
    "always_followed_by": always_followed_by,
    "never_together": never_together,
    "requires_permission": requires_permission,
    "no_data_leak": no_data_leak,
    "mutual_exclusion": mutual_exclusion,
    "rate_limit": rate_limit,
    "no_reversal": no_reversal,
    "idempotent": idempotent,
    "deadline": deadline,
    "must_confirm": must_confirm,
    "cooldown": cooldown,
    "segregation_of_duty": segregation_of_duty,
    "bounded_retry": bounded_retry,
    "arg_blacklist": arg_blacklist,
    "arg_length_limit": arg_length_limit,
    "scope_limit": scope_limit,
    "data_intact": data_intact,
    # Layer 1: OWASP Agentic Top 10
    "destructive_action_gate": destructive_action_gate,
    "untrusted_source_gate": untrusted_source_gate,
    "required_steps_completion": required_steps_completion,
    "loop_detection": loop_detection,
    "tool_allowlist": tool_allowlist,
    "dangerous_bash_commands": dangerous_bash_commands,
    "dangerous_sql_verbs": dangerous_sql_verbs,
    "irreversible_once": irreversible_once,
    "confirm_after_source": confirm_after_source,
    # Layer 2: Atom extensions
    "token_budget": token_budget,
    "delegation_depth_limit": delegation_depth_limit,
    "arg_value_range": arg_value_range,
}


def get_available_patterns() -> dict[str, Callable[..., DetFormula]]:
    """Returns the registry of available pattern functions."""
    return dict(_PATTERN_REGISTRY)


# ---------------------------------------------------------------------------
# LLM backend protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMBackend(Protocol):
    """Protocol for LLM backends used in NL → contract translation."""

    def translate(
        self, nl_text: str, available_patterns: list[str]
    ) -> list[dict[str, Any]]:
        """Translates NL text to pattern function calls.

        Args:
            nl_text: Natural language constraint description.
            available_patterns: List of available pattern function names.

        Returns:
            List of dicts with keys: "pattern", "args", "kwargs".
            Example: [{"pattern": "must_precede", "args": ["A", "B"], "kwargs": {}}]
        """
        ...


# ---------------------------------------------------------------------------
# Concrete LLM backend: OpenAI
# ---------------------------------------------------------------------------


class OpenAIBackend:
    """Concrete LLM backend using OpenAI API for NL -> contract translation."""

    def __init__(self, model: str = "gpt-4o-mini", api_key: str | None = None):
        try:
            import openai
        except ImportError:
            raise ImportError("Install openai: pip install 'sponsio[llm]'")
        self._client = openai.OpenAI(api_key=api_key)
        self._model = model

    def translate(
        self, nl_description: str, available_patterns: list[str]
    ) -> list[dict]:
        """Translate NL constraints to pattern function calls via OpenAI.

        Returns:
            List of dicts with keys: "pattern", "args", "kwargs".
        """
        import json as _json

        system_prompt = self._build_system_prompt(available_patterns)
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": nl_description},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        result = _json.loads(response.choices[0].message.content)
        return result.get("contracts", [])

    def _build_system_prompt(self, available_patterns: list[str]) -> str:
        """Build system prompt listing available patterns and expected output format."""
        pattern_list = "\n".join(f"  - {p}" for p in available_patterns)
        return (
            "You are a constraint translator. Given natural language constraint descriptions, "
            "translate each one into a pattern function call.\n\n"
            f"Available patterns:\n{pattern_list}\n\n"
            "Each pattern takes positional string arguments (action/tool names) and an optional "
            "'desc' keyword argument. rate_limit takes (action: str, count: int).\n\n"
            "Return a JSON object with a 'contracts' array. Each element should have:\n"
            '  - "pattern": one of the available pattern names\n'
            '  - "args": list of positional arguments\n'
            '  - "kwargs": dict of keyword arguments (at minimum {"desc": "<original NL>"})\n\n'
            "Example output:\n"
            '{"contracts": [{"pattern": "must_precede", "args": ["A", "B"], '
            '"kwargs": {"desc": "A before B"}}]}'
        )


# ---------------------------------------------------------------------------
# NLContractGenerator — convenience wrapper with optional LLM fallback
# ---------------------------------------------------------------------------


class NLContractGenerator:
    """High-level NL -> contract generator with optional LLM backend.

    When ``backend`` is provided and rule-based parsing fails for a line,
    the LLM backend is used as a fallback. When ``backend`` is None,
    only rule-based parsing is used (current default behavior).
    """

    def __init__(self, registry: dict | None = None, backend: LLMBackend | None = None):
        self._backend = backend  # None = rule-based only
        self._registry = registry or dict(_PATTERN_REGISTRY)

    def generate(self, nl_text: str, agent: Any | None = None) -> Any:
        """Parse NL text, falling back to LLM backend if rule-based fails."""
        return nl_to_contracts(nl_text, agent=agent, llm_backend=self._backend)


# ---------------------------------------------------------------------------
# Rule-based keyword matcher (fallback when no LLM is available)
# ---------------------------------------------------------------------------

# Regex patterns for extracting action names from NL text
_QUOTED_RE = re.compile(r'["\']([^"\']+)["\']')
_BACKTICK_RE = re.compile(r"`([^`]+)`")
# Bare snake_case identifiers (at least one underscore to avoid matching
# common English words).  Must be preceded by a word boundary and not be a
# known stop word.
_BARE_SNAKE_RE = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b")
# Known "not tool names" — common English phrases that happen to look
# snake_case-ish when squished.
_BARE_STOP = frozenset(
    {
        "at_most",
        "at_least",
        "no_more",
        "per_session",
        "per_call",
        "must_not",
        "should_not",
        "same_session",
        "each_other",
    }
)


# Positional patterns: extract the word right after these cue phrases.
# e.g. "tool deploy" → "deploy", "call delete" → "delete"
_CUE_PHRASE_RE = re.compile(
    r"(?:^|\s)(?:tool|tools|action|actions|call(?:ing)?|run(?:ning)?|"
    r"execute|invoke|use|using)\s+"
    r"([a-zA-Z][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)
# Extended cue: "call X and Y", "tools X and Y", etc.
_CUE_AND_RE = re.compile(
    r"(?:^|\s)(?:tool|tools|call(?:ing)?|run(?:ning)?|execute|invoke)\s+"
    r"([a-zA-Z][a-zA-Z0-9_]*)\s+and\s+([a-zA-Z][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)
# Word before "command"/"args"/"argument" — likely a tool name
# e.g. "bash command" → "bash"
_TOOL_BEFORE_FIELD_RE = re.compile(
    r"([a-zA-Z][a-zA-Z0-9_]*)\s+(?:command|args?|arguments?|input|params?)",
    re.IGNORECASE,
)

# Common English words that should NOT be extracted as tool names
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "my",
        "your",
        "our",
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "from",
        "as",
        "and",
        "or",
        "but",
        "not",
        "no",
        "nor",
        "so",
        "if",
        "then",
        "than",
        "both",
        "each",
        "every",
        "all",
        "any",
        "few",
        "more",
        "most",
        "some",
        "only",
        "once",
        "just",
        "also",
        "very",
        "too",
        "can",
        "may",
        "must",
        "will",
        "shall",
        "should",
        "would",
        "could",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "get",
        "got",
        "make",
        "made",
        "let",
        "set",
        "same",
        "different",
        "other",
        "new",
        "old",
        "first",
        "last",
        "before",
        "after",
        "between",
        "within",
        "always",
        "never",
        "them",
        "they",
        "he",
        "she",
        "we",
        "you",
        "me",
        "him",
        "her",
        "us",
        "confirmed",
        "called",
        "required",
        "allowed",
        "restricted",
        "file",
        "data",
        "permission",
        "admin",
        "user",
        "agent",
    }
)


def _extract_actions(text: str) -> list[str]:
    """Extracts tool/action names from text.

    Priority order:
    1. Backtick-delimited: ``tool `name` ``
    2. Quoted: ``"name"`` or ``'name'``
    3. Bare snake_case identifiers (e.g. ``check_policy``, ``issue_refund``)
    4. Positional cue phrases: word after "tool"/"call"/"run"/"execute"

    Only one extraction method is used — whichever yields results first.
    """
    actions = _BACKTICK_RE.findall(text) or _QUOTED_RE.findall(text)
    if actions:
        return actions
    # Fallback 1: bare snake_case identifiers (high confidence)
    bare = [m for m in _BARE_SNAKE_RE.findall(text) if m not in _BARE_STOP]
    if bare:
        return bare
    # Fallback 2: "call X and Y" / "tools X and Y" pattern
    and_match = _CUE_AND_RE.search(text)
    if and_match:
        a, b = and_match.group(1), and_match.group(2)
        result = [w for w in [a, b] if w.lower() not in _STOP_WORDS]
        if len(result) >= 2:
            return result

    # Fallback 3: positional extraction after cue phrases
    cue_matches = _CUE_PHRASE_RE.findall(text)
    cue_actions = [w for w in cue_matches if w.lower() not in _STOP_WORDS]
    if cue_actions:
        return cue_actions

    # Fallback 4: word before "command"/"args" (for arg_blacklist context)
    field_match = _TOOL_BEFORE_FIELD_RE.search(text)
    if field_match:
        word = field_match.group(1)
        if word.lower() not in _STOP_WORDS:
            return [word]

    return []


# Keyword rules: (keywords_to_match, pattern_name, min_args)
# Order matters — first match wins. More specific patterns first.
_KEYWORD_RULES: list[tuple[list[str], str, int]] = [
    # --- arg_blacklist (must come before general "must not contain") ---
    (
        [
            r"arg(?:ument)?s?\s+(?:must\s+)?not\s+contain",
            r"(?:command|input|param)\s+must\s+not\s+contain",
            r"blacklist",
            r"must\s+not\s+contain\s+(?:.*(?:rm\s*-rf|sudo|DROP|eval))",
            r"forbid.*(?:in\s+(?:arguments?|params?|input))",
            r"ban\s+(?:patterns?|commands?)\s+in",
        ],
        "arg_blacklist",
        2,
    ),
    # --- scope_limit ---
    (
        [
            r"restrict\s+(?:file\s+)?(?:access|operations?)\s+to\s+(?:`|/)",
            r"scope\s*limit",
            r"only\s+(?:access|read|write|operate)\s+(?:files?\s+)?(?:in|within|under)\s+(?:`|/)",
            r"file\s+(?:operations?\s+)?restricted\s+to\s+(?:`|/)",
            r"(?:paths?|files?|directories?)\s+(?:must\s+be\s+)?(?:within|under)\s+(?:`|/)",
            r"confine.*to\s+(?:/|`)",
            r"restricted\s+to\s+(?:`?/)",
        ],
        "scope_limit",
        2,
    ),
    # --- data_intact ---
    (
        [
            r"data\s+(?:must\s+)?remain\s+(?:un(?:modified|changed|altered)|intact)",
            r"(?:must\s+)?(?:only|exclusively)\s+(?:read|operate\s+on)\s+(?:from\s+)?(?:original|unmodified)",
            r"data\s*intact",
            r"read[- ]?only\s+(?:from|on)\b",
        ],
        "data_intact",
        2,
    ),
    # --- Bounded retry (must come before rate_limit — "at most N retries") ---
    (
        [
            r"at most.*retr",
            r"retry.*at most",
            r"bounded retry",
            r"max.*retries",
            r"maximum.*retries",
            r"no more than.*retr",
            r"limit.*retries?\s+to\b",
        ],
        "bounded_retry",
        2,
    ),
    # --- Rate limit ---
    (
        [
            r"rate\s*limit",
            r"at most.*times",
            r"maximum.*invocations",
            r"limit.*(?:calls|invocations|uses)\b",
            r"no duplicate",
            r"never repeat",
            r"must not be called more than",
            r"(?:at most|no more than|up to|maximum|max)\s+(\d+)\s+(?:per|times|calls)",
            r"limit.*to\s+(\d+)\s+(?:per|times|calls)",
        ],
        "rate_limit",
        2,
    ),
    # --- Idempotent ---
    (
        [
            r"idempotent",
            r"at most once",
            r"only (?:once|run once|call(?:ed)? once)",
            r"called? once\b",
            r"should only (?:run|be called|execute) once",
            r"single invocation",
            r"no repeated calls?\b",
        ],
        "idempotent",
        1,
    ),
    # --- Mutual exclusion ---
    (
        [
            r"mutually exclusive",
            r"exactly one of",
            r"either.*or.*not both",
            r"cannot (?:both|call both)",
            r"only one of",
            r"at most one of",
        ],
        "mutual_exclusion",
        2,
    ),
    # --- Never together → routes to mutual_exclusion ---
    (
        [
            r"never together",
            r"never both",
            r"not at the same time",
            r"never co-occur",
            r"must never.*called together",
            r"never be called together",
            r"not.*(?:in|within|during)\s+(?:the\s+)?same\s+session",
            r"do not call.*(?:and|,).*(?:in|within|during)\s+(?:the\s+)?same",
        ],
        "mutual_exclusion",
        2,
    ),
    # --- Cooldown ---
    (
        [
            r"cooldown",
            r"cool\s*down\s+(?:of|between|period\s+of)\s+\d+",
            r"minimum\s+\d+\s+steps?\s+between",
            r"at least\s+\d+\s+steps?\s+between",
            r"wait\s+\d+\s+steps?\s+between",
            r"gap of\s+\d+\s+steps?",
            r"interval\s+(?:of\s+)?\d+\s+steps?",
        ],
        "cooldown",
        2,
    ),
    # --- Segregation of duty ---
    (
        [
            r"segregation of dut",
            r"separation of dut",
            r"same agent.*cannot.*both",
            r"different agent",
            r"cannot do both",
            r"must be (?:done|performed) by different",
            r"two[- ]person\s+rule",
            r"dual\s+control",
        ],
        "segregation_of_duty",
        2,
    ),
    # --- Deadline ---
    (
        [
            r"within\s+\d+\s+steps?\s+(?:of|after)",
            r"deadline\s+(?:of\s+)?\d+\s+steps?",
            r"must.*within\s+\d+\s+steps?",
            r"at most\s+\d+\s+steps?\s+after",
        ],
        "deadline",
        3,
    ),
    # --- Must confirm ---
    (
        [
            r"must be confirmed",
            r"confirm(?:ation)?\s+(?:before|required|needed)",
            r"requires?\s+confirmation",
            r"must confirm before",
            r"(?:needs?|requires?)\s+(?:user\s+)?(?:approval|consent)\s+before",
            r"without\s+confirmation",
            r"never\s+call.*without\s+confirm",
        ],
        "must_confirm",
        1,
    ),
    # --- No reversal ---
    (
        [
            r"cannot.*after\s+approv",
            r"no reversal",
            r"never\s+reverse",
            r"cannot\s+deny\s+after",
            r"cannot\s+reject\s+after",
            r"cannot\s+contradict",
            r"cannot\s+be\s+reversed",
            r"(?:never|cannot|must\s+not)\s+(?:call\s+)?.*after\s+(?:calling\s+)?",
            r"forbidden\s+after",
            r"prohibited\s+after",
            r"must\s+not\s+follow",
            r"must\s+not.*after",
            r"should\s+not\s+follow",
            r"not\s+allowed\s+after",
            r"once.*(?:cannot|must\s+not|never)",
            r"irreversible",
        ],
        "no_reversal",
        2,
    ),
    # --- Requires permission ---
    (
        [
            r"requires?\s+(?:\w+\s+)?permission",
            r"needs?\s+permission",
            r"must\s+have\s+permission",
            r"(?:requires?|needs?)\s+(?:\w+\s+)?(?:authorization|auth)\b",
            r"requires?\s+admin\b",
            r"(?:only\s+)?(?:authorized|permitted)\s+(?:users?|agents?|roles?)\s+(?:can|may)",
            r"require\s+\w+\s+(?:permission|role|access)\s+to\b",
        ],
        "requires_permission",
        2,
    ),
    # --- No data leak ---
    (
        [
            r"no data leak",
            r"data must not (?:flow|leak|be\s+sent)",
            r"no leak",
            r"(?:must\s+)?not\s+(?:send|transmit|expose|share).*(?:to\s+external|outside)",
            r"protect.*from\s+(?:leaking|exposure)",
        ],
        "no_data_leak",
        2,
    ),
    # --- Always followed by ---
    (
        [
            r"(?:must\s+be\s+|always\s+)?followed\s+by",
            r"must eventually follow",
            r"eventually.*after",
            r"after\s+(?:calling\s+)?.*(?:always|must)\s+(?:call\s+|run\s+)?",
            r"(?:always|must)\s+(?:call|run|execute)\s+.*after",
            r"whenever.*(?:is\s+)?called.*(?:must|should|always)",
            r"(?:should|must)\s+(?:always\s+)?come\s+after",
        ],
        "always_followed_by",
        2,
    ),
    # --- Must precede (LAST — most general) ---
    # Requires tool-like context to avoid matching plain English "before".
    (
        [
            r"precede",
            r"prior to\s+`",
            r"`[^`]+`\s+(?:must\s+)?(?:be\s+)?(?:called\s+|run\s+|executed\s+)?before\s+`",
            r"before\s+(?:calling\s+)?`",
            r"required\s+before\s+`",
            r"is\s+(?:a\s+)?prerequisite\s+for\b",
            r"(?:always|must)\s+run\s+`[^`]+`\s+first",
            r"is\s+required\s+before",
            r"needs?\s+to\s+(?:be\s+)?(?:called|run)\s+before",
            r"must\s+(?:be\s+)?(?:called|run|executed)\s+first",
        ],
        "must_precede",
        2,
    ),
]


def _match_keyword_rule(text: str) -> tuple[str, int] | None:
    """Matches text against keyword rules, returns (pattern_name, min_args) or None."""
    lower = text.lower()
    for keywords, pattern_name, min_args in _KEYWORD_RULES:
        for kw in keywords:
            if re.search(kw, lower):
                return pattern_name, min_args
    return None


# Word → number mapping for small numbers
_WORD_NUMBERS: dict[str, int] = {
    "one": 1,
    "once": 1,
    "two": 2,
    "twice": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def _parse_number(text: str) -> int | None:
    """Parse a digit or word number (e.g. '3', 'three', 'once')."""
    m = re.search(r"(\d+)", text)
    if m:
        return int(m.group(1))
    lower = text.lower()
    for word, num in _WORD_NUMBERS.items():
        if word in lower:
            return num
    return None


def _parse_rate_limit_count(text: str) -> int | None:
    """Extracts a numeric count from rate limit NL text."""
    lower = text.lower()
    m = re.search(r"at most (\d+)", lower)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*(?:times|invocations|calls|per)", lower)
    if m:
        return int(m.group(1))
    m = re.search(r"limit.*?(\d+)", lower)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:no more than|up to|maximum|max|more than)\s+(\d+)", lower)
    if m:
        return int(m.group(1))
    # Word numbers after "more than" / "at most" / "no more than": e.g.
    # "must not be called more than once" → 1
    word_pattern = "|".join(re.escape(w) for w in _WORD_NUMBERS.keys())
    m = re.search(
        rf"(?:more than|at most|no more than|up to)\s+({word_pattern})\b",
        lower,
    )
    if m:
        return _WORD_NUMBERS[m.group(1)]
    # Word numbers followed by "times/calls/per": e.g. "at most three times"
    for word, num in _WORD_NUMBERS.items():
        if word in lower and re.search(rf"{word}\s+(?:times|calls|per)", lower):
            return num
    return None


def _parse_retry_count(text: str) -> int | None:
    """Extracts a retry count from NL text."""
    m = re.search(r"at most (\d+)\s*retr", text.lower())
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*retr", text.lower())
    if m:
        return int(m.group(1))
    m = re.search(r"max(?:imum)?\s*(\d+)", text.lower())
    if m:
        return int(m.group(1))
    return None


def _parse_step_count(text: str) -> int | None:
    """Extracts a step count from NL text."""
    m = re.search(r"(\d+)\s*steps?", text.lower())
    if m:
        return int(m.group(1))
    m = re.search(r"cooldown\s+(?:of\s+)?(\d+)", text.lower())
    if m:
        return int(m.group(1))
    return None


def _extract_blacklist_patterns(text: str) -> list[str]:
    """Extracts forbidden string patterns from NL text.

    Looks for quoted or backtick-delimited strings after 'contain',
    'include', or specific known dangerous commands.
    """
    # First try: extract from the part after "contain" / "include"
    m = re.search(
        r"(?:contain|include|allow|permit)\s+(.+)",
        text,
        re.IGNORECASE,
    )
    if m:
        tail = m.group(1)
        # Extract quoted/backtick items from the tail
        items = _BACKTICK_RE.findall(tail) or _QUOTED_RE.findall(tail)
        if items:
            return items
        # Split on "or" / "," / "and"
        parts = re.split(r"\s+or\s+|\s*,\s*|\s+and\s+", tail)
        return [p.strip().rstrip(".") for p in parts if p.strip()]
    return []


def _extract_paths(text: str) -> list[str]:
    """Extracts filesystem path prefixes from NL text.

    Looks for quoted/backtick paths or bare paths starting with '/'.
    """
    # Backtick/quoted paths first
    items = _BACKTICK_RE.findall(text) or _QUOTED_RE.findall(text)
    paths = [p for p in items if p.startswith("/")]
    if paths:
        return paths
    # Bare paths: /some/path
    bare = re.findall(r"(/[a-zA-Z0-9_./-]+)", text)
    return bare


def _build_constraint(
    nl_line: str, pattern_name: str, args: tuple, formula: Any
) -> ParsedConstraint:
    """Helper to build a successful ParsedConstraint."""
    return ParsedConstraint(
        original_nl=nl_line,
        pattern_name=pattern_name,
        args=args,
        kwargs={"desc": nl_line.strip()},
        formula=formula,
    )


def _build_error(
    nl_line: str, pattern_name: str, error: str, args: tuple = ()
) -> ParsedConstraint:
    """Helper to build a failed ParsedConstraint."""
    return ParsedConstraint(
        original_nl=nl_line, pattern_name=pattern_name, args=args, error=error
    )


# Response-content NL patterns — matched BEFORE the generic keyword rules so
# that length / PII / content-prohibition constraints route to the det pipeline
# (see P2 of sto-refactor.md).
_LENGTH_PATTERN = re.compile(
    r"(?:response|output)\s+(?:must\s+be\s+)?(?:under|at\s+most|no\s+more\s+than|fewer\s+than|max(?:imum)?)\s+(\d+)\s+(words?|characters?|chars?)",
    re.IGNORECASE,
)
_NO_PII_PATTERN = re.compile(
    r"(?:response|output).*(?:must|should)\s+not\s+contain\s+(?:any\s+)?(pii|personal\s+info(?:rmation)?|ssns?|credit[\s-]?cards?|emails?(?:\s+address(?:es)?)?|phones?(?:\s+numbers?)?)",
    re.IGNORECASE,
)

# Map a user-mentioned PII keyword to a concrete field list for
# ``no_pii(fields=[...])``. The NL parser previously captured but
# discarded the keyword, so "response must not contain emails"
# silently expanded to the full SSN/CC/email/phone union — a
# lossy and confusing false positive.
_PII_KEYWORD_TO_FIELDS: dict[str, list[str]] = {
    "ssn": ["ssn"],
    "ssns": ["ssn"],
    "credit card": ["credit_card"],
    "credit cards": ["credit_card"],
    "credit-card": ["credit_card"],
    "credit-cards": ["credit_card"],
    "email": ["email"],
    "emails": ["email"],
    "email address": ["email"],
    "email addresses": ["email"],
    "phone": ["phone"],
    "phones": ["phone"],
    "phone number": ["phone"],
    "phone numbers": ["phone"],
}
_NO_KEYWORD_PATTERN = re.compile(
    # Must be specific to (response|output) to avoid shadowing arg_blacklist /
    # generic must-not-contain rules. Also skips if the phrase says "pii" etc.
    r"(?:response|output)\s+(?:must|should)\s+not\s+(?:contain|include|mention)\s+(?:the\s+)?(?:words?|keywords?|terms?|phrase)\s+[`\"']?([^`\"']+)[`\"']?",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Bare-word structural ordering patterns
# ---------------------------------------------------------------------------
#
# "X before Y" and "no Y after X" without backticks. We restrict these to
# short, whole-input matches so casual English ("delivered before christmas
# in the morning") doesn't get mis-routed. Both candidate words are
# stop-word filtered.

# Single-word or snake_case identifier.
_IDENT = r"[a-zA-Z][a-zA-Z0-9_]*"

# Full-input "<ident> before <ident>" — tolerates light modal fluff on
# either side ("must", "should", "always", "be", "called", "run") but
# requires the whole line to reduce to the pair of identifiers.
_BARE_PRECEDE_RE = re.compile(
    rf"""
    ^\s*
    (?:tool\s+)?
    (?P<before>{_IDENT})
    \s+
    (?:
        (?:must|should|always|needs?\s+to|has\s+to)\s+
        (?:be\s+)?(?:called\s+|run\s+|executed\s+)?
        (?:come\s+)?
    )?
    before
    \s+
    (?:tool\s+)?
    (?:calling\s+|running\s+|executing\s+)?
    (?P<after>{_IDENT})
    \s*\.?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# "no X after Y" / "cannot X after Y" — a shorthand for no_reversal.
_BARE_NO_REVERSAL_RE = re.compile(
    rf"""
    ^\s*
    (?:no|don'?t|cannot|can\s+not|must\s+not)\s+
    (?P<after>{_IDENT})
    \s+after\s+
    (?P<before>{_IDENT})
    \s*\.?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _is_plausible_tool_name(w: str) -> bool:
    """Filter for words extracted from bare-word patterns.

    Snake_case names are always accepted. Single English words are only
    accepted when they're not known stop words or common discourse
    fillers — this keeps "refund" and "cancel" in, but keeps out "it",
    "the", "this", etc.
    """
    if "_" in w:
        return True  # snake_case wins
    lw = w.lower()
    if lw in _STOP_WORDS or lw in _BARE_STOP:
        return False
    # Reject common English verbs/adverbs that read as ordering but
    # aren't tool names.
    if lw in {
        "then",
        "now",
        "later",
        "soon",
        "always",
        "ever",
        "never",
        "anything",
        "everything",
        "nothing",
        "something",
    }:
        return False
    return True


def _try_bare_ordering_patterns(text: str, nl_line: str) -> ParsedConstraint | None:
    """Recognise short "X before Y" / "no X after Y" without requiring
    backticks around the tool names.

    Returns a populated ``ParsedConstraint`` on a confident match, else
    ``None`` so the caller can fall through.
    """
    m = _BARE_PRECEDE_RE.match(text)
    if m:
        before, after = m.group("before"), m.group("after")
        if _is_plausible_tool_name(before) and _is_plausible_tool_name(after):
            try:
                formula = must_precede(before, after, desc=text)
            except Exception as e:
                return _build_error(nl_line, "must_precede", str(e), (before, after))
            return _build_constraint(nl_line, "must_precede", (before, after), formula)

    m = _BARE_NO_REVERSAL_RE.match(text)
    if m:
        before, after = m.group("before"), m.group("after")
        if _is_plausible_tool_name(before) and _is_plausible_tool_name(after):
            try:
                formula = no_reversal(before, after, desc=text)
            except Exception as e:
                return _build_error(nl_line, "no_reversal", str(e), (before, after))
            return _build_constraint(nl_line, "no_reversal", (before, after), formula)

    return None


# ---------------------------------------------------------------------------
# Trigger-atom patterns — "called `X`" and close variants
# ---------------------------------------------------------------------------
#
# Used as the assumption (``A`` side) of conditional contracts:
#
#     .assume("called `issue_refund`").enforce("must call `check_policy` before `issue_refund`")
#
# Semantically "some time in the trace, X was invoked" — compiled to
# ``F(Atom("called", X))``. Wrapping in ``F`` matches the hand-written
# form used by the packaged demos (``sponsio/demos/replay.py``) so the
# public NL builders and the internal demo contracts stay in sync.
#
# We intentionally keep these patterns narrow: the whole input must be
# a single trigger clause, otherwise we'd steal matches from the
# temporal keyword rules further down.

# Short list of phrasings that mean "X was called":
#   called `X`
#   `X` was called / is called / has been called
#   tool `X` was called / is called / has been called
#   once `X` is called / after `X` was called  (common .assume prose)
_TRIGGER_CALLED_PATTERNS = [
    re.compile(
        r"""^\s*(?:once\s+|after\s+|when\s+)?
            (?:tool\s+|action\s+)?
            `(?P<name>[^`]+)`
            \s+(?:was|is|has\s+been|gets?)\s+
            (?:called|invoked|run|executed|used)\s*\.?\s*$""",
        re.IGNORECASE | re.VERBOSE,
    ),
    re.compile(
        r"""^\s*(?:once\s+|after\s+|when\s+)?
            (?:called|invoked|ran|executed|used)
            \s+(?:tool\s+|action\s+)?
            `(?P<name>[^`]+)`\s*\.?\s*$""",
        re.IGNORECASE | re.VERBOSE,
    ),
]

# Negated trigger: "X has not been called" / "`X` was never called".
# Compiled to ``G(Not(called(X)))`` — the assumption holds iff X never
# appears in the trace.
_TRIGGER_NOT_CALLED_PATTERNS = [
    re.compile(
        r"""^\s*(?:tool\s+|action\s+)?
            `(?P<name>[^`]+)`
            \s+(?:was|is|has\s+been|gets?)\s+
            (?:never|not)\s+
            (?:called|invoked|run|executed|used)\s*\.?\s*$""",
        re.IGNORECASE | re.VERBOSE,
    ),
    re.compile(
        r"""^\s*(?:never|not)\s+
            (?:called|invoked|ran|executed|used)
            \s+(?:tool\s+|action\s+)?
            `(?P<name>[^`]+)`\s*\.?\s*$""",
        re.IGNORECASE | re.VERBOSE,
    ),
]


def _try_trigger_atom_patterns(text: str) -> ParsedConstraint | None:
    """Parse text as a trigger-atom assumption/enforcement.

    Matches short "``called `X```" / "``tool `X` was called``" phrases
    and compiles them to ``F(Atom("called", X))``. Returns ``None`` if
    no rule matches so the caller falls through to the temporal rules.
    """
    from sponsio.formulas.formula import Atom, F, G, Not

    for rx in _TRIGGER_CALLED_PATTERNS:
        m = rx.match(text)
        if m:
            name = m.group("name").strip()
            if not name:
                continue
            formula = DetFormula(
                formula=F(Atom("called", name)),
                desc=text,
                pattern_name="trigger_called",
            )
            return _build_constraint(text, "trigger_called", (name,), formula)

    for rx in _TRIGGER_NOT_CALLED_PATTERNS:
        m = rx.match(text)
        if m:
            name = m.group("name").strip()
            if not name:
                continue
            formula = DetFormula(
                formula=G(Not(Atom("called", name))),
                desc=text,
                pattern_name="trigger_not_called",
            )
            return _build_constraint(text, "trigger_not_called", (name,), formula)

    return None


def _try_response_content_patterns(text: str) -> ParsedConstraint | None:
    """Attempt to parse text as a response-content det pattern.

    Returns a populated ``ParsedConstraint`` on match, else ``None`` so
    the caller can fall through to the generic keyword rules.
    """
    from sponsio.patterns.library import max_length, no_keywords, no_pii

    # max_length — "response under 200 words" / "output at most 500 chars"
    m = _LENGTH_PATTERN.search(text)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        try:
            if "char" in unit:
                formula = max_length(max_chars=n, desc=text)
            else:
                formula = max_length(max_words=n, desc=text)
        except Exception as e:
            return _build_error(text, "max_length", str(e), (n, unit))
        return _build_constraint(text, "max_length", (n, unit), formula)

    # no_pii — "response must not contain PII" / "... emails" / "... SSN"
    #
    # When the user names a specific PII category (email, phone, SSN,
    # credit card), narrow ``fields=`` to just that one so the resulting
    # regex doesn't silently false-positive on the other three. Bare
    # "PII" / "personal information" keeps the full default union.
    m = _NO_PII_PATTERN.search(text)
    if m:
        raw_kw = re.sub(r"\s+", " ", m.group(1).lower().replace("-", " ")).strip()
        fields = _PII_KEYWORD_TO_FIELDS.get(raw_kw)
        try:
            formula = no_pii(fields=fields, desc=text)
        except Exception as e:
            return _build_error(text, "no_pii", str(e), tuple(fields or ()))
        return _build_constraint(text, "no_pii", tuple(fields or ()), formula)

    # no_keywords — "response must not mention the words 'password, secret'"
    m = _NO_KEYWORD_PATTERN.search(text)
    if m:
        raw = m.group(1).strip().rstrip(".")
        words = [w.strip() for w in re.split(r"[,\s]+", raw) if w.strip()]
        if not words:
            return None
        try:
            formula = no_keywords(words, desc=text)
        except Exception as e:
            return _build_error(text, "no_keywords", str(e), tuple(words))
        return _build_constraint(text, "no_keywords", tuple(words), formula)

    return None


def parse_dsl(expr: str) -> ParsedConstraint:
    """Parse a single contract expression in the Sponsio DSL.

    Sponsio's contract DSL is a bounded set of phrasings that map to
    pattern functions in :mod:`sponsio.patterns.library`. This parser
    is pure rule-based (regex cascade over tool-name extractors and
    keyword rules) — no grammar, no LLM.

    For free-form NL, use :func:`parse_contract` with an
    ``llm_extractor``; that path translates NL into the DSL then
    re-enters this parser.

    Args:
        expr: A single contract expression (DSL string).

    Returns:
        A ParsedConstraint with the matched pattern and compiled
        formula, or ``.error`` set if no rule matched.
    """
    # Alias kept so the body below (which grew up around the old
    # `nl_line` parameter name) stays readable after the rename.
    nl_line = expr
    text = nl_line.strip()
    if not text:
        return ParsedConstraint(original_nl=nl_line, error="Empty input")

    # --- Trigger atoms (``called \`X\``` and variants) ---
    # Used as the ``A`` side of .assume()/.enforce() A-E pairs: "once X
    # has been called, enforce E". These are short, standalone phrases
    # that don't compose with other DSL verbs, so we match them before
    # the heavier temporal patterns to avoid spurious false negatives.
    trigger_result = _try_trigger_atom_patterns(text)
    if trigger_result is not None:
        return trigger_result

    # --- P2 response-content patterns (must run before general rules to
    # override the sto keyword matcher in parse_nl_unified) ---
    response_result = _try_response_content_patterns(text)
    if response_result is not None:
        return response_result

    # --- Bare-word structural patterns (no backticks required) ---
    # Cover the common, short "X before Y" / "no Y after X" phrasings a user
    # types without bothering with backticks. Stopword-filtered to avoid
    # false positives like "delivered before christmas".
    bare_result = _try_bare_ordering_patterns(text, nl_line)
    if bare_result is not None:
        return bare_result

    match = _match_keyword_rule(text)
    if match is None:
        return ParsedConstraint(
            original_nl=nl_line,
            error=f"No pattern matched for: {text}",
        )

    pattern_name, min_args = match
    actions = _extract_actions(text)

    # --- Special handlers for patterns with numeric args or 1-arg ---

    if pattern_name == "rate_limit":
        count = _parse_rate_limit_count(text)
        if len(actions) < 1:
            return _build_error(
                nl_line, "rate_limit", "rate_limit needs at least 1 quoted action"
            )
        if count is None:
            return _build_error(
                nl_line,
                "rate_limit",
                "rate_limit needs a numeric count (e.g. 'at most 3 times')",
            )
        try:
            formula = rate_limit(actions[0], count, desc=text)
        except Exception as e:
            return _build_error(nl_line, "rate_limit", str(e), (actions[0], count))
        return _build_constraint(nl_line, "rate_limit", (actions[0], count), formula)

    if pattern_name == "bounded_retry":
        count = _parse_retry_count(text)
        if len(actions) < 1:
            return _build_error(
                nl_line, "bounded_retry", "bounded_retry needs at least 1 quoted action"
            )
        if count is None:
            return _build_error(
                nl_line,
                "bounded_retry",
                "bounded_retry needs a retry count (e.g. 'at most 3 retries')",
            )
        try:
            formula = bounded_retry(actions[0], count, desc=text)
        except Exception as e:
            return _build_error(nl_line, "bounded_retry", str(e), (actions[0], count))
        return _build_constraint(nl_line, "bounded_retry", (actions[0], count), formula)

    if pattern_name == "cooldown":
        steps = _parse_step_count(text)
        if len(actions) < 1:
            return _build_error(
                nl_line, "cooldown", "cooldown needs at least 1 quoted action"
            )
        if steps is None:
            return _build_error(
                nl_line,
                "cooldown",
                "cooldown needs a step count (e.g. 'cooldown of 2 steps')",
            )
        try:
            formula = cooldown(actions[0], steps, desc=text)
        except Exception as e:
            return _build_error(nl_line, "cooldown", str(e), (actions[0], steps))
        return _build_constraint(nl_line, "cooldown", (actions[0], steps), formula)

    if pattern_name == "deadline":
        steps = _parse_step_count(text)
        if len(actions) < 2:
            return _build_error(nl_line, "deadline", "deadline needs 2 quoted actions")
        if steps is None:
            return _build_error(
                nl_line,
                "deadline",
                "deadline needs a step count (e.g. 'within 3 steps')",
            )
        try:
            formula = deadline(actions[0], actions[1], steps, desc=text)
        except Exception as e:
            return _build_error(
                nl_line, "deadline", str(e), (actions[0], actions[1], steps)
            )
        return _build_constraint(
            nl_line, "deadline", (actions[0], actions[1], steps), formula
        )

    if pattern_name == "idempotent":
        if len(actions) < 1:
            return _build_error(
                nl_line, "idempotent", "idempotent needs at least 1 quoted action"
            )
        try:
            formula = idempotent(actions[0], desc=text)
        except Exception as e:
            return _build_error(nl_line, "idempotent", str(e), (actions[0],))
        return _build_constraint(nl_line, "idempotent", (actions[0],), formula)

    if pattern_name == "must_confirm":
        if len(actions) < 1:
            return _build_error(
                nl_line, "must_confirm", "must_confirm needs at least 1 quoted action"
            )
        try:
            formula = must_confirm(actions[0], desc=text)
        except Exception as e:
            return _build_error(nl_line, "must_confirm", str(e), (actions[0],))
        return _build_constraint(nl_line, "must_confirm", (actions[0],), formula)

    # --- arg_blacklist: extract tool, param field, and forbidden patterns ---
    if pattern_name == "arg_blacklist":
        # Extract forbidden patterns from the NL text
        # e.g. "bash command must not contain rm -rf or sudo"
        #   → arg_blacklist("bash", "command", ["rm -rf", "sudo"])
        forbidden = _extract_blacklist_patterns(text)
        if len(actions) < 1:
            return _build_error(
                nl_line,
                "arg_blacklist",
                "arg_blacklist needs at least 1 tool name",
            )
        # If 2 actions, second might be a field name; if 1, use "command" default
        tool = actions[0]
        param = actions[1] if len(actions) >= 2 else "command"
        if not forbidden:
            return _build_error(
                nl_line,
                "arg_blacklist",
                "arg_blacklist needs forbidden patterns (e.g. 'must not contain rm -rf or sudo')",
            )
        try:
            formula = arg_blacklist(tool, param, forbidden, desc=text)
        except Exception as e:
            return _build_error(nl_line, "arg_blacklist", str(e), (tool, param))
        return _build_constraint(nl_line, "arg_blacklist", (tool, param), formula)

    # --- scope_limit: extract tool and allowed path prefixes ---
    if pattern_name == "scope_limit":
        paths = _extract_paths(text)
        if len(actions) < 1 and not paths:
            return _build_error(
                nl_line,
                "scope_limit",
                "scope_limit needs a tool name and path prefixes",
            )
        tool = actions[0] if actions else "file_ops"
        if not paths:
            return _build_error(
                nl_line,
                "scope_limit",
                "scope_limit needs path prefixes (e.g. '/workspace/')",
            )
        try:
            formula = scope_limit(tool, paths, desc=text)
        except Exception as e:
            return _build_error(nl_line, "scope_limit", str(e), (tool,))
        return _build_constraint(nl_line, "scope_limit", (tool, paths), formula)

    # --- data_intact: extract tool and path prefixes ---
    if pattern_name == "data_intact":
        paths = _extract_paths(text)
        if len(actions) < 1 and not paths:
            return _build_error(
                nl_line,
                "data_intact",
                "data_intact needs a tool name or path prefixes",
            )
        tool = actions[0] if actions else ".*"
        if not paths:
            # If no paths, use a generic original data prefix
            paths = ["/data/original/"]
        try:
            formula = data_intact(tool, paths, desc=text)
        except Exception as e:
            return _build_error(nl_line, "data_intact", str(e), (tool,))
        return _build_constraint(nl_line, "data_intact", (tool, paths), formula)

    # --- always_followed_by: fix argument order ---
    # always_followed_by(trigger, response): whenever trigger fires,
    # response must eventually follow. But NL like
    # "send_email should always come after review_draft" or
    # "after calling fetch_data, always call log_result"
    # places the response first or uses "after" to indicate the trigger.
    if pattern_name == "always_followed_by" and len(actions) >= 2:
        _lower = text.lower()
        # "A should/must come after B" → trigger=B, response=A → swap
        if re.search(
            r"come\s+after|should\s+(?:always\s+)?(?:come|happen)\s+after",
            _lower,
        ):
            actions = [actions[1], actions[0]] + actions[2:]
        # "after calling A, always call B" → trigger=A, response=B (correct order)
        # "always call B after A" → trigger=A, response=B → swap
        elif re.search(r"(?:always|must)\s+(?:call|run)\s+\S+\s+after\b", _lower):
            actions = [actions[1], actions[0]] + actions[2:]

    # --- Smart routing: no_data_leak with tool names → no_reversal ---
    if pattern_name == "no_data_leak" and len(actions) >= 2:
        _DATA_FLOW_FIELDS = {
            "pii",
            "credentials",
            "personal_data",
            "sensitive",
            "secrets",
        }
        if actions[0].lower() not in _DATA_FLOW_FIELDS:
            try:
                formula = no_reversal(actions[0], actions[1], desc=text)
            except Exception as e:
                return _build_error(
                    nl_line, "no_reversal", str(e), (actions[0], actions[1])
                )
            return _build_constraint(
                nl_line, "no_reversal", (actions[0], actions[1]), formula
            )

    # --- Smart routing: requires_permission with tool-like names → must_precede ---
    if pattern_name == "requires_permission" and len(actions) >= 2:
        perm_name = actions[1]
        if "_" in perm_name or perm_name.endswith("()"):
            # Looks like a tool name → route to must_precede(permission, tool)
            try:
                formula = must_precede(perm_name, actions[0], desc=text)
            except Exception as e:
                return _build_error(
                    nl_line, "must_precede", str(e), (perm_name, actions[0])
                )
            return _build_constraint(
                nl_line, "must_precede", (perm_name, actions[0]), formula
            )

    # --- no_reversal: fix argument order ---
    # no_reversal(commitment, contradiction) expects the commitment (first
    # action) before the contradiction (second).  But NL phrases like
    # "tool `reject` must not follow `approve`" place the contradiction
    # first.  Detect this pattern and swap so that the commitment comes
    # first.
    if pattern_name == "no_reversal" and len(actions) >= 2:
        _lower = text.lower()
        if re.search(
            r"must not follow|should not follow|not allowed after|forbidden after|prohibited after|never after",
            _lower,
        ):
            actions = [actions[1], actions[0]] + actions[2:]

    # --- Standard 2-argument patterns ---
    if len(actions) < min_args:
        return _build_error(
            nl_line,
            pattern_name,
            f"{pattern_name} needs {min_args} quoted actions, found {len(actions)}",
        )

    pattern_fn = _PATTERN_REGISTRY[pattern_name]
    args = tuple(actions[:min_args])

    try:
        formula = pattern_fn(*args, desc=text)
    except Exception as e:
        return _build_error(nl_line, pattern_name, str(e), args)

    return _build_constraint(nl_line, pattern_name, args, formula)


# Backward-compatibility alias.  The rule-based parser was renamed from
# ``parse_nl_rule_based`` to ``parse_dsl`` during a refactor, but the
# old name is re-exported from :mod:`sponsio.generation.__init__` and
# used internally by the LLM-fallback path — keep both names wired up
# so no caller breaks.
parse_nl_rule_based = parse_dsl


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------


def nl_to_contracts(
    nl_text: str,
    agent: Agent | None = None,
    llm_backend: LLMBackend | None = None,
    validate: bool = True,
) -> NLParseResult:
    """Translates natural language constraint descriptions to contracts.

    Supports batch input: each non-empty line is parsed as a separate
    constraint.

    Args:
        nl_text: One or more NL constraint descriptions (one per line).
        agent: Optional agent to bind contracts to.
        llm_backend: Optional LLM backend for translation. Falls back to
            rule-based keyword matching if not provided.
        validate: If True, validates each generated formula can be
            evaluated (catches malformed formulas).

    Returns:
        An NLParseResult containing parsed constraints and any errors.
    """
    lines = [line.strip() for line in nl_text.strip().splitlines() if line.strip()]

    if not lines:
        return NLParseResult()

    result = NLParseResult()

    if llm_backend is not None:
        _parse_with_llm(lines, llm_backend, result)
    else:
        for line in lines:
            parsed = parse_nl_rule_based(line)
            result.constraints.append(parsed)

    # Validate formulas
    if validate:
        for constraint in result.constraints:
            if constraint.formula is not None:
                _validate_formula(constraint)

    return result


def _parse_with_llm(
    lines: list[str],
    backend: LLMBackend,
    result: NLParseResult,
) -> None:
    """Parses NL lines using an LLM backend."""
    full_text = "\n".join(lines)
    pattern_names = list(_PATTERN_REGISTRY.keys())

    try:
        translations = backend.translate(full_text, pattern_names)
    except Exception as e:
        # If LLM fails, fall back to rule-based for each line
        for line in lines:
            parsed = parse_nl_rule_based(line)
            if not parsed.ok:
                parsed.error = (
                    f"LLM failed ({e}), rule-based fallback also failed: {parsed.error}"
                )
            result.constraints.append(parsed)
        return

    for i, translation in enumerate(translations):
        original = lines[i] if i < len(lines) else f"(LLM output {i})"
        pattern_name = translation.get("pattern", "")
        args = tuple(translation.get("args", []))
        kwargs = translation.get("kwargs", {})

        if pattern_name not in _PATTERN_REGISTRY:
            result.constraints.append(
                ParsedConstraint(
                    original_nl=original,
                    pattern_name=pattern_name,
                    args=args,
                    error=f"Unknown pattern: {pattern_name}",
                )
            )
            continue

        pattern_fn = _PATTERN_REGISTRY[pattern_name]
        try:
            formula = pattern_fn(*args, **kwargs)
        except Exception as e:
            result.constraints.append(
                ParsedConstraint(
                    original_nl=original,
                    pattern_name=pattern_name,
                    args=args,
                    error=f"Pattern call failed: {e}",
                )
            )
            continue

        result.constraints.append(
            ParsedConstraint(
                original_nl=original,
                pattern_name=pattern_name,
                args=args,
                kwargs=kwargs,
                formula=formula,
            )
        )


def _validate_formula(constraint: ParsedConstraint) -> None:
    """Validates that a formula can be evaluated on a minimal trace."""
    if constraint.formula is None:
        return

    raw = constraint.formula.formula
    try:
        # Evaluate on a minimal empty trace — should not raise
        evaluate(raw, [{}])
    except Exception as e:
        constraint.error = f"Validation failed: {e}"
        constraint.formula = None


# ---------------------------------------------------------------------------
# Unified parse: auto-detect det vs sto
# ---------------------------------------------------------------------------

# Keyword rules for sto category classification — LEGACY closure categories
# only. The atom-registered evaluators (injection_free, jailbreak_free,
# toxic_free, harmful, semantic_pii_free, …) carry their NL keywords on
# their own registry entries — see
# :func:`sponsio.patterns.sto_registry.list_sto_atom_infos`. Adding a
# new atom-registered evaluator auto-wires NL routing here via
# :func:`_iter_atom_keyword_rules` without needing to edit this file.
_STO_KEYWORDS: list[tuple[str, str, dict]] = [
    # PII (regex-based, fast)
    ("pii", r"\bpii\b|personal\s+information|ssn|credit\s+card|social\s+security", {}),
    ("pii", r"not\s+contain\s+(?:any\s+)?(?:pii|personal|sensitive)", {}),
    # Length
    (
        "length",
        r"(?:under|at\s+most|no\s+more\s+than|fewer\s+than|max(?:imum)?)\s+(\d+)\s+(?:words?|characters?|chars?)",
        {},
    ),
    # Format
    ("format", r"\bjson\s+format\b", {"expected_format": "json"}),
    ("format", r"\bmarkdown\b", {"expected_format": "markdown"}),
    (
        "format",
        r"\bbullet\s*points?\b|\bbulleted?\b",
        {"expected_format": "bullet_points"},
    ),
    # Tone
    (
        "tone",
        r"\b(?:empathetic|empathy|professional|friendly|formal|polite|neutral|respectful|warm|courteous)\b",
        {},
    ),
    # Relevance
    ("relevance", r"\brelevant\s+to\b|\brelated\s+to\b|\babout\b|\bon[- ]topic\b", {}),
    # Content prohibition
    (
        "content_prohibition",
        r"\bmust\s+not\s+(?:contain|include|mention)\b|\bshould\s+not\s+(?:contain|include|mention)\b|\bavoid\s+mention",
        {},
    ),
]


def _iter_atom_keyword_rules() -> list[tuple[str, str, dict]]:
    """Auto-generate ``(predicate, keyword_regex, extra)`` tuples from the
    sto atom registry for any atom with ``required_args == 0`` and at
    least one ``nl_keywords`` entry.

    Adding a new arg-less atom requires only registering it with
    ``@register_sto_atom(..., nl_keywords=[...])`` — this iterator
    surfaces it to the rule-based NL parser automatically.
    """
    from sponsio.patterns.sto_registry import list_sto_atom_infos

    rules: list[tuple[str, str, dict]] = []
    for info in list_sto_atom_infos():
        if info.required_args != 0 or not info.nl_keywords:
            continue
        pattern = "|".join(info.nl_keywords)
        rules.append((info.predicate, pattern, {}))
    return rules


def _build_atom_sto_formula(category: str, nl_text: str) -> Any:
    """Wrap an atom-registered evaluator in a ``StoFormula(formula=...)``.

    Uses the atom's registered ``default_context_scope`` /
    ``default_output_type`` so each atom declares its own preferred
    shape once (in :mod:`sponsio.patterns.sto_catalog`) and the parser
    respects it automatically.
    """
    from sponsio.formulas.formula import Atom, G
    from sponsio.patterns.sto import StoFormula
    from sponsio.patterns.sto_registry import get_sto_atom_info

    info = get_sto_atom_info(category)
    atom = Atom(
        category,
        atom_type="sto",
        output_type=info.default_output_type,
        context_scope=info.default_context_scope,
    )
    return StoFormula(
        desc=nl_text,
        category=category,
        formula=G(atom),
        threshold=0.7,
        requires_llm=True,
    )


class ContractSyntaxError(ValueError):
    """Raised when a contract expression doesn't match the Sponsio
    contract DSL and no LLM extractor is available to translate free-form
    NL into DSL.

    Sponsio's rule-based contract parser is a DSL (see
    :mod:`sponsio.generation.nl_to_contract`) — a bounded set of phrasings
    that map to patterns in :mod:`sponsio.patterns.library`. Input that
    doesn't match any rule is a syntax error in that DSL, not a "probably
    sto, let's no-op it" situation. Raising is the only way to prevent
    silently-dead contracts from slipping into production.

    Attributes:
        expr: The offending contract expression.
        hint: Human-readable suggestion (e.g. "did you mean `A` precedes `B`?").
    """

    def __init__(self, expr: str, hint: str = "") -> None:
        self.expr = expr
        self.hint = hint
        msg = f"Unparseable contract: {expr!r}"
        if hint:
            msg += f". {hint}"
        super().__init__(msg)


@dataclass
class ContractParseResult:
    """Result of parsing a contract expression (DSL or LLM-translated NL)."""

    original_nl: str
    hard: DetFormula | None = None
    sto: Any = None  # StoFormula, imported lazily to avoid circular
    error: str = ""

    @property
    def is_det(self) -> bool:
        return self.hard is not None

    @property
    def is_sto(self) -> bool:
        return self.sto is not None

    @property
    def ok(self) -> bool:
        return self.hard is not None or self.sto is not None


# Backwards-compatible alias. New code should use ``ContractParseResult``.
UnifiedParseResult = ContractParseResult


# Backwards-compatible alias. New code should use :func:`parse_dsl`.
parse_nl_rule_based = parse_dsl


def classify_sto(nl_text: str, llm_client: Any = None) -> Any:
    """Classify an NL string as a sto constraint.

    Tries keyword matching first (zero dependencies), then — if an
    ``llm_client`` is available — falls back to a generic
    :func:`llm_judge_evaluator` wrapper.

    Returns ``None`` if no keyword category matches and no LLM client
    is available. The caller (:func:`parse_contract`) turns that
    ``None`` into a :class:`ContractSyntaxError`; there is no longer a
    "silently return a no-op stub" path — stubs were the single
    biggest silent-wrong failure mode and have been removed.

    Args:
        nl_text: Natural language constraint text.
        llm_client: Optional OpenAI client for LLM-based evaluators.

    Returns:
        A :class:`StoFormula`, or ``None`` if classification fell
        through and no LLM client was provided.
    """
    from sponsio.patterns.sto import StoFormula
    from sponsio.patterns.sto_catalog import (
        content_prohibition_evaluator,
        format_evaluator,
        length_evaluator,
        llm_judge_evaluator,
        pii_evaluator,
        relevance_evaluator,
        tone_evaluator,
    )

    text_lower = nl_text.lower()

    # Phase 1 — atom-registered evaluators (self-described via the
    # sto registry's ``nl_keywords`` metadata). Checked first so newly
    # added atoms get auto-routing without touching this function.
    for category, pattern, _extra in _iter_atom_keyword_rules():
        if re.search(pattern, text_lower):
            return _build_atom_sto_formula(category, nl_text)

    # Phase 2 — legacy closure categories (hardcoded below for tight
    # arg extraction: length numbers, tone adjectives, format variants
    # etc. that need per-category parsing logic).
    for category, pattern, extra in _STO_KEYWORDS:
        match = re.search(pattern, text_lower)
        if not match:
            continue

        if category == "pii":
            return StoFormula(
                desc=nl_text,
                category="pii",
                evaluator_fn=pii_evaluator(),
                threshold=0.9,
                feedback_template="Remove all PII from the response. {evidence}. {suggestion}",
                requires_llm=False,
            )

        if category == "length":
            num = int(match.group(1))
            if "char" in text_lower:
                fn = length_evaluator(max_chars=num)
            else:
                fn = length_evaluator(max_words=num)
            return StoFormula(
                desc=nl_text,
                category="length",
                evaluator_fn=fn,
                threshold=0.9,
                requires_llm=False,
            )

        if category == "format":
            fmt = extra.get("expected_format", "json")
            return StoFormula(
                desc=nl_text,
                category="format",
                evaluator_fn=format_evaluator(fmt),
                threshold=0.9,
                requires_llm=False,
            )

        if category == "tone":
            # Extract the tone word from the match
            tone_match = re.search(
                r"\b(empathetic|empathy|professional|friendly|formal|polite|neutral|respectful|warm|courteous)\b",
                text_lower,
            )
            tone = tone_match.group(1) if tone_match else "professional"
            return StoFormula(
                desc=nl_text,
                category="tone",
                evaluator_fn=tone_evaluator(tone, client=llm_client),
                threshold=0.6,
                feedback_template=f"Rewrite the response to be more {tone}. {{evidence}}. {{suggestion}}",
                requires_llm=True,
            )

        if category == "relevance":
            # Extract topic after "relevant to" / "related to"
            topic_match = re.search(r"(?:relevant|related)\s+to\s+(.+)", text_lower)
            topic = (
                topic_match.group(1).strip().rstrip(".") if topic_match else "the topic"
            )
            return StoFormula(
                desc=nl_text,
                category="relevance",
                evaluator_fn=relevance_evaluator(topic, client=llm_client),
                threshold=0.6,
                requires_llm=True,
            )

        if category == "content_prohibition":
            # Extract what's prohibited
            prohibition_match = re.search(
                r"(?:not\s+(?:contain|include|mention)|avoid\s+mention(?:ing)?)\s+(.+)",
                text_lower,
            )
            prohibited = (
                prohibition_match.group(1).strip().rstrip(".")
                if prohibition_match
                else ""
            )
            if prohibited:
                return StoFormula(
                    desc=nl_text,
                    category="content_prohibition",
                    evaluator_fn=content_prohibition_evaluator(prohibited),
                    threshold=0.9,
                    requires_llm=False,
                )

    # Fallback: generic LLM judge if a client is available. Otherwise
    # signal that we couldn't classify this text — the caller decides
    # what to do (parse_contract turns it into a ContractSyntaxError).
    if llm_client is not None:
        return StoFormula(
            desc=nl_text,
            category="custom",
            evaluator_fn=llm_judge_evaluator(nl_text, client=llm_client),
            threshold=0.6,
            requires_llm=True,
        )
    return None


def parse_contract(
    expr: str,
    llm_client: Any = None,
    llm_extractor: Any = None,
    tool_inventory: list[dict] | None = None,
) -> ContractParseResult:
    """Parse a contract expression into either a det or sto Formula.

    Sponsio's rule-based contract parser is a DSL on top of
    :mod:`sponsio.patterns.library` — a bounded set of phrasings that map
    to the pattern library. The two built-in stages are pure regex/keyword
    matching; the optional third stage is an LLM translator from free-form
    NL → DSL.

    Parsing pipeline (cheapest first, strict-by-default):

    1. **DSL det patterns** — :func:`parse_dsl` matches against
       ``_KEYWORD_RULES`` (and the bare-word ordering rules).
    2. **DSL sto patterns** — :func:`classify_sto` matches against
       ``_STO_KEYWORDS`` (pii / injection_free / jailbreak_free / …).
    3. **LLM translator** — :class:`UnifiedExtractor` turns free-form NL
       into DSL patterns. Only invoked when ``llm_extractor`` is passed.

    If none of the above yields a match the parser raises
    :class:`ContractSyntaxError`. It does **not** return a silent no-op
    stub — a contract that compiles but enforces nothing is the single
    worst failure mode of a contract system.

    Args:
        expr: A single contract expression (DSL string or free-form NL).
        llm_client: Optional OpenAI client used by ``classify_sto`` to
            build a generic ``llm_judge`` evaluator as a sto fallback.
        llm_extractor: Optional :class:`UnifiedExtractor`. When set,
            free-form NL that doesn't match the rule-based DSL is sent
            to the LLM to be translated into DSL patterns.
        tool_inventory: Optional known tool names (passed to the LLM).

    Returns:
        A :class:`ContractParseResult` with either ``.hard`` or ``.sto``
        populated.

    Raises:
        ContractSyntaxError: If nothing matched and no LLM extractor is
            available to translate.
    """
    # Stage 1: DSL det pattern (regex, milliseconds)
    parsed = parse_dsl(expr)
    if parsed.ok:
        return ContractParseResult(original_nl=expr, hard=parsed.formula)

    # Stage 2: DSL sto pattern (regex, milliseconds)
    sto_result = classify_sto(expr, llm_client=llm_client)
    if sto_result is not None:
        return ContractParseResult(original_nl=expr, sto=sto_result)

    # Stage 3: LLM translator (seconds, only if provided)
    if llm_extractor is not None:
        try:
            from sponsio.generation.llm_extraction import ExtractionResult

            results: list[ExtractionResult] = llm_extractor.extract_from_nl(
                expr,
                tool_inventory=tool_inventory,
            )
            for r in results:
                if r.ok:
                    if r.constraint_type == "det":
                        return ContractParseResult(original_nl=expr, hard=r.compiled)
                    else:
                        return ContractParseResult(original_nl=expr, sto=r.compiled)
        except Exception as e:
            import logging

            logging.getLogger(__name__).debug(
                "LLM translation failed for %r: %s", expr, e
            )

    # Strict: nothing matched — raise with a targeted hint.
    raise ContractSyntaxError(expr, hint=_syntax_hint(expr))


# Backwards-compatible alias. New code should use :func:`parse_contract`.
def parse_nl_unified(
    nl_line: str,
    llm_client: Any = None,
    llm_extractor: Any = None,
    tool_inventory: list[dict] | None = None,
) -> ContractParseResult:
    """Deprecated alias for :func:`parse_contract`.

    Preserved so existing callers keep working. New code should use
    ``parse_contract(expr, ...)`` and catch :class:`ContractSyntaxError`.
    """
    return parse_contract(
        nl_line,
        llm_client=llm_client,
        llm_extractor=llm_extractor,
        tool_inventory=tool_inventory,
    )


def _syntax_hint(expr: str) -> str:
    """Return a targeted suggestion for an unparseable contract expression."""
    lower = expr.lower()
    # Ordering-without-backticks is the single most common miss.
    if "before" in lower or " after " in lower or "precede" in lower:
        return (
            "If this is an ordering rule, wrap tool names in backticks: "
            "``tool `A` must precede `B```, or use snake_case identifiers so "
            "the bare-word matcher can pick them up."
        )
    if "at most" in lower or "at least" in lower or "times" in lower:
        return "If this is a rate limit, use the form ``tool `X` at most N times``."
    if "not contain" in lower or "must not" in lower:
        return (
            "If this is a content rule, see sponsio patterns (e.g. PII, "
            "toxic_free, injection_free) or wrap tool names in backticks."
        )
    return (
        "Expected a contract expression that matches one of the patterns "
        "listed by `sponsio patterns`. Pass `llm_extractor=...` to "
        "`parse_contract` to allow free-form NL → DSL translation."
    )


def build_contracts(
    nl_text: str,
    agent: Agent,
    llm_backend: LLMBackend | None = None,
) -> list[Contract]:
    """Convenience: parse NL text and emit one unconditional Contract per rule.

    Each successfully parsed rule becomes its own :class:`Contract` with
    ``assumption=None``. No implicit joining across rules — the monitor
    evaluates each contract independently.

    Args:
        nl_text: One or more NL constraint descriptions.
        agent: The agent to bind the contracts to.
        llm_backend: Optional LLM backend.

    Returns:
        A list of ``Contract`` objects, one per parsed rule.

    Raises:
        ValueError: If no constraints could be parsed.
    """
    result = nl_to_contracts(nl_text, agent=agent, llm_backend=llm_backend)
    formulas = result.formulas
    if not formulas:
        errors = "; ".join(e.error for e in result.errors)
        raise ValueError(f"No constraints parsed from NL input. Errors: {errors}")
    return [Contract(agent=agent, enforcement=f) for f in formulas]


# Backward-compatible alias that returns a single Contract with a list
# enforcement — used by older callers that expect a single object.
def build_contract(
    nl_text: str,
    agent: Agent,
    llm_backend: LLMBackend | None = None,
) -> Contract:
    """Deprecated: returns a single Contract whose enforcement is the list
    of parsed formulas. Prefer :func:`build_contracts` (plural)."""
    contracts = build_contracts(nl_text, agent, llm_backend)
    enforcements = [c.enforcement for c in contracts]
    return Contract(
        agent=agent,
        enforcement=enforcements if len(enforcements) > 1 else enforcements[0],
    )
