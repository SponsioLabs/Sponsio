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
 *         .guarantees("must call `check_policy` before `issue_refund`"),
 *       contract("rate cap")
 *         .guarantees("tool `issue_refund` at most 3 times"),
 *     ],
 *   });
 *
 * Repeated ``.assume`` / ``.guarantees`` calls AND-combine (matching
 * the Python builder). If ``.assume`` is omitted the contract is
 * unconditional and the guarantee formula is used as-is. If
 * ``.assume`` is present the formula is ``A -> G``.
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
  private _guarantee: Formula | null;

  constructor(desc?: string) {
    this.desc = desc ?? "contract";
    this._assumption = null;
    this._guarantee = null;
  }

  /** Add an assumption clause (A side). Repeated calls AND-combine. */
  assume(clause: string | DetFormula): ContractBuilder {
    const next = this._clone();
    const f = toFormula(clause, "assume");
    next._assumption = next._assumption ? new And(next._assumption, f) : f;
    return next;
  }

  /** Add a guarantee clause (G side). Repeated calls AND-combine. */
  guarantees(clause: string | DetFormula): ContractBuilder {
    const next = this._clone();
    const f = toFormula(clause, "guarantees");
    next._guarantee = next._guarantee ? new And(next._guarantee, f) : f;
    return next;
  }

  /**
   * The compiled LTL formula. Accessed at `Sponsio` construction time.
   *
   * - If both A and G are set: ``G(A -> G_)``. We wrap in ``G`` because
   *   the evaluator runs from ``pos=0``; a bare ``Implies(A, G_)`` would
   *   short-circuit whenever ``A`` was false at step 0 regardless of
   *   later events. ``G`` lifts the implication to every step so the
   *   guarantee fires whenever the assumption holds.
   * - If only G: ``G_`` (unconditional; the pattern factories already
   *   emit safety properties wrapped in ``G`` where needed).
   * - If neither: throws — every contract needs a guarantee.
   */
  get formula(): Formula {
    if (!this._guarantee) {
      throw new Error(
        `contract(${JSON.stringify(this.desc)}): .guarantees(...) is required`,
      );
    }
    return this._assumption
      ? new G(new Implies(this._assumption, this._guarantee))
      : this._guarantee;
  }

  private _clone(): ContractBuilder {
    const next = new ContractBuilder(this.desc);
    next._assumption = this._assumption;
    next._guarantee = this._guarantee;
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
