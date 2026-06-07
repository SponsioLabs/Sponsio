/**
 * Immutable AST nodes for the Sponsio formula language.
 *
 * Direct port of sponsio/formulas/formula.py.
 * Three families: Propositional, Temporal (LTL), Arithmetic.
 */

// --- Base type ---
export type Formula =
  | Atom | Not | And | Or | Implies
  | G | F | X | U
  | Le | Lt | Ge | Gt | Eq
  | Var | Const | ArgValue | CtxValue | UnaryFn | ArgLength;

// --- Propositional ---

export class Atom {
  readonly kind = "Atom" as const;
  constructor(
    readonly predicate: string,
    readonly args: readonly string[] = [],
  ) {}

  key(): string {
    return predKey(this.predicate, ...this.args);
  }
}

export class Not {
  readonly kind = "Not" as const;
  constructor(readonly child: Formula) {}
}

export class And {
  readonly kind = "And" as const;
  constructor(readonly left: Formula, readonly right: Formula) {}
}

export class Or {
  readonly kind = "Or" as const;
  constructor(readonly left: Formula, readonly right: Formula) {}
}

export class Implies {
  readonly kind = "Implies" as const;
  constructor(readonly left: Formula, readonly right: Formula) {}
}

// --- Temporal (LTL) ---

export class G {
  readonly kind = "G" as const;
  constructor(readonly child: Formula) {}
}

export class F {
  readonly kind = "F" as const;
  constructor(readonly child: Formula) {}
}

export class X {
  readonly kind = "X" as const;
  constructor(readonly child: Formula) {}
}

export class U {
  readonly kind = "U" as const;
  constructor(readonly left: Formula, readonly right: Formula) {}
}

// --- Arithmetic / Term abstraction ---
//
// Comparison nodes (Eq, Le, Lt, Ge, Gt) accept any ``Term`` on either
// side. A Term is anything that knows how to evaluate(state) -> value;
// the evaluator dispatches on `kind`. This lets contract authors
// compose runtime-bound values (``new ArgValue("issue_refund",
// "amount")``) against constants (``new Const(50)``) or against other
// runtime values (``new CtxValue("approved_amount")``) in the same
// comparison.
//
// Returning ``undefined`` from evaluation is the canonical "missing"
// signal: comparison evaluation treats either operand being missing as
// false (the comparison cannot decide). Wrap fragile comparisons in
// ``Implies(scopePredicate, comparison)`` to suppress them where the
// relevant arg is not applicable.

export class Var {
  readonly kind = "Var" as const;
  readonly args: readonly string[];
  constructor(readonly name: string, ...args: string[]) {
    this.args = args;
  }
  key(): string {
    if (this.args.length > 0) {
      return predKey(this.name, ...this.args);
    }
    return this.name;
  }
}

export class Const {
  readonly kind = "Const" as const;
  constructor(readonly value: number) {}
}

/**
 * Read ``args[field]`` from the current event when it is a call to ``tool``.
 *
 * Returns ``undefined`` ("missing" signal) when the current event is a
 * call to a different tool, or not a tool call, or args[field] absent.
 * Pair with ``Implies(called(tool), ...)`` to scope the rule cleanly.
 */
export class ArgValue {
  readonly kind = "ArgValue" as const;
  constructor(readonly tool: string, readonly field: string) {}
}

/**
 * Read a fact pushed via ``guard.observeContext({ key: value })``.
 *
 * Returns ``undefined`` when the key has never been pushed for this trace.
 */
export class CtxValue {
  readonly kind = "CtxValue" as const;
  constructor(readonly key: string) {}
}

/**
 * Apply a JavaScript callable to another Term's value.
 *
 * Common cases:
 *   new UnaryFn(s => String(s).toLowerCase(),
 *               new ArgValue("send_email", "subject"))
 *
 * If the inner Term resolves to undefined, UnaryFn also returns undefined.
 * If the callable throws, UnaryFn returns undefined rather than crashing.
 *
 * The ``name`` field is for debug repr only.
 */
export class UnaryFn {
  readonly kind = "UnaryFn" as const;
  constructor(
    readonly fn: (v: unknown) => unknown,
    readonly arg: Term,
    readonly name: string = "fn",
  ) {}
}

/**
 * Convenience: ``args[field].length`` for the current event.
 *
 * Equivalent to ``new UnaryFn(v => (v as { length: number }).length,
 * new ArgValue(tool, field))`` but exposed as its own class for clean
 * repr and faster eval. Returns ``undefined`` when missing or not a
 * .length-bearing value.
 */
export class ArgLength {
  readonly kind = "ArgLength" as const;
  constructor(readonly tool: string, readonly field: string) {}
}

/** A Term is anything that resolves to a value at evaluation time. */
export type Term = Var | Const | ArgValue | CtxValue | UnaryFn | ArgLength;

/** Backward-compatible alias. */
export type ArithExpr = Term;

export class Le {
  readonly kind = "Le" as const;
  constructor(readonly left: Term, readonly right: Term) {}
}

export class Lt {
  readonly kind = "Lt" as const;
  constructor(readonly left: Term, readonly right: Term) {}
}

export class Ge {
  readonly kind = "Ge" as const;
  constructor(readonly left: Term, readonly right: Term) {}
}

export class Gt {
  readonly kind = "Gt" as const;
  constructor(readonly left: Term, readonly right: Term) {}
}

export class Eq {
  readonly kind = "Eq" as const;
  constructor(readonly left: Term, readonly right: Term) {}
}

// --- Predicate key ---

function escape(s: string): string {
  return s
    .replace(/\\/g, "\\\\")
    .replace(/\(/g, "\\(")
    .replace(/\)/g, "\\)")
    .replace(/,/g, "\\,")
    .replace(/ /g, "\\ ");
}

export function predKey(predicate: string, ...args: string[]): string {
  if (args.length === 0) return `${predicate}()`;
  return `${predicate}(${args.map(escape).join(", ")})`;
}
