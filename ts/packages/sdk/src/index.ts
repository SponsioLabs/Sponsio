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
import {
  loadSponsoConfig,
  type SkippedItem,
  type StoContractSpec,
  type JudgeConfigSpec,
} from "./core/config-loader.js";
import { SessionLogger } from "./core/session-log.js";
import {
  AgentTurnSpan,
  ContractCheckSpan,
  EnforcementSpan,
  GuaranteeSpan,
  SpanCollector,
} from "./core/spans.js";
import { renderSession } from "./render/session-view.js";
import type { DetFormula } from "./core/patterns.js";
import {
  createJudge,
  LlmJudgeEvaluator,
  ToneEvaluator,
  RelevanceEvaluator,
  SemanticPiiFreeEvaluator,
  HallucinationFreeEvaluator,
  ScopeRespectEvaluator,
  MetricIntegrityEvaluator,
  InjectionFreeEvaluator,
  type JudgeClient,
  type JudgeConfig,
  type StoContract,
  type StoContextSnapshot,
  type StoEvaluator,
} from "./core/sto.js";

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
export { contract, ContractBuilder } from "./contract.js";
export {
  createJudge,
  LlmJudgeEvaluator,
  ToneEvaluator,
  RelevanceEvaluator,
  SemanticPiiFreeEvaluator,
  HallucinationFreeEvaluator,
  ScopeRespectEvaluator,
  MetricIntegrityEvaluator,
  InjectionFreeEvaluator,
  parseScore,
} from "./core/sto.js";
export type {
  StoEvaluator,
  StoContract,
  StoInput,
  StoResult,
  StoContextSnapshot,
  JudgeClient,
  JudgeConfig,
} from "./core/sto.js";

/**
 * One det violation, with enough structure to feed into downstream
 * agent feedback — mirrors Python's ``EnforcementResult`` fields that
 * examples reach for (``check.det_violations[0].message``).
 *
 * Phrasing contract (mirrors Python ``OutcomeBuilder``):
 *
 * - ``message`` is the **human-facing** log line —
 *   ``"BLOCKED: agent.tool — det constraint violated: …"``. It keeps
 *   the legacy prefix so log-parsing back-compat holds. Don't inject
 *   it into the LLM's next prompt; it's noise to the model.
 * - ``agentMsg`` is the **agent-facing** line, phrased to nudge the
 *   model into the right reaction. For block this should read
 *   "abandon this action" rather than parroting the log line.
 *   Empty until the strategy populates it; integrations fall back to
 *   ``message`` when empty.
 * - ``ruleId`` is a stable identifier (``DetFormula.patternName`` or
 *   sto atom name) integrations can group on without parsing
 *   free text.
 * - ``retryHint`` is populated only on retry-style outcomes — the
 *   "to fix this, do X" guidance, kept distinct from ``agentMsg``
 *   so adapters can format the two parts in framework-native ways.
 * - ``alternatives`` is an optional list of suggested replacement
 *   actions for blocked / redirected outcomes.
 */
export interface DetViolation {
  /** Human-readable contract description (``DetFormula.desc``). */
  desc: string;
  /** Formatted ``"[WOULD-]BLOCKED: agent.tool — det constraint …"``. */
  message: string;
  /** Stable rule identifier (pattern name / contract id). */
  ruleId?: string;
  /** Agent-facing line, tuned per action voice. Falls back to ``message``. */
  agentMsg?: string;
  /** "To fix this, do X" guidance. Only set on retry-style outcomes. */
  retryHint?: string;
  /** Suggested replacement actions for the agent. */
  alternatives?: string[];
}

/**
 * Best-effort accessor for the agent-facing line. Prefers the
 * structured ``agentMsg`` field when populated; falls back to
 * ``message`` for adapters that haven't migrated yet. Mirrors the
 * Python ``select_agent_message`` helper.
 */
export function selectAgentMessage(
  violations: DetViolation[],
  fallback: string = "Contract violation",
): string {
  for (const v of violations) {
    if (v.agentMsg && v.agentMsg.length > 0) {
      return v.agentMsg;
    }
  }
  if (violations.length > 0) {
    return violations[0].message;
  }
  return fallback;
}

export interface CheckResult {
  blocked: boolean;
  allowed: boolean;
  message: string;
  /**
   * Flat list of violation messages — kept for back-compat with
   * existing TS call sites that only need a string feed.
   */
  violations: string[];
  /**
   * Structured det violations — parallel to Python's
   * ``CheckResult.det_violations``. Populated in both enforce-block
   * and observe-log paths; empty on a clean allow.
   */
  detViolations: DetViolation[];
  /**
   * Sto-pipeline violations — populated by ``guardAfter`` when
   * stochastic contracts (e.g. ``tone``, ``llm_judge``) are declared
   * in the yaml and a ``judge:`` block is configured. Empty on a
   * clean pass or when no sto contracts exist.
   */
  stoViolations: DetViolation[];
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

  /**
   * Judge config for the sto pipeline. Either a plain config object
   * (provider / model / apiKey / baseUrl / fallbackMode) or a
   * pre-built ``JudgeClient``. Falls through to the yaml ``judge:``
   * block if the ctor arg is omitted. When no sto contracts exist,
   * the judge is never contacted.
   */
  judge?: JudgeConfig | JudgeClient;
}

export class Sponsio {
  readonly agentId: string;
  readonly mode: SponsoMode;
  private _contracts: DetFormula[];
  private _stoContracts: StoContract[];
  private _judge: JudgeClient | null;
  private _judgeFallback: "allow" | "deny" | "skip";
  private _stoContext: StoContextSnapshot;
  private _trace: Valuation[];
  private _state: GroundingState;
  private _contentAtoms: Record<string, Set<string>>;
  private _violations: string[];
  private _logger: SessionLogger | null;
  private _turnSpans: AgentTurnSpan[] = [];

  constructor(options: SponsoOptions = {}) {
    this.agentId = options.agentId ?? "agent";
    this._trace = [];
    this._state = newGroundingState();
    this._violations = [];
    this._contracts = [];
    this._stoContracts = [];
    this._judge = null;
    this._judgeFallback = "allow";
    this._stoContext = {};

    // ── Gather contracts + yaml-derived settings ────────────────────
    let yamlMode: SponsoMode | undefined;
    let yamlSkipped: SkippedItem[] = [];
    let yamlJudge: JudgeConfigSpec | undefined;
    let yamlStoSpecs: StoContractSpec[] = [];
    const sources: (string | DetFormula)[] = [];

    if (options.config) {
      const loaded = loadSponsoConfig(options.config, this.agentId);
      for (const c of loaded.contracts) sources.push(c);
      yamlMode = loaded.mode;
      yamlSkipped = loaded.skipped;
      yamlJudge = loaded.judge;
      yamlStoSpecs = loaded.stoSpecs;
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

    // ── Build judge client + sto evaluators ──────────────────────────
    // Precedence: ctor arg > yaml. Ctor may be either a JudgeClient
    // (has `.complete`) or a JudgeConfig (has at least `model` /
    // `apiKey` / `baseUrl`).
    this._judgeFallback = yamlJudge?.fallbackMode ?? "allow";
    this._judge = resolveJudge(options.judge, yamlJudge);
    for (const spec of yamlStoSpecs) {
      if (!this._judge) {
        // Surface once via the skipped-items warn path.
        yamlSkipped.push({
          kind: "sto-contract",
          detail: `${spec.desc} (no judge: configured)`,
        });
        continue;
      }
      this._stoContracts.push(buildStoContract(spec, this._judge));
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
  /** Read the human-readable descs of all compiled contracts (det only). */
  contractDescs(): string[] {
    return this._contracts.map((c) => c.desc);
  }

  guardBefore(toolName: string, args: Record<string, unknown> = {}): CheckResult {
    const event: ToolEvent = { tool: toolName, args };
    const snapshot = this._snapshotState();
    const valuation = groundEvent(event, this._state, this._contentAtoms);
    this._trace.push(valuation);

    // Build a span tree for this turn (mirrors Python's RuntimeMonitor).
    const collector = new SpanCollector(this.agentId, toolName);

    const violations: string[] = [];
    const violatedDescs: string[] = [];
    const detViolations: DetViolation[] = [];
    for (const contract of this._contracts) {
      const checkSpan = new ContractCheckSpan(contract.desc, "hard");
      collector.push(checkSpan);
      const result = evaluate(contract.formula, this._trace);
      // The TS evaluator is currently a single-pass deterministic
      // check; it doesn't separately track assumption vs guarantee
      // sub-formulas. Synthesise a guarantee span so the renderer can
      // surface failed contracts without refactoring the evaluator.
      const guaranteeSpan = new GuaranteeSpan(contract.desc, result);
      collector.add(guaranteeSpan, result ? "ok" : "violated");
      if (!result) {
        const verb = this.mode === "observe" ? "WOULD-BLOCK" : "BLOCKED";
        const msg = `${verb}: ${this.agentId}.${toolName} — det constraint violated: ${contract.desc}`;
        violations.push(msg);
        violatedDescs.push(contract.desc);
        detViolations.push({
          desc: contract.desc,
          message: msg,
          ruleId: contract.patternName || contract.desc,
          agentMsg:
            `The action \`${toolName}\` was rejected by policy ` +
            `(${contract.patternName || contract.desc}): ${contract.desc}. ` +
            `Choose a different approach.`,
        });
        // Nest violation + enforcement spans under the failed
        // guarantee so the renderer can pick up the verdict word.
        const enforce = new EnforcementSpan("DetBlock", this.mode === "enforce" ? "blocked" : "blocked");
        guaranteeSpan.children.push(enforce);
        enforce.finish("violated");
      }
      collector.pop(result ? "ok" : "violated");
    }

    const hasViolations = violations.length > 0;
    const blocked = hasViolations && this.mode === "enforce";
    this._turnSpans.push(
      collector.finishRoot(blocked, this._contracts.length, violations.length),
    );

    if (blocked) {
      this._trace.pop();
      this._state = snapshot;
      this._violations.push(...violations);

      this._logViolations(toolName, violations, violatedDescs, "blocked");

      return {
        blocked: true,
        allowed: false,
        message: violations[0],
        violations,
        detViolations,
        stoViolations: [],
      };
    }

    // Either no violations, or observe mode: allow + log.
    if (hasViolations) {
      // observe mode: capture for summary(), but don't roll back.
      this._violations.push(...violations);
      this._logViolations(toolName, violations, violatedDescs, "observed");
    } else {
      // Clean allow: one "allow" record per guardBefore so
      // ``sponsio report`` sees a complete turn ledger.
      this._logAllow(toolName);
    }

    return {
      blocked: false,
      allowed: true,
      message: "",
      violations: [],
      detViolations,
      stoViolations: [],
    };
  }

  /**
   * Snapshot of the per-turn span trees this guard has produced since
   * construction (or the last ``resetSession``). One root
   * ``AgentTurnSpan`` per ``guardBefore`` call, with nested
   * ``ContractCheckSpan`` / ``GuaranteeSpan`` / ``EnforcementSpan``
   * children. Mirrors Python's ``RuntimeMonitor.turn_spans``.
   */
  turnSpans(): AgentTurnSpan[] {
    return [...this._turnSpans];
  }

  /** Drop accumulated turn spans (used between sessions in tests). */
  resetSession(): void {
    this._turnSpans = [];
  }

  /**
   * Render the end-of-session view to ``out`` (defaults to stderr) —
   * banner, contracts armed, trace tree, verdict, perf, CTA. Mirrors
   * Python's ``RuntimeMonitor.finish_session`` + ``render_session``
   * pipeline. Call this once at the end of the agent's run.
   */
  finishSession(opts: {
    out?: NodeJS.WritableStream;
    sessionId?: string;
    tenant?: string;
    env?: string;
    sdk?: string;
    ctas?: string[];
    useColor?: boolean;
  } = {}): void {
    renderSession({
      agentId: this.agentId,
      mode: this.mode,
      contracts: this._contracts,
      turnSpans: this._turnSpans,
      ...opts,
    });
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
    action: "blocked" | "observed",
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
      action: "allowed",
      pipeline: "det",
      constraint: `${this.agentId}.${toolName}`,
      result: { action: "allowed", message: "" },
    });
  }

  /**
   * Record tool output after execution and run the sto pipeline.
   *
   * Returns a ``CheckResult`` whose ``stoViolations`` carries any
   * stochastic rule failures (``tone`` / ``llm_judge``). When no sto
   * contracts are declared the method returns synchronously in
   * effect (the ``Promise`` resolves on the microtask queue) and
   * never contacts the judge LLM.
   *
   * In **observe mode** violations are logged but the method still
   * reports ``allowed: true``. In **enforce mode** the first sto
   * violation flips ``blocked: true`` — but the tool output has
   * already executed, so the caller is responsible for routing the
   * result (retry with feedback, redirect to safe, etc.).
   */
  async guardAfter(
    toolName: string,
    output: string = "",
  ): Promise<CheckResult> {
    if (this._stoContracts.length === 0) {
      return emptyAllow();
    }

    const stoViolations: DetViolation[] = [];
    const flatMessages: string[] = [];

    for (const sto of this._stoContracts) {
      try {
        const result = await sto.evaluator.evaluate({
          toolName,
          output,
          context: this._stoContext,
        });
        if (result.passed) {
          this._logSto(sto.desc, "allowed", result.score, result.evidence);
          continue;
        }
        const verb = this.mode === "observe" ? "WOULD-BLOCK" : "BLOCKED";
        const msg =
          `${verb}: ${this.agentId}.${toolName} — sto constraint ` +
          `violated: ${sto.desc} (score=${result.score.toFixed(2)})`;
        // Sto outcomes default to retry-style phrasing — the model
        // is being asked to regenerate addressing the failed
        // property, not to abandon the action altogether. retryHint
        // carries the evaluator's suggestion when available.
        stoViolations.push({
          desc: sto.desc,
          message: msg,
          ruleId: sto.desc,
          agentMsg:
            `Your output failed the \`${sto.desc}\` check ` +
            `(score=${result.score.toFixed(2)}). Regenerate addressing the issue.`,
          retryHint: result.evidence || undefined,
        });
        flatMessages.push(msg);
        this._logSto(
          sto.desc,
          this.mode === "observe" ? "observed" : "blocked",
          result.score,
          result.evidence,
        );
      } catch (err) {
        const errMsg = err instanceof Error ? err.message : String(err);
        const handling = this._judgeFallback;
        if (handling === "skip") {
          console.warn(
            `[sponsio] sto ${sto.desc}: judge error (${errMsg}) — skipping`,
          );
          continue;
        }
        const verb = this.mode === "observe" ? "WOULD-BLOCK" : "BLOCKED";
        if (handling === "deny") {
          const msg =
            `${verb}: ${this.agentId}.${toolName} — sto judge error ` +
            `(${errMsg}); fallback_mode=deny`;
          stoViolations.push({ desc: sto.desc, message: msg });
          flatMessages.push(msg);
          this._logSto(
            sto.desc,
            this.mode === "observe" ? "observed" : "blocked",
            undefined,
            `judge-error: ${errMsg}`,
          );
        } else {
          // allow (default) — log a pass so the operator can see the
          // judge was exercised, but do not create a violation.
          console.warn(
            `[sponsio] sto ${sto.desc}: judge error (${errMsg}) — allowing`,
          );
          this._logSto(
            sto.desc,
            "allowed",
            undefined,
            `judge-error-allowed: ${errMsg}`,
          );
        }
      }
    }

    if (stoViolations.length === 0) {
      return emptyAllow();
    }

    this._violations.push(...flatMessages);

    if (this.mode === "enforce") {
      return {
        blocked: true,
        allowed: false,
        message: flatMessages[0],
        violations: flatMessages,
        detViolations: [],
        stoViolations,
      };
    }

    return {
      blocked: false,
      allowed: true,
      message: "",
      violations: [],
      detViolations: [],
      stoViolations,
    };
  }

  private _logSto(
    desc: string,
    action: "allowed" | "blocked" | "observed",
    score: number | undefined,
    evidence: string | undefined,
  ): void {
    if (!this._logger) return;
    const record: {
      ts: number;
      agent_id: string;
      action: "allowed" | "blocked" | "observed";
      pipeline: "sto";
      constraint: string;
      result: {
        action: "allowed" | "blocked" | "observed";
        message: string;
      };
      sto?: { score: number; evidence?: string };
    } = {
      ts: Date.now() / 1000,
      agent_id: this.agentId,
      action,
      pipeline: "sto",
      constraint: desc,
      result: {
        action,
        message: evidence ?? "",
      },
    };
    if (score !== undefined) {
      record.sto = evidence ? { score, evidence } : { score };
    }
    this._logger.log(record);
  }

  /**
   * Reset guard state for a new session.
   */
  reset(): void {
    this._trace = [];
    this._state = newGroundingState();
    this._violations = [];
    this._stoContext = {};
    this._contentAtoms = collectContentAtoms(
      this._contracts.map((c) => c.formula),
    );
  }

  /**
   * Stash per-turn context for the sto pipeline. Atoms that need
   * grounding — ``relevance`` (``query``), ``hallucination_free``
   * (``source``), ``scope_respect`` (``scope`` override),
   * ``metric_integrity`` (``history``) — read from this snapshot on
   * the next ``guardAfter`` call.
   *
   * Merges with any previously-set context; pass ``{ query: undefined }``
   * to explicitly clear a field. ``reset()`` clears the whole snapshot.
   */
  setContext(ctx: Partial<StoContextSnapshot>): void {
    this._stoContext = { ...this._stoContext, ...ctx };
  }

  /**
   * Get all violations from this session.
   */
  get violations(): string[] {
    return [...this._violations];
  }

  /**
   * Get a summary string. Returns a formatted list of violations
   * observed in this session, or ``"No violations"`` if clean.
   */
  summary(): string {
    if (this._violations.length === 0) return "No violations";
    return this._violations.map((v) => `- ${v}`).join("\n");
  }

  /**
   * Print the session summary to stdout — Python parity for
   * ``guard.print_summary()``. Equivalent to
   * ``console.log(guard.summary())``; exists so copy-pasted Python
   * snippets compile (and so ad-hoc review in scripts reads the
   * same way across both SDKs).
   */
  printSummary(): void {
    console.log(this.summary());
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

function isJudgeClient(v: unknown): v is JudgeClient {
  return (
    !!v &&
    typeof v === "object" &&
    typeof (v as JudgeClient).complete === "function"
  );
}

function resolveJudge(
  ctorJudge: JudgeConfig | JudgeClient | undefined,
  yamlJudge: JudgeConfigSpec | undefined,
): JudgeClient | null {
  if (ctorJudge) {
    if (isJudgeClient(ctorJudge)) return ctorJudge;
    return createJudge(ctorJudge);
  }
  if (yamlJudge) {
    // Only build when there's enough to talk to a model.
    const hasAny =
      yamlJudge.provider || yamlJudge.model || yamlJudge.apiKey || yamlJudge.baseUrl;
    if (!hasAny) return null;
    return createJudge({
      provider: yamlJudge.provider,
      model: yamlJudge.model,
      apiKey: yamlJudge.apiKey,
      baseUrl: yamlJudge.baseUrl,
      fallbackMode: yamlJudge.fallbackMode,
    });
  }
  return null;
}

function buildStoContract(
  spec: StoContractSpec,
  judge: JudgeClient,
): StoContract {
  let evaluator: StoEvaluator;
  switch (spec.atom) {
    case "tone":
      evaluator = new ToneEvaluator(
        spec.desc,
        spec.threshold,
        judge,
        spec.args[0],
      );
      break;
    case "relevance":
      evaluator = new RelevanceEvaluator(spec.desc, spec.threshold, judge);
      break;
    case "semantic_pii_free":
      evaluator = new SemanticPiiFreeEvaluator(spec.desc, spec.threshold, judge);
      break;
    case "hallucination_free":
      evaluator = new HallucinationFreeEvaluator(
        spec.desc,
        spec.threshold,
        judge,
      );
      break;
    case "scope_respect":
      evaluator = new ScopeRespectEvaluator(
        spec.desc,
        spec.threshold,
        judge,
        spec.args[0],
      );
      break;
    case "metric_integrity":
      evaluator = new MetricIntegrityEvaluator(
        spec.desc,
        spec.threshold,
        judge,
      );
      break;
    case "injection_free":
      evaluator = new InjectionFreeEvaluator(spec.desc, spec.threshold, judge);
      break;
    default: {
      // llm_judge (and any future free-form atom)
      const prompt =
        spec.promptOverride ??
        "Score how well the tool output satisfies the rule: " + spec.desc;
      evaluator = new LlmJudgeEvaluator(
        spec.desc,
        spec.threshold,
        judge,
        prompt,
      );
    }
  }
  return { desc: spec.desc, evaluator };
}

function emptyAllow(): CheckResult {
  return {
    blocked: false,
    allowed: true,
    message: "",
    violations: [],
    detViolations: [],
    stoViolations: [],
  };
}

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
