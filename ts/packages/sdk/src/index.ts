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
import type {
  JudgeClient,
  JudgeConfig,
  StoContract,
  StoContextSnapshot,
  StoEvaluator,
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
export { parseScore, CloudFeatureError } from "./core/sto.js";
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
   * Sto-pipeline violations. Not part of this build (the engine is
   * deterministic-only). Always ``[]`` on the TS SDK (the ctor
   * rejects yaml sto contracts at load time and never builds a
   * judge). External subclasses can populate this with results from
   * stochastic contracts (``tone`` / ``llm_judge`` / ``injection_free``
   * / ...). Kept on the schema so dashboards / session loggers branch
   * consistently across both surfaces.
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
   * Judge config for the sto pipeline. **Not supported in this build**
   * (the engine is deterministic-only). Either a plain config object
   * (provider / model / apiKey / baseUrl / fallbackMode) or a pre-built
   * ``JudgeClient``. The TS SDK accepts the option for shared-yaml
   * compatibility but never constructs a judge: passing this surfaces
   * a warning via the skipped-items path and the field is otherwise
   * ignored.
   */
  judge?: JudgeConfig | JudgeClient;
}

export class Sponsio {
  readonly agentId: string;
  readonly mode: SponsoMode;
  private _contracts: DetFormula[];
  // Sto state. Always empty/null in this build, the constructor
  // rejects yaml sto contracts and inline `judge:` at config load.
  // Kept as schema-stable surface for external composition / wrapping.
  private _stoContracts: StoContract[];
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

    // ── Sto pipeline is not part of this build ──────────────────────
    // The managed LLM-judge catalog (`tone` / `relevance` / `llm_judge`
    // ...) and the judge client are not part of this build (the
    // deterministic engine provides no implementation). The engine
    // logs-and-skips any yaml sto contracts and any inline `judge:`
    // option, never constructs an evaluator, never contacts an LLM.
    // The API surface (`guardAfter`, `setContext`, `judge` ctor
    // option) is preserved so a shared yaml between deterministic
    // builds and external sto implementations doesn't refuse to load.
    for (const spec of yamlStoSpecs) {
      yamlSkipped.push({
        kind: "sto-contract",
        detail: `${spec.desc} (sto pipeline is not supported in this build)`,
      });
    }
    if (options.judge) {
      yamlSkipped.push({
        kind: "sto-contract",
        detail:
          "`judge:` option ignored (sto pipeline is not supported in this build)",
      });
    }
    // Reference yamlJudge so unused-var lints don't fire: the field
    // is parsed by the loader for shared-yaml compatibility but this
    // build never constructs a judge from it.
    void yamlJudge;

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
    // Stash args on the root for the renderer (it shows them in the trace).
    collector.rootSpan().attributes.args = args;

    const violations: string[] = [];
    const violatedDescs: string[] = [];
    const detViolations: DetViolation[] = [];
    for (const contract of this._contracts) {
      const checkSpan = new ContractCheckSpan(contract.desc, "hard");
      collector.push(checkSpan);
      // Liveness obligations (``audit_after`` / ``deadline`` /
      // ``required_steps_completion`` / ``always_followed_by``) cannot
      // be discharged on a *prefix* of a finite trace — checking them
      // mid-session would fire on every trigger before the obligated
      // step has had a chance to run. Mirrors Python's
      // ``RuntimeMonitor`` which skips ``liveness=True`` formulas
      // during incremental checks and revisits them at session end
      // (``finishSession``).
      if (contract.liveness) {
        const guaranteeSpan = new GuaranteeSpan(contract.desc, true);
        collector.add(guaranteeSpan, "ok");
        collector.pop("ok");
        continue;
      }
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
        const enforce = new EnforcementSpan("DetBlock", this.mode === "enforce" ? "blocked" : "observed");
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
      currentCtx: { ...this._state.currentCtx },
      now: this._state.now,
      lastTs: { ...this._state.lastTs },
      trueAtPrev: new Set(this._state.trueAtPrev),
    };
  }

  /**
   * Push external facts into the grounding context. Mirrors Python's
   * ``BaseGuard.observe_context`` — every subsequent event sees the
   * keys as ``ctx(k, v)`` atoms, so contracts like
   * ``ctx_required("wire", "caller_id", ["alice"])`` can fire.
   *
   * Emits a synthetic ``context_update`` event into the trace so
   * patterns that anchor on the moment a fact was set (notably
   * ``time_since(ctx(approval.role, alice))``) start their clock here.
   */
  observeContext(ctx: Record<string, unknown>): void {
    const event: ToolEvent = {
      tool: "",
      event_type: "context_update",
      args: ctx as Record<string, unknown>,
    };
    const valuation = groundEvent(event, this._state, this._contentAtoms);
    this._trace.push(valuation);
  }

  /**
   * Convenience helper for the approval-active gate: pushes
   * ``approval.role`` / ``approval.decision`` (+ optional ``approver``
   * / ``reason``) via ``observeContext``. Pairs with the
   * ``approval_active`` pattern.
   */
  observeApproval(opts: {
    role: string;
    decision?: "allow" | "deny";
    approver?: string;
    reason?: string;
  }): void {
    const ctx: Record<string, string> = {
      "approval.role": opts.role,
      "approval.decision": opts.decision ?? "allow",
    };
    if (opts.approver) ctx["approval.approver"] = opts.approver;
    if (opts.reason) ctx["approval.reason"] = opts.reason;
    this.observeContext(ctx);
  }

  /**
   * Record an LLM response into the trace. Drives ``max_length``,
   * ``no_pii``, ``no_keywords`` (any pattern that grounds against
   * ``llm_said`` / ``response_words`` / ``response_chars``). Mirrors
   * the Python ``llm_response`` event path.
   *
   * Returns a ``CheckResult`` so callers can branch on observe-mode
   * violations the same way they branch on ``guardBefore``. In
   * enforce mode, ``blocked: true`` rolls back the synthetic event.
   */
  observeResponse(content: string, opts: { segment?: string } = {}): CheckResult {
    const args: Record<string, unknown> = {};
    if (opts.segment) args.segment = opts.segment;
    const event: ToolEvent = {
      tool: "",
      event_type: "llm_response",
      content,
      args,
    };
    const snapshot = this._snapshotState();
    const valuation = groundEvent(event, this._state, this._contentAtoms);
    this._trace.push(valuation);

    const violations: string[] = [];
    const violatedDescs: string[] = [];
    const detViolations: DetViolation[] = [];
    for (const contract of this._contracts) {
      const result = evaluate(contract.formula, this._trace);
      if (!result) {
        const verb = this.mode === "observe" ? "WOULD-BLOCK" : "BLOCKED";
        const msg = `${verb}: ${this.agentId}.<llm_response> — det constraint violated: ${contract.desc}`;
        violations.push(msg);
        violatedDescs.push(contract.desc);
        detViolations.push({
          desc: contract.desc,
          message: msg,
          ruleId: contract.patternName || contract.desc,
          agentMsg:
            `LLM response was rejected by policy ` +
            `(${contract.patternName || contract.desc}): ${contract.desc}.`,
        });
      }
    }

    const hasViolations = violations.length > 0;
    const blocked = hasViolations && this.mode === "enforce";

    if (blocked) {
      this._trace.pop();
      this._state = snapshot;
      this._violations.push(...violations);
      this._logViolations("<llm_response>", violations, violatedDescs, "blocked");
      return {
        blocked: true,
        allowed: false,
        message: violations[0],
        violations,
        detViolations,
        stoViolations: [],
      };
    }

    if (hasViolations) {
      this._violations.push(...violations);
      this._logViolations("<llm_response>", violations, violatedDescs, "observed");
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
   * Record tool output after execution.
   *
   * This build ships **no sto pipeline** (the engine is deterministic-only).
   * Stochastic (LLM-judged) atoms are the extension point that would
   * populate ``stoViolations``; with no external implementation and no
   * sto contracts loaded (sto entries in yaml are rejected at config
   * load), ``stoViolations`` is always ``[]`` and ``allowed: true``.
   *
   * The method stays ``async`` for API parity: an external subclass
   * or wrapper can run the judge pipeline and surface real
   * ``stoViolations`` without changing the call site. In **enforce
   * mode** such subclasses can flip ``blocked: true`` on the first
   * sto violation; the tool output has already executed, so the
   * caller is responsible for routing the result (retry with
   * feedback, redirect to safe, etc.).
   */
  async guardAfter(
    _toolName: string,
    _output: string = "",
  ): Promise<CheckResult> {
    // This build is deterministic-only, the sto pipeline is not
    // implemented. Always return clean-pass; external subclasses can
    // override.
    return emptyAllow();
  }

  /**
   * Reset guard state for a new session. Clears the trace, the
   * grounding accumulators (including ``currentCtx``, ``lastTs``,
   * event clock), the violation log, and the sto-context snapshot.
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
   * Stash per-turn context for the sto pipeline.
   *
   * Not supported in this build (the engine is deterministic-only).
   * Atoms that need grounding (``relevance`` -> ``query``,
   * ``hallucination_free`` -> ``source``, ``scope_respect`` ->
   * ``scope`` override, ``metric_integrity`` -> ``history``) would
   * read from this snapshot on the next ``guardAfter`` call. This
   * build accepts the call as a no-op so shared agent code can stay
   * framework-neutral; the snapshot is never consumed.
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

// Note: ``resolveJudge`` / ``buildStoContract`` / ``isJudgeClient``
// helpers were removed when the sto pipeline became an external
// extension point. This build never constructs a judge or evaluator;
// yaml sto specs flow through the ``yamlSkipped`` warning path in
// the ctor.

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

  const stoNote =
    sto > 0
      ? " Sto (LLM-judge) contracts are not supported in this build (the engine is deterministic-only)."
      : "";
  console.warn(
    "[sponsio] skipped unsupported yaml items: " +
      bits.join("; ") +
      "." +
      stoNote,
  );
}
