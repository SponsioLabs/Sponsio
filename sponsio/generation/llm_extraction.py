"""Unified LLM-based constraint extraction layer.

This module provides a single LLM prompt + compilation pipeline shared by
all three input paths:

1. **YAML/inline NL** — guarantee strings that fail rule-based parsing
2. **Policy documents** — natural language SOPs, compliance docs
3. **Code scanning** — tool inventory + source context → inferred constraints

The LLM is told about the **Atom vocabulary** (what grounding can observe)
and the **Pattern catalog** (how Atoms compose into LTL/arithmetic formulas).
Its structured JSON output is compiled into ``DetFormula`` (hard) or
``StoFormula`` (sto) objects via the pattern registry.

Design principles:

- **Atom-grounded**: The LLM prompt enumerates every Atom predicate that
  ``grounding.py`` can produce.  This prevents the LLM from hallucinating
  patterns that can't be evaluated at runtime.
- **Pattern-compiled**: The LLM output references pattern function names
  and args.  Compilation goes through the same ``_PATTERN_REGISTRY`` used
  by rule-based parsing, so formulas are identical regardless of input path.
- **Det/sto auto-classification**: The LLM classifies each constraint as
  ``"hard"`` (enforceable via tool-call trace) or ``"sto"`` (requires
  content/LLM evaluation).  This replaces the heuristic keyword routing.
- **Validation built-in**: Every compiled formula is test-evaluated on an
  empty trace to catch malformed outputs before they reach the monitor.

Usage::

    from sponsio.generation.llm_extraction import UnifiedExtractor

    extractor = UnifiedExtractor()  # uses OPENAI_API_KEY

    # Path 1: single NL string
    results = extractor.extract_from_nl("refunds require a policy check first")

    # Path 2: policy document
    results = extractor.extract_from_document(policy_text)

    # Path 3: code scan (provide tool inventory for context)
    results = extractor.extract_from_code(
        tool_inventory=[
            {"name": "check_policy", "docstring": "Check refund eligibility"},
            {"name": "issue_refund", "docstring": "Process a refund"},
        ],
        source_snippet="...",
    )
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from sponsio.patterns.library import DetFormula

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Atom vocabulary — the full set of observable predicates
# ---------------------------------------------------------------------------

_BUILTIN_ATOMS = """\
Tool-call atoms (fired when event_type == "tool_call"):
  called(tool_name)                    — tool was invoked at this timestep
  called_with(tool, pattern)           — tool called AND args match regex pattern
  count(tool_name)                     — cumulative invocation count (integer)
  count_with(tool, pattern)            — cumulative count of tool+pattern matches
  perm(permission)                     — the agent holds this permission
  arg_has(tool, pattern)               — tool's serialized args match regex
  arg_field_has(tool, field, pattern)  — specific arg field matches regex
  arg_length_exceeds(tool, field, N)   — arg field length > N chars (injection detection)
  arg_paths_within(tool, prefix, ...)  — all file paths in args are within allowed prefixes
  arg_numeric(tool, field)             — numeric value of a numeric arg field
                                         (use inside Var(...) for arithmetic, e.g.
                                         G(Le(Var(arg_numeric, wire_transfer, amount), Const(50000))))
                                         to express bounds like "amount must be ≤ 50000".

Data-flow atoms (fired on data_read / data_write / message events):
  contains(field)                      — a data_write event included this field
  flow(source_agent, dest_agent)       — data flowed from source to destination

Content-observation atoms (fired on tool output / LLM response / LLM request):
  output_has(tool, pattern)            — tool output matches regex
  llm_said(pattern)                    — LLM response matches regex
  prompt_contains(pattern)             — LLM input prompt matches regex
  system_prompt_present()              — LLM request has a system message
  context_length()                     — total char count of LLM input (integer)

IMPORTANT — tool:pattern format for logical operations:
  When an agent uses a single tool (e.g. "bash") for all operations, use
  "tool:pattern" syntax to distinguish different commands by their args.
  Example: "bash:sed -i" means "bash tool when args match 'sed -i'".
  This works with ALL patterns:
    must_precede("bash:run_check", "bash:generate_report")
    rate_limit("bash:sed -i", 0)    — ban sed -i entirely
    arg_blacklist("bash", "command", ["rm -rf", "chmod.*\\+x"])
"""


_STO_ATOM_PREAMBLE = """\
Sto atoms (fired on output/LLM content, return confidence ∈ [0,1] —
build them with atom_type="sto" inside a Formula AST, e.g.
G(Atom("injection_free", atom_type="sto", output_type="classify",
        context_scope="event"))):

"""


def _render_sto_atoms() -> str:
    """Auto-generate the sto-atom section of the LLM prompt from the
    :mod:`sponsio.patterns.sto_registry` metadata.

    Adding a new atom with ``@register_sto_atom(..., description=...,
    required_args=..., default_context_scope=...)`` auto-populates this
    section — the LLM sees the new atom without touching this module.
    """
    try:
        from sponsio.patterns.sto_registry import list_sto_atom_infos
    except ImportError:
        return ""

    infos = list_sto_atom_infos()
    if not infos:
        return ""

    argless = [i for i in infos if i.required_args == 0]
    argful = [i for i in infos if i.required_args > 0]

    lines: list[str] = [_STO_ATOM_PREAMBLE.rstrip()]
    lines.append("")

    if argless:
        lines.append("  Arg-less atoms (score the last response directly):")
        for info in argless:
            desc = info.description or "(no description registered)"
            lines.append(f"    {info.predicate:<22s} — {desc}")
        lines.append("")

    if argful:
        lines.append("  Arg-ful atoms (embed the required value as Atom's first arg):")
        for info in argful:
            arity = "arg" if info.required_args == 1 else f"{info.required_args} args"
            desc = info.description or "(no description registered)"
            scope = info.default_context_scope
            scope_hint = f" [context_scope={scope!r}]" if scope != "event" else ""
            lines.append(f"    {info.predicate:<18s}({arity})  — {desc}{scope_hint}")
        lines.append("")

    lines.append(
        "  Prefer these over 'custom LLM judge' whenever the constraint fits one\n"
        "  of the above shapes — they have hand-tuned prompts and the right\n"
        '  default context_scope. Use context_scope="full_trace" for cross-turn\n'
        '  checks; "event" (the default) for single-response checks.'
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Extensible atom registry
# ---------------------------------------------------------------------------

# User-defined atoms: list of (atom_signature, description) tuples.
# These are appended to the LLM prompt alongside built-in atoms.
_custom_atoms: list[tuple[str, str]] = []


def register_atom(signature: str, description: str) -> None:
    """Register a custom atom predicate for the LLM prompt.

    Custom atoms extend the built-in vocabulary so the LLM knows it can
    reference them when extracting constraints.  The corresponding
    grounding logic must be registered separately in ``grounding.py``.

    Args:
        signature: Atom signature, e.g. ``"latency(tool_name)"``.
        description: Human-readable description, e.g. ``"response latency in ms (integer)"``.

    Example::

        from sponsio.generation.llm_extraction import register_atom
        register_atom("latency(tool_name)", "response latency in ms (integer)")
        register_atom("user_role(role)", "current user's role string")
    """
    _custom_atoms.append((signature, description))


def register_atoms(atoms: list[tuple[str, str]] | dict[str, str]) -> None:
    """Register multiple custom atoms at once.

    Accepts either a list of ``(signature, description)`` tuples or a
    dict mapping signature → description.

    Example::

        register_atoms({
            "latency(tool_name)": "response latency in ms",
            "user_role(role)": "current user's role string",
        })
    """
    if isinstance(atoms, dict):
        atoms = list(atoms.items())
    _custom_atoms.extend(atoms)


def get_custom_atoms() -> list[tuple[str, str]]:
    """Return a copy of all registered custom atoms."""
    return list(_custom_atoms)


def clear_custom_atoms() -> None:
    """Remove all registered custom atoms (mainly for testing)."""
    _custom_atoms.clear()


def _build_atom_vocabulary() -> str:
    """Build the full atom vocabulary text (built-in + sto-registry +
    custom).

    Sections:

    * ``_BUILTIN_ATOMS`` — hand-curated det atoms (tool / data-flow /
      content-observation) with the ``tool:pattern`` idiom notes.
    * Auto-rendered sto atoms — one entry per
      :func:`sponsio.patterns.sto_registry.register_sto_atom` call,
      grouped by arg-count. Adding a new atom requires no edit here.
    * Custom user-registered atoms via :func:`register_atom` /
      :func:`register_atoms`.
    """
    header = (
        "The runtime monitor observes agent execution as a linear trace of events.\n"
        "Each event is grounded into atomic predicates.  These are ALL the atoms\n"
        "the system can observe — do NOT invent atoms outside this list:\n\n"
    )
    text = header + _BUILTIN_ATOMS

    sto_section = _render_sto_atoms()
    if sto_section:
        text += "\n" + sto_section + "\n"

    if _custom_atoms:
        lines = ["Custom atoms (domain-specific, registered by the user):"]
        for sig, desc in _custom_atoms:
            lines.append(f"  {sig:<40s} — {desc}")
        text += "\n" + "\n".join(lines) + "\n"

    return text


# ---------------------------------------------------------------------------
# Pattern catalog — compiled descriptions for the LLM
# ---------------------------------------------------------------------------

_PATTERN_CATALOG = """\
Det constraint patterns (enforceable on the tool-call trace, binary pass/fail):

  must_precede(before, after)
    — "before" must be called before "after" can be called
    Example NL: "must check policy before issuing refund"

  always_followed_by(trigger, response)
    — whenever "trigger" is called, "response" must eventually follow
    Example NL: "every database query must be followed by a log entry"

  mutual_exclusion(a, b)
    — at most one of a, b may ever be called in the session
    Example NL: "approve and reject are mutually exclusive"

  no_reversal(commitment, contradiction)
    — once "commitment" fires, "contradiction" is forever forbidden
    Example NL: "cannot deny a refund after approving it"

  rate_limit(action, max_count)
    — action may be called at most max_count times total
    Example NL: "at most 3 refunds per session"

  idempotent(action)
    — action may be called at most once
    Example NL: "deployment must be idempotent"

  bounded_retry(action, max_retries)
    — action limited to max_retries attempts
    Example NL: "retry API call at most 5 times"

  cooldown(action, steps)
    — minimum steps between consecutive calls
    Example NL: "wait at least 2 steps between API calls"

  deadline(trigger, action, steps)
    — action must occur within steps after trigger
    Example NL: "must respond within 3 steps of receiving a complaint"

  must_confirm(action)
    — confirm_{action} must be called before action
    Example NL: "delete requires confirmation"

  segregation_of_duty(a, b)
    — same agent cannot perform both a and b
    Example NL: "reviewer and approver must be different"

  requires_permission(tool, permission)
    — tool requires the agent to hold a permission
    Example NL: "delete_user requires admin permission"

  no_data_leak(source_field, external_agent)
    — data from source must not flow to external agent
    Example NL: "PII must not be sent to external API"

  arg_blacklist(tool, param, [patterns...])
    — forbid regex patterns in a tool argument field
    Example NL: "bash command must not contain rm -rf or sudo"

  scope_limit(tool, [allowed_path_prefixes...])
    — restrict tool's file operations to allowed paths
    Example NL: "file operations restricted to /workspace/"

  arg_length_limit(tool, param, max_chars)
    — block if argument field exceeds length limit (injection detection)
    Example: arg_length_limit("bash", "command", 500)
    Use to detect code injection where agent inlines a full script into args

  data_intact(bound_tool_regex, [original_path_prefixes...])
    — tool must only operate on original unmodified data
    Example NL: "grep must only read from /data/original/"

Sto constraint categories (evaluated on content, scored 0.0-1.0):

  pii — response must not contain personally identifiable information
    Optional params: fields (list of PII types: ssn, email, credit_card, phone)

  length — response length constraint
    Params: max_words (int) and/or max_chars (int)

  format — response format validation
    Params: format (one of: json, markdown, bullet_points)

  tone — response tone/style (requires LLM evaluation)
    Params: desired_tone (string, e.g. "empathetic", "professional")

  relevance — response must be relevant to topic (requires LLM evaluation)
    Params: topic (string)

  content_prohibition — response must not mention specific content
    Params: prohibited (string)
"""

# ---------------------------------------------------------------------------
# Extraction result
# ---------------------------------------------------------------------------


@dataclass
class ExtractionResult:
    """Result of LLM-based constraint extraction.

    Attributes:
        constraint_type: ``"hard"`` or ``"sto"``.
        pattern_name: Pattern function name (hard) or sto category.
        args: Positional arguments for the pattern function.
        kwargs: Keyword arguments (e.g. ``{"desc": "..."}``).
        confidence: LLM's confidence in this extraction (0.0-1.0).
        nl_description: Original or rephrased NL text.
        source_quote: Exact text from source that implies this constraint.
        compiled: The compiled ``DetFormula`` or ``StoFormula``, if successful.
        error: Error message if compilation failed.
    """

    constraint_type: str = "hard"  # "hard" or "sto"
    pattern_name: str = ""
    args: list = field(default_factory=list)
    kwargs: dict = field(default_factory=dict)
    confidence: float = 0.5
    nl_description: str = ""
    source_quote: str = ""
    compiled: Any = None  # DetFormula | StoFormula | None
    compiled_assumption: Any = None  # DetFormula | None — assumption formula
    assumption_raw: str = ""  # Raw assumption string (LTL or NL) for round-tripping
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.compiled is not None and not self.error


# ---------------------------------------------------------------------------
# Suggestion engine for parse/compilation failures
# ---------------------------------------------------------------------------


def _suggest_pattern(text: str, error: str) -> str:
    """Generate a helpful suggestion when extraction/compilation fails."""
    suggestions = []

    # Check if the issue is about missing tool names
    if "args" in error.lower() or "argument" in error.lower():
        suggestions.append(
            "Ensure tool names are specified. Use backtick-quoted names: `tool_name`"
        )

    # Check if pattern name is close to a known pattern
    from sponsio.generation.nl_to_contract import _PATTERN_REGISTRY

    lower_text = text.lower()
    for pname in _PATTERN_REGISTRY:
        readable = pname.replace("_", " ")
        if readable in lower_text or pname in lower_text:
            suggestions.append(f"Looks like pattern '{pname}' — check args format")
            break

    if not suggestions:
        suggestions.append(
            "Try rephrasing with explicit tool names and one of: "
            "must_precede, rate_limit, mutual_exclusion, no_reversal, "
            "idempotent, must_confirm, cooldown, deadline, bounded_retry, "
            "segregation_of_duty, requires_permission, no_data_leak, "
            "arg_blacklist, arg_length_limit, scope_limit"
        )

    return " | ".join(suggestions)


# ---------------------------------------------------------------------------
# Compilation: JSON → DetFormula / StoFormula
# ---------------------------------------------------------------------------


def _compile_det(item: dict) -> ExtractionResult:
    """Compile a single det constraint from LLM JSON output.

    Supports two paths:
    - "formula" field: direct formula string → parse into AST
    - "pattern" + "args": legacy pattern function call
    """
    from sponsio.formulas.evaluator import evaluate

    confidence = float(item.get("confidence", 0.5))
    source_quote = item.get("source_quote", "")
    # Support both old "formula" key and new "guarantee" key
    formula_str = item.get("guarantee", "") or item.get("formula", "")
    assumption_str = item.get("assumption") or None
    pattern_name = item.get("pattern", "")
    args = item.get("args", [])
    nl = item.get("nl", "")

    result = ExtractionResult(
        constraint_type="det",
        pattern_name=pattern_name or "formula",
        args=args,
        confidence=confidence,
        nl_description=nl,
        source_quote=source_quote,
    )

    # Path 1: Direct formula string
    if formula_str:
        try:
            from sponsio.formulas.parser import parse_formula
            from sponsio.formulas.nl_gen import formula_to_nl
            from sponsio.patterns.library import DetFormula

            ast_node = parse_formula(formula_str)
            generated_nl = formula_to_nl(ast_node)
            result.nl_description = nl or generated_nl
            result.compiled = DetFormula(
                formula=ast_node,
                desc=result.nl_description,
                pattern_name="formula",
            )

            # Parse assumption if provided
            if assumption_str and assumption_str != "null":
                try:
                    assumption_ast = parse_formula(assumption_str)
                    assumption_nl = formula_to_nl(assumption_ast)
                    result.compiled_assumption = DetFormula(
                        formula=assumption_ast,
                        desc=f"assumes: {assumption_nl}",
                        pattern_name="assumption",
                    )
                    # Keep the raw form so downstream emitters (e.g.
                    # generate_yaml) can round-trip it as `A:`.
                    result.assumption_raw = assumption_str
                except Exception as e:
                    logger.warning(
                        "Assumption parse failed: %s — %s", assumption_str, e
                    )

        except Exception as e:
            result.error = f"Formula parse failed: {e} — input: {formula_str}"
            return result

        # Validate on empty trace
        try:
            evaluate(result.compiled.formula, [])
        except Exception as e:
            result.error = f"Formula validation failed: {e}"
            result.compiled = None
            return result

        return result

    # Path 2: Legacy pattern function call
    from sponsio.generation.nl_to_contract import _PATTERN_REGISTRY

    if pattern_name not in _PATTERN_REGISTRY:
        result.error = (
            f"Unknown pattern '{pattern_name}'. "
            f"Available: {sorted(_PATTERN_REGISTRY.keys())}"
        )
        return result

    # Convert args: ensure correct types
    typed_args = []
    for a in args:
        if isinstance(a, (int, float)):
            typed_args.append(a)
        elif isinstance(a, str):
            try:
                typed_args.append(int(a))
            except ValueError:
                typed_args.append(a)
        elif isinstance(a, list):
            typed_args.append(a)
        else:
            typed_args.append(str(a))

    try:
        formula = _PATTERN_REGISTRY[pattern_name](*typed_args, desc=nl)
        result.compiled = formula
    except Exception as e:
        result.error = f"Compilation failed for {pattern_name}({args}): {e}"
        suggestion = _suggest_pattern(nl, result.error)
        if suggestion:
            result.error += f" — Suggestion: {suggestion}"
        return result

    # Validate on empty trace
    try:
        evaluate(formula.formula, [])
    except Exception as e:
        result.error = f"Formula validation failed: {e}"
        result.compiled = None
        return result

    return result


def _compile_sto(item: dict) -> ExtractionResult:
    """Compile a single sto constraint from LLM JSON output."""
    from sponsio.patterns.sto import StoFormula
    from sponsio.patterns.sto_catalog import _SOFT_CATALOG

    category = item.get("category", "custom")
    params = item.get("params", {})
    nl = item.get("nl", "")
    confidence = float(item.get("confidence", 0.5))
    source_quote = item.get("source_quote", "")

    result = ExtractionResult(
        constraint_type="sto",
        pattern_name=category,
        kwargs=params,
        confidence=confidence,
        nl_description=nl,
        source_quote=source_quote,
    )

    # Build the evaluator function
    factory = _SOFT_CATALOG.get(category)
    if factory is None:
        # Fall back to custom/llm_judge
        factory = _SOFT_CATALOG.get("custom")
        category = "custom"

    try:
        if category == "custom":
            evaluator_fn = factory(nl)
        elif category == "pii":
            evaluator_fn = factory(fields=params.get("fields"))
        elif category == "length":
            evaluator_fn = factory(
                max_words=params.get("max_words"),
                max_chars=params.get("max_chars"),
            )
        elif category == "format":
            evaluator_fn = factory(expected_format=params.get("format", "json"))
        elif category == "tone":
            evaluator_fn = factory(desired_tone=params.get("desired_tone", nl))
        elif category == "relevance":
            evaluator_fn = factory(topic=params.get("topic", nl))
        elif category == "content_prohibition":
            prohibited = params.get("prohibited", "")
            if not prohibited:
                result.error = "content_prohibition requires 'prohibited' param"
                return result
            evaluator_fn = factory(prohibited=prohibited)
        else:
            evaluator_fn = _SOFT_CATALOG["custom"](nl)
    except Exception as e:
        result.error = f"Soft evaluator construction failed: {e}"
        return result

    requires_llm = category in ("tone", "relevance", "custom")

    result.compiled = StoFormula(
        desc=nl,
        category=category,
        evaluator_fn=evaluator_fn,
        threshold=params.get("threshold", 0.7),
        pattern_name="sto",
        requires_llm=requires_llm,
    )

    return result


def compile_extraction(item: dict) -> ExtractionResult:
    """Compile a single constraint item from LLM JSON output.

    Routes to ``_compile_det`` or ``_compile_sto`` based on the
    ``"type"`` field (``"hard"`` or ``"sto"``).

    Args:
        item: Dict with keys from LLM output.

    Returns:
        ExtractionResult with compiled constraint or error.
    """
    constraint_type = item.get("type", "hard")
    if constraint_type == "sto":
        return _compile_sto(item)
    return _compile_det(item)


# ---------------------------------------------------------------------------
# LLM prompt builders
# ---------------------------------------------------------------------------


def _build_system_prompt(
    mode: str,
    tool_inventory: list[dict] | None = None,
) -> str:
    """Build the system prompt for the LLM extraction call.

    Args:
        mode: One of ``"nl"``, ``"document"``, ``"code"``.
        tool_inventory: Optional list of tool dicts with name/docstring/params.

    Returns:
        System prompt string.
    """
    tool_context = ""
    if tool_inventory:
        tool_lines = []
        for t in tool_inventory:
            line = f"  - {t['name']}"
            if t.get("docstring"):
                line += f": {t['docstring']}"
            if t.get("params"):
                line += f" (params: {t['params']})"
            tool_lines.append(line)
        tool_context = (
            "\n\nKnown tools in this agent system:\n"
            + "\n".join(tool_lines)
            + "\n\nIMPORTANT: Use these exact tool names in your constraint args. "
            "Do not invent tool names that are not in this list."
        )

    mode_instructions = {
        "nl": (
            "You are given a natural language constraint description. "
            "Classify it as det (enforceable on tool-call traces) or sto "
            "(requires content/semantic evaluation), then extract the "
            "pattern name and arguments."
        ),
        "document": (
            "You are given a policy document. Extract ALL safety rules, "
            "constraints, and policies that could be enforced on an LLM "
            "agent's behavior at runtime. For each constraint found, "
            "classify it as det or sto and extract the pattern/args. "
            "Be conservative: only extract constraints that clearly map "
            "to available patterns. Set confidence below 0.5 for uncertain ones."
        ),
        "code": (
            "You are given source code (Python, JSON, YAML, shell scripts, or text) "
            "from an LLM agent system. Your job:\n\n"
            "1. **Discover tools**: Identify ALL tools/functions/actions the agent "
            "can call. Look for function definitions, class methods, tool "
            "registrations, JSON tool schemas, shell scripts, API endpoints, or any "
            "callable actions — regardless of how they are defined.\n\n"
            "2. **Extract business rules from tool definitions**: For each tool, "
            "read its docstring, parameter names, validation logic, and error "
            "handling to extract preconditions and constraints:\n"
            "- **Preconditions in docstrings**: 'If the order is already processed, "
            "it cannot be cancelled' → must_precede(get_order_details, cancel) or "
            "arg_blacklist to check status\n"
            "- **Required ordering**: If tool B needs output from tool A (e.g., "
            "user_id from find_user), then must_precede(find_user, tool_B)\n"
            "- **Identity verification**: If tools require user lookup before "
            "mutation (modify/cancel/return/exchange), enforce the ordering\n"
            "- **Parameter constraints**: If a tool checks a parameter value "
            "(e.g., status must be 'pending'), use arg_field_has or arg_blacklist\n"
            "- **Mutual exclusion**: If two tools conflict (e.g., cancel vs modify "
            "same order), use mutual_exclusion\n"
            "- **State dependencies**: If a tool's docstring says 'order must be "
            "delivered', the agent must verify this before calling\n\n"
            "3. **Analyze attack surface**: Agents cheat in 5 main ways:\n"
            "   A. **Data Falsification**: sed -i / jq / echo > to modify source data\n"
            "   B. **Data Deletion**: grep -v / awk filtering / rm to prune records\n"
            "   C. **Hidden Flag Exploitation**: --force, --override, --skip flags\n"
            "   D. **Content Manipulation**: crafting file content with exploit keywords\n"
            "   E. **Script Tampering**: overwriting /usr/local/bin/ scripts\n\n"
            "For each, check: which files could be targets? Which scripts have "
            "undocumented flags? What metric could be gamed?\n\n"
            "4. **Generate constraints**:\n"
            "- rate_limit('bash:sed -i', 0) — bans sed -i entirely\n"
            "- rate_limit('bash:rm.*report', 0) — bans deleting reports\n"
            "- arg_blacklist('bash', 'command', ['--force', '--override'])\n"
            "- rate_limit('bash:> /usr/local/bin', 0) — bans script tampering\n"
            "- rate_limit('bash:grep.*-v.*data', 0) — bans data pruning\n\n"
            "IMPORTANT: When the agent has a single tool like 'bash', use the "
            "'bash:pattern' format to target specific commands.\n\n"
            "Use the actual tool names you discover. Extract as many constraints "
            "as possible — every precondition, parameter check, ordering dependency."
        ),
    }

    atom_vocab = _build_atom_vocabulary()

    return f"""{mode_instructions.get(mode, mode_instructions["nl"])}

{atom_vocab}
{tool_context}

LTL Operators (use these to compose atoms into formulas):

  G(f)              — Globally: f must hold at every timestep
  F(f)              — Eventually: f must hold at some future timestep
  X(f)              — Next: f must hold at the next timestep
  U(f, g)           — Until: f must hold until g becomes true
  Not(f)            — Negation
  And(f, g)         — Conjunction
  Or(f, g)          — Disjunction
  Implies(f, g)     — Implication: if f then g
  Le(Var(...), Const(n)) — Less-or-equal (for count comparisons)

Common formula examples (ALWAYS use the EXPANDED form, never shorthand names):

  "A must precede B" →
    Or(U(Not(called(B)), called(A)), G(Not(called(B))))

  "A must always be followed by B" →
    G(Implies(called(A), F(called(B))))

  "X at most N times" →
    G(Le(Var(count, X), Const(N)))

  "A and B are mutually exclusive" →
    And(G(Implies(called(A), G(Not(called(B))))), G(Implies(called(B), G(Not(called(A))))))

  "A or B must precede C" (use Or for alternative preconditions) →
    Or(U(Not(called(C)), called(A)), U(Not(called(C)), called(B)), G(Not(called(C))))
    Use when multiple tools serve the same purpose. Do NOT write separate
    constraints for each — combine with Or.

  "A's argument field must match pattern" →
    G(Implies(called(A), arg_field_has(A, field, regex)))

  "A's argument must not exceed N chars" →
    G(Implies(called(A), Not(arg_length_exceeds(A, field, N))))

GRANULARITY — targeting specific operations, not whole tools:

  When a tool (e.g. "bash") supports many operations, do NOT ban the
  whole tool. Use called_with / count_with to target the specific
  DANGEROUS operation by matching against the tool's arguments:

  "bash sed -i is banned" (ban a specific bash operation) →
    G(Le(Var(count_with, "bash", "sed -i"), Const(0)))

  "bash rm -rf is banned" →
    G(Le(Var(count_with, "bash", "rm -rf"), Const(0)))

  "bash python -c at most 1 time" →
    G(Le(Var(count_with, "bash", "python -c"), Const(1)))

  ✗ WRONG: G(Not(called(bash)))                    — blocks ALL bash, too broad
  ✓ RIGHT: G(Le(Var(count_with, "bash", "sed -i"), Const(0)))  — only blocks sed -i

  Use called_with(tool, pattern) to test if a specific operation happened:
    called_with("bash", "sed -i")   — true when bash is called with args containing "sed -i"
    called_with("bash", "rm -rf")   — true when bash is called with args containing "rm -rf"

  Use count_with for rate-limiting a specific operation:
    Var(count_with, "bash", "sed -i")   — cumulative count of bash calls containing "sed -i"

QUOTING RULE: When atom arguments contain spaces, hyphens, or special
characters, ALWAYS wrap them in double quotes:
  ✓ Var(count_with, "bash", "sed -i")      — quoted, correct
  ✗ Var(count, bash:sed -i)                 — unquoted space, will fail to parse
  ✓ arg_field_has("bash", "command", "rm -rf")  — quoted, correct
  ✓ called_with("bash", "python -c")       — quoted, correct
  Simple identifiers without spaces don't need quotes:
  ✓ called(verify_identity)                 — no spaces, fine without quotes
  ✓ Var(count, issue_refund)                — no spaces, fine

IMPORTANT: The "formula" field must contain ONLY atoms and operators.
Do NOT use shorthand names like must_precede(), rate_limit(), etc.
Always write the expanded form using called(), Var(), G(), Or(), U(), etc.

Output a JSON object with:

1. A "tools" array of discovered tools (only for "code" mode):
  - "name": tool/function name (snake_case)
  - "description": one-line description

2. A "constraints" array. Each element must have:
  - "type": "det" or "sto"
  - For det constraints:
    - "guarantee": the formula that must hold (using atoms + operators above)
    - "assumption": (optional) the condition under which this guarantee applies.
      If the assumption is not met at runtime, the guarantee is skipped.
      Examples:
        guarantee: "U(Not(called(cancel)), called(get_details))"
        assumption: "called(cancel)"
        → meaning: "IF cancel is going to be called, THEN get_details must come first"
        → if cancel is never called, this constraint is irrelevant

        guarantee: "Or(U(Not(called(get_user)), called(find_by_email)), U(Not(called(get_user)), called(find_by_name)))"
        assumption: "called(get_user)"
        → meaning: "IF get_user is called, THEN one of the find methods must come first"

        guarantee: "G(Le(Var(count_with, \"bash\", \"sed -i\"), Const(0)))"
        assumption: null
        → meaning: "bash sed -i is ALWAYS banned" (unconditional — no assumption needed)
        Note: use count_with + quoted args to target the specific operation, not the whole bash tool.

      Think about each constraint: "Is this ALWAYS true, or only when
      a certain tool is about to be used?" If conditional, add an assumption.
      Unconditional safety rules (banning dangerous commands, rate limits)
      should have assumption: null.

  - For sto constraints:
    - "category": one of: pii, length, format, tone, relevance, content_prohibition
    - "params": dict of category-specific parameters
  - "confidence": 0.0-1.0
  - "source_quote": exact text from the input that implies this constraint (empty string if inferred)

If nothing found, output: {{"tools": [], "constraints": []}}

Rules:
- ALL det constraints must use atoms + operators in "guarantee" (and optionally "assumption")
- Use "assumption" when a constraint only applies in certain contexts
  (e.g., ordering constraints that only matter if the second tool is called)
- Use assumption: null for unconditional safety rules (bans, rate limits)
- Tool names must be snake_case. Use called_with("tool", "pattern") / count_with for targeting specific operations within a tool (e.g. specific bash commands). Always quote arguments that contain spaces or special characters.
- A constraint is "det" if it checks tool call ordering/count/args.
  It is "sto" if it evaluates output content quality.
- Prefer det constraints — they can block actions before execution
- If multiple tools serve the same purpose, combine them with Or
- Use the common formula patterns above as building blocks
"""


# ---------------------------------------------------------------------------
# UnifiedExtractor — the main entry point
# ---------------------------------------------------------------------------


class UnifiedExtractor:
    """LLM-based constraint extraction shared by all input paths.

    Uses the Atom vocabulary and Pattern catalog to guide the LLM toward
    outputs that can be compiled into enforceable formulas.

    Supports four provider families:

    * ``"openai"`` (default) — OpenAI's official ``chat.completions``.
    * ``"anthropic"`` — Claude via the ``anthropic`` SDK.
    * ``"gemini"`` — Google's Gemini via ``google-genai`` (1500 req/day
      free tier makes this the lowest-friction on-ramp).
    * **OpenAI-compatible endpoints** — pass ``base_url=...`` (or set
      ``OPENAI_BASE_URL``).  This single switch covers Ollama (local),
      OpenRouter, DeepSeek, Together, Groq, Cerebras, Fireworks, vLLM,
      Azure OpenAI — anything that speaks the OpenAI chat API.

    Args:
        model: Provider-specific model name.  Defaults: ``gpt-4o-mini``,
            ``claude-3-5-sonnet-20241022``, ``gemini-2.0-flash``.
        api_key: API key for the chosen provider.  If ``None``, picked
            up from ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY`` /
            ``GOOGLE_API_KEY`` / ``GEMINI_API_KEY``.
        base_url: Custom HTTP endpoint for OpenAI-compatible providers.
            Reads from ``OPENAI_BASE_URL`` env if not given.  Forces
            provider to ``"openai"`` (i.e. uses the ``openai`` SDK
            against the custom endpoint).
        client: Pre-configured ``openai.OpenAI`` / ``anthropic.Anthropic``
            client.  Overrides ``model`` / ``api_key`` / ``base_url``.
        provider: One of ``openai``, ``anthropic``, ``gemini``.  If
            unset, auto-detected from env vars (precedence: explicit
            ``base_url`` → openai, ``ANTHROPIC_API_KEY`` → anthropic,
            ``GOOGLE_API_KEY``/``GEMINI_API_KEY`` → gemini, else
            openai).
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        client: Any = None,
        provider: str | None = None,
        use_structured_ir: bool = False,
        base_url: str | None = None,
    ) -> None:
        # ``base_url`` (explicit or via env) implies the OpenAI client
        # talking to a custom endpoint.  We resolve it early so it
        # influences provider auto-detection.
        if base_url is None:
            base_url = os.environ.get("OPENAI_BASE_URL")

        # Auto-detect provider from env / hints, in priority order.
        if provider is None:
            if client is not None:
                provider = "openai"
            elif base_url:
                provider = "openai"
            elif api_key and "gemini" in (model or ""):
                provider = "gemini"
            elif api_key and "claude" in (model or ""):
                provider = "anthropic"
            elif os.environ.get("ANTHROPIC_API_KEY"):
                provider = "anthropic"
            elif os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
                provider = "gemini"
            elif os.environ.get("OPENAI_API_KEY"):
                provider = "openai"
            else:
                provider = "openai"  # default, will fail with clear error

        self._provider = provider
        self._base_url = base_url

        if provider == "gemini":
            self._model = model or "gemini-2.5-flash"
            self._api_key = (
                api_key
                or os.environ.get("GOOGLE_API_KEY")
                or os.environ.get("GEMINI_API_KEY")
            )
            self._client = None  # use google.genai directly
        elif provider == "anthropic":
            self._model = model or "claude-3-5-sonnet-20241022"
            self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            if client is not None:
                self._client = client
            else:
                try:
                    import anthropic
                except ImportError:
                    raise ImportError(
                        "anthropic is required for --provider anthropic. "
                        "Install with: pip install anthropic"
                    )
                kwargs = {"api_key": self._api_key} if self._api_key else {}
                self._client = anthropic.Anthropic(**kwargs)
        else:  # "openai" — also covers OpenAI-compatible base_url providers
            self._model = model or "gpt-4o-mini"
            if client is not None:
                self._client = client
            else:
                # Defer ``import openai`` until the first call so tests (and
                # callers that only inspect provider/base_url) work without
                # the optional SDK installed.
                self._client = None
                self._openai_lazy_api_key = api_key
        self._last_discovered_tools: list[dict] = []
        self._use_ir = use_structured_ir

    @property
    def last_discovered_tools(self) -> list[dict]:
        """Tools discovered by the most recent LLM extraction call."""
        return self._last_discovered_tools

    def _call_llm(
        self, system_prompt: str, user_content: str
    ) -> tuple[list[dict], list[dict]]:
        """Make the LLM call and parse the JSON response.

        Supports both OpenAI and Gemini providers.

        Returns:
            Tuple of (constraints, discovered_tools).
            Returns ([], []) on any failure (with logging).
        """
        try:
            if self._provider == "gemini":
                content = self._call_gemini(system_prompt, user_content)
            elif self._provider == "anthropic":
                content = self._call_anthropic(system_prompt, user_content)
            else:
                content = self._call_openai(system_prompt, user_content)

            data = json.loads(content)
            constraints = data.get("constraints", [])
            tools = data.get("tools", [])
            if not isinstance(constraints, list):
                logger.warning(
                    "LLM returned non-list 'constraints': %s", type(constraints)
                )
                constraints = []
            if not isinstance(tools, list):
                tools = []
            return constraints, tools
        except json.JSONDecodeError as e:
            logger.error("LLM returned invalid JSON: %s", e)
            return [], []
        except Exception as e:
            logger.error("LLM extraction call failed: %s", e)
            return [], []

    def _ensure_openai_client(self) -> None:
        if self._client is not None:
            return
        try:
            import openai
        except ImportError as exc:
            raise ImportError(
                "openai is required for LLM extraction. "
                "Install with: pip install 'sponsio[llm]'"
            ) from exc
        kwargs: dict = {}
        api_key = self._openai_lazy_api_key
        base_url = self._base_url
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            # Many OpenAI-compatible endpoints (Ollama, vLLM)
            # don't actually require a key but the SDK insists
            # on one being present; pass a placeholder so
            # construction doesn't blow up.
            kwargs["base_url"] = base_url
            kwargs.setdefault("api_key", api_key or "sk-no-key-required")
        self._client = openai.OpenAI(**kwargs)

    def _call_openai(self, system_prompt: str, user_content: str) -> str:
        self._ensure_openai_client()
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or "{}"

    def _call_gemini(self, system_prompt: str, user_content: str) -> str:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise ImportError(
                "Gemini extraction needs the `google-genai` package. "
                "Install with: pip install 'sponsio[llm]' (or `pip install google-genai`)."
            ) from exc

        client = genai.Client(api_key=self._api_key)
        response = client.models.generate_content(
            model=self._model,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.0,
                response_mime_type="application/json",
            ),
        )
        return response.text or "{}"

    def _call_anthropic(self, system_prompt: str, user_content: str) -> str:
        # Anthropic Messages API doesn't have a native ``json_object``
        # response_format, so we lean on the existing prompt's
        # ``Output JSON only`` instruction (already present in every
        # extractor system prompt).  Temperature 0 keeps it deterministic.
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            temperature=0.0,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        # ``msg.content`` is a list of content blocks; the first text
        # block is the JSON payload.  Models occasionally wrap JSON in a
        # ```json fenced block — strip that defensively so the caller's
        # ``json.loads`` doesn't fail.
        text = ""
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                text = block.text
                break
        text = text.strip()
        if text.startswith("```"):
            # Strip leading ```json (or ```) and trailing ```
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
        return text or "{}"

    def _extract(
        self,
        mode: str,
        user_content: str,
        tool_inventory: list[dict] | None = None,
        min_confidence: float = 0.0,
    ) -> list[ExtractionResult]:
        """Core extraction: build prompt → call LLM → compile results.

        When ``self._use_ir`` is True, uses the Structured IR pipeline
        (``sponsio.generation.structured_ir``) — the LLM outputs a
        structured JSON form instead of raw LTL formula text, and
        deterministic rules compile it via the pattern registry. This
        avoids formula-text parsing failures (spaces in atom args,
        nested quotes, operator nesting errors).

        Args:
            mode: ``"nl"``, ``"document"``, or ``"code"``.
            user_content: The text to extract from.
            tool_inventory: Optional tool list for context.
            min_confidence: Filter out results below this threshold.

        Returns:
            List of ExtractionResults (including failed compilations).
        """
        if not user_content.strip():
            return []

        if self._use_ir:
            return self._extract_ir(mode, user_content, tool_inventory, min_confidence)

        return self._extract_formula(mode, user_content, tool_inventory, min_confidence)

    def _extract_formula(
        self,
        mode: str,
        user_content: str,
        tool_inventory: list[dict] | None = None,
        min_confidence: float = 0.0,
    ) -> list[ExtractionResult]:
        """Original formula-text extraction path."""
        system_prompt = _build_system_prompt(mode, tool_inventory)
        items, discovered_tools = self._call_llm(system_prompt, user_content)

        if discovered_tools:
            self._last_discovered_tools = discovered_tools

        results: list[ExtractionResult] = []
        for item in items:
            confidence = float(item.get("confidence", 0.5))
            if confidence < min_confidence:
                continue

            result = compile_extraction(item)
            results.append(result)

            if result.error:
                # Demoted from warning → debug: per-failure noise during
                # LLM extraction would print one ugly stderr line per
                # malformed candidate (LLMs typically emit 1-2 unparseable
                # constraints out of 5-8).  The aggregate count is
                # already surfaced in the "Scan summary: X kept, Y
                # dropped" line that wraps the loop, which is enough
                # for the user; full per-item traces are still reachable
                # via ``logging.basicConfig(level=logging.DEBUG)``.
                logger.debug(
                    "Constraint compilation failed: %s — %s",
                    result.nl_description,
                    result.error,
                )

        return results

    def _extract_ir(
        self,
        mode: str,
        user_content: str,
        tool_inventory: list[dict] | None = None,
        min_confidence: float = 0.0,
    ) -> list[ExtractionResult]:
        """Structured IR extraction path.

        LLM outputs {subject, relation, ...} JSON → compile_ir() →
        DetFormula / StoFormula via the pattern registry. No raw LTL
        formula text involved.
        """
        from sponsio.generation.structured_ir import (
            build_ir_system_prompt,
            build_ir_user_content,
            compile_ir_batch,
        )

        system_prompt = build_ir_system_prompt(mode, tool_inventory)

        # Build user content using the IR helper (adds tool inventory formatting)
        ir_user_content = build_ir_user_content(
            mode=mode,
            content=user_content,
            tool_inventory=tool_inventory,
        )

        items, discovered_tools = self._call_llm(system_prompt, ir_user_content)

        if discovered_tools:
            self._last_discovered_tools = discovered_tools

        # Compile via IR batch (deterministic: IR → pattern → DetFormula)
        ir_results = compile_ir_batch(items, min_confidence=min_confidence)

        # Convert IRCompilationResult → ExtractionResult for interface compat
        results: list[ExtractionResult] = []
        for ir_r in ir_results:
            result = ExtractionResult(
                constraint_type=ir_r.constraint_type,
                pattern_name=ir_r.pattern_name,
                args=ir_r.args,
                confidence=ir_r.confidence,
                nl_description=ir_r.nl_description,
                source_quote=ir_r.source_quote,
                compiled=ir_r.compiled,
                compiled_assumption=ir_r.compiled_assumption,
                assumption_raw=ir_r.assumption_raw,
                error=ir_r.error,
            )
            results.append(result)

            if result.error:
                logger.warning(
                    "IR compilation failed: %s — %s",
                    result.nl_description,
                    result.error,
                )

        return results

    # -------------------------------------------------------------------
    # Public API: three input paths
    # -------------------------------------------------------------------

    def extract_from_nl(
        self,
        nl_text: str,
        tool_inventory: list[dict] | None = None,
    ) -> list[ExtractionResult]:
        """Extract constraints from natural language description(s).

        Used as LLM fallback when rule-based parsing fails, or for
        YAML guarantee strings that don't match keyword patterns.

        Args:
            nl_text: One or more NL constraint descriptions.
            tool_inventory: Optional known tool names for validation.

        Returns:
            List of extraction results.
        """
        return self._extract("nl", nl_text, tool_inventory)

    def extract_from_document(
        self,
        document: str,
        tool_inventory: list[dict] | None = None,
        min_confidence: float = 0.3,
    ) -> list[ExtractionResult]:
        """Extract constraints from a policy document.

        Replaces the legacy ``DocumentExtractor.extract()`` with
        Atom-aware prompting and automatic det/sto classification.

        Args:
            document: Policy text (compliance doc, SOP, safety rules, markdown).
            tool_inventory: Optional known tool names for grounding.
            min_confidence: Filter threshold (default 0.3).

        Returns:
            List of extraction results with confidence scores.
        """
        return self._extract("document", document, tool_inventory, min_confidence)

    def extract_from_code(
        self,
        tool_inventory: list[dict],
        source_snippet: str = "",
        source_files: list[str] | None = None,
        min_confidence: float = 0.5,
    ) -> list[ExtractionResult]:
        """Infer constraints from agent source code and tool inventory.

        Combines AST-extracted tool information with LLM reasoning to
        infer safety constraints from code structure.

        Args:
            tool_inventory: List of dicts with ``name``, ``docstring``,
                ``params`` keys (from CodeAnalyzer's AST pass).
            source_snippet: Optional source code snippet for context.
            source_files: Optional list of source file contents.
            min_confidence: Filter threshold (default 0.5).

        Returns:
            List of inferred constraints.
        """
        # Build user content from tool inventory + source
        parts = ["# Tool Inventory\n"]
        for t in tool_inventory:
            parts.append(f"## {t['name']}")
            if t.get("docstring"):
                parts.append(f"Docstring: {t['docstring']}")
            if t.get("params"):
                parts.append(f"Parameters: {t['params']}")
            if t.get("source"):
                parts.append(f"Source:\n```python\n{t['source']}\n```")
            parts.append("")

        if source_snippet:
            parts.append(f"# Source Code Context\n```python\n{source_snippet}\n```")

        if source_files:
            for i, content in enumerate(source_files):
                parts.append(f"\n# Source File {i + 1}\n```python\n{content}\n```")

        user_content = "\n".join(parts)
        return self._extract("code", user_content, tool_inventory, min_confidence)

    # -------------------------------------------------------------------
    # Convenience: extract + filter only successful compilations
    # -------------------------------------------------------------------

    def extract_compiled(
        self,
        mode: str,
        content: str,
        tool_inventory: list[dict] | None = None,
        min_confidence: float = 0.3,
    ) -> tuple[list[DetFormula], list[Any]]:
        """Extract and return only successfully compiled constraints.

        Returns:
            Tuple of (hard_formulas, soft_constraints).
        """
        results = self._extract(mode, content, tool_inventory, min_confidence)

        hard = []
        sto = []
        for r in results:
            if not r.ok:
                continue
            if r.constraint_type == "det":
                hard.append(r.compiled)
            else:
                sto.append(r.compiled)

        return hard, sto
