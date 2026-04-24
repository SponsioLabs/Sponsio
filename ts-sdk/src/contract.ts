/**
 * Fluent contract builder — TS parity for the Python
 * ``from sponsio import contract`` helper.
 *
 * Usage::
 *
 *   import { Sponsio, contract } from "@sponsio/sdk";
 *
 *   const guard = new Sponsio({
 *     agentId: "refund_bot",
 *     contracts: [
 *       contract("refund policy gate")
 *         .assume("called `issue_refund`")
 *         .enforce("must call `check_policy` before `issue_refund`"),
 *       contract("rate cap")
 *         .enforce("tool `issue_refund` at most 3 times"),
 *     ],
 *   });
 *
 * Repeated ``.assume`` / ``.enforce`` calls AND-combine (matching
 * the Python builder). If ``.assume`` is omitted the contract is
 * unconditional and the enforcement formula is used as-is. If
 * ``.assume`` is present the formula is ``A -> E``.
 *
 * The builder satisfies ``DetFormula`` structurally, so it can be
 * passed directly to ``Sponsio({ contracts: [...] })`` — no ``build()``
 * step required.
 */

import { parseNl } from "./core/nl-parser.js";
import { And, G, Implies, type Formula } from "./core/formula.js";
import type { DetFormula } from "./core/patterns.js";

export class ContractBuilder implements DetFormula {
  readonly desc: string;
  readonly patternName: string = "contract";
  readonly liveness: boolean = false;

  private _assumption: Formula | null;
  private _enforcement: Formula | null;

  constructor(desc?: string) {
    this.desc = desc ?? "contract";
    this._assumption = null;
    this._enforcement = null;
  }

  /** Add an assumption clause (A side). Repeated calls AND-combine. */
  assume(clause: string | DetFormula): ContractBuilder {
    const next = this._clone();
    const f = toFormula(clause, "assume");
    next._assumption = next._assumption ? new And(next._assumption, f) : f;
    return next;
  }

  /** Add an enforcement clause (E side). Repeated calls AND-combine. */
  enforce(clause: string | DetFormula): ContractBuilder {
    const next = this._clone();
    const f = toFormula(clause, "enforce");
    next._enforcement = next._enforcement ? new And(next._enforcement, f) : f;
    return next;
  }

  /**
   * The compiled LTL formula. Accessed at `Sponsio` construction time.
   *
   * - If both A and E are set: ``G(A -> E)``. We wrap in ``G`` because
   *   the evaluator runs from ``pos=0``; a bare ``Implies(A, E)`` would
   *   short-circuit whenever ``A`` was false at step 0 regardless of
   *   later events. ``G`` lifts the implication to every step so the
   *   enforcement fires whenever the assumption holds.
   * - If only E: ``E`` (unconditional; the pattern factories already
   *   emit safety properties wrapped in ``G`` where needed).
   * - If neither: throws — every contract needs an enforcement.
   */
  get formula(): Formula {
    if (!this._enforcement) {
      throw new Error(
        `contract(${JSON.stringify(this.desc)}): .enforce(...) is required`,
      );
    }
    return this._assumption
      ? new G(new Implies(this._assumption, this._enforcement))
      : this._enforcement;
  }

  private _clone(): ContractBuilder {
    const next = new ContractBuilder(this.desc);
    next._assumption = this._assumption;
    next._enforcement = this._enforcement;
    return next;
  }
}

function toFormula(clause: string | DetFormula, which: string): Formula {
  if (typeof clause === "string") {
    const parsed = parseNl(clause);
    if (!parsed) {
      throw new Error(
        `contract().${which}("${clause}"): could not parse NL clause`,
      );
    }
    return parsed.formula;
  }
  return clause.formula;
}

/** Start a fluent contract. Mirrors Python's ``sponsio.contract(desc)``. */
export function contract(desc?: string): ContractBuilder {
  return new ContractBuilder(desc);
}
