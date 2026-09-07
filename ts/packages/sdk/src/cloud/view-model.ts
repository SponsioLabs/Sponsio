/**
 * One finished run, in the shape the cloud stores.
 *
 * Port of the projection in ``sponsio/bridge/session.py``. Field names
 * are Python's, not new ones: the server, the console and the findings
 * pipeline all read this object, and a TypeScript run that spelled
 * ``argsPreview`` differently would upload successfully and then appear
 * as a run with no arguments and no rules.
 */

import { randomBytes } from "node:crypto";

import type { DetFormula } from "../core/patterns.js";
import type { AgentTurnSpan, SpanLike } from "../core/spans.js";
import type { DetViolation } from "../index.js";
import * as privacy from "./privacy.js";
import { pyJson } from "./privacy.js";

const SLUG = /[^a-z0-9]+/g;

/** Parity with ``bridge.spans.slug``. */
export function slug(text: string): string {
  return String(text).trim().toLowerCase().replace(SLUG, "-").replace(/^-|-$/g, "") || "rule";
}

/**
 * One line, truncated. Truncation happens before the value leaves the
 * machine, so an argument that should never have been sent cannot be
 * reconstructed from what was.
 */
export function argsPreview(args: unknown, limit = 160): string {
  if (args === null || args === undefined) return "";
  // Python's json.dumps spacing, so the same call renders identically in
  // the console whichever runtime recorded it.
  let text = pyJson(args);
  text = text.split(/\s+/).join(" ");
  return text.length <= limit ? text : text.slice(0, limit - 1) + "…";
}

/** Statuses the console has a colour for. */
const KNOWN_ACTIONS = new Set([
  "blocked", "escalated", "redirected", "retrying", "warned", "observed",
]);

function stepStatus(span: AgentTurnSpan, violations: DetViolation[]): string {
  if (!violations.length) return "ok";
  const acted = violations.find((v) => v.action && KNOWN_ACTIONS.has(v.action));
  if (acted?.action) return acted.action;
  return span.blocked ? "blocked" : "observed";
}

/**
 * Hex from the system CSPRNG, as Python's ``secrets.token_hex`` does.
 *
 * ``Math.random()`` is not one, and this is the one place where that
 * matters: the server keys a run by its id and upserts, so two runs
 * sharing an id silently become one and the older is overwritten with no
 * error. A booting fleet seeds ``Math.random`` from a clock they share.
 */
function hex(chars: number): string {
  return randomBytes(Math.ceil(chars / 2))
    .toString("hex")
    .slice(0, chars);
}

/** A run id with enough entropy that two runs cannot silently become one. */
export function newSessionId(): string {
  return "run-" + hex(16);
}

export interface RunSpan {
  span: AgentTurnSpan;
  /** The violations the check produced, in the order it produced them. */
  violations: DetViolation[];
  /** What kind of step this was; only ``tool_call`` survives `tool_calls`. */
  type?: string;
}

export interface ViewModelInput {
  agentId: string;
  mode: string;
  sessionId: string;
  startedAt: number;
  contracts: DetFormula[];
  spans: RunSpan[];
  privacyLevel: privacy.PrivacyLevel;
  /** Which book this run enforced, when it came from the cloud. */
  rulebookStamp?: string;
}

function firstContractName(span: SpanLike): string | undefined {
  for (const child of span.children ?? []) {
    const named = child as SpanLike & { contractName?: string };
    if (named.contractName) return named.contractName;
  }
  return undefined;
}

/**
 * Build the run object.
 *
 * The privacy level is applied here rather than at the transport, so
 * there is one place that decides what leaves and no path around it.
 */
export function buildViewModel(input: ViewModelInput): Record<string, unknown> {
  const { privacyLevel: level } = input;
  const byLabel = new Map(input.contracts.map((c) => [c.desc, c]));
  const violationCounts = new Map<string, number>();

  const traceId = hex(32);
  const steps: Record<string, unknown>[] = [];

  input.spans.forEach((entry, idx) => {
    const type = entry.type ?? "tool_call";
    if (!privacy.keepsStep(type, level)) return;

    const rawArgs = (entry.span.attributes as { args?: unknown }).args;
    const violations = entry.violations.map((v) => {
      const contract = byLabel.get(v.desc);
      const label = v.desc;
      violationCounts.set(label, (violationCounts.get(label) ?? 0) + 1);
      return {
        contractId: slug(label),
        contractLabel: label,
        category: "Prohibited",
        kind: "guarantee",
        severity: "HIGH",
        pipeline: "det",
        enforcement: {
          strategy: v.action === "escalated" ? "EscalateToHuman" : "DetBlock",
          action: v.action ?? "observed",
        },
        evidence: `Runtime det violation: ${label}`,
        reason: label,
        // The rule's identity, which is what the cloud groups findings
        // by. Without it a violation carries only its English sentence
        // and a reworded rule loses its own history.
        pattern: contract?.patternName ?? v.ruleId ?? "",
        args: contract?.args ?? [],
      };
    });

    steps.push({
      id: `s${idx}`,
      serviceName: entry.span.agentId || input.agentId,
      traceId,
      spanId: "0".repeat(16),
      type,
      tool: entry.span.action,
      argsPreview: privacy.preview(rawArgs, level, argsPreview(rawArgs)),
      durationMs: Number((entry.span.durationMs() ?? 0).toFixed(2)),
      status: stepStatus(entry.span, entry.violations),
      verdict: {
        blocked: entry.span.blocked,
        contractsChecked: entry.span.totalContractsChecked,
        violations,
      },
    });
  });

  const contracts = input.contracts.map((c) => ({
    id: slug(c.desc),
    label: c.desc,
    status: (violationCounts.get(c.desc) ?? 0) > 0 ? "watching" : "armed",
    pipeline: "det",
    violationCount: violationCounts.get(c.desc) ?? 0,
    pattern: c.patternName,
    args: c.args ?? [],
    source: "policy",
    boundAgents: [] as string[],
  }));

  const total = steps.length;
  const failed = steps.filter((s) => s.status !== "ok").length;
  const blocked = steps.filter((s) => s.status === "blocked").length;

  const vm: Record<string, unknown> = {
    session: {
      id: input.sessionId,
      agentId: input.agentId,
      mode: input.mode,
      startedAt: input.startedAt,
    },
    agents: [{ id: input.agentId, label: input.agentId }],
    edges: [],
    steps,
    contracts,
    summary: {
      passRate: total ? (total - failed) / total : 1,
      totalSteps: total,
      detBlocks: blocked,
      stoRetries: 0,
      escalations: steps.filter((s) => s.status === "escalated").length,
      contractsArmed: contracts.filter((c) => c.violationCount === 0).length,
      contractsTotal: contracts.length,
      stopped: blocked,
    },
    // So a reader can tell "the agent passed no arguments" from "this
    // deployment does not send them".
    privacy: privacy.describe(level),
  };

  if (input.rulebookStamp) {
    vm.rulebook = input.rulebookStamp;
    vm.rulebookRef = input.rulebookStamp;
  }
  return vm;
}
