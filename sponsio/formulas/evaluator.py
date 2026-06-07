"""Finite-trace evaluator for the formula AST.

Evaluates any ``Formula`` tree against a grounded trace (a list of dicts
produced by ``tracer/grounding.py``).

Supports all three formula families:

- **Propositional** (Atom, Not, And, Or, Implies): standard boolean logic.
  ``Atom.key()`` is looked up in the current timestep's dict.
- **Temporal / LTL** (G, F, X, U): recursive evaluation using *weak
  finite-trace semantics* -- at the end of the trace, ``G`` is vacuously
  true and ``F`` is vacuously false.
- **Arithmetic** (Le, Lt, Ge, Gt, Eq): resolve ``Var``/``Const`` to
  numbers, then compare.
"""

from __future__ import annotations

import warnings

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
    Le,
    Lt,
    Ge,
    Gt,
    Eq,
    Subset,
    Term,
    Var,
    Const,
    Formula,
)

# Var name prefixes that have counter semantics — for these, "missing
# from state" legitimately means "zero" (the tool was never called, no
# tokens accumulated, etc.) so defaulting to ``0`` is semantically
# correct, not a bug. Anything OUTSIDE this set that's missing from
# state is suspicious — either a typo in the Var key or a grounding
# gap — and surfaces a one-shot ``UserWarning``.
_COUNTER_VAR_NAMES: frozenset[str] = frozenset(
    {
        "count",
        "count_with",
        "consecutive_count",
        "token_count",
        "delegation_depth",
        "response_words",
        "response_chars",
        "context_length",
        "arg_numeric",
        # Time atoms (event-clock). ``now`` defaulting to 0 is the
        # correct "no events grounded yet" view. ``time_since`` is
        # only emitted for keys requested via ``content_atoms`` and
        # carries its own large sentinel for "never seen", so a
        # missing lookup here means the contract author asked for a
        # ``time_since`` predicate that no events ever populated —
        # safe to default to 0 silently.
        "now",
        "time_since",
        # Test-only / generic numeric vars that ground to 0 by convention.
        "cost",
        "x",
    }
)

_warned_missing_vars: set[str] = set()


def _resolve_arith(expr: object, state: dict[str, object]) -> object:
    """Resolves a ``Term`` to its underlying value.

    For ``Const`` returns the literal. For ``Var``:

    * If the key is in *state* and numeric, return its value.
    * If absent and the Var name is a known **counter-semantic** prefix
      (``count``, ``token_count``, ``delegation_depth``, …), default
      to ``0`` silently — "the tool was never called" / "no tokens
      accumulated yet" is the intended interpretation.
    * If absent and the Var name is **unknown**, default to ``0`` for
      backward compatibility but emit a one-shot ``UserWarning`` so
      typos and grounding gaps don't stay hidden forever.

    For any other ``Term`` (``ArgValue``, ``CtxValue``, ``UnaryFn``,
    ``ArgLength``), dispatch polymorphically via ``term.evaluate(state)``
    and return whatever the term produces — may be ``None`` (canonical
    "missing" signal) or a non-numeric value (string equality is common
    for ``Eq(ArgValue(...), CtxValue(...))``). Comparison operators
    above handle ``None`` / ``TypeError`` by evaluating to ``False``.

    Args:
        expr: A ``Term`` (Var / Const / ArgValue / CtxValue / UnaryFn /
            ArgLength / ...).
        state: Predicate valuation dict for the current timestep.

    Returns:
        The resolved value. For Var/Const this is numeric (0 default
        for missing). For other Terms it is whatever ``evaluate``
        returns — possibly ``None``.
    """
    if isinstance(expr, Const):
        return expr.value
    if isinstance(expr, Var):
        key = expr.key()
        if key in state:
            val = state[key]
            if isinstance(val, (int, float)):
                return val
            # Present but non-numeric — that's a real grounding bug;
            # warn (once) and treat as 0 to avoid crashing evaluation.
            if key not in _warned_missing_vars:
                _warned_missing_vars.add(key)
                warnings.warn(
                    f"_resolve_arith: predicate {key!r} grounded to "
                    f"non-numeric value {val!r}; treating as 0. "
                    "Check the grounding rule for this variable.",
                    UserWarning,
                    stacklevel=2,
                )
            return 0
        # Missing from state.
        if expr.name not in _COUNTER_VAR_NAMES and key not in _warned_missing_vars:
            _warned_missing_vars.add(key)
            warnings.warn(
                f"_resolve_arith: predicate {key!r} not present in trace "
                f"valuations; defaulting to 0. This usually means a typo "
                f"in the Var name or a missing grounding rule. Add "
                f"{expr.name!r} to grounding or to the counter-semantics "
                "allowlist if a 0 default is intended.",
                UserWarning,
                stacklevel=2,
            )
        return 0
    # General Term — polymorphic dispatch. May return None.
    if isinstance(expr, Term):
        return expr.evaluate(state)
    return 0


def _safe_compare(op: str, left: object, right: object) -> bool:
    """Compare two resolved values with the canonical "missing" semantics.

    If either operand is ``None`` (Term resolved to "missing"), the
    comparison evaluates to ``False`` rather than raising. Same for
    ``TypeError`` from mismatched types (e.g. ``str < int``). This is
    the Hoare-vacuity convention: a comparison that can't decide
    falls through as not-satisfied, and the contract author should
    scope it with ``Implies(scope, comparison)`` to suppress where
    irrelevant.
    """
    if left is None or right is None:
        return False
    try:
        if op == "le":
            return left <= right  # type: ignore[operator]
        if op == "lt":
            return left < right  # type: ignore[operator]
        if op == "ge":
            return left >= right  # type: ignore[operator]
        if op == "gt":
            return left > right  # type: ignore[operator]
        if op == "eq":
            return left == right
    except TypeError:
        return False
    return False


def evaluate(formula: Formula, trace: list[dict[str, object]], pos: int = 0) -> bool:
    """Evaluates a formula on a finite trace starting at a given position.

    The trace is a list of dicts where each dict maps predicate keys to
    values:

    * Propositional / temporal atoms: ``key -> bool``
    * Arithmetic variables: ``key -> int | float``
    * Set relations: ``key -> bool``

    Temporal semantics use **weak finite-trace** interpretation:

    * ``G(phi)``: *phi* holds at all positions from *pos* to end.
    * ``F(phi)``: *phi* holds at some position from *pos* to end.
    * ``X(phi)``: *phi* holds at ``pos + 1`` (``True`` at trace end).
    * ``U(phi, psi)``: *psi* holds at some ``j >= pos`` and *phi*
      holds at all positions in ``[pos, j)``.

    Args:
        formula: The formula to evaluate.
        trace: Grounded predicate valuations, one dict per timestep.
        pos: Starting position in the trace.

    Returns:
        ``True`` if the formula is satisfied on the trace from *pos*.

    Raises:
        TypeError: If the formula contains an unknown node type.
    """
    if pos >= len(trace):
        # Past end of trace — weak finite-trace semantics:
        #   G(φ) → True  (vacuously globally)
        #   F(φ) → False (never eventually)
        #   U    → False (ψ never discharged)
        #   X(φ) → True  (weak next)
        #   all others → True
        if isinstance(formula, (F, U)):
            return False
        return True

    state = trace[pos]

    # --- Propositional ---

    if isinstance(formula, Atom):
        return bool(state.get(formula.key(), False))

    if isinstance(formula, Not):
        return not evaluate(formula.child, trace, pos)

    if isinstance(formula, And):
        return evaluate(formula.left, trace, pos) and evaluate(
            formula.right, trace, pos
        )

    if isinstance(formula, Or):
        return evaluate(formula.left, trace, pos) or evaluate(formula.right, trace, pos)

    if isinstance(formula, Implies):
        return not evaluate(formula.left, trace, pos) or evaluate(
            formula.right, trace, pos
        )

    # --- Temporal (LTL) ---

    if isinstance(formula, G):
        for i in range(pos, len(trace)):
            if not evaluate(formula.child, trace, i):
                return False
        return True

    if isinstance(formula, F):
        for i in range(pos, len(trace)):
            if evaluate(formula.child, trace, i):
                return True
        return False

    if isinstance(formula, X):
        if pos + 1 >= len(trace):
            return True  # weak next
        return evaluate(formula.child, trace, pos + 1)

    if isinstance(formula, U):
        for j in range(pos, len(trace)):
            if evaluate(formula.right, trace, j):
                return True
            if not evaluate(formula.left, trace, j):
                return False
        return False  # ψ never became true

    # --- Arithmetic / Set (SMT family) ---

    if isinstance(formula, Le):
        return _safe_compare(
            "le", _resolve_arith(formula.left, state), _resolve_arith(formula.right, state)
        )

    if isinstance(formula, Lt):
        return _safe_compare(
            "lt", _resolve_arith(formula.left, state), _resolve_arith(formula.right, state)
        )

    if isinstance(formula, Ge):
        return _safe_compare(
            "ge", _resolve_arith(formula.left, state), _resolve_arith(formula.right, state)
        )

    if isinstance(formula, Gt):
        return _safe_compare(
            "gt", _resolve_arith(formula.left, state), _resolve_arith(formula.right, state)
        )

    if isinstance(formula, Eq):
        return _safe_compare(
            "eq", _resolve_arith(formula.left, state), _resolve_arith(formula.right, state)
        )

    if isinstance(formula, Subset):
        return bool(state.get(formula.key(), False))

    raise TypeError(f"Unknown formula type: {type(formula)}")
