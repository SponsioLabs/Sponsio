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
  | Var | Const;

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

// --- Arithmetic ---

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

export type ArithExpr = Var | Const;

export class Le {
  readonly kind = "Le" as const;
  constructor(readonly left: ArithExpr, readonly right: ArithExpr) {}
}

export class Lt {
  readonly kind = "Lt" as const;
  constructor(readonly left: ArithExpr, readonly right: ArithExpr) {}
}

export class Ge {
  readonly kind = "Ge" as const;
  constructor(readonly left: ArithExpr, readonly right: ArithExpr) {}
}

export class Gt {
  readonly kind = "Gt" as const;
  constructor(readonly left: ArithExpr, readonly right: ArithExpr) {}
}

export class Eq {
  readonly kind = "Eq" as const;
  constructor(readonly left: ArithExpr, readonly right: ArithExpr) {}
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
