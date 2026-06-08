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
  Var, Const, Term,
} from "./formula.js";
import { predKey } from "./formula.js";

/**
 * State valuation at a single timestep.
 *
 * Atoms emit boolean values. Counter-style Vars emit numbers.
 * arg_value / ctx_value (used by ArgValue / CtxValue Terms) emit any
 * raw value pushed by grounding; can be string, number, object, etc.
 * Missing keys evaluate to ``undefined``.
 */
export type Valuation = Record<string, boolean | number | string | unknown>;

/**
 * Resolve a Term to its underlying value at the current state.
 *
 * Var / Const: counter-style semantics (numeric value; 0 for missing).
 * ArgValue / CtxValue: raw value from grounding (may be any type, or
 * undefined when missing).
 * UnaryFn / ArgLength: derived value (undefined when inner is missing
 * or when the callable throws / .length is unsupported).
 */
function resolveArith(expr: Term, state: Valuation): unknown {
  switch (expr.kind) {
    case "Const":
      return expr.value;
    case "Var": {
      const key = expr.key();
      const val = state[key];
      if (typeof val === "number") return val;
      return 0; // counter-style default for missing
    }
    case "ArgValue":
      return state[predKey("arg_value", expr.tool, expr.field)];
    case "CtxValue":
      return state[predKey("ctx_value", expr.key)];
    case "ArgLength": {
      const v = state[predKey("arg_value", expr.tool, expr.field)];
      if (v == null) return undefined;
      try {
        if (typeof (v as { length?: unknown }).length === "number") {
          return (v as { length: number }).length;
        }
        if (typeof v === "string") return v.length;
        return undefined;
      } catch {
        return undefined;
      }
    }
    case "UnaryFn": {
      const inner = resolveArith(expr.arg, state);
      if (inner === undefined || inner === null) return undefined;
      try {
        return expr.fn(inner);
      } catch {
        return undefined;
      }
    }
  }
}

/**
 * Structural value-equality, matching Python's `==` for the value
 * shapes that flow through grounding (numbers, strings, booleans,
 * arrays, plain objects).
 *
 * The naive `l === r` diverged from the Python evaluator (`left ==
 * right`) for composite values: an `Eq(ArgValue(...), CtxValue(...))`
 * over list- or object-valued args compares by *value* in Python
 * (`[1] == [1]` is True) but `===` compares arrays/objects by
 * *reference* in JS (`[1] === [1]` is False). With the v0.2 Term
 * abstraction making value-equality reachable, that gap could pass a
 * contract in Python and fail it in TS on the same trace. `valuesEqual`
 * closes it with element-/key-wise deep comparison.
 */
function valuesEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a === null || b === null || a === undefined || b === undefined) {
    return false;
  }
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) {
      return false;
    }
    return a.every((x, i) => valuesEqual(x, b[i]));
  }
  if (typeof a === "object" && typeof b === "object") {
    const ka = Object.keys(a as object);
    const kb = Object.keys(b as object);
    if (ka.length !== kb.length) return false;
    return ka.every(
      (k) =>
        Object.prototype.hasOwnProperty.call(b, k) &&
        valuesEqual(
          (a as Record<string, unknown>)[k],
          (b as Record<string, unknown>)[k],
        ),
    );
  }
  return false;
}

/**
 * Compare two resolved values with the canonical "missing" semantics.
 *
 * If either operand is undefined / null, the comparison is False (the
 * comparison cannot decide). Same for type errors (mismatched types).
 * This is the Hoare-vacuity convention. `eq` uses `valuesEqual` for
 * Python `==` parity on composite values.
 */
function safeCompare(op: string, left: unknown, right: unknown): boolean {
  if (left === undefined || left === null) return false;
  if (right === undefined || right === null) return false;
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const l = left as any;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const r = right as any;
    switch (op) {
      case "le": return l <= r;
      case "lt": return l < r;
      case "ge": return l >= r;
      case "gt": return l > r;
      case "eq": return valuesEqual(l, r);
    }
  } catch {
    return false;
  }
  return false;
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

    // --- Arithmetic / Term comparisons ---
    case "Le":
      return safeCompare("le", resolveArith(formula.left, state), resolveArith(formula.right, state));

    case "Lt":
      return safeCompare("lt", resolveArith(formula.left, state), resolveArith(formula.right, state));

    case "Ge":
      return safeCompare("ge", resolveArith(formula.left, state), resolveArith(formula.right, state));

    case "Gt":
      return safeCompare("gt", resolveArith(formula.left, state), resolveArith(formula.right, state));

    case "Eq":
      return safeCompare("eq", resolveArith(formula.left, state), resolveArith(formula.right, state));

    case "Var":
      return Boolean(state[formula.key()] ?? false);

    case "Const":
      return formula.value !== 0;

    // Terms cannot appear at formula-level (only as comparison operands)
    // but the Formula type union includes them. Treat as truthy fallback.
    case "ArgValue":
    case "CtxValue":
    case "ArgLength":
    case "UnaryFn":
      return false;

    default: {
      const _exhaustive: never = formula;
      throw new Error(`Unknown formula kind: ${(_exhaustive as Formula).kind}`);
    }
  }
}
