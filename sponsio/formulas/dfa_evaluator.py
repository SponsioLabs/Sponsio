"""Formula-progression LTL monitor — drop-in alternative to the stateless
recursive evaluator for runtime verification on long traces.

Conceptually equivalent to a lazily-constructed 3-valued DFA:

* **DFA states** are reachable *residual formulas* (what's still left to
  verify after observing some prefix of the trace).
* **DFA transitions** are the rewriting rules in :func:`_progress`,
  taking a residual formula + one event's atom valuations to the next
  residual formula.
* **DFA verdicts** are the result of :func:`_simplify` collapsing a
  residual to one of three values:

  - ``True``  → formula is **definitively satisfied** (``⊤``). No future
    event can change this.
  - ``False`` → formula is **definitively violated** (``⊥``). No future
    event can change this either.
  - A non-trivial residual → **undecided** (``?``) — waiting for more
    events to decide.

We don't precompute the DFA state space (that would require the
classical NNF → ABA → subset-construction pipeline, ~1000 LOC). Instead,
we **progress lazily**: each call to :meth:`DFAEvaluator.step` runs one
rewriting + simplification round, which is O(|residual|) where
|residual| stays bounded by the closure of the original formula.
Reachable residuals form exactly the DFA state set — we just derive them
on the fly rather than enumerating up front.

Background: Rosu & Havelund 2005 "Rewriting-Based Techniques for
Runtime Verification"; Bauer-Leucker-Schallhart 2011 "Runtime
Verification for LTL and TLTL" for the 3-valued / finite-trace
semantics this module implements.

Public API is just :class:`DFAEvaluator`. Everything else is private.

Typical use::

    from sponsio.formulas.dfa_evaluator import DFAEvaluator
    from sponsio.patterns.library import rate_limit

    f = rate_limit("X", 3).formula      # raw LTL AST
    dfa = DFAEvaluator(f)
    for event_valuation in grounded_trace:
        verdict = dfa.step(event_valuation)  # "⊤" | "⊥" | "?"
        if verdict == "⊥":
            break   # violated, no point continuing
    final = dfa.finalize()               # "⊤" | "⊥" (weak finite semantics)
"""

from __future__ import annotations

from typing import Any, Literal, Union

from sponsio.formulas.formula import (
    And,
    Atom,
    Const,
    Eq,
    F,
    Formula,
    G,
    Ge,
    Gt,
    Implies,
    Le,
    Lt,
    Not,
    Or,
    Subset,
    U,
    Var,
    X,
)

# ---------------------------------------------------------------------------
# Verdict type
# ---------------------------------------------------------------------------

Verdict3 = Literal["⊤", "⊥", "?"]

# Residual formulas during progression can be either a Formula AST node or
# a Python ``True`` / ``False`` sentinel once simplification has fully
# decided that subterm. ``_simplify`` returns one of these.
Residual = Union[Formula, bool]


# ---------------------------------------------------------------------------
# Internal: resolve arithmetic (same as stateless evaluator)
# ---------------------------------------------------------------------------


def _resolve_arith(expr: Any, state: dict[str, object]) -> int | float:
    """Resolve ``Var`` / ``Const`` to a numeric value via the valuation."""
    if isinstance(expr, Const):
        return expr.value
    if isinstance(expr, Var):
        val = state.get(expr.key(), 0)
        if isinstance(val, (int, float)):
            return val
        return 0
    return 0


# ---------------------------------------------------------------------------
# Internal: evaluate a *non-temporal* (propositional + arithmetic) formula
# on a single valuation dict. Used by _progress at atom level.
# ---------------------------------------------------------------------------


def _eval_pointwise(node: Any, state: dict[str, object]) -> Residual:
    """Evaluate a temporal-free subtree against one valuation.

    Returns ``True`` / ``False``. Propositional + arithmetic operators
    are fully decided at a single timestep; this is the base case of
    progression.
    """
    if isinstance(node, bool):
        return node
    if isinstance(node, Atom):
        return bool(state.get(node.key(), False))
    if isinstance(node, Not):
        inner = _eval_pointwise(node.child, state)
        return not inner
    if isinstance(node, And):
        left = _eval_pointwise(node.left, state)
        if left is False:
            return False
        right = _eval_pointwise(node.right, state)
        return bool(left) and bool(right)
    if isinstance(node, Or):
        left = _eval_pointwise(node.left, state)
        if left is True:
            return True
        right = _eval_pointwise(node.right, state)
        return bool(left) or bool(right)
    if isinstance(node, Implies):
        left = _eval_pointwise(node.left, state)
        if left is False:
            return True
        right = _eval_pointwise(node.right, state)
        return bool(right)
    if isinstance(node, Le):
        return _resolve_arith(node.left, state) <= _resolve_arith(node.right, state)
    if isinstance(node, Lt):
        return _resolve_arith(node.left, state) < _resolve_arith(node.right, state)
    if isinstance(node, Ge):
        return _resolve_arith(node.left, state) >= _resolve_arith(node.right, state)
    if isinstance(node, Gt):
        return _resolve_arith(node.left, state) > _resolve_arith(node.right, state)
    if isinstance(node, Eq):
        return _resolve_arith(node.left, state) == _resolve_arith(node.right, state)
    if isinstance(node, Subset):
        return bool(state.get(node.key(), False))
    raise TypeError(
        f"_eval_pointwise: unexpected non-temporal node {type(node).__name__}"
    )


def _is_temporal(node: Any) -> bool:
    """True if ``node`` or any descendant contains G/F/U/X."""
    if isinstance(node, (G, F, U, X)):
        return True
    for attr in ("child", "left", "right"):
        child = getattr(node, attr, None)
        if child is not None and _is_temporal(child):
            return True
    return False


# ---------------------------------------------------------------------------
# Internal: simplification (constant folding)
# ---------------------------------------------------------------------------


def _simplify(node: Residual) -> Residual:
    """Constant-fold a residual formula.

    Propagates ``True`` / ``False`` bool sentinels through And/Or/Not/
    Implies. Leaves temporal nodes (G, F, X, U) alone — those are decided
    by progression, not simplification. Arithmetic leaves are also left
    alone (they'll be evaluated at the next timestep via progression).

    Idempotent; safe to call on already-simplified formulas.
    """
    if isinstance(node, bool):
        return node

    if isinstance(node, Not):
        inner = _simplify(node.child)
        if inner is True:
            return False
        if inner is False:
            return True
        if isinstance(inner, Not):
            return inner.child  # double negation
        if inner is node.child:
            return node
        return Not(inner)

    if isinstance(node, And):
        left = _simplify(node.left)
        if left is False:
            return False
        right = _simplify(node.right)
        if right is False:
            return False
        if left is True:
            return right
        if right is True:
            return left
        if left is node.left and right is node.right:
            return node
        return And(left, right)

    if isinstance(node, Or):
        left = _simplify(node.left)
        if left is True:
            return True
        right = _simplify(node.right)
        if right is True:
            return True
        if left is False:
            return right
        if right is False:
            return left
        if left is node.left and right is node.right:
            return node
        return Or(left, right)

    if isinstance(node, Implies):
        left = _simplify(node.left)
        if left is False:
            return True
        right = _simplify(node.right)
        if right is True:
            return True
        if left is True:
            return right
        if right is False:
            return _simplify(Not(left))
        if left is node.left and right is node.right:
            return node
        return Implies(left, right)

    # Temporal operators: keep as-is (progression handles them).
    # Atoms / arithmetic leaves: keep as-is.
    return node


# ---------------------------------------------------------------------------
# Internal: progression — rewrite residual for one new valuation
# ---------------------------------------------------------------------------


def _progress(node: Residual, state: dict[str, object]) -> Residual:
    """Rewrite ``node`` against one timestep's atom valuation.

    Returns the residual formula that must hold on the remaining suffix
    of the trace. If the result simplifies to ``True`` or ``False``, the
    formula is definitively decided.

    Rewriting rules (finite-trace semantics, formula must be in NNF):

    * Atom / arithmetic        → evaluate against ``state``
    * Not(φ)                   → Not(progress(φ, state))
    * And(φ, ψ)                → And(progress(φ), progress(ψ))
    * Or(φ, ψ)                 → Or(progress(φ), progress(ψ))
    * Implies(φ, ψ)            → Implies(progress(φ), progress(ψ))
    * G(φ)                     → And(progress(φ), G(φ))
    * F(φ)                     → Or(progress(φ), F(φ))
    * X(φ)                     → φ          (peel off next-operator)
    * φ U ψ                    → Or(progress(ψ), And(progress(φ), φ U ψ))

    After rewriting, the caller should simplify (via :func:`_simplify`).
    """
    if isinstance(node, bool):
        return node

    if isinstance(node, Atom):
        return bool(state.get(node.key(), False))

    if isinstance(node, (Le, Lt, Ge, Gt, Eq, Subset)):
        return _eval_pointwise(node, state)

    if isinstance(node, Not):
        # If the child is purely temporal-free, evaluate directly.
        # Otherwise recurse (NNF-ish behaviour: Not over a temporal is
        # tolerated here and handled compositionally).
        if not _is_temporal(node.child):
            return not _eval_pointwise(node.child, state)
        return Not(_progress(node.child, state))

    if isinstance(node, And):
        return And(_progress(node.left, state), _progress(node.right, state))

    if isinstance(node, Or):
        return Or(_progress(node.left, state), _progress(node.right, state))

    if isinstance(node, Implies):
        return Implies(_progress(node.left, state), _progress(node.right, state))

    if isinstance(node, G):
        return And(_progress(node.child, state), node)

    if isinstance(node, F):
        return Or(_progress(node.child, state), node)

    if isinstance(node, X):
        # One event consumed: next-operator peels off, exposing its child
        # as the obligation for remaining suffix.
        return node.child

    if isinstance(node, U):
        return Or(
            _progress(node.right, state),
            And(_progress(node.left, state), node),
        )

    raise TypeError(f"_progress: unsupported formula node {type(node).__name__}")


# ---------------------------------------------------------------------------
# Internal: session-end collapse (weak finite-trace semantics)
# ---------------------------------------------------------------------------


def _finalize(node: Residual) -> Residual:
    """Collapse a residual formula under weak finite-trace semantics.

    Pending temporal obligations become:

    * ``G(_)`` → ``True``   (vacuously true on empty remaining suffix)
    * ``F(_)`` → ``False``  (never discharged)
    * ``X(_)`` → ``True``   (weak-next: vacuously true past trace end)
    * ``φ U ψ`` → ``False`` (ψ never became true before end)

    Atoms and arithmetic nodes that happen to still be in the residual
    (rare in practice; shouldn't occur for well-formed LTL at session
    end) collapse to ``False`` under weak semantics as a safe default.
    """
    if isinstance(node, bool):
        return node

    if isinstance(node, G):
        return True
    if isinstance(node, F):
        return False
    if isinstance(node, X):
        return True
    if isinstance(node, U):
        return False

    if isinstance(node, Not):
        inner = _finalize(node.child)
        if isinstance(inner, bool):
            return not inner
        return Not(inner)

    if isinstance(node, And):
        return _simplify(And(_finalize(node.left), _finalize(node.right)))

    if isinstance(node, Or):
        return _simplify(Or(_finalize(node.left), _finalize(node.right)))

    if isinstance(node, Implies):
        return _simplify(Implies(_finalize(node.left), _finalize(node.right)))

    # Atom / arithmetic residuals shouldn't survive to finalize in
    # practice — progression consumes them at each step. If they do
    # somehow, we can't evaluate them without a valuation, so treat
    # them conservatively: collapse to False (weak semantics).
    return False


def _to_verdict(residual: Residual) -> Verdict3:
    """Classify a simplified residual into the 3-valued verdict."""
    if residual is True:
        return "⊤"
    if residual is False:
        return "⊥"
    return "?"


# ---------------------------------------------------------------------------
# Public: DFAEvaluator
# ---------------------------------------------------------------------------


class DFAEvaluator:
    """3-valued LTL runtime monitor via lazy formula progression.

    One instance per formula. Step the monitor forward with one
    valuation dict per trace event; the current verdict is always
    retrievable via :meth:`peek`.

    This is an alternative backend for
    :class:`sponsio.runtime.verifier.TraceVerifier`. The stateless
    recursive evaluator in :mod:`sponsio.formulas.evaluator` stays as
    the default and as ground truth for differential testing.

    Args:
        formula: The raw LTL ``Formula`` AST (no ``DetFormula`` wrapper
            — callers should ``_raw_formula`` it first).

    Example::

        from sponsio.formulas.dfa_evaluator import DFAEvaluator
        from sponsio.patterns.library import rate_limit

        dfa = DFAEvaluator(rate_limit("X", 3).formula)

        for valuation in grounded_trace:
            verdict = dfa.step(valuation)
            if verdict == "⊥":
                print("violation!")
                break

        end_verdict = dfa.finalize()  # collapses "?" to "⊥" / "⊤"
    """

    def __init__(self, formula: Formula) -> None:
        self._initial: Residual = _simplify(formula)
        self._residual: Residual = self._initial
        self._steps_consumed: int = 0

    # -----------------------------------------------------------------
    # Core stepping API
    # -----------------------------------------------------------------

    def step(self, valuation: dict[str, object]) -> Verdict3:
        """Consume one event's valuation, rewrite the residual, return the verdict.

        Once the residual has collapsed to ``True`` / ``False``, further
        calls are O(1) no-ops that keep returning the same verdict.
        """
        if self._residual is True or self._residual is False:
            self._steps_consumed += 1
            return _to_verdict(self._residual)

        new_residual = _progress(self._residual, valuation)
        self._residual = _simplify(new_residual)
        self._steps_consumed += 1
        return _to_verdict(self._residual)

    def peek(self) -> Verdict3:
        """Return the current 3-valued verdict without consuming an event."""
        return _to_verdict(self._residual)

    def finalize(self) -> Verdict3:
        """Session-end: collapse pending temporal obligations via weak semantics.

        Does **not** mutate the internal state — safe to call multiple
        times. Use this for :meth:`BaseGuard.finish_session` style
        checks where the trace is final.

        Returns ``"⊤"`` if all obligations were discharged (or vacuous),
        ``"⊥"`` otherwise.
        """
        collapsed = _finalize(self._residual)
        simplified = _simplify(collapsed)
        # Finalize should always collapse to a bool under well-formed LTL.
        if simplified is True:
            return "⊤"
        if simplified is False:
            return "⊥"
        # Defensive fallback: unexpected residual — conservative ⊥.
        return "⊥"

    # -----------------------------------------------------------------
    # Snapshot / restore (for dry-run, rollback, session reset)
    # -----------------------------------------------------------------

    def snapshot(self) -> tuple[Residual, int]:
        """Return an immutable snapshot of the current state.

        Formula AST nodes are frozen dataclasses, so the residual tuple
        can be stored, passed around, and later replayed via
        :meth:`restore` — enabling dry-run / what-if evaluation without
        mutating live state.
        """
        return (self._residual, self._steps_consumed)

    def restore(self, snap: tuple[Residual, int]) -> None:
        """Restore a previously captured snapshot."""
        self._residual, self._steps_consumed = snap

    def reset(self) -> None:
        """Rewind to the initial state (before any events)."""
        self._residual = self._initial
        self._steps_consumed = 0

    # -----------------------------------------------------------------
    # Introspection
    # -----------------------------------------------------------------

    @property
    def residual(self) -> Residual:
        """Current residual formula (debug use — do not mutate)."""
        return self._residual

    @property
    def steps_consumed(self) -> int:
        """Number of events the evaluator has stepped through."""
        return self._steps_consumed

    def __repr__(self) -> str:
        v = self.peek()
        return f"DFAEvaluator(verdict={v!r}, steps={self._steps_consumed})"
