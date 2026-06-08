"""Pattern library — the constraint primitive layer.

Patterns are the building blocks the rest of Sponsio compiles to: each
function takes plain string args and returns a ``DetFormula`` (an LTL
AST plus the original description + pattern name, for diagnostics).
Users never need to write raw LTL.

The pattern library is *not* itself the contract DSL. the DSL is the
text layer above it (``generation/dsl_to_contract.py``), which parses
phrasings like ``tool `check_policy` must precede `issue_refund``` into
the corresponding pattern call. Patterns can also be invoked directly
from Python or produced by the LLM extractor; the DSL is just one of
several front-ends.

Available patterns (35 det + 1 deprecated):

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

  Argument / path (5):
    arg_blacklist(tool, param, patterns) -- forbid patterns in tool args
    arg_allowlist(tool, param, patterns) -- arg must match one of the allowed patterns
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

  Workflow hygiene (6):
    dry_run_before_commit(dry_run, commit)          -- dry-run before commit
    backup_before_destructive(backup, action)       -- backup before destructive action
    audit_after(action, audit)                      -- audit/log must follow action
    approval_freshness(approval, action, N)         -- approval expires after N steps
    sanitized_before_sink(source, sanitizer, sink)  -- sanitizer after source before sink
    duplicate_call_limit(tool, pattern, N)          -- cap repeated matching calls
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass
from typing import Any

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


_NAMESPACED_TOOL_RE = _re.compile(r"^[A-Za-z_][\w-]*:[A-Za-z_][\w-]*$")


def _is_namespaced_tool_name(tool: str) -> bool:
    """Decide whether ``foo:bar`` is a literal namespaced tool name
    (Claude Code plugin skill / MCP server convention) rather than the
    ``tool:argpattern`` shortcut used by ``bans`` / ``called_with``.

    Heuristic: both sides of ``:`` must be bare identifiers. no
    whitespace, no regex metacharacters, no shell-like punctuation.
    This lets us recognise ``acme:fetch_data`` / ``my-plugin:hello`` /
    ``mcp__server:tool`` as literal tool names while preserving the
    existing pattern usages (``bash:rm -rf``, ``bash:sed -i``,
    ``bash:python -c``) which all contain whitespace.

    The corner case ``bash:rm`` (a hypothetical bare-identifier
    argpattern) tips toward "literal tool name". no shipped pack
    uses that form, so the change is safe.
    """
    return bool(_NAMESPACED_TOOL_RE.match(str(tool)))


def _physical_tool(tool: str) -> str:
    """Return the tool name to ground against.

    Strips the ``:argpattern`` suffix when the form is a true
    pattern-shortcut; passes namespaced literal names through.
    """
    if ":" in tool and not _is_namespaced_tool_name(tool):
        return tool.split(":", 1)[0]
    return tool


def _called(tool: str) -> Atom:
    """Create a ``called`` / ``called_with`` atom for ``tool``.

    ``tool:argpattern`` -> ``called_with(physical, argpattern)``.
    Bare ``tool`` or namespaced-literal ``plugin:skill`` ->
    ``called(tool)``.
    """
    tool = str(tool)
    if ":" in tool and not _is_namespaced_tool_name(tool):
        physical, pattern = tool.split(":", 1)
        return Atom("called_with", physical, pattern)
    return Atom("called", tool)


def _count_var(tool: str) -> Var:
    """Create a ``count`` / ``count_with`` Var for ``tool``.

    Same disambiguation as :func:`_called`.
    """
    tool = str(tool)
    if ":" in tool and not _is_namespaced_tool_name(tool):
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
        liveness: True for liveness patterns (``F``, ``always_followed_by``,
            ``required_steps_completion``, …). used by the runtime to
            suppress spurious mid-trace violations.
        args: Original arguments the factory was invoked with. Needed for
            lossless discovery-store round-trip: ``_extract_args_from_formula``
            used to walk the formula tree and only recovered ``called()``
            tool names, silently dropping numeric thresholds (``rate_limit``
            N, ``deadline`` steps, ``bounded_retry`` max, …). When a stored
            pattern was re-materialized, those numeric args collapsed to
            nothing and the rule degraded (#13). Patterns now record their
            args directly, so store serialization is exact.
    """

    formula: Formula
    desc: str
    pattern_name: str
    liveness: bool = False
    args: tuple = ()
    enforcement_strategy: Any = None
    """Optional per-pattern strategy override. When set, the monitor
    routes a violation of this formula to ``enforcement_strategy``
    instead of the default ``DetBlock``. Used by patterns whose
    intent is something other than "block on violation". currently
    ``redirect_to_safe`` (which attaches a ``RedirectToSafe`` so a
    violation surfaces as a substitute tool call). The user's
    explicit ``policy={...}`` mapping still wins over this when both
    are set, since the user has the final say on enforcement choice.
    """

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


def _ensure_non_empty(value: str, *, pattern: str, arg: str) -> str:
    """Reject ``""``, ``None``, or whitespace-only tool names at factory time.

    Why this exists
    ---------------
    ``_called("")`` produces the atom ``called()`` which the grounding layer
    never emits, so the formula is vacuously satisfied *and* vacuously
    unreachable. Silent vacuity is the exact failure mode we're hardening
    against here. the operator thinks they added a guard; the runtime sees
    nothing.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{pattern}: argument {arg!r} must be a non-empty string "
            f"(got {value!r}). An empty tool name silently disables the "
            "contract. this is almost never what you want."
        )
    return value


def _ensure_distinct(a: str, b: str, *, pattern: str, arg_a: str, arg_b: str) -> None:
    """Reject degenerate ``f(x, x)`` pattern calls.

    Why this exists
    ---------------
    Most two-arg patterns (``must_precede``, ``always_followed_by``,
    ``mutual_exclusion``, ``no_reversal``, ``deadline``, …) become trivially
    satisfied or trivially violated when the two tool names collide:

    * ``must_precede("A", "A")`` compiles to ``!called(A) U called(A)``.
      every call to ``A`` satisfies the Until at the same step, so the
      constraint is *always* True. Operators typing the same tool twice by
      mistake get a silent no-op.
    * ``mutual_exclusion("A", "A")`` is ``G(called(A) → G(!called(A)))``,
      which forbids any second call to ``A`` (silently turning into a weak
      ``idempotent`` with a misleading pattern name).
    * ``deadline("A", "A", n)`` is satisfied at the trigger step itself
      and is therefore a no-op.

    All of these are almost certainly user errors; surface them at
    construction time with a clear message instead of letting the trace
    evaluator quietly pass.
    """
    _ensure_non_empty(a, pattern=pattern, arg=arg_a)
    _ensure_non_empty(b, pattern=pattern, arg=arg_b)
    if a == b:
        raise ValueError(
            f"{pattern}: {arg_a!r} and {arg_b!r} must refer to different "
            f"tools (got {a!r} for both). A same-tool pattern is either "
            "vacuously satisfied or silently degenerates into a different "
            "contract. use ``idempotent`` / ``rate_limit`` if you meant "
            "'at most once' / 'at most N times'."
        )


def workflow_step(
    trigger: Atom,
    next_action: Atom,
    desc: str = "",
) -> DetFormula:
    """Prescriptive next-step obligation: ``G(trigger -> X(next_action))``.

    Reads as "whenever ``trigger`` holds at the current event, the agent
    must satisfy ``next_action`` at the very next event". This is the
    *prescriptive* counterpart of the existing block-style patterns
    (``never_together``, ``arg_blacklist``, ...): instead of saying
    "you must not do X", a workflow_step says "you must do X next".

    Both ``trigger`` and ``next_action`` may be ANY atomic predicate
    (``called(...)``, ``ctx(k, v)``, ``arg_field_has(...)``, ...) — the
    runtime extracts them via the same valuation machinery that powers
    block contracts, so the symmetry is genuine.

    Use case: workflow contracts where ``policy.md`` says "if you
    observe X, the next step is Y" — the contract author maps the
    diagnostic outcome (pushed into ``ctx`` by an integration hook or
    pulled from a tool result) to the prescribed remediation tool.

    Example::

        workflow_step(
            Atom("ctx", "roaming_status", "disabled"),
            Atom("called", "toggle_roaming"),
            desc="after detecting roaming disabled, the next action must be toggle_roaming",
        )

    Args:
        trigger: Atomic predicate; when this is True at an event, the
            obligation is incurred.
        next_action: Atomic predicate; must hold at the very next event.
        desc: Optional human-readable description (default auto-built
            from the two atoms).

    Returns:
        A ``DetFormula`` (NOT marked liveness — X is one-step bounded
        and the runtime can decide it after a single event, unlike F).

    Caveat (end-of-trace): under weak finite-trace semantics ``X`` is
    vacuously true at the last position, so a ``trigger`` that fires on
    the *final* event of a batch-verified trace incurs no violation
    (there is no "next" event to inspect). In incremental enforce mode
    this self-corrects — when the next event arrives, a non-matching
    next action is blocked and rolled back, effectively forcing
    ``next_action`` — but a whole-trace ``verify`` / replay will
    silently pass a trailing trigger. Mirrors the ``rotate_session``
    liveness caveat; relevant only to post-hoc batch checks, not live
    guarding.
    """
    if not isinstance(trigger, Atom) or not isinstance(next_action, Atom):
        raise TypeError(
            f"workflow_step requires Atom arguments; got "
            f"trigger={type(trigger).__name__}, next_action={type(next_action).__name__}"
        )

    formula = G(Implies(trigger, X(next_action)))
    return DetFormula(
        formula=formula,
        desc=desc
        or (
            f"after {trigger.predicate}({', '.join(trigger.args)}) "
            f"the next event must satisfy "
            f"{next_action.predicate}({', '.join(next_action.args)})"
        ),
        pattern_name="workflow_step",
        liveness=False,
        args=(trigger, next_action),
    )


def must_precede(before: str, after: str, desc: str = "") -> DetFormula:
    """Enforces that one action must happen before another.

    Compiles to: ``!called(after) U called(before)``. the ``after`` action
    is forbidden until ``before`` has occurred at least once.

    Args:
        before: Tool or action that must occur first.
        after: Tool or action that must occur second.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the ordering constraint.
    """
    _ensure_distinct(
        before, after, pattern="must_precede", arg_a="before", arg_b="after"
    )
    # after is forbidden until before appears, OR after is never called
    formula = Or(
        U(Not(_called(after)), _called(before)),
        G(Not(_called(after))),
    )
    return DetFormula(
        formula=formula,
        desc=desc or f"{before} must precede {after}",
        pattern_name="must_precede",
        args=(before, after),
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
    _ensure_distinct(
        trigger,
        response,
        pattern="always_followed_by",
        arg_a="trigger",
        arg_b="response",
    )
    formula = G(Implies(_called(trigger), F(_called(response))))
    return DetFormula(
        formula=formula,
        desc=desc or f"{trigger} must always be followed by {response}",
        pattern_name="always_followed_by",
        liveness=True,
        args=(trigger, response),
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
        "never_together is deprecated. use mutual_exclusion instead. "
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
    _ensure_distinct(
        commitment,
        contradiction,
        pattern="no_reversal",
        arg_a="commitment",
        arg_b="contradiction",
    )
    formula = G(Implies(_called(commitment), G(Not(_called(contradiction)))))
    return DetFormula(
        formula=formula,
        desc=desc or f"{contradiction} must never occur after {commitment}",
        pattern_name="no_reversal",
        args=(commitment, contradiction),
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
        args=(tool, permission),
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
    _ensure_distinct(
        source, external, pattern="no_data_leak", arg_a="source", arg_b="external"
    )
    formula = G(Implies(Atom("contains", source), Not(Atom("flow", source, external))))
    return DetFormula(
        formula=formula,
        desc=desc or f"no data leak from {source} to {external}",
        pattern_name="no_data_leak",
        args=(source, external),
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
    _ensure_distinct(a, b, pattern="mutual_exclusion", arg_a="a", arg_b="b")
    formula = And(
        G(Implies(_called(a), G(Not(_called(b))))),
        G(Implies(_called(b), G(Not(_called(a))))),
    )
    return DetFormula(
        formula=formula,
        desc=desc or f"{a} and {b} are mutually exclusive",
        pattern_name="mutual_exclusion",
        args=(a, b),
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
        args=(action, max_count),
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


def _next_n(phi: Formula, n: int) -> Formula:
    """Shift ``phi`` forward by ``n`` weak-next steps."""
    result = phi
    for _ in range(n):
        result = X(result)
    return result


def _forbidden_until(until: Formula, forbidden: Formula) -> Formula:
    """``forbidden`` may not occur until ``until`` occurs, or never occurs."""
    return Or(U(Not(forbidden), until), G(Not(forbidden)))


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
        args=(action,),
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
    _ensure_distinct(
        trigger, action, pattern="deadline", arg_a="trigger", arg_b="action"
    )
    if not isinstance(steps, int) or steps < 1:
        raise ValueError(
            f"deadline: 'steps' must be a positive integer (got {steps!r}). "
            "A non-positive deadline is unsatisfiable."
        )
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
        args=(trigger, action, steps),
    )


def must_confirm(action: str, desc: str = "") -> DetFormula:
    """Enforces that an action requires explicit confirmation before execution.

    Uses a naming convention: ``confirm_{action}`` must precede ``action``.
    The confirmation tool must exist in the agent's tool set.

    Compiles to: ``!called(action) U called(confirm_action)``. the action
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
        args=(action,),
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
        args=(action, steps),
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
    _ensure_distinct(a, b, pattern="segregation_of_duty", arg_a="a", arg_b="b")
    formula = And(
        G(Implies(_called(a), G(Not(_called(b))))),
        G(Implies(_called(b), G(Not(_called(a))))),
    )
    return DetFormula(
        formula=formula,
        desc=desc or f"{a} and {b} must be performed by different agents",
        pattern_name="segregation_of_duty",
        args=(a, b),
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
        args=(action, max_retries),
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
    physical_tool = _physical_tool(tool)
    body: Formula = Not(Atom("arg_field_has", physical_tool, param, patterns[0]))
    for pattern in patterns[1:]:
        body = And(body, Not(Atom("arg_field_has", physical_tool, param, pattern)))

    formula = G(Implies(_called(tool), body))
    return DetFormula(
        formula=formula,
        desc=desc or f"{tool}.{param} must not match forbidden patterns",
        pattern_name="arg_blacklist",
        args=(tool, param, tuple(patterns)),
    )


def arg_allowlist(
    tool: str, param: str, patterns: list[str], desc: str = ""
) -> DetFormula:
    """Restricts a tool argument's value to a whitelist of regex patterns.

    The dual of :func:`arg_blacklist`: instead of banning a list of
    forbidden patterns, every call must satisfy at least one of the
    allowed patterns. Use this when the safe set is small and the
    threat surface is "anything else" (e.g. recipient must be one of
    a known set of internal IBANs, URL must point at one of an
    approved set of internal hosts).

    Compiles to LTL::

        G(called(tool) → arg_field_has(tool, param, p1) ∨ arg_field_has(tool, param, p2) ∨ ...)

    Uses ``arg_field_has`` for field-specific matching: only the value
    of ``args[param]`` is checked, not the entire serialized args dict.

    Args:
        tool: Tool name to monitor.
        param: Argument key whose value to inspect (e.g. ``"recipient"``).
        patterns: List of regex patterns. The arg value must match at
            least one. Empty list raises ``ValueError``.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the constraint.

    Raises:
        ValueError: If ``patterns`` is empty (an empty allowlist would
            block every call, which is almost always a config bug;
            use ``tool_allowlist`` to ban a tool entirely instead).
    """
    if not patterns:
        raise ValueError(
            "arg_allowlist: 'patterns' must be non-empty. An empty "
            "allowlist would block every call to the tool. Use "
            "tool_allowlist to ban the tool itself, or arg_blacklist "
            "if you want to forbid specific patterns."
        )

    physical_tool = _physical_tool(tool)
    body: Formula = Atom("arg_field_has", physical_tool, param, patterns[0])
    for pattern in patterns[1:]:
        body = Or(body, Atom("arg_field_has", physical_tool, param, pattern))

    formula = G(Implies(_called(tool), body))
    return DetFormula(
        formula=formula,
        desc=desc or f"{tool}.{param} must match one of the allowed patterns",
        pattern_name="arg_allowlist",
        args=(tool, param, tuple(patterns)),
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
    physical_tool = _physical_tool(tool)
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
        args=(tool, tuple(allowed_paths)),
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
    physical_tool = _physical_tool(tool)
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
        args=(tool, param, max_chars),
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
        args=(bound_tool, tuple(original_paths)),
    )


# ---------------------------------------------------------------------------
# Layer 1. OWASP Agentic Top 10 patterns (pure LTL over existing atoms)
# ---------------------------------------------------------------------------


def destructive_action_gate(
    tool: str, approver_role: str = "approver", desc: str = ""
) -> DetFormula:
    """Gate a destructive tool behind human confirmation + role permission.

    Stronger than ``must_confirm``. forces a human (or a different agent
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
        args=(tool, approver_role),
    )


def untrusted_source_gate(
    sources: list[str], sinks: list[str], desc: str = ""
) -> tuple[DetFormula, DetFormula]:
    """After reading from an untrusted source, sensitive sinks require
    re-confirmation before proceeding.

    The **single most differentiating P0** pattern. compositional over
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
        Contract(agent=agent, assumption=assumption, guarantee=enforcement)

    Args:
        sources: Untrusted input tools.
        sinks: Sensitive output tools.
        desc: Optional human-readable description.

    Returns:
        A tuple of ``(assumption_formula, enforcement_formula)``. both
        ``DetFormula``. Use with ``Contract(assumption=..., guarantee=...)``.
    """
    if not sources:
        raise ValueError(
            "untrusted_source_gate: 'sources' must not be empty; "
            "without a source, the assumption never fires and the gate is a no-op."
        )
    if not sinks:
        raise ValueError(
            "untrusted_source_gate: 'sinks' must not be empty; "
            "without a sink there is nothing to guard."
        )
    for s in sources:
        _ensure_non_empty(s, pattern="untrusted_source_gate", arg="sources")
    for s in sinks:
        _ensure_non_empty(s, pattern="untrusted_source_gate", arg="sinks")
    overlap = set(sources) & set(sinks)
    if overlap:
        raise ValueError(
            f"untrusted_source_gate: sources and sinks overlap on {sorted(overlap)!r}. "
            "A tool listed as both 'tainted input' and 'sensitive output' makes "
            "the gate self-triggering. every call becomes its own unconfirmed sink."
        )
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
            args=(tuple(sources), tuple(sinks)),
        )
    else:
        enforcement = DetFormula(
            formula=enforcement.formula,
            desc=desc or f"after [{src_str}], [{sink_str}] requires re-confirmation",
            pattern_name="untrusted_source_gate",
            args=(tuple(sources), tuple(sinks)),
        )

    return assumption, enforcement


def required_steps_completion(
    trigger: str, required_set: list[str], desc: str = ""
) -> DetFormula:
    """Every trigger must eventually be followed by ALL required steps.

    A liveness checklist. the trigger-side agent's guarantee becomes
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
    _ensure_non_empty(trigger, pattern="required_steps_completion", arg="trigger")
    if not required_set:
        raise ValueError(
            "required_steps_completion: 'required_set' must not be empty. "
            "An empty checklist is vacuously satisfied for every trigger."
        )
    # Reject duplicate / self-referential steps: they either no-op (dup) or
    # silently collapse the contract into a tautology (trigger also in set).
    seen: set[str] = set()
    for r in required_set:
        _ensure_non_empty(r, pattern="required_steps_completion", arg="required_set")
        if r == trigger:
            raise ValueError(
                f"required_steps_completion: trigger {trigger!r} cannot also "
                "appear in required_set. the trigger would be its own "
                "follow-up, making the constraint trivially satisfied."
            )
        if r in seen:
            raise ValueError(
                f"required_steps_completion: required_set contains a duplicate "
                f"step {r!r}. Deduplicate before building the contract."
            )
        seen.add(r)

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
        args=(trigger, tuple(required_set)),
    )


def loop_detection(action: str, max_consecutive: int, desc: str = "") -> DetFormula:
    """Prevent tight loops: the same tool called N times consecutively.

    Distinct from ``bounded_retry`` (global count) and ``cooldown``
    (minimum interval between calls). This catches runaway loops
    regardless of what happens between bursts.

    Covers: Runaway agent failure class.

    Uses the ``consecutive_count(tool)`` atom. a grounding-level
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
        args=(action, max_consecutive),
    )


# ---------------------------------------------------------------------------
# Workflow hygiene patterns. no new atoms required
# ---------------------------------------------------------------------------


def dry_run_before_commit(
    dry_run: str,
    commit: str,
    desc: str = "",
) -> DetFormula:
    """Require a dry-run / plan step before a committing action."""
    base = must_precede(dry_run, commit, desc)
    return DetFormula(
        formula=base.formula,
        desc=desc or f"{dry_run} dry-run must precede {commit}",
        pattern_name="dry_run_before_commit",
        args=(dry_run, commit),
    )


def backup_before_destructive(
    backup: str,
    action: str,
    desc: str = "",
) -> DetFormula:
    """Require a backup/snapshot before a destructive action."""
    base = must_precede(backup, action, desc)
    return DetFormula(
        formula=base.formula,
        desc=desc or f"{backup} backup must precede destructive action {action}",
        pattern_name="backup_before_destructive",
        args=(backup, action),
    )


def audit_after(action: str, audit: str, desc: str = "") -> DetFormula:
    """Require an audit/log step after a sensitive action."""
    base = always_followed_by(action, audit, desc)
    return DetFormula(
        formula=base.formula,
        desc=desc or f"{action} must be followed by audit step {audit}",
        pattern_name="audit_after",
        liveness=True,
        args=(action, audit),
    )


def approval_freshness(
    approval: str,
    action: str,
    steps: int,
    desc: str = "",
) -> DetFormula:
    """Require ``action`` to happen within ``steps`` of a fresh approval.

    Encodes a past-looking business rule with future LTL: ``action`` is
    forbidden until approval; after approval, ``action`` is allowed for
    ``steps`` future positions; once the window closes, it is forbidden
    until the next approval.
    """
    _ensure_distinct(
        approval, action, pattern="approval_freshness", arg_a="approval", arg_b="action"
    )
    if not isinstance(steps, int) or steps < 1:
        raise ValueError(
            f"approval_freshness: 'steps' must be a positive integer (got {steps!r})."
        )
    approval_atom = _called(approval)
    action_atom = _called(action)
    closed_window = _forbidden_until(approval_atom, action_atom)
    formula = And(
        closed_window,
        G(Implies(approval_atom, _next_n(closed_window, steps + 1))),
    )
    return DetFormula(
        formula=formula,
        desc=desc or f"{action} requires approval {approval} within {steps} steps",
        pattern_name="approval_freshness",
        args=(approval, action, steps),
    )


def sanitized_before_sink(
    source: str,
    sanitizer: str,
    sink: str,
    desc: str = "",
) -> DetFormula:
    """After an untrusted source is read, require sanitization before sink use."""
    _ensure_distinct(
        source,
        sanitizer,
        pattern="sanitized_before_sink",
        arg_a="source",
        arg_b="sanitizer",
    )
    _ensure_distinct(
        sanitizer,
        sink,
        pattern="sanitized_before_sink",
        arg_a="sanitizer",
        arg_b="sink",
    )
    _ensure_distinct(
        source, sink, pattern="sanitized_before_sink", arg_a="source", arg_b="sink"
    )
    formula = G(
        Implies(
            _called(source),
            X(_forbidden_until(_called(sanitizer), _called(sink))),
        )
    )
    return DetFormula(
        formula=formula,
        desc=desc or f"after {source}, {sanitizer} must precede {sink}",
        pattern_name="sanitized_before_sink",
        args=(source, sanitizer, sink),
    )


def duplicate_call_limit(
    tool: str,
    args_pattern: str,
    max_count: int,
    desc: str = "",
) -> DetFormula:
    """Limit repeated calls to one physical tool with matching arguments."""
    _ensure_non_empty(tool, pattern="duplicate_call_limit", arg="tool")
    _ensure_non_empty(args_pattern, pattern="duplicate_call_limit", arg="args_pattern")
    if not isinstance(max_count, int) or max_count < 0:
        raise ValueError(
            f"duplicate_call_limit: 'max_count' must be a non-negative integer "
            f"(got {max_count!r})."
        )
    formula = G(Le(Var("count_with", tool, args_pattern), Const(max_count)))
    return DetFormula(
        formula=formula,
        desc=desc
        or f"{tool} calls matching {args_pattern!r} at most {max_count} times",
        pattern_name="duplicate_call_limit",
        args=(tool, args_pattern, max_count),
    )


def tool_allowlist(allowed_tools: list[str], desc: str = "") -> DetFormula:
    """Only tools in the allowlist may be called.

    First-line defense against prompt-injection-introduced tool
    invocations. if a malicious prompt tricks the agent into calling
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
    # Build the rule shape: at every timestep where SOME tool fires
    # (``called_any``), the call must be one of the allowed tools.
    # Using a guarded implication rather than a bare disjunction is
    # essential. the bare ``G(∨ called(tᵢ))`` form is FALSE at the
    # initial / non-tool timestep where no called(tᵢ) is true,
    # which made the rule self-violate before any call ever fired.
    # See cross_language ``tool_allowlist__empty_trace_satisfied``.
    called_any = Atom("called_any")
    if not allowed_tools:
        # Empty allowlist = nothing allowed.  Whenever a tool fires,
        # the consequent (an empty Or) is false, so the implication
        # forces ``called_any`` to be false at every timestep.
        formula = G(Not(called_any))
    else:
        if len(allowed_tools) == 1:
            allowed = _called(allowed_tools[0])
        else:
            allowed = _called(allowed_tools[0])
            for t in allowed_tools[1:]:
                allowed = Or(allowed, _called(t))
        formula = G(Implies(called_any, allowed))

    tools_str = ", ".join(allowed_tools)
    return DetFormula(
        formula=formula,
        desc=desc or f"only [{tools_str}] may be called",
        pattern_name="tool_allowlist",
        args=(tuple(allowed_tools),),
    )


def redirect_to_safe(
    unsafe: str,
    safe: str,
    message: str = "",
    desc: str = "",
) -> DetFormula:
    """Substitute a forbidden tool call with a pre-approved safe one.

    Compiles to ``G(Not(called(unsafe)))``: any call to ``unsafe`` is
    a violation. The bundled ``RedirectToSafe`` strategy turns that
    violation into a ``redirected`` outcome carrying ``safe`` as the
    fallback tool name; adapters then invoke ``safe`` in place of the
    original call, transparently to the model.

    Combine with an assumption to make the redirect conditional::

        contract("refund safety")
            .assume("amount > 10000")
            .guarantees(redirect_to_safe("issue_refund", "log_refund_request"))

    Without an assumption, every call to ``unsafe`` is redirected. The
    user must register both tools with the integration framework:
    Sponsio does NOT synthesize tools. For a clean model experience,
    ``safe`` should accept the same arguments as ``unsafe`` (or the
    adapter must coerce them).

    Args:
        unsafe: Tool name whose calls should be substituted.
        safe: Tool name to invoke instead. Must already be registered
            with the framework (LangGraph ``ToolNode``, CrewAI agent
            tools, etc.).
        message: Optional human-readable note appended to the
            enforcement message (visible in logs / dashboard).
        desc: Optional override for the contract description. When
            empty, defaults to ``"redirect `unsafe` -> `safe`"`` plus
            the ``message`` in parentheses. Accepting this kwarg
            keeps the factory uniform with the rest of the pattern
            library so LLM extraction / yaml ``desc:`` overrides
            work the same way they do for ``rate_limit`` etc.

    Returns:
        A ``DetFormula`` whose ``enforcement_strategy`` is bound to a
        ``RedirectToSafe(safe=safe)`` instance.

    Raises:
        ValueError: If ``unsafe`` / ``safe`` are empty or identical.
    """
    _ensure_non_empty(unsafe, pattern="redirect_to_safe", arg="unsafe")
    _ensure_non_empty(safe, pattern="redirect_to_safe", arg="safe")
    _ensure_distinct(
        unsafe,
        safe,
        pattern="redirect_to_safe",
        arg_a="unsafe",
        arg_b="safe",
    )

    # Local import keeps ``patterns.library`` free of a hard runtime
    # dependency at import time; the strategy class only matters when
    # the formula actually fires.
    from sponsio.runtime.strategies import RedirectToSafe

    formula = G(Not(_called(unsafe)))
    if desc:
        final_desc = desc
    else:
        final_desc = f"redirect `{unsafe}` -> `{safe}`"
        if message:
            final_desc = f"{final_desc} ({message})"

    return DetFormula(
        formula=formula,
        desc=final_desc,
        pattern_name="redirect_to_safe",
        args=(unsafe, safe, message),
        enforcement_strategy=RedirectToSafe(safe=safe, message=message),
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
        args=(tuple(forbidden),),
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

    base = arg_blacklist(
        tool,
        "query",
        forbidden,
        desc=desc or f"{tool} must not use [{', '.join(forbidden)}]",
    )
    # Preserve the caller-facing pattern identity and its args so the
    # discovery store re-materializes ``dangerous_sql_verbs`` instead of
    # the internal ``arg_blacklist`` delegation.
    return DetFormula(
        formula=base.formula,
        desc=base.desc,
        pattern_name="dangerous_sql_verbs",
        args=(tool, tuple(forbidden)),
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
        args=(action,),
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
        Contract(agent=agent, assumption=assumption, guarantee=enforcement)

    Args:
        source: The untrusted input tool.
        action: The action that needs confirmation after the source.
        desc: Optional human-readable description.

    Returns:
        Tuple of ``(assumption, enforcement)``. both ``DetFormula``.
    """
    _ensure_distinct(
        source, action, pattern="confirm_after_source", arg_a="source", arg_b="action"
    )
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
        args=(source, action),
    )

    return assumption, enforcement


# ---------------------------------------------------------------------------
# Layer 2. Atom extensions (new accumulators in grounding)
# ---------------------------------------------------------------------------


def token_budget(max_tokens: int, scope: str = "total", desc: str = "") -> DetFormula:
    """Limit total token consumption within a session.

    Covers: **ASI08** (cascading failures via token exhaustion),
    runaway agent class.

    New atom: ``token_count(type)``. int accumulator extracted from
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
        args=(max_tokens, scope),
    )


def arg_value_range(
    tool: str,
    field: str,
    min_val: int | float | None = None,
    max_val: int | float | None = None,
    desc: str = "",
) -> DetFormula:
    """Constrain a numeric argument to a value range.

    Uses the ``arg_numeric(tool, field)`` atom. a grounding-level
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
        args=(tool, field, min_val, max_val),
    )


# ---------------------------------------------------------------------------
# Layer 3. Response content constraints (migrated from sto_catalog in P2).
#
# These were previously sto evaluators but don't need LLM judging. they
# are precisely computable against llm_response events. Ship-as-det gives
# them clean A/G composition and the fast LTL path.
# ---------------------------------------------------------------------------


def max_length(
    max_words: int | None = None,
    max_chars: int | None = None,
    desc: str = "",
) -> DetFormula:
    """Response length must stay within the given word / character budget.

    Grounded against ``response_words`` / ``response_chars`` (populated on
    every ``llm_response`` event with content). At non-response events
    both atoms default to 0, so the constraint is vacuously satisfied.

    Compiles to::

        G(response_words ≤ max_words  ∧  response_chars ≤ max_chars)

    Args:
        max_words: Maximum allowed word count. Either this or ``max_chars``
            must be provided.
        max_chars: Maximum allowed character count.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula``.

    Raises:
        ValueError: If neither limit is provided.
    """
    if max_words is None and max_chars is None:
        raise ValueError("max_length requires max_words or max_chars")

    parts: list[Formula] = []
    if max_words is not None:
        parts.append(Le(Var("response_words"), Const(max_words)))
    if max_chars is not None:
        parts.append(Le(Var("response_chars"), Const(max_chars)))

    body: Formula = parts[0] if len(parts) == 1 else And(parts[0], parts[1])
    formula = G(body)

    if desc:
        desc_str = desc
    elif max_words is not None and max_chars is not None:
        desc_str = f"response ≤ {max_words} words and ≤ {max_chars} chars"
    elif max_words is not None:
        desc_str = f"response ≤ {max_words} words"
    else:
        desc_str = f"response ≤ {max_chars} chars"

    return DetFormula(
        formula=formula,
        desc=desc_str,
        pattern_name="max_length",
        args=(max_words, max_chars),
    )


# Default PII regex patterns. reuse sto_catalog's regex set for parity.
_DEFAULT_PII_PATTERNS: dict[str, str] = {
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "credit_card": r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",
    "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    "phone": r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
}


def no_pii(fields: list[str] | None = None, desc: str = "") -> DetFormula:
    """Response must not contain regex-detectable PII (SSN, CC, email, phone).

    Uses the ``llm_said`` grounding atom. patterns are compiled into a
    single alternation regex checked against each ``llm_response`` event.

    For semantic PII detection (names, contextual identifiers), use a
    sto atom with an LLM judge. this pattern only covers syntactic PII.

    Args:
        fields: Subset of ``{"ssn", "credit_card", "email", "phone"}``.
            None = all four.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula``.
    """
    selected = list(_DEFAULT_PII_PATTERNS) if fields is None else list(fields)
    unknown = set(selected) - set(_DEFAULT_PII_PATTERNS)
    if unknown:
        raise ValueError(
            f"unknown PII field(s): {sorted(unknown)}. "
            f"Available: {sorted(_DEFAULT_PII_PATTERNS)}"
        )
    pattern = "|".join(_DEFAULT_PII_PATTERNS[f] for f in selected)

    formula = G(Not(Atom("llm_said", pattern)))
    return DetFormula(
        formula=formula,
        desc=desc or f"response must not contain PII ({', '.join(selected)})",
        pattern_name="no_pii",
        args=(tuple(selected),),
    )


def no_keywords(words: list[str], desc: str = "") -> DetFormula:
    """Response must not contain any of the given keywords.

    Keywords are escaped and joined into a word-boundary-anchored regex
    checked against each ``llm_response`` event via ``llm_said``.

    Args:
        words: Non-empty list of forbidden keywords. Matched case-insensitively.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula``.

    Raises:
        ValueError: If ``words`` is empty.
    """
    if not words:
        raise ValueError("no_keywords requires at least one keyword")

    pattern = r"(?i)\b(" + "|".join(_re.escape(w) for w in words) + r")\b"
    formula = G(Not(Atom("llm_said", pattern)))
    return DetFormula(
        formula=formula,
        desc=desc or f"response must not contain keywords: {words}",
        pattern_name="no_keywords",
        args=(tuple(words),),
    )


def delegation_depth_limit(max_depth: int, desc: str = "") -> DetFormula:
    """Limit the depth of agent-to-agent delegation chains.

    Covers: **ASI07** (inter-agent communication safety, recursive
    delegation).

    New atom: ``delegation_depth()``. int accumulator maintained by
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
        args=(max_depth,),
    )


# ---------------------------------------------------------------------------
# Layer 3. External-fact (ctx) patterns
#
# Bridge Sponsio to the host stack's identity, provenance, and trust
# systems. The raw plumbing is ``guard.observe_context({k: v, ...})``
# which emits ``ctx(k, v)`` atoms at every subsequent event; this
# factory wraps the common shape "when tool X is called, ctx[k] must
# be one of these allowed values" so users don't hand-write the LTL
# disjunction every time.
#
# Covers the runtime half of **ASI-03** (identity), **ASI-06** (memory
# poisoning via content-source gating), and **ASI-07** (inter-agent
# comm via msg_verified gating). Users supply their own key convention;
# Sponsio doesn't hard-code "caller_id" vs "source" vs "msg_sender"
# because each team has their own tagging scheme.
# ---------------------------------------------------------------------------


def ctx_required(
    tool: str,
    key: str,
    allowed_values: list[str],
    desc: str = "",
) -> DetFormula:
    """When ``tool`` is called, ``ctx[key]`` must be one of ``allowed_values``.

    The external fact ``ctx[key]`` is populated by the integration via
    ``guard.observe_context({key: value, ...})``. If the key is missing
    from the current context at the time of the call, the contract
    violates (fail-closed): this is a deliberate choice so forgetting
    to wire up the host adapter is loud, not silent.

    Covers: **ASI-03** (when ``key`` is an identity attestation),
    **ASI-06** (when ``key`` is a content-source tag), **ASI-07**
    (when ``key`` carries signed-message metadata).

    Compiles to::

        G(called(tool) → (ctx(key, v1) ∨ ctx(key, v2) ∨ … ∨ ctx(key, vN)))

    Usage::

        # ASI-03: wire_transfer only by attested prod agents
        ctx_required("wire_transfer", "caller_id_prefix", ["spiffe://prod/"])

        # ASI-06: the answer must cite canonical sources only
        ctx_required("answer_policy", "content_source",
                     ["canonical:/v3", "canonical:/v2"])

        # ASI-07: downstream publish only when upstream msg was verified
        ctx_required("publish", "msg_verified", ["true"])

    Args:
        tool: Tool name this contract applies to.
        key: Context key whose value is checked.
        allowed_values: Non-empty list of permitted values for ``ctx[key]``.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the requirement.

    Raises:
        ValueError: If ``allowed_values`` is empty (an empty allowlist
            would reject every call to ``tool``, almost certainly a
            user error. surface it at construction time instead of
            letting every call fail silently at runtime).
    """
    _ensure_non_empty(tool, pattern="ctx_required", arg="tool")
    _ensure_non_empty(key, pattern="ctx_required", arg="key")
    if not allowed_values:
        raise ValueError(
            "ctx_required: 'allowed_values' must not be empty. an empty "
            "allowlist rejects every call to the tool. Use "
            "`tool_allowlist([])` if you really want to block everything, "
            "or pass at least one permitted value here."
        )
    clean_values = [str(v) for v in allowed_values]

    # Build the disjunction: ctx(key, v1) ∨ ctx(key, v2) ∨ ...
    disjunction: Formula = Atom("ctx", key, clean_values[0])
    for val in clean_values[1:]:
        disjunction = Or(disjunction, Atom("ctx", key, val))

    formula = G(Implies(_called(tool), disjunction))

    values_str = ", ".join(clean_values)
    return DetFormula(
        formula=formula,
        desc=desc or f"{tool} requires ctx[{key}] ∈ [{values_str}]",
        pattern_name="ctx_required",
        args=(tool, key, tuple(clean_values)),
    )


def ctx_matches_required(
    tool: str,
    key: str,
    pattern: str,
    desc: str = "",
) -> DetFormula:
    r"""When ``tool`` is called, ``ctx[key]`` must match the regex ``pattern``.

    Regex variant of :func:`ctx_required` for cases where the allowed
    set is better expressed as a pattern (e.g. ``spiffe://prod/.*``,
    ``^canonical:/v[0-9]+$``) than an exhaustive list.

    Covers: same ASI slice as ``ctx_required``. identity / content-
    source / signed-message gating where the valid value set is open-
    ended.

    Compiles to::

        G(called(tool) → ctx_matches(key, pattern))

    Usage::

        # Any caller_id under the prod SPIFFE trust domain
        ctx_matches_required("wire_transfer", "caller_id",
                             r"^spiffe://prod/.*")

        # Any canonical versioned policy (v1, v2, v3, ...)
        ctx_matches_required("answer_policy", "content_source",
                             r"^canonical:/v\d+$")

    Args:
        tool: Tool name this contract applies to.
        key: Context key whose value is regex-matched.
        pattern: Python regex to match ``ctx[key]`` against.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` encoding the requirement.
    """
    _ensure_non_empty(tool, pattern="ctx_matches_required", arg="tool")
    _ensure_non_empty(key, pattern="ctx_matches_required", arg="key")
    _ensure_non_empty(pattern, pattern="ctx_matches_required", arg="pattern")

    formula = G(Implies(_called(tool), Atom("ctx_matches", key, pattern)))
    return DetFormula(
        formula=formula,
        desc=desc or f"{tool} requires ctx[{key}] to match /{pattern}/",
        pattern_name="ctx_matches_required",
        args=(tool, key, pattern),
    )


# ---------------------------------------------------------------------------
# Time-window patterns (event-clock. replay-deterministic)
# ---------------------------------------------------------------------------
#
# These compose around the ``time_since(predicate_key)`` numeric atom
# emitted by ``sponsio.tracer.grounding``. The grounding layer tracks
# ``last_ts[predicate_key]`` for every freshly-emitted boolean
# predicate; ``time_since(P) = state.now - state.last_ts[P]`` is
# derived per event and lifted into the valuation dict for the keys
# the contracts actually reference (extracted via
# ``collect_content_atoms`` over ``"time_since"`` Var nodes).
#
# Sentinel: when ``P`` was never emitted, grounding returns ``1e18``
# rather than ``0`` so ``Le(time_since(P), N)`` evaluates False.
# "never happened" is "very long ago", not "just now". This is the
# semantic trap the dedicated derived atom exists to avoid; do not
# expose ``last_ts`` directly.


def time_since(
    predicate_key: str, max_seconds: int | float, desc: str = ""
) -> DetFormula:
    """Constrain how recently a predicate was last true.

    Compiles to::

        G(time_since(predicate_key) ≤ max_seconds)

    Pair this with another temporal pattern via ``&`` to gate an
    action on a recent occurrence. e.g. an approval that's still
    fresh::

        # "refund only allowed within 1h of an active senior_eng approval"
        gate = G(Implies(
            _called("refund"),
            Le(Var("time_since", "ctx(approval.role, senior_eng)"), Const(3600))
        ))

    The ``predicate_key`` argument is the *grounded* key string
    (``"called(refund)"``, ``"ctx(approval.role, alice)"``, ``"flow(rag,
    answer)"``). i.e. what ``Atom.key()`` would produce for the
    predicate you want to time. We require the explicit string rather
    than an Atom because ``time_since`` covers the union of every atom
    family (called / ctx / flow / contains / segment / …) and we don't
    want to re-derive each family's key shape here.

    Args:
        predicate_key: Grounded predicate key string. Use ``Atom(...).key()``
            or ``pred_key(...)`` to build it if unsure.
        max_seconds: Maximum allowed delta (event-clock seconds). Use
            integer ts if the integration is ticking events with a
            logical clock. the comparison is unitless.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` enforcing the recency bound globally.
    """
    _ensure_non_empty(predicate_key, pattern="time_since", arg="predicate_key")
    if not isinstance(max_seconds, (int, float)) or max_seconds < 0:
        raise ValueError(
            f"time_since: max_seconds must be a non-negative number "
            f"(got {max_seconds!r})."
        )

    formula = G(Le(Var("time_since", predicate_key), Const(max_seconds)))
    return DetFormula(
        formula=formula,
        desc=desc or f"{predicate_key} must have occurred within last {max_seconds}s",
        pattern_name="time_since",
        args=(predicate_key, max_seconds),
    )


def approval_active(
    action: str,
    role: str,
    max_seconds: int | float,
    desc: str = "",
) -> DetFormula:
    """Gate ``action`` on a recent allow-decision approval from ``role``.

    Compiles to::

        G(called(action) → (
              ctx_matches("approval.role", role)
            ∧ ctx_matches("approval.decision", "allow")
            ∧ time_since(ctx(approval.role, role)) ≤ max_seconds
        ))

    Pairs with :meth:`BaseGuard.observe_approval` on the integration
    side: the approver pushes ``approval.role`` / ``approval.decision``
    via ``observe_context`` and the contract checks both the static
    facts and the recency. ``max_seconds`` measures event-clock time
    since the approval was last refreshed (the predicate key timed is
    ``"ctx(approval.role, <role>)"``. that key gets re-emitted each
    event the role is current, so its ``last_ts`` advances naturally
    while the approval stays in context).

    Args:
        action: Tool name to gate (e.g. ``"issue_refund"``).
        role: Required approver role (must match
            ``observe_approval(role=...)``).
        max_seconds: Approval validity window in event-clock seconds.
        desc: Optional human-readable description.

    Returns:
        A ``DetFormula`` enforcing the approval gate.
    """
    _ensure_non_empty(action, pattern="approval_active", arg="action")
    _ensure_non_empty(role, pattern="approval_active", arg="role")
    if not isinstance(max_seconds, (int, float)) or max_seconds < 0:
        raise ValueError(
            f"approval_active: max_seconds must be a non-negative number "
            f"(got {max_seconds!r})."
        )

    role_key = f"ctx(approval.role, {role})"
    body = And(
        And(
            Atom("ctx_matches", "approval.role", _re.escape(role)),
            Atom("ctx_matches", "approval.decision", "allow"),
        ),
        Le(Var("time_since", role_key), Const(max_seconds)),
    )
    formula = G(Implies(_called(action), body))
    return DetFormula(
        formula=formula,
        desc=desc or f"{action} requires active {role} approval (≤{max_seconds}s old)",
        pattern_name="approval_active",
        args=(action, role, max_seconds),
    )
