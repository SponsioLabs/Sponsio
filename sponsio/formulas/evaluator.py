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
    Var,
    Const,
    Formula,
)


def _resolve_arith(expr: Var | Const, state: dict[str, object]) -> int | float:
    """Resolves an arithmetic expression to a numeric value.

    Args:
        expr: A ``Var`` (looked up in *state*) or ``Const`` (literal).
        state: Predicate valuation dict for the current timestep.

    Returns:
        The numeric value, defaulting to ``0`` for missing variables.
    """
    if isinstance(expr, Const):
        return expr.value
    # Var: look up in the state dict
    val = state.get(expr.key(), 0)
    if isinstance(val, (int, float)):
        return val
    return 0


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
        return _resolve_arith(formula.left, state) <= _resolve_arith(
            formula.right, state
        )

    if isinstance(formula, Lt):
        return _resolve_arith(formula.left, state) < _resolve_arith(
            formula.right, state
        )

    if isinstance(formula, Ge):
        return _resolve_arith(formula.left, state) >= _resolve_arith(
            formula.right, state
        )

    if isinstance(formula, Gt):
        return _resolve_arith(formula.left, state) > _resolve_arith(
            formula.right, state
        )

    if isinstance(formula, Eq):
        return _resolve_arith(formula.left, state) == _resolve_arith(
            formula.right, state
        )

    if isinstance(formula, Subset):
        return bool(state.get(formula.key(), False))

    raise TypeError(f"Unknown formula type: {type(formula)}")
