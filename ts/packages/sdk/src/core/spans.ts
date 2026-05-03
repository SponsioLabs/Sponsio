/**
 * Span data model — mirrors ``sponsio/models/spans.py``.
 *
 * The runtime monitor builds one ``AgentTurnSpan`` tree per
 * ``guardBefore`` call. Children include per-contract evaluation
 * spans, with grand-children for assumption / guarantee / violation /
 * enforcement nodes. The session view renderer walks this tree to
 * produce the ``contracts armed`` + ``trace`` + ``VERDICT`` zones.
 *
 * Subclasses match the Python ``span_type`` strings field-for-field
 * so traces serialise identically across languages.
 */

export type SpanStatus = "ok" | "violated" | "error";

export interface SpanLike {
  spanType: string;
  startTime: number; // ms (perf.now)
  endTime?: number;
  status: SpanStatus;
  attributes: Record<string, unknown>;
  children: SpanLike[];
  durationMs(): number | null;
  finish(status?: SpanStatus): void;
}

export class Span implements SpanLike {
  spanType: string;
  startTime: number;
  endTime?: number;
  status: SpanStatus;
  attributes: Record<string, unknown>;
  children: SpanLike[];

  constructor(spanType: string, attributes: Record<string, unknown> = {}) {
    this.spanType = spanType;
    this.startTime = performance.now();
    this.status = "ok";
    this.attributes = attributes;
    this.children = [];
  }

  durationMs(): number | null {
    if (this.endTime === undefined) return null;
    return this.endTime - this.startTime;
  }

  finish(status?: SpanStatus): void {
    this.endTime = performance.now();
    if (status) this.status = status;
  }
}

export class AgentTurnSpan extends Span {
  agentId = "";
  action = "";
  totalContractsChecked = 0;
  detViolations = 0;
  stoViolations = 0;
  blocked = false;

  constructor(agentId: string, action: string) {
    super("sponsio.agent_turn");
    this.agentId = agentId;
    this.action = action;
  }
}

export class ContractCheckSpan extends Span {
  contractName: string;
  pipeline: "hard" | "sto";

  constructor(contractName: string, pipeline: "hard" | "sto" = "hard") {
    super("sponsio.contract_check");
    this.contractName = contractName;
    this.pipeline = pipeline;
  }
}

export class PreconditionSpan extends Span {
  formulaDesc: string;
  result: boolean;

  constructor(formulaDesc: string, result: boolean) {
    super("sponsio.precondition");
    this.formulaDesc = formulaDesc;
    this.result = result;
  }
}

export class GuaranteeSpan extends Span {
  formulaDesc: string;
  result: boolean;

  constructor(formulaDesc: string, result: boolean) {
    super("sponsio.guarantee");
    this.formulaDesc = formulaDesc;
    this.result = result;
  }
}

export class ViolationSpan extends Span {
  kind: "assumption" | "guarantee" | "sto" | "liveness";
  severity: "HIGH" | "MEDIUM" | "LOW";
  evidence: string;

  constructor(kind: ViolationSpan["kind"], severity: ViolationSpan["severity"] = "HIGH", evidence = "") {
    super("sponsio.violation");
    this.kind = kind;
    this.severity = severity;
    this.evidence = evidence;
  }
}

export class EnforcementSpan extends Span {
  strategy: string;
  resultAction: "blocked" | "escalated" | "retrying" | "redirected" | "";

  constructor(strategy: string, resultAction: EnforcementSpan["resultAction"] = "") {
    super("sponsio.enforcement");
    this.strategy = strategy;
    this.resultAction = resultAction;
  }
}

/**
 * SpanCollector — builds a span tree during one ``guardBefore`` call.
 * Mirrors Python's ``models.spans.SpanCollector``: a stack of
 * currently-open spans, push/pop to nest. The root is the
 * ``AgentTurnSpan`` returned by ``rootSpan``.
 */
export class SpanCollector {
  private root: AgentTurnSpan;
  private stack: SpanLike[];

  constructor(agentId: string, action: string) {
    this.root = new AgentTurnSpan(agentId, action);
    this.stack = [this.root];
  }

  rootSpan(): AgentTurnSpan {
    return this.root;
  }

  push(span: SpanLike): void {
    const parent = this.stack[this.stack.length - 1];
    parent.children.push(span);
    this.stack.push(span);
  }

  pop(status?: SpanStatus): void {
    const span = this.stack.pop();
    if (span) span.finish(status);
  }

  /** Add a leaf span (no children) and finalize it immediately. */
  add(span: SpanLike, status?: SpanStatus): void {
    const parent = this.stack[this.stack.length - 1];
    parent.children.push(span);
    span.finish(status);
  }

  /** Finalize the root after the guard call completes. */
  finishRoot(blocked: boolean, totalContractsChecked: number, detViolations: number): AgentTurnSpan {
    this.root.totalContractsChecked = totalContractsChecked;
    this.root.detViolations = detViolations;
    this.root.blocked = blocked;
    this.root.finish(blocked ? "violated" : "ok");
    return this.root;
  }
}

/**
 * Walk a span tree depth-first, yielding every span (including the root).
 */
export function* walk(span: SpanLike): Generator<SpanLike> {
  yield span;
  for (const child of span.children) {
    yield* walk(child);
  }
}
