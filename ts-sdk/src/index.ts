/**
 * @sponsio/sdk — Runtime contract enforcement for LLM agents.
 *
 * Native TypeScript implementation. < 50KB bundle, zero cold start,
 * Edge/Serverless compatible.
 *
 * Usage:
 *   import { Sponsio } from "@sponsio/sdk"
 *
 *   const guard = new Sponsio({
 *     contracts: ["tool `check_policy` must precede `issue_refund`"]
 *   })
 *   const result = guard.guardBefore("issue_refund", { orderId: "#123" })
 *   if (result.blocked) { ... }
 */

import { evaluate, type Valuation } from "./core/evaluator.js";
import {
  groundEvent,
  newGroundingState,
  collectContentAtoms,
  type ToolEvent,
  type GroundingState,
} from "./core/grounding.js";
import { parseNl } from "./core/nl-parser.js";
import type { DetFormula } from "./core/patterns.js";
import type { Formula } from "./core/formula.js";

// Re-exports
export { evaluate } from "./core/evaluator.js";
export type { Valuation } from "./core/evaluator.js";
export * from "./core/formula.js";
export * from "./core/patterns.js";
export { parseNl } from "./core/nl-parser.js";
export { parseRepr, ParseError } from "./core/parser.js";
export {
  groundEvent,
  newGroundingState,
  collectContentAtoms,
} from "./core/grounding.js";

export interface CheckResult {
  blocked: boolean;
  allowed: boolean;
  message: string;
  violations: string[];
}

export interface SponsoOptions {
  agentId?: string;
  contracts?: (string | DetFormula)[];
}

export class Sponsio {
  readonly agentId: string;
  private _contracts: DetFormula[];
  private _trace: Valuation[];
  private _state: GroundingState;
  private _contentAtoms: Record<string, Set<string>>;
  private _violations: string[];

  constructor(options: SponsoOptions = {}) {
    this.agentId = options.agentId ?? "agent";
    this._trace = [];
    this._state = newGroundingState();
    this._violations = [];
    this._contracts = [];

    // Parse contracts
    const raw = options.contracts ?? [];
    for (const c of raw) {
      if (typeof c === "string") {
        const parsed = parseNl(c);
        if (parsed) {
          this._contracts.push(parsed);
        } else {
          console.warn(`[sponsio] Could not parse: "${c}"`);
        }
      } else {
        this._contracts.push(c);
      }
    }

    // Collect content atoms from all formulas
    this._contentAtoms = collectContentAtoms(
      this._contracts.map((c) => c.formula),
    );
  }

  /**
   * Check a tool call against contracts before execution.
   *
   * On block, **all** mutations made by ``groundEvent`` are rolled back via a
   * pre-call snapshot. Previously only ``callCounts[toolName]`` was undone,
   * leaving ``consecutiveCounts``, ``lastTool``, ``callWithCounts``,
   * ``tokenCounts``, and ``delegationDepth`` in a stale state; subsequent
   * guards saw counts as if the blocked call had executed.
   */
  guardBefore(toolName: string, args: Record<string, unknown> = {}): CheckResult {
    const event: ToolEvent = { tool: toolName, args };
    const snapshot = this._snapshotState();
    const valuation = groundEvent(event, this._state, this._contentAtoms);
    this._trace.push(valuation);

    const violations: string[] = [];
    for (const contract of this._contracts) {
      const result = evaluate(contract.formula, this._trace);
      if (!result) {
        const msg = `BLOCKED: ${this.agentId}.${toolName} — det constraint violated: ${contract.desc}`;
        violations.push(msg);
      }
    }

    if (violations.length > 0) {
      this._trace.pop();
      this._state = snapshot;
      this._violations.push(...violations);

      return {
        blocked: true,
        allowed: false,
        message: violations[0],
        violations,
      };
    }

    return { blocked: false, allowed: true, message: "", violations: [] };
  }

  /** Deep-copy the grounding state so it can be restored on a blocked call. */
  private _snapshotState(): GroundingState {
    return {
      callCounts: { ...this._state.callCounts },
      callWithCounts: { ...this._state.callWithCounts },
      lastTool: this._state.lastTool,
      consecutiveCounts: { ...this._state.consecutiveCounts },
      tokenCounts: { ...this._state.tokenCounts },
      delegationDepth: this._state.delegationDepth,
    };
  }

  /**
   * Record tool output after execution.
   */
  guardAfter(_toolName: string, _output: string = ""): void {
    // Det pipeline doesn't need post-check.
    // Sto pipeline would check here (not ported to TS).
  }

  /**
   * Reset guard state for a new session.
   */
  reset(): void {
    this._trace = [];
    this._state = newGroundingState();
    this._violations = [];
    this._contentAtoms = collectContentAtoms(
      this._contracts.map((c) => c.formula),
    );
  }

  /**
   * Get all violations from this session.
   */
  get violations(): string[] {
    return [...this._violations];
  }

  /**
   * Get a summary string.
   */
  summary(): string {
    if (this._violations.length === 0) return "No violations";
    return this._violations.map((v) => `- ${v}`).join("\n");
  }
}
