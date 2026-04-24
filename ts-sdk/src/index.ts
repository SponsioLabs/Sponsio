/**
 * @sponsio/sdk — Runtime contract enforcement for LLM agents.
 *
 * Native TypeScript implementation. < 50KB bundle, zero cold start,
 * Edge/Serverless compatible.
 *
 * Usage:
 *   import { Sponsio } from "@sponsio/sdk"
 *
 *   // Inline contracts:
 *   const guard = new Sponsio({
 *     agentId: "refund_bot",
 *     contracts: ["tool `check_policy` must precede `issue_refund`"]
 *   })
 *
 *   // Or loaded from a shared sponsio.yaml:
 *   const guard = new Sponsio({ config: "sponsio.yaml", agentId: "refund_bot" })
 *
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
import { loadSponsoConfig, type SkippedItem } from "./core/config-loader.js";
import { SessionLogger } from "./core/session-log.js";
import type { DetFormula } from "./core/patterns.js";

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
export { loadSponsoConfig } from "./core/config-loader.js";
export type { LoadedConfig, SkippedItem } from "./core/config-loader.js";
export { SessionLogger, rotateSessions } from "./core/session-log.js";
export type {
  SessionRecord,
  SessionLoggerOptions,
} from "./core/session-log.js";

export interface CheckResult {
  blocked: boolean;
  allowed: boolean;
  message: string;
  violations: string[];
}

export type SponsoMode = "enforce" | "observe";

export interface SponsoOptions {
  /** Logical agent id — used for session log paths. */
  agentId?: string;

  /** Inline NL-string / DetFormula contracts. Merged with `config:` if both given. */
  contracts?: (string | DetFormula)[];

  /**
   * Path to a ``sponsio.yaml`` config file. Loaded synchronously at
   * construction time; contracts and runtime settings from the yaml
   * merge with inline options.
   *
   * When set, the TS SDK reads:
   *   - ``runtime.mode`` (observe | enforce)
   *   - ``agents.<agentId>.contracts[]`` (NL strings; structured
   *     patterns and packs are skipped with a warning)
   *
   * Uses the ``yaml`` package (declared as a dependency of
   * ``@sponsio/sdk``).
   */
  config?: string;

  /**
   * Runtime mode. Precedence (matches the Python SDK):
   *
   *     SPONSIO_MODE env  >  ctor arg  >  yaml runtime.mode  >  "observe"
   *
   * ``observe`` (the default) logs every would-have-blocked decision
   * to ``~/.sponsio/sessions/<agent_id>/*.jsonl`` without actually
   * blocking. ``enforce`` returns ``blocked: true`` on violation.
   */
  mode?: SponsoMode;

  /**
   * Write session JSONL log to ``~/.sponsio/sessions/<agent_id>/…``.
   * Defaults to ``true`` in observe mode, ``true`` in enforce mode
   * (matches Python — the log is the audit trail, not an observe
   * mode artefact). Pass ``false`` to disable (tests, edge runtimes
   * without a writable home dir).
   */
  sessionLog?: boolean;

  /** Override session log base dir (tests / alternative layouts). */
  sessionLogBaseDir?: string;
}

export class Sponsio {
  readonly agentId: string;
  readonly mode: SponsoMode;
  private _contracts: DetFormula[];
  private _trace: Valuation[];
  private _state: GroundingState;
  private _contentAtoms: Record<string, Set<string>>;
  private _violations: string[];
  private _logger: SessionLogger | null;

  constructor(options: SponsoOptions = {}) {
    this.agentId = options.agentId ?? "agent";
    this._trace = [];
    this._state = newGroundingState();
    this._violations = [];
    this._contracts = [];

    // ── Gather contracts + yaml-derived settings ────────────────────
    let yamlMode: SponsoMode | undefined;
    let yamlSkipped: SkippedItem[] = [];
    const sources: (string | DetFormula)[] = [];

    if (options.config) {
      const loaded = loadSponsoConfig(options.config, this.agentId);
      for (const c of loaded.contracts) sources.push(c);
      yamlMode = loaded.mode;
      yamlSkipped = loaded.skipped;
    }
    for (const c of options.contracts ?? []) sources.push(c);

    // ── Resolve mode: env > ctor > yaml > default ────────────────────
    this.mode = resolveMode(options.mode, yamlMode);

    // ── Parse NL strings into det formulas ───────────────────────────
    for (const c of sources) {
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

    // ── Warn once about yaml features the TS runtime can't handle ───
    warnOnceAboutSkipped(yamlSkipped);

    this._contentAtoms = collectContentAtoms(
      this._contracts.map((c) => c.formula),
    );

    // ── Session log ─────────────────────────────────────────────────
    const wantLog = options.sessionLog ?? true;
    this._logger = wantLog
      ? new SessionLogger(this.agentId, { baseDir: options.sessionLogBaseDir })
      : null;
  }

  /**
   * Check a tool call against contracts before execution.
   *
   * On block, **all** mutations made by ``groundEvent`` are rolled back via a
   * pre-call snapshot. Previously only ``callCounts[toolName]`` was undone,
   * leaving ``consecutiveCounts``, ``lastTool``, ``callWithCounts``,
   * ``tokenCounts``, and ``delegationDepth`` in a stale state; subsequent
   * guards saw counts as if the blocked call had executed.
   *
   * In **observe mode**, violations are logged to the session JSONL
   * but not reported as blocks — the method always returns ``allowed: true``.
   * In **enforce mode**, the first violation blocks the call.
   */
  guardBefore(toolName: string, args: Record<string, unknown> = {}): CheckResult {
    const event: ToolEvent = { tool: toolName, args };
    const snapshot = this._snapshotState();
    const valuation = groundEvent(event, this._state, this._contentAtoms);
    this._trace.push(valuation);

    const violations: string[] = [];
    const violatedDescs: string[] = [];
    for (const contract of this._contracts) {
      const result = evaluate(contract.formula, this._trace);
      if (!result) {
        const verb = this.mode === "observe" ? "WOULD-BLOCK" : "BLOCKED";
        const msg = `${verb}: ${this.agentId}.${toolName} — det constraint violated: ${contract.desc}`;
        violations.push(msg);
        violatedDescs.push(contract.desc);
      }
    }

    const hasViolations = violations.length > 0;

    if (hasViolations && this.mode === "enforce") {
      this._trace.pop();
      this._state = snapshot;
      this._violations.push(...violations);

      this._logViolations(toolName, violations, violatedDescs, "block");

      return {
        blocked: true,
        allowed: false,
        message: violations[0],
        violations,
      };
    }

    // Either no violations, or observe mode: allow + log.
    if (hasViolations) {
      // observe mode: capture for summary(), but don't roll back.
      this._violations.push(...violations);
      this._logViolations(toolName, violations, violatedDescs, "observe_log");
    } else {
      // Clean allow: one "allow" record per guardBefore so
      // ``sponsio report`` sees a complete turn ledger.
      this._logAllow(toolName);
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

  private _logViolations(
    toolName: string,
    messages: string[],
    descs: string[],
    action: "block" | "observe_log",
  ): void {
    if (!this._logger) return;
    const ts = Date.now() / 1000;
    for (let i = 0; i < messages.length; i++) {
      this._logger.log({
        ts,
        agent_id: this.agentId,
        action,
        pipeline: "det",
        constraint: descs[i] ?? `${this.agentId}.${toolName}`,
        result: { action, message: messages[i] },
      });
    }
  }

  private _logAllow(toolName: string): void {
    if (!this._logger) return;
    this._logger.log({
      ts: Date.now() / 1000,
      agent_id: this.agentId,
      action: "allow",
      pipeline: "det",
      constraint: `${this.agentId}.${toolName}`,
      result: { action: "allow", message: "" },
    });
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

/* -----------------------------------------------------------------
 * helpers
 * -----------------------------------------------------------------*/

/**
 * Resolve the runtime mode with the same precedence as the Python
 * SDK: ``SPONSIO_MODE`` env var wins over an explicit ctor arg so
 * ops can flip enforcement in production without a code change.
 */
function resolveMode(
  ctorMode: SponsoMode | undefined,
  yamlMode: SponsoMode | undefined,
): SponsoMode {
  const envRaw = process.env.SPONSIO_MODE;
  if (envRaw === "enforce" || envRaw === "observe") return envRaw;
  if (envRaw) {
    console.warn(
      `[sponsio] ignoring unknown SPONSIO_MODE="${envRaw}" ` +
        `(expected "enforce" | "observe")`,
    );
  }
  if (ctorMode) return ctorMode;
  if (yamlMode) return yamlMode;
  return "observe";
}

let _skippedWarned = false;

function warnOnceAboutSkipped(skipped: SkippedItem[]): void {
  if (_skippedWarned || skipped.length === 0) return;
  _skippedWarned = true;

  const packs = skipped.filter((s) => s.kind === "pack").map((s) => s.detail);
  const structured = skipped.filter(
    (s) => s.kind === "structured-contract",
  ).length;
  const sto = skipped.filter((s) => s.kind === "sto-contract").length;
  const unknown = skipped.filter((s) => s.kind === "unknown-contract").length;

  const bits: string[] = [];
  if (packs.length > 0) {
    bits.push(`${packs.length} pack include(s) [${packs.join(", ")}]`);
  }
  if (structured > 0) {
    bits.push(`${structured} structured pattern contract(s) (token_budget, loop_detection, …)`);
  }
  if (sto > 0) {
    bits.push(`${sto} sto contract(s) (LLM-judged)`);
  }
  if (unknown > 0) {
    bits.push(`${unknown} unrecognised contract entr${unknown === 1 ? "y" : "ies"}`);
  }

  console.warn(
    "[sponsio] skipped unsupported yaml items: " +
      bits.join("; ") +
      ". These features are Python-only today; TS runtime will ship them in a future release.",
  );
}
