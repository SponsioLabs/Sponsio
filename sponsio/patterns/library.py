"""Pattern library -- the user-facing constraint DSL.

Users describe constraints by calling pattern functions.  Each function
compiles a human-readable description into an LTL formula wrapped in a
``DetFormula`` (which carries the description + pattern name for
diagnostics).

Users never need to write raw LTL.  The NL parser
(``generation/nl_to_contract.py``) maps natural language strings to
calls into this library.

Available patterns (29 det + 1 deprecated):

  Core temporal (14):
    must_precede(A, B)              -- A must happen before B
    always_followed_by(A, B)        -- whenever A, eventually B
    never_together(A, B)            -- [DEPRECATED → mutual_exclusion]
    no_reversal(A, B)               -- B forbidden after A commits
    requires_permission(tool, perm) -- tool needs permission
    no_data_leak(src, ext)          -- no flow from src to ext
    mutual_exclusion(A, B)          -- at most one ever called
    rate_limit(action, N)           -- action called at most N times
    idempotent(action)              -- action may occur at most once
    deadline(trigger, action, N)    -- action within N steps of trigger
    must_confirm(action)            -- confirmation before action
    cooldown(action, N)             -- min N steps between calls
    segregation_of_duty(A, B)       -- same agent can't do both
    bounded_retry(action, N)        -- at most N retries
    loop_detection(action, N)       -- max N consecutive calls

  Argument / path (4):
    arg_blacklist(tool, param, patterns) -- forbid patterns in tool args
    scope_limit(tool, allowed)      -- restrict tool to allowed paths
    arg_length_limit(tool, param, N)-- max N chars in argument field
    data_intact(tool, paths)        -- tool must use original data

  OWASP agentic security (8):
    destructive_action_gate(tool, role)       -- human approval + role
    untrusted_source_gate(sources, sinks)     -- re-confirm after untrusted input
    required_steps_completion(trigger, steps) -- all steps must follow trigger
    tool_allowlist(tools)                     -- only listed tools allowed
    dangerous_bash_commands(forbidden)        -- preset: ban shell commands
    dangerous_sql_verbs(tool, forbidden)      -- preset: ban SQL verbs
    irreversible_once(action)                 -- at most once per session
    confirm_after_source(source, action)      -- confirm after untrusted source

  Resource / delegation (3):
    token_budget(max_tokens, scope)           -- limit token consumption
    arg_value_range(tool, field, min, max)    -- constrain numeric args
    delegation_depth_limit(max_depth)         -- limit delegation chain
"""

from __future__ import annotations

from dataclasses import dataclass
from sponsio.formulas.formula import (
    Atom,
    Not,
    And,
    Or,
    Implies,
    G,
    F,
    X,
    U,
    Formula,
    Var,
    Const,
    Le,
    Ge,
)


def _called(tool: str) -> Atom:
    """Create a called/called_with atom based on tool:pattern format."""
    tool = str(tool)
    if ":" in tool:
        physical, pattern = tool.split(":", 1)
        return Atom("called_with", physical, pattern)
    return Atom("called", tool)


def _count_var(tool: str) -> Var:
    """Create a count/count_with Var based on tool:pattern format."""
    tool = str(tool)
    if ":" in tool:
        physical, pattern = tool.split(":", 1)
        return Var("count_with", physical, pattern)
    return Var("count", tool)


@dataclass(frozen=True)
class DetFormula:
    """Wraps an LTL formula with a human-readable description.

    Delegates operator overloading (``>>``, ``&``, ``|``, ``~``) to the
    inner formula so det formulas compose transparently.

    Attributes:
        formula: The underlying LTL formula.
        desc: Human-readable description of the property.
        pattern_name: Name of the pattern function that created this.
    """

    formula: Formula
    desc: str
    pattern_name: str
    liveness: bool = False

    # Delegate all formula operations to the inner formula
    def __rshift__(self, other):
        return self.formula >> other

    def __and__(self, other):
        return self.formula & other

    def __or__(self, other):
        return self.formula | other

    def __invert__(self):
        return ~self.formula


# Backward-compatible alias
AnnotatedFormula = DetFormula


def must_precede(before: str, after: str, desc: str = "") -> DetFormula:
    """Enforces that one action must happen before another.

    Compiles to: ``!called(after) U called(before)`` — the ``after`` action
    is forbidden until ``before`` has occurred at least once.

    Args:
        before: Tool or action that must occur first.
        after: Tool or action that must occur second.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the ordering constraint.
    """
    # after is forbidden until before appears, OR after is never called
    formula = Or(
        U(Not(_called(after)), _called(before)),
        G(Not(_called(after))),
    )
    return DetFormula(
        formula=formula,
        desc=desc or f"{before} must precede {after}",
        pattern_name="must_precede",
    )


def always_followed_by(trigger: str, response: str, desc: str = "") -> DetFormula:
    """Enforces that a trigger is always eventually followed by a response.

    Compiles to: ``G(called(trigger) -> F(called(response)))``.

    Args:
        trigger: Tool or action that triggers the obligation.
        response: Tool or action that must eventually follow.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the liveness constraint.
    """
    formula = G(Implies(_called(trigger), F(_called(response))))
    return DetFormula(
        formula=formula,
        desc=desc or f"{trigger} must always be followed by {response}",
        pattern_name="always_followed_by",
        liveness=True,
    )


def never_together(a: str, b: str, desc: str = "") -> DetFormula:
    """Deprecated: use ``mutual_exclusion`` instead.

    In sequential traces, two tool calls are always at different timesteps,
    so this pattern's formula ``G(!(called(A) & called(B)))`` is trivially
    satisfied and can never detect violations.

    This function now delegates to ``mutual_exclusion`` for correct behavior.

    Args:
        a: First action.
        b: Second action.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` from ``mutual_exclusion``.
    """
    import warnings

    warnings.warn(
        "never_together is deprecated — use mutual_exclusion instead. "
        "In sequential traces, never_together can never trigger.",
        DeprecationWarning,
        stacklevel=2,
    )
    return mutual_exclusion(a, b, desc=desc or f"{a} and {b} must never occur together")


def no_reversal(commitment: str, contradiction: str, desc: str = "") -> DetFormula:
    """Enforces that a contradicting action never occurs after a commitment.

    Once the commitment action fires, the contradiction must never happen.
    This catches cross-turn contradictions at the tool-call level.

    Example: ``no_reversal("approve_refund", "deny_refund")`` means once a
    refund is approved, it can never be denied in the same session.

    Compiles to: ``G(called(commitment) -> G(!called(contradiction)))``.

    Args:
        commitment: The action that establishes a commitment.
        contradiction: The action that would contradict the commitment.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the no-reversal constraint.
    """
    formula = G(Implies(_called(commitment), G(Not(_called(contradiction)))))
    return DetFormula(
        formula=formula,
        desc=desc or f"{contradiction} must never occur after {commitment}",
        pattern_name="no_reversal",
    )


def requires_permission(tool: str, permission: str, desc: str = "") -> DetFormula:
    """Enforces that a tool call requires a specific permission.

    Compiles to: ``G(called(tool) -> perm(P))``.

    Args:
        tool: Tool name that requires authorization.
        permission: Permission label that must be held.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the permission guard.
    """
    formula = G(Implies(_called(tool), Atom("perm", permission)))
    return DetFormula(
        formula=formula,
        desc=desc or f"{tool} requires permission {permission}",
        pattern_name="requires_permission",
    )


def no_data_leak(source: str, external: str, desc: str = "") -> DetFormula:
    """Enforces that data never flows from a source to an external sink.

    Compiles to: ``G(contains(source) -> !flow(source, external))``.

    Args:
        source: Data field or agent that must be protected.
        external: External agent or sink that must not receive the data.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the data-leak prohibition.
    """
    formula = G(Implies(Atom("contains", source), Not(Atom("flow", source, external))))
    return DetFormula(
        formula=formula,
        desc=desc or f"no data leak from {source} to {external}",
        pattern_name="no_data_leak",
    )


def mutual_exclusion(a: str, b: str, desc: str = "") -> DetFormula:
    """Enforces that exactly one of two actions may occur across the trace.

    If ``a`` happens, ``b`` must never happen (at any point), and vice versa.
    Compiles to: ``G(called(a) -> G(!called(b))) & G(called(b) -> G(!called(a)))``.

    This is stronger than ``never_together`` which only prevents co-occurrence
    at the *same* timestep. ``mutual_exclusion`` prevents both from ever
    appearing in the same trace.

    Args:
        a: First mutually exclusive action.
        b: Second mutually exclusive action.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the mutual-exclusion constraint.
    """
    formula = And(
        G(Implies(_called(a), G(Not(_called(b))))),
        G(Implies(_called(b), G(Not(_called(a))))),
    )
    return DetFormula(
        formula=formula,
        desc=desc or f"{a} and {b} are mutually exclusive",
        pattern_name="mutual_exclusion",
    )


def rate_limit(action: str, max_count: int, desc: str = "") -> DetFormula:
    """Enforces a maximum invocation count for an action.

    Compiles to an arithmetic constraint:
    ``G(count(action) <= max_count)``.

    The ``count(action)`` variable must be maintained by the grounding
    layer or a custom ``DetEvaluator``.

    Args:
        action: The action to rate-limit.
        max_count: Maximum number of allowed invocations.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the rate-limit constraint.
    """

    formula = G(Le(_count_var(action), Const(max_count)))
    return DetFormula(
        formula=formula,
        desc=desc or f"{action} limited to {max_count} invocations",
        pattern_name="rate_limit",
    )


# ---------------------------------------------------------------------------
# Helper: bounded temporal operators
# ---------------------------------------------------------------------------


def _bounded_eventually(phi: Formula, n: int) -> Formula:
    """Build F_bounded(phi, n) = phi | X(phi | X(phi | ...)) for n steps."""
    result = phi
    for _ in range(n - 1):
        result = Or(phi, X(result))
    return result


def _bounded_never(phi: Formula, n: int) -> Formula:
    """Build !phi & X(!phi & X(!phi & ...)) for n steps."""
    result = Not(phi)
    for _ in range(n - 1):
        result = And(Not(phi), X(result))
    return result


# ---------------------------------------------------------------------------
# New patterns
# ---------------------------------------------------------------------------


def idempotent(action: str, desc: str = "") -> DetFormula:
    """Enforces that an action may occur at most once in the entire session.

    Compiles to: ``G(count(action) <= 1)``.

    Args:
        action: The action that must be idempotent.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the idempotency constraint.
    """

    formula = G(Le(_count_var(action), Const(1)))
    return DetFormula(
        formula=formula,
        desc=desc or f"{action} must be idempotent (at most once)",
        pattern_name="idempotent",
    )


def deadline(trigger: str, action: str, steps: int, desc: str = "") -> DetFormula:
    """Enforces that an action must occur within N steps after a trigger.

    Compiles to: ``G(called(trigger) -> X(F_bounded(called(action), N)))``.

    Args:
        trigger: The event that starts the deadline.
        action: The action that must happen within the deadline.
        steps: Maximum number of steps allowed after the trigger.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the deadline constraint.
    """
    formula = G(
        Implies(
            _called(trigger),
            X(_bounded_eventually(_called(action), steps)),
        )
    )
    return DetFormula(
        formula=formula,
        desc=desc or f"{action} must occur within {steps} steps of {trigger}",
        pattern_name="deadline",
    )


def must_confirm(action: str, desc: str = "") -> DetFormula:
    """Enforces that an action requires explicit confirmation before execution.

    Uses a naming convention: ``confirm_{action}`` must precede ``action``.
    The confirmation tool must exist in the agent's tool set.

    Compiles to: ``!called(action) U called(confirm_action)`` — the action
    is forbidden until the confirmation tool has been called.

    Args:
        action: The action that requires confirmation.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the confirmation requirement.
    """
    confirm_action = f"confirm_{action}"
    # action is forbidden until confirm appears, OR action is never called
    formula = Or(
        U(Not(_called(action)), _called(confirm_action)),
        G(Not(_called(action))),
    )
    return DetFormula(
        formula=formula,
        desc=desc or f"{action} requires confirmation (confirm_{action})",
        pattern_name="must_confirm",
    )


def cooldown(action: str, steps: int, desc: str = "") -> DetFormula:
    """Enforces a minimum interval between consecutive calls to the same action.

    After calling the action, it cannot be called again for N steps.

    Compiles to: ``G(called(action) -> X(!called(action) & X(!called(action) & ...)))``
    for N steps.

    Args:
        action: The action to apply cooldown to.
        steps: Minimum number of steps between consecutive calls.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the cooldown constraint.
    """
    formula = G(
        Implies(
            _called(action),
            X(_bounded_never(_called(action), steps)),
        )
    )
    return DetFormula(
        formula=formula,
        desc=desc or f"{action} has a cooldown of {steps} steps",
        pattern_name="cooldown",
    )


def segregation_of_duty(a: str, b: str, desc: str = "") -> DetFormula:
    """Enforces that the same agent cannot perform both actions in a session.

    Semantically identical to ``mutual_exclusion`` but named for compliance
    contexts (e.g., the same agent cannot both review and approve).

    Compiles to: ``G(called(a) -> G(!called(b))) & G(called(b) -> G(!called(a)))``.

    Args:
        a: First action.
        b: Second action.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the segregation-of-duty constraint.
    """
    formula = And(
        G(Implies(_called(a), G(Not(_called(b))))),
        G(Implies(_called(b), G(Not(_called(a))))),
    )
    return DetFormula(
        formula=formula,
        desc=desc or f"{a} and {b} must be performed by different agents",
        pattern_name="segregation_of_duty",
    )


def bounded_retry(action: str, max_retries: int, desc: str = "") -> DetFormula:
    """Enforces a maximum number of retry attempts for an action.

    Prevents agents from entering infinite retry loops.

    Compiles to: ``G(count(action) <= max_retries)``.

    Args:
        action: The action to limit retries for.
        max_retries: Maximum allowed invocations.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the bounded-retry constraint.
    """

    formula = G(Le(_count_var(action), Const(max_retries)))
    return DetFormula(
        formula=formula,
        desc=desc or f"{action} limited to {max_retries} retries",
        pattern_name="bounded_retry",
    )


# ---------------------------------------------------------------------------
# Argument / path / length constraints
# ---------------------------------------------------------------------------


def arg_blacklist(
    tool: str, param: str, patterns: list[str], desc: str = ""
) -> DetFormula:
    """Forbids specific content in a tool call's arguments.

    Compiles to LTL::

        G(called(tool) → ¬arg_field_has(tool, param, p1) ∧ ¬arg_field_has(tool, param, p2) ∧ ...)

    Uses ``arg_field_has`` for field-specific matching: only the value
    of ``args[param]`` is checked, not the entire serialized args dict.

    Args:
        tool: Tool name to monitor.
        param: Argument key whose value to inspect (e.g. ``"command"``).
        patterns: List of regex patterns. Any match -> violation.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the constraint.
    """
    physical_tool = tool.split(":", 1)[0] if ":" in tool else tool
    body: Formula = Not(Atom("arg_field_has", physical_tool, param, patterns[0]))
    for pattern in patterns[1:]:
        body = And(body, Not(Atom("arg_field_has", physical_tool, param, pattern)))

    formula = G(Implies(_called(tool), body))
    return DetFormula(
        formula=formula,
        desc=desc or f"{tool}.{param} must not match forbidden patterns",
        pattern_name="arg_blacklist",
    )


def scope_limit(tool: str, allowed_paths: list[str], desc: str = "") -> DetFormula:
    """Restricts a tool's file operations to a whitelist of path prefixes.

    Compiles to LTL::

        G(called(tool) → arg_paths_within(tool, *allowed_paths))

    Args:
        tool: Tool name to restrict.
        allowed_paths: List of allowed path prefixes.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the constraint.
    """
    # For tool:pattern format, use the physical tool name for arg_paths_within
    physical_tool = tool.split(":", 1)[0] if ":" in tool else tool
    formula = G(
        Implies(
            _called(tool),
            Atom("arg_paths_within", physical_tool, *allowed_paths),
        )
    )
    return DetFormula(
        formula=formula,
        desc=desc or f"{tool} restricted to paths: {', '.join(allowed_paths)}",
        pattern_name="scope_limit",
    )


def arg_length_limit(
    tool: str, param: str, max_chars: int, desc: str = ""
) -> DetFormula:
    """Blocks tool calls where an argument field exceeds a length limit.

    Detects code injection attacks where an agent inlines an entire
    script into a command argument instead of calling the intended tool.

    Compiles to LTL::

        G(called(tool) → ¬arg_length_exceeds(tool, param, max_chars))

    Args:
        tool: Tool name to monitor (supports ``tool:pattern`` format).
        param: Argument field to check length of (e.g. ``"command"``).
        max_chars: Maximum allowed length in characters.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the length constraint.
    """
    physical_tool = tool.split(":", 1)[0] if ":" in tool else tool
    formula = G(
        Implies(
            _called(tool),
            Not(Atom("arg_length_exceeds", physical_tool, param, str(max_chars))),
        )
    )
    return DetFormula(
        formula=formula,
        desc=desc or f"{tool}.{param} must not exceed {max_chars} characters",
        pattern_name="arg_length_limit",
    )


def data_intact(
    bound_tool: str,
    original_paths: list[str],
    desc: str = "",
) -> DetFormula:
    """Assumption: a tool must only operate on original, unmodified data.

    Compiles to LTL::

        G(arg_has(bash, bound_tool) → arg_paths_within(bash, *original_paths))

    Uses ``bash`` as the default tool since ``data_intact`` was designed
    for shell command checking.  The ``bound_tool`` regex matches against
    the args to detect the specific command (e.g. ``"grep"``).

    Args:
        bound_tool: Regex pattern matching the command name.
        original_paths: Allowed input file path prefixes.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the assumption.
    """
    formula = G(
        Implies(
            Atom("arg_has", "bash", bound_tool),
            Atom("arg_paths_within", "bash", *original_paths),
        )
    )
    return DetFormula(
        formula=formula,
        desc=desc or f"{bound_tool} must use only original data from {original_paths}",
        pattern_name="data_intact",
    )


# ---------------------------------------------------------------------------
# Layer 1 — OWASP Agentic Top 10 patterns (pure LTL over existing atoms)
# ---------------------------------------------------------------------------


def destructive_action_gate(
    tool: str, approver_role: str = "approver", desc: str = ""
) -> DetFormula:
    """Gate a destructive tool behind human confirmation + role permission.

    Stronger than ``must_confirm`` — forces a human (or a different agent
    with the approver permission) into the loop before the destructive
    action can proceed.

    Covers: **ASI02** (tool misuse), **ASI05** (code execution),
    **ASI09** (human-agent trust).

    Compiles to::

        G(¬called(tool)) ∨ (¬called(tool) U (called(confirm_<tool>) ∧ perm(approver_role)))

    Args:
        tool: The destructive tool name.
        approver_role: Permission label the confirmer must hold.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula``.
    """
    confirm = f"confirm_{tool}"
    formula = Or(
        G(Not(_called(tool))),
        U(
            Not(_called(tool)),
            And(_called(confirm), Atom("perm", approver_role)),
        ),
    )
    return DetFormula(
        formula=formula,
        desc=desc
        or f"{tool} is destructive and requires approval from {approver_role}",
        pattern_name="destructive_action_gate",
    )


def untrusted_source_gate(
    sources: list[str], sinks: list[str], desc: str = ""
) -> tuple[DetFormula, DetFormula]:
    """After reading from an untrusted source, sensitive sinks require
    re-confirmation before proceeding.

    The **single most differentiating P0** pattern — compositional over
    source/sink sets, which AgentSpec, Guardrails AI, and NeMo cannot
    express.

    Covers: **ASI01** (indirect prompt injection defense).

    Returns a ``(assumption, enforcement)`` pair designed for
    Sponsio's per-contract model:

    * **Assumption**: any source has been called (``∨ called(source_i)``)
    * **Enforcement**: sinks must be preceded by re-confirmation
      (``must_precede(confirm_reconfirmed, sink)`` for each sink)

    Before any source fires, the assumption fails → sinks are allowed.
    After a source fires, the enforcement activates → sinks require
    ``confirm_reconfirmed`` first.

    Usage::

        assumption, enforcement = untrusted_source_gate(
            ["web_fetch"], ["send_email"]
        )
        Contract(agent=agent, assumption=assumption, enforcement=enforcement)

    Args:
        sources: Untrusted input tools.
        sinks: Sensitive output tools.
        desc: Optional human-readable description.

    Returns:
        A tuple of ``(assumption_formula, enforcement_formula)`` — both
        ``DetFormula``. Use with ``Contract(assumption=..., enforcement=...)``.
    """
    # Assumption: any source has been called
    if len(sources) == 1:
        src_formula = _called(sources[0])
    else:
        src_formula = _called(sources[0])
        for s in sources[1:]:
            src_formula = Or(src_formula, _called(s))

    src_str = ", ".join(sources)
    sink_str = ", ".join(sinks)

    assumption = DetFormula(
        formula=src_formula,
        desc=f"any of [{src_str}] has been called",
        pattern_name="untrusted_source_gate_assumption",
    )

    # Enforcement: confirm_reconfirmed must precede each sink
    enforcement = must_precede("confirm_reconfirmed", sinks[0])
    if len(sinks) > 1:
        # AND-combine must_precede for each sink
        formulas = [must_precede("confirm_reconfirmed", s) for s in sinks]
        combined = formulas[0].formula
        for f in formulas[1:]:
            combined = And(combined, f.formula)
        enforcement = DetFormula(
            formula=combined,
            desc=desc or f"after [{src_str}], [{sink_str}] requires re-confirmation",
            pattern_name="untrusted_source_gate",
        )
    else:
        enforcement = DetFormula(
            formula=enforcement.formula,
            desc=desc or f"after [{src_str}], [{sink_str}] requires re-confirmation",
            pattern_name="untrusted_source_gate",
        )

    return assumption, enforcement


def required_steps_completion(
    trigger: str, required_set: list[str], desc: str = ""
) -> DetFormula:
    """Every trigger must eventually be followed by ALL required steps.

    A liveness checklist — the trigger-side agent's guarantee becomes
    the next agent's assumption in assume-guarantee composition.

    Covers: **MAST premature-termination** (6.2% of observed failures).

    Compiles to::

        G(called(trigger) → F(called(r₁)) ∧ F(called(r₂)) ∧ … ∧ F(called(rₙ)))

    Args:
        trigger: The tool that triggers the obligation.
        required_set: Tools that must all eventually follow.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` (liveness).
    """
    obligations = F(_called(required_set[0]))
    for r in required_set[1:]:
        obligations = And(obligations, F(_called(r)))

    formula = G(Implies(_called(trigger), obligations))

    steps_str = ", ".join(required_set)
    return DetFormula(
        formula=formula,
        desc=desc or f"every {trigger} must be followed by all of [{steps_str}]",
        pattern_name="required_steps_completion",
        liveness=True,
    )


def loop_detection(action: str, max_consecutive: int, desc: str = "") -> DetFormula:
    """Prevent tight loops: the same tool called N times consecutively.

    Distinct from ``bounded_retry`` (global count) and ``cooldown``
    (minimum interval between calls). This catches runaway loops
    regardless of what happens between bursts.

    Covers: Runaway agent failure class.

    Uses the ``consecutive_count(tool)`` atom — a grounding-level
    accumulator that increments on each consecutive call to the same
    tool and resets to 0 when a different tool is called.

    Compiles to::

        G(consecutive_count(action) ≤ max_consecutive)

    Args:
        action: The tool to monitor for consecutive calls.
        max_consecutive: Maximum allowed consecutive calls.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula``.
    """
    formula = G(Le(Var("consecutive_count", action), Const(max_consecutive)))

    return DetFormula(
        formula=formula,
        desc=desc
        or f"{action} must not be called more than {max_consecutive} times consecutively",
        pattern_name="loop_detection",
    )


def tool_allowlist(allowed_tools: list[str], desc: str = "") -> DetFormula:
    """Only tools in the allowlist may be called.

    First-line defense against prompt-injection-introduced tool
    invocations — if a malicious prompt tricks the agent into calling
    an unexpected tool, the guard blocks it.

    Covers: **ASI04** (supply chain vulnerabilities).

    Runtime enforcement: ``guard_before`` rejects any tool not in
    ``allowed_tools``. The LTL encoding is vacuously true when the
    allowlist is respected.

    Compiles to::

        G(∨ called(tᵢ) for tᵢ ∈ allowed)

    Note: This formula is vacuously true on traces where only allowed
    tools appear. The real enforcement is done by the monitor matching
    the tool name against the list. The formula serves as documentation
    and compositional verification.

    Args:
        allowed_tools: Exhaustive list of permitted tool names.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula``.
    """
    # Build disjunction: called(t1) ∨ called(t2) ∨ ...
    if not allowed_tools:
        # Empty allowlist = nothing allowed = always false
        formula = G(Not(Atom("called", "__any__")))
    elif len(allowed_tools) == 1:
        formula = G(_called(allowed_tools[0]))
    else:
        allowed = _called(allowed_tools[0])
        for t in allowed_tools[1:]:
            allowed = Or(allowed, _called(t))
        formula = G(allowed)

    tools_str = ", ".join(allowed_tools)
    return DetFormula(
        formula=formula,
        desc=desc or f"only [{tools_str}] may be called",
        pattern_name="tool_allowlist",
    )


def dangerous_bash_commands(
    forbidden: list[str] | None = None, desc: str = ""
) -> DetFormula:
    """Preset: ban common dangerous bash operations.

    Combines multiple ``count_with("bash", pattern) ≤ 0`` constraints
    into a single formula.

    Covers: **ASI05** (unexpected code execution).

    Args:
        forbidden: List of bash command patterns to ban. Defaults to
            ``["rm -rf", "sudo", "chmod", "sed -i", "python -c"]``.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula``.
    """
    if forbidden is None:
        forbidden = [
            "rm -rf",
            "sudo",
            "chmod",
            "sed -i",
            "python -c",
            "> /app",  # redirect overwrite to app data
            "cp /app/data",  # file replacement via copy
            "mv /app/data",  # file replacement via move
            "tee /app",  # pipe overwrite
        ]

    # Build: G(count_with(bash, p1) ≤ 0 ∧ count_with(bash, p2) ≤ 0 ∧ ...)
    constraints = [
        Le(Var("count_with", "bash", pattern), Const(0)) for pattern in forbidden
    ]
    if len(constraints) == 1:
        combined = constraints[0]
    else:
        combined = And(constraints[0], constraints[1])
        for c in constraints[2:]:
            combined = And(combined, c)

    formula = G(combined)
    patterns_str = ", ".join(forbidden)
    return DetFormula(
        formula=formula,
        desc=desc or f"bash commands [{patterns_str}] are banned",
        pattern_name="dangerous_bash_commands",
    )


def dangerous_sql_verbs(
    tool: str = "execute_sql",
    forbidden: list[str] | None = None,
    desc: str = "",
) -> DetFormula:
    """Preset: ban dangerous SQL verbs in a database tool's arguments.

    Covers: **ASI05** (SQL injection via agent).

    Args:
        tool: The SQL execution tool name.
        forbidden: SQL verbs to ban. Defaults to
            ``["DROP", "TRUNCATE", "DELETE", "ALTER"]``.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula``.
    """
    if forbidden is None:
        forbidden = ["DROP", "TRUNCATE", "DELETE", "ALTER"]

    from sponsio.patterns.library import arg_blacklist

    return arg_blacklist(
        tool,
        "query",
        forbidden,
        desc=desc or f"{tool} must not use [{', '.join(forbidden)}]",
    )


def irreversible_once(action: str, desc: str = "") -> DetFormula:
    """An irreversible action may be called at most once per session.

    Covers: **ASI09** (irreversible action protection).

    Compiles to::

        G(count(action) ≤ 1)

    This is semantically equivalent to ``idempotent(action)`` but named
    for clarity in security contexts.

    Args:
        action: The irreversible action name.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula``.
    """
    formula = G(Le(_count_var(action), Const(1)))
    return DetFormula(
        formula=formula,
        desc=desc or f"{action} is irreversible and may be called at most once",
        pattern_name="irreversible_once",
    )


def confirm_after_source(
    source: str, action: str, desc: str = ""
) -> tuple[DetFormula, DetFormula]:
    """After reading from an untrusted source, an action requires
    confirmation before proceeding.

    Narrower variant of ``untrusted_source_gate`` for a single
    source → single action pair.

    Covers: **ASI01** (narrow case).

    Returns a ``(assumption, enforcement)`` pair:

    * **Assumption**: ``called(source)``
    * **Enforcement**: ``must_precede(confirm_<action>, action)``

    Usage::

        assumption, enforcement = confirm_after_source("fetch_url", "file_write")
        Contract(agent=agent, assumption=assumption, enforcement=enforcement)

    Args:
        source: The untrusted input tool.
        action: The action that needs confirmation after the source.
        desc: Optional human-readable description.

    Returns:
        Tuple of ``(assumption, enforcement)`` — both ``DetFormula``.
    """
    confirm = f"confirm_{action}"

    assumption = DetFormula(
        formula=_called(source),
        desc=f"{source} has been called",
        pattern_name="confirm_after_source_assumption",
    )

    enforcement_formula = must_precede(confirm, action)
    enforcement = DetFormula(
        formula=enforcement_formula.formula,
        desc=desc or f"after {source}, {action} requires confirmation via {confirm}",
        pattern_name="confirm_after_source",
    )

    return assumption, enforcement


# ---------------------------------------------------------------------------
# Layer 2 — Atom extensions (new accumulators in grounding)
# ---------------------------------------------------------------------------


def token_budget(max_tokens: int, scope: str = "total", desc: str = "") -> DetFormula:
    """Limit total token consumption within a session.

    Covers: **ASI08** (cascading failures via token exhaustion),
    runaway agent class.

    New atom: ``token_count(type)`` — int accumulator extracted from
    ``event.args["tokens"]`` (OTEL ``gen_ai.usage.*`` span attributes).

    Compiles to::

        G(token_count("total") ≤ max_tokens)

    Args:
        max_tokens: Maximum allowed token count.
        scope: Token type to limit (``"total"``, ``"input_tokens"``,
            ``"output_tokens"``). Default ``"total"``.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula``.
    """
    formula = G(Le(Var("token_count", scope), Const(max_tokens)))
    return DetFormula(
        formula=formula,
        desc=desc or f"session {scope} tokens must not exceed {max_tokens}",
        pattern_name="token_budget",
    )


def arg_value_range(
    tool: str,
    field: str,
    min_val: int | float | None = None,
    max_val: int | float | None = None,
    desc: str = "",
) -> DetFormula:
    """Constrain a numeric argument to a value range.

    Uses the ``arg_numeric(tool, field)`` atom — a grounding-level
    extractor that pulls numeric values from tool arguments via three
    strategies: dict key lookup, CLI ``--field VALUE`` flag, or
    positional token index.

    Covers: metric gaming (parameter manipulation), input validation.

    Compiles to::

        G(Ge(arg_numeric(tool, field), Const(min)) ∧ Le(arg_numeric(tool, field), Const(max)))

    Args:
        tool: Tool name (or ``tool:pattern`` for bash subcommands).
        field: Argument field name, CLI flag name (without ``--``), or
            positional index as a string (``"0"``, ``"1"``, ...).
        min_val: Minimum allowed value (inclusive). ``None`` = no lower bound.
        max_val: Maximum allowed value (inclusive). ``None`` = no upper bound.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula``.
    """
    var = Var("arg_numeric", tool, field)
    parts = []
    if min_val is not None:
        parts.append(Ge(var, Const(min_val)))
    if max_val is not None:
        parts.append(Le(var, Const(max_val)))

    if not parts:
        raise ValueError("arg_value_range requires at least min_val or max_val")

    if len(parts) == 1:
        body = parts[0]
    else:
        body = And(parts[0], parts[1])

    formula = G(body)

    range_str = ""
    if min_val is not None and max_val is not None:
        range_str = f"[{min_val}, {max_val}]"
    elif min_val is not None:
        range_str = f">= {min_val}"
    else:
        range_str = f"<= {max_val}"

    return DetFormula(
        formula=formula,
        desc=desc or f"{tool}.{field} must be in range {range_str}",
        pattern_name="arg_value_range",
    )


def delegation_depth_limit(max_depth: int, desc: str = "") -> DetFormula:
    """Limit the depth of agent-to-agent delegation chains.

    Covers: **ASI07** (inter-agent communication safety, recursive
    delegation).

    New atom: ``delegation_depth()`` — int accumulator maintained by
    the ``flow`` grounding layer, incremented on each ``message``
    event.

    Compiles to::

        G(delegation_depth ≤ max_depth)

    Args:
        max_depth: Maximum allowed delegation depth.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula``.
    """
    formula = G(Le(Var("delegation_depth"), Const(max_depth)))
    return DetFormula(
        formula=formula,
        desc=desc or f"delegation chain must not exceed depth {max_depth}",
        pattern_name="delegation_depth_limit",
    )
