/**
 * Formula repr() ↔ AST parser.
 *
 * Port of sponsio/formulas/parser.py parse_repr().
 * Parses the human-readable repr format back into Formula AST:
 *
 *   "G((called('auth') -> F(called('query'))))"  →  G(Implies(Atom('called', ['auth']), F(Atom('called', ['query']))))
 *
 * Uses recursive descent with tokenizer — the repr uses custom infix
 * operators (!, ->, &, |, U, <=, >=) that aren't valid JS.
 */

import {
  Formula,
  Atom, Not, And, Or, Implies,
  G, F, X, U,
  Le, Lt, Ge, Gt, Eq,
  Var, Const,
  ArgValue, CtxValue, ArgLength,
} from "./formula.js";

export class ParseError extends Error {}

/**
 * Parse a formula repr string back into an AST.
 */
export function parseRepr(text: string): Formula {
  const trimmed = text.trim();
  if (!trimmed) throw new ParseError("Empty formula");

  const tokens = tokenize(trimmed);
  const state = { pos: 0, tokens };

  const result = parseExpr(state);
  if (state.pos !== tokens.length) {
    throw new ParseError(`Unexpected tokens at position ${state.pos}: ${tokens.slice(state.pos).join(" ")}`);
  }
  return result;
}

// --- Tokenizer ---

function tokenize(text: string): string[] {
  const tokens: string[] = [];
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    if (c === " " || c === "\t" || c === "\n") {
      i++;
    } else if (text.slice(i, i + 2) === "->") {
      tokens.push("->"); i += 2;
    } else if (text.slice(i, i + 2) === "<=") {
      tokens.push("<="); i += 2;
    } else if (text.slice(i, i + 2) === ">=") {
      tokens.push(">="); i += 2;
    } else if (text.slice(i, i + 2) === "==") {
      tokens.push("=="); i += 2;
    } else if ("()<>&|!,".includes(c)) {
      tokens.push(c); i++;
    } else if (c === "'" || c === '"') {
      // Quoted string — include quotes
      const q = c;
      let j = i + 1;
      while (j < text.length && text[j] !== q) {
        if (text[j] === "\\") j++;
        j++;
      }
      tokens.push(text.slice(i, j + 1));
      i = j + 1;
    } else if (c >= "0" && c <= "9" || (c === "-" && i + 1 < text.length && text[i + 1] >= "0" && text[i + 1] <= "9")) {
      let j = c === "-" ? i + 1 : i;
      while (j < text.length && ((text[j] >= "0" && text[j] <= "9") || text[j] === ".")) j++;
      tokens.push(text.slice(i, j));
      i = j;
    } else if ((c >= "a" && c <= "z") || (c >= "A" && c <= "Z") || c === "_") {
      let j = i;
      while (j < text.length && (
        (text[j] >= "a" && text[j] <= "z") ||
        (text[j] >= "A" && text[j] <= "Z") ||
        (text[j] >= "0" && text[j] <= "9") ||
        text[j] === "_"
      )) j++;
      tokens.push(text.slice(i, j));
      i = j;
    } else {
      i++;
    }
  }
  return tokens;
}

// --- Recursive descent parser ---

interface State {
  pos: number;
  tokens: string[];
}

function peek(s: State): string | undefined {
  return s.tokens[s.pos];
}

function consume(s: State, expected?: string): string {
  if (s.pos >= s.tokens.length) {
    throw new ParseError(`Unexpected end, expected ${expected ?? "token"}`);
  }
  const t = s.tokens[s.pos];
  if (expected != null && t !== expected) {
    throw new ParseError(`Expected ${expected} at position ${s.pos}, got ${t}`);
  }
  s.pos++;
  return t;
}

function parseExpr(s: State): Formula {
  let left: Formula = parseUnary(s);
  while (true) {
    const op = peek(s);
    if (op === "->" || op === "&" || op === "|" || op === "U" ||
        op === "<=" || op === ">=" || op === "<" || op === ">" || op === "==") {
      consume(s);
      const right = parseUnary(s);
      switch (op) {
        case "->": left = new Implies(left, right); break;
        case "&": left = new And(left, right); break;
        case "|": left = new Or(left, right); break;
        case "U": left = new U(left, right); break;
        case "<=": left = new Le(left as Var | Const, right as Var | Const); break;
        case ">=": left = new Ge(left as Var | Const, right as Var | Const); break;
        case "<": left = new Lt(left as Var | Const, right as Var | Const); break;
        case ">": left = new Gt(left as Var | Const, right as Var | Const); break;
        case "==": left = new Eq(left as Var | Const, right as Var | Const); break;
      }
    } else {
      break;
    }
  }
  return left;
}

function parseUnary(s: State): Formula {
  const t = peek(s);
  if (t === "!") {
    consume(s, "!");
    // Accept both ``!(expr)`` and the bare ``!foo`` / ``!foo(a)`` /
    // ``!Atom(...)`` forms. LTL-style yaml in the README writes
    // ``!called(git_commit)`` without explicit parens — without
    // this loosening the loader has to pre-wrap every negation.
    const child = parseUnary(s);
    return new Not(child);
  } else if (t === "(") {
    consume(s, "(");
    const expr = parseExpr(s);
    consume(s, ")");
    return expr;
  } else if (t === "G" || t === "F" || t === "X") {
    consume(s);
    consume(s, "(");
    const child = parseExpr(s);
    consume(s, ")");
    return t === "G" ? new G(child) : t === "F" ? new F(child) : new X(child);
  } else if (t === "Var") {
    consume(s, "Var");
    consume(s, "(");
    const args: string[] = [];
    while (peek(s) !== ")") {
      if (peek(s) === ",") consume(s, ",");
      args.push(stripQuotes(consume(s)));
    }
    consume(s, ")");
    return new Var(args[0], ...args.slice(1));
  } else if (t === "ArgValue" || t === "ArgLength" || t === "CtxValue") {
    // Term subclasses for runtime-bound comparisons.
    // ArgValue / ArgLength: (tool, field). CtxValue: (key).
    const tok = consume(s);
    consume(s, "(");
    const args: string[] = [];
    while (peek(s) !== ")") {
      if (peek(s) === ",") consume(s, ",");
      args.push(stripQuotes(consume(s)));
    }
    consume(s, ")");
    if (tok === "ArgValue") {
      if (args.length !== 2) {
        throw new ParseError(`ArgValue requires (tool, field); got ${args.length} args`);
      }
      return new ArgValue(args[0], args[1]) as unknown as Formula;
    }
    if (tok === "ArgLength") {
      if (args.length !== 2) {
        throw new ParseError(`ArgLength requires (tool, field); got ${args.length} args`);
      }
      return new ArgLength(args[0], args[1]) as unknown as Formula;
    }
    if (args.length !== 1) {
      throw new ParseError(`CtxValue requires (key); got ${args.length} args`);
    }
    return new CtxValue(args[0]) as unknown as Formula;
  } else if (t === "Atom") {
    consume(s, "Atom");
    consume(s, "(");
    const args: string[] = [];
    while (peek(s) !== ")") {
      if (peek(s) === ",") consume(s, ",");
      args.push(stripQuotes(consume(s)));
    }
    consume(s, ")");
    return new Atom(args[0], args.slice(1));
  } else if (t === "Not") {
    consume(s, "Not");
    consume(s, "(");
    const child = parseExpr(s);
    consume(s, ")");
    return new Not(child);
  } else if (t && (/^[-0-9.]/.test(t))) {
    consume(s);
    return new Const(parseFloat(t));
  } else if (t) {
    // Atom shorthand: predicate_name('arg1', 'arg2', ...)
    const name = consume(s);
    if (peek(s) === "(") {
      consume(s, "(");
      const args: string[] = [];
      while (peek(s) !== ")") {
        if (peek(s) === ",") consume(s, ",");
        args.push(stripQuotes(consume(s)));
      }
      consume(s, ")");
      return new Atom(name, args);
    }
    return new Atom(name);
  }
  throw new ParseError(`Unexpected token: ${t}`);
}

function stripQuotes(s: string): string {
  if (s.length >= 2 && (s[0] === "'" || s[0] === '"')) {
    return s.slice(1, -1);
  }
  return s;
}
