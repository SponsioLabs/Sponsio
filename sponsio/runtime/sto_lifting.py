"""Probabilistic lifting of formulas containing sto atoms.

Walks a formula tree and produces a confidence in [0, 1] that the
formula holds at a given timestep. Det atoms contribute 0.0 or 1.0
(from grounded valuations), sto atoms contribute continuous scores
from their registered evaluators (see ``sponsio.patterns.sto_registry``).

Boolean and temporal operators are lifted through
**independent-product semantics** (see ``docs/cost-based-thresholds.md`` §8):

* ``¬φ`` → ``1 − p``
* ``φ ∧ ψ`` → ``p_φ · p_ψ``
* ``φ ∨ ψ`` → ``1 − (1 − p_φ)(1 − p_ψ)``
* ``φ → ψ`` → ``¬φ ∨ ψ``
* ``G φ`` → product over all future timesteps
* ``F φ`` → ``1 − ∏(1 − p_t)`` over future timesteps
* ``X φ`` → ``p_{t+1}`` (0.0 past end of trace)
* ``φ U ψ`` → disjunction over "switch points" k of ``p_ψ(k) · ∏ p_φ(j∈[t,k))``

Pure-det trees reduce to strict 0.0 / 1.0 results, so the same lifting
handles mixed contracts and acts as a correctness check for the
fast-path dispatch in ``Contract.evaluate()``.

Fréchet bounds (min/max, union bound) are NOT exposed here — discussed
in the cost-based-thresholds doc as advanced topic only.
"""

from __future__ import annotations

import math
from typing import Any

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
from sponsio.models.trace import Trace
from sponsio.patterns.sto_registry import resolve_sto_evaluator


def eval_sto_confidence(
    formula: Formula,
    valuations: list[dict[str, Any]],
    trace: Trace,
    t: int = 0,
    cache: dict[tuple[int, int], float] | None = None,
    atom_cache: dict[tuple[int, int], float] | None = None,
) -> float:
    """Return confidence in [0, 1] that ``formula`` holds starting at ``t``.

    Args:
        formula: Formula AST. Leaves may be det Atoms, sto Atoms, or
            arithmetic comparisons; all internal nodes are boolean /
            temporal operators.
        valuations: Per-timestep grounded state (from
            :func:`sponsio.tracer.grounding.ground`). Used to resolve det
            atoms and arithmetic variables.
        trace: Raw trace. Passed to sto atom evaluators that need event
            content (e.g. LLM judges reading response text).
        t: Current timestep (default 0 — evaluate over whole trace).
        cache: Memoization of ``(id(formula), t) → conf`` within a single
            call. Handles shared subterms under ``G`` / ``F`` / ``U``
            unrolling and reuse of sto atom scores between ``A`` and ``G``
            in the same evaluation.
        atom_cache: **Persistent** memoization of sto atom evaluations
            keyed by ``(id(atom), position)``. Unlike ``cache`` (per-call,
            per-formula-instance), ``atom_cache`` persists across calls
            so a contract that re-evaluates its enforcement on every new
            event only pays for one judge call per atom per position —
            total cost linear in trace length instead of quadratic. Pass
            a dict that outlives the call (e.g. stored on the monitor
            per contract). Det atoms are unaffected — they're cheap to
            re-read from grounded valuations.

    Returns:
        Float in [0, 1]. For pure-det trees the result is strictly 0.0
        or 1.0.
    """
    if cache is None:
        cache = {}
    key = (id(formula), t)
    if key in cache:
        return cache[key]

    result = _eval_uncached(formula, valuations, trace, t, cache, atom_cache)
    cache[key] = result
    return result


def _eval_uncached(
    formula: Formula,
    valuations: list[dict[str, Any]],
    trace: Trace,
    t: int,
    cache: dict[tuple[int, int], float],
    atom_cache: dict[tuple[int, int], float] | None = None,
) -> float:
    n = len(valuations)

    # --- Past end of trace: weak finite-trace semantics ---
    if t >= n:
        # G, X, and everything other than F/U are vacuously satisfied.
        if isinstance(formula, (F, U)):
            return 0.0
        return 1.0

    # --- Atoms ---
    if isinstance(formula, Atom):
        if formula.atom_type == "sto":
            # Persistent per-position memo: avoids re-asking the judge
            # about an event that was already scored on a previous
            # call. Keyed on (atom id, position) since event content at
            # a given position is immutable once appended to the trace.
            if atom_cache is not None:
                ac_key = (id(formula), t)
                if ac_key in atom_cache:
                    return atom_cache[ac_key]
            fn = resolve_sto_evaluator(formula.predicate)
            score = float(fn(formula, trace, t).score)
            # Clamp defensively — evaluators should return [0,1] but be robust.
            score = max(0.0, min(1.0, score))
            if atom_cache is not None:
                atom_cache[(id(formula), t)] = score
            return score
        # det atom: look up grounded valuation (cheap — skip atom_cache)
        return 1.0 if valuations[t].get(formula.key(), False) else 0.0

    # Arithmetic / set comparisons are always det-valued
    if isinstance(formula, (Le, Lt, Ge, Gt, Eq, Subset)):
        return 1.0 if _eval_det_comparison(formula, valuations, t) else 0.0

    # --- Boolean operators ---
    if isinstance(formula, Not):
        return 1.0 - eval_sto_confidence(
            formula.child, valuations, trace, t, cache, atom_cache
        )

    if isinstance(formula, And):
        p1 = eval_sto_confidence(formula.left, valuations, trace, t, cache, atom_cache)
        p2 = eval_sto_confidence(formula.right, valuations, trace, t, cache, atom_cache)
        return p1 * p2

    if isinstance(formula, Or):
        p1 = eval_sto_confidence(formula.left, valuations, trace, t, cache, atom_cache)
        p2 = eval_sto_confidence(formula.right, valuations, trace, t, cache, atom_cache)
        return 1.0 - (1.0 - p1) * (1.0 - p2)

    if isinstance(formula, Implies):
        # P → Q ≡ ¬P ∨ Q
        p1 = eval_sto_confidence(formula.left, valuations, trace, t, cache, atom_cache)
        p2 = eval_sto_confidence(formula.right, valuations, trace, t, cache, atom_cache)
        return 1.0 - (p1) * (1.0 - p2)

    # --- Temporal operators ---
    if isinstance(formula, G):
        vals = [
            eval_sto_confidence(formula.child, valuations, trace, s, cache, atom_cache)
            for s in range(t, n)
        ]
        return math.prod(vals) if vals else 1.0

    if isinstance(formula, F):
        vals = [
            eval_sto_confidence(formula.child, valuations, trace, s, cache, atom_cache)
            for s in range(t, n)
        ]
        if not vals:
            return 0.0
        return 1.0 - math.prod(1.0 - v for v in vals)

    if isinstance(formula, X):
        if t + 1 >= n:
            return 1.0  # weak next, matches det evaluator
        return eval_sto_confidence(
            formula.child, valuations, trace, t + 1, cache, atom_cache
        )

    if isinstance(formula, U):
        # φ U ψ  ≡  ∨_{k∈[t,n)} [ ψ(k) ∧ ∧_{j∈[t,k)} φ(j) ]
        switch_confs: list[float] = []
        for k in range(t, n):
            psi_k = eval_sto_confidence(
                formula.right, valuations, trace, k, cache, atom_cache
            )
            phi_prefix = [
                eval_sto_confidence(
                    formula.left, valuations, trace, j, cache, atom_cache
                )
                for j in range(t, k)
            ]
            prefix_conf = math.prod(phi_prefix) if phi_prefix else 1.0
            switch_confs.append(psi_k * prefix_conf)
        if not switch_confs:
            return 0.0
        return 1.0 - math.prod(1.0 - sc for sc in switch_confs)

    raise TypeError(
        f"eval_sto_confidence: unknown formula node {type(formula).__name__}"
    )


def _eval_det_comparison(
    formula: Formula, valuations: list[dict[str, Any]], t: int
) -> bool:
    """Evaluate arithmetic/set nodes against the grounded state at timestep t."""

    def _resolve(expr):
        if isinstance(expr, Const):
            return expr.value
        if isinstance(expr, Var):
            val = valuations[t].get(expr.key(), 0)
            return val if isinstance(val, (int, float)) else 0
        return expr

    if isinstance(formula, Le):
        return _resolve(formula.left) <= _resolve(formula.right)
    if isinstance(formula, Lt):
        return _resolve(formula.left) < _resolve(formula.right)
    if isinstance(formula, Ge):
        return _resolve(formula.left) >= _resolve(formula.right)
    if isinstance(formula, Gt):
        return _resolve(formula.left) > _resolve(formula.right)
    if isinstance(formula, Eq):
        return _resolve(formula.left) == _resolve(formula.right)
    if isinstance(formula, Subset):
        return bool(valuations[t].get(formula.key(), False))
    raise TypeError(f"not a comparison node: {type(formula).__name__}")


def _all_det(formula: Formula) -> bool:
    """Recursively check whether every ``Atom`` leaf has ``atom_type == "det"``.

    Used by ``Contract.evaluate()`` to dispatch pure-det contracts to
    the fast LTL path, skipping the lifting overhead.
    """
    if isinstance(formula, Atom):
        return formula.atom_type == "det"
    # Arithmetic / set leaves are always det
    if isinstance(formula, (Le, Lt, Ge, Gt, Eq, Subset, Var, Const)):
        return True
    # Walk children of internal nodes
    if isinstance(formula, Not):
        return _all_det(formula.child)
    if isinstance(formula, (And, Or, Implies, U)):
        return _all_det(formula.left) and _all_det(formula.right)
    if isinstance(formula, (G, F, X)):
        return _all_det(formula.child)
    # Unknown node — treat as non-det to force lifting path (safer)
    return False
