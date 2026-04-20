/**
 * Finite-trace LTL evaluator with weak semantics.
 *
 * Direct port of sponsio/formulas/evaluator.py.
 *
 * Weak finite-trace semantics at trace end:
 *   G(φ) → true  (vacuously globally)
 *   F(φ) → false (never eventually)
 *   U    → false (ψ never discharged)
 *   X(φ) → true  (weak next)
 */

import {
  Formula, Atom, Not, And, Or, Implies,
  G, F, X, U,
  Le, Lt, Ge, Gt, Eq,
  Var, Const,
} from "./formula.js";

export type Valuation = Record<string, boolean | number>;

function resolveArith(expr: Var | Const, state: Valuation): number {
  if (expr.kind === "Const") return expr.value;
  const key = expr.key();
  const val = state[key];
  if (typeof val === "number") return val;
  return 0; // default for missing variables
}

export function evaluate(
  formula: Formula,
  trace: Valuation[],
  pos: number = 0,
): boolean {
  // Past end of trace — weak semantics
  if (pos >= trace.length) {
    if (formula.kind === "F" || formula.kind === "U") return false;
    return true;
  }

  const state = trace[pos];

  // --- Propositional ---
  switch (formula.kind) {
    case "Atom":
      return Boolean(state[formula.key()] ?? false);

    case "Not":
      return !evaluate(formula.child, trace, pos);

    case "And":
      return evaluate(formula.left, trace, pos) && evaluate(formula.right, trace, pos);

    case "Or":
      return evaluate(formula.left, trace, pos) || evaluate(formula.right, trace, pos);

    case "Implies":
      return !evaluate(formula.left, trace, pos) || evaluate(formula.right, trace, pos);

    // --- Temporal ---
    case "G":
      for (let i = pos; i < trace.length; i++) {
        if (!evaluate(formula.child, trace, i)) return false;
      }
      return true;

    case "F":
      for (let i = pos; i < trace.length; i++) {
        if (evaluate(formula.child, trace, i)) return true;
      }
      return false;

    case "X":
      if (pos + 1 >= trace.length) return true; // weak next
      return evaluate(formula.child, trace, pos + 1);

    case "U":
      for (let j = pos; j < trace.length; j++) {
        if (evaluate(formula.right, trace, j)) return true;
        if (!evaluate(formula.left, trace, j)) return false;
      }
      return false; // ψ never became true

    // --- Arithmetic ---
    case "Le":
      return resolveArith(formula.left, state) <= resolveArith(formula.right, state);

    case "Lt":
      return resolveArith(formula.left, state) < resolveArith(formula.right, state);

    case "Ge":
      return resolveArith(formula.left, state) >= resolveArith(formula.right, state);

    case "Gt":
      return resolveArith(formula.left, state) > resolveArith(formula.right, state);

    case "Eq":
      return resolveArith(formula.left, state) === resolveArith(formula.right, state);

    case "Var":
      return Boolean(state[formula.key()] ?? false);

    case "Const":
      return formula.value !== 0;

    default: {
      const _exhaustive: never = formula;
      throw new Error(`Unknown formula kind: ${(_exhaustive as Formula).kind}`);
    }
  }
}
