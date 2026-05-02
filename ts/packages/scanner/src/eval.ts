/**
 * ``sponsio eval`` — replay a labelled corpus, report FPR/FNR.
 *
 * Mirrors the Python ``sponsio eval`` UX. Filename prefix is the
 * label:
 *   - ``safe_*``    expected to PASS every contract
 *   - ``unsafe_*``  expected to be BLOCKED by ≥1 contract
 *   - other         counted but not used in FPR/FNR
 *
 * Reports per-contract FPR/FNR + overall numbers. Supports
 * ``--baseline`` diffing for CI gates (``--max-fpr-delta`` /
 * ``--max-fnr-delta``).
 */
import { readFileSync, writeFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { resolve, join, basename } from "node:path";
import * as yaml from "js-yaml";
import { extractOtlpEvents, isOtlpPayload } from "./otlp";

const HELP =
  "sponsio eval — replay a labelled trace corpus and report FPR/FNR\n" +
  "\n" +
  "USAGE:\n" +
  "  sponsio eval <trace_path> [contracts...] [options]\n" +
  "\n" +
  "Label convention (filename prefix):\n" +
  "  safe_*     expected to PASS\n" +
  "  unsafe_*   expected to be BLOCKED by >=1 contract\n" +
  "  *          counted, not used in FPR/FNR\n" +
  "\n" +
  "OPTIONS:\n" +
  "  -c, --config <file>       sponsio.yaml\n" +
  "  -a, --agent <id>          Agent id (with --config)\n" +
  "      --json                Emit JSON report on stdout\n" +
  "      --baseline <file>     Compare against a prior --json report\n" +
  "      --max-fpr-delta <pp>  Exit 1 if overall FPR rose more than this (pp)\n" +
  "      --max-fnr-delta <pp>  Exit 1 if overall FNR rose more than this (pp)\n" +
  "      --write-baseline <f>  Write JSON report to this path after running\n" +
  "  -h, --help                Show this help\n" +
  "\n" +
  "EXAMPLES:\n" +
  "  sponsio eval traces/ --config sponsio.yaml --json\n" +
  "  sponsio eval traces/ \"tool `transfer` at most 1 times\"\n" +
  "  sponsio eval traces/ -c sponsio.yaml --baseline main.json --max-fpr-delta 0.01\n";

interface RawEvent {
  tool: string;
  args?: Record<string, unknown>;
}

interface SdkLike {
  guardBefore: (
    tool: string,
    args?: Record<string, unknown>,
  ) => { blocked: boolean; violatedDescs?: string[]; detViolations?: { desc: string }[] };
  contractDescs: () => string[];
}

interface PerContract {
  desc: string;
  tp: number;
  fp: number;
  fn: number;
  tn: number;
}

interface EvalReport {
  config: string | null;
  contracts: number;
  totalTraces: number;
  safeTraces: number;
  unsafeTraces: number;
  unlabeled: number;
  fpr: number;
  fnr: number;
  perContract: PerContract[];
}

function coerceEvent(raw: unknown): RawEvent | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const tool = (r.tool as string) ?? (r.action as string);
  if (typeof tool !== "string") return null;
  return { tool, args: (r.args as Record<string, unknown>) ?? {} };
}

function loadEvents(path: string): RawEvent[] {
  const text = readFileSync(path, "utf-8");
  const trimmed = text.trimStart();
  if (trimmed.startsWith("{")) {
    const data = JSON.parse(text);
    if (isOtlpPayload(data)) return extractOtlpEvents(data);
    if (Array.isArray(data?.events)) return data.events.map(coerceEvent).filter(Boolean) as RawEvent[];
    return [];
  }
  if (trimmed.startsWith("[")) {
    const data = JSON.parse(text) as unknown[];
    return data.map(coerceEvent).filter(Boolean) as RawEvent[];
  }
  const out: RawEvent[] = [];
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) continue;
    try {
      const ev = coerceEvent(JSON.parse(line));
      if (ev) out.push(ev);
    } catch {
      // skip malformed
    }
  }
  return out;
}

function listCorpus(p: string): string[] {
  const abs = resolve(p);
  const st = statSync(abs);
  if (st.isFile()) return [abs];
  const out: string[] = [];
  for (const f of readdirSync(abs)) {
    if (f.endsWith(".json") || f.endsWith(".jsonl")) out.push(join(abs, f));
  }
  return out;
}

function labelOf(file: string): "safe" | "unsafe" | "other" {
  const name = basename(file).toLowerCase();
  if (name.startsWith("safe_") || name.startsWith("safe-")) return "safe";
  if (name.startsWith("unsafe_") || name.startsWith("unsafe-")) return "unsafe";
  return "other";
}

async function buildGuard(
  inline: string[],
  configPath?: string,
  agentId?: string,
): Promise<SdkLike> {
  const mod = await import("@sponsio/sdk");
  const Sponsio = mod.Sponsio;
  if (!Sponsio) throw new Error("[eval] @sponsio/sdk does not export Sponsio");
  const ctorOpts: Record<string, unknown> = { mode: "enforce", sessionLog: false };
  if (configPath) {
    const cfgAbs = resolve(configPath);
    ctorOpts.config = cfgAbs;
    if (agentId) {
      ctorOpts.agentId = agentId;
    } else {
      try {
        const data = yaml.load(readFileSync(cfgAbs, "utf-8")) as Record<string, unknown>;
        const ids = data?.agents ? Object.keys(data.agents as Record<string, unknown>) : [];
        if (ids.length === 1) ctorOpts.agentId = ids[0];
      } catch {
        // ignore
      }
    }
  } else {
    ctorOpts.agentId = "eval";
    ctorOpts.contracts = inline;
  }
  return new (Sponsio as new (o: Record<string, unknown>) => SdkLike)(ctorOpts);
}

function pct(n: number): string {
  return (n * 100).toFixed(2) + "%";
}

function diffPct(curr: number, prev: number | undefined): string {
  if (prev === undefined) return "";
  const d = curr - prev;
  const sign = d >= 0 ? "+" : "";
  return ` (${sign}${(d * 100).toFixed(2)}pp)`;
}

export async function runEvalCli(argv: string[]): Promise<void> {
  let tracePath: string | undefined;
  let configPath: string | undefined;
  let agentId: string | undefined;
  let asJson = false;
  let baselineFile: string | undefined;
  let writeBaseline: string | undefined;
  let maxFprDelta: number | undefined;
  let maxFnrDelta: number | undefined;
  const inline: string[] = [];

  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "-h" || a === "--help") {
      process.stdout.write(HELP);
      return;
    }
    if (a === "-c" || a === "--config") {
      configPath = argv[++i];
      continue;
    }
    if (a === "-a" || a === "--agent") {
      agentId = argv[++i];
      continue;
    }
    if (a === "--json") {
      asJson = true;
      continue;
    }
    if (a === "--baseline") {
      baselineFile = argv[++i];
      continue;
    }
    if (a === "--max-fpr-delta") {
      maxFprDelta = parseFloat(argv[++i]);
      continue;
    }
    if (a === "--max-fnr-delta") {
      maxFnrDelta = parseFloat(argv[++i]);
      continue;
    }
    if (a === "--write-baseline") {
      writeBaseline = argv[++i];
      continue;
    }
    if (a.startsWith("-")) {
      process.stderr.write(`unknown flag: ${a}\n${HELP}`);
      process.exit(2);
    }
    if (!tracePath) {
      tracePath = a;
    } else {
      inline.push(a);
    }
  }

  if (!tracePath) {
    process.stderr.write(`Error: trace path required\n\n${HELP}`);
    process.exit(2);
  }
  if (configPath && inline.length > 0) {
    process.stderr.write(`Error: cannot use both --config and positional contracts\n`);
    process.exit(2);
  }
  if (!configPath && inline.length === 0) {
    process.stderr.write(`Error: pass --config or positional contracts\n`);
    process.exit(2);
  }

  const files = listCorpus(tracePath);
  if (files.length === 0) {
    process.stderr.write(`Error: no .json/.jsonl files in ${tracePath}\n`);
    process.exit(1);
  }

  // Per-contract counters need a fresh guard per trace (state isolation).
  const aggregate: Map<string, PerContract> = new Map();
  let safeN = 0,
    unsafeN = 0,
    otherN = 0;
  let safeBlockedAny = 0; // false positives at trace level
  let unsafeBlockedAny = 0; // true positives at trace level

  for (const file of files) {
    const label = labelOf(file);
    if (label === "safe") safeN++;
    else if (label === "unsafe") unsafeN++;
    else otherN++;

    const guard = await buildGuard(inline, configPath, agentId);
    if (aggregate.size === 0) {
      for (const d of guard.contractDescs()) {
        aggregate.set(d, { desc: d, tp: 0, fp: 0, fn: 0, tn: 0 });
      }
    }
    const events = loadEvents(file);
    const blocksByDesc: Set<string> = new Set();
    for (const ev of events) {
      const r = guard.guardBefore(ev.tool, ev.args ?? {});
      if (r.blocked) {
        for (const d of r.violatedDescs ?? r.detViolations?.map((v) => v.desc) ?? []) {
          blocksByDesc.add(d);
        }
      }
    }

    if (label === "safe") {
      if (blocksByDesc.size > 0) safeBlockedAny++;
      for (const stat of aggregate.values()) {
        if (blocksByDesc.has(stat.desc)) stat.fp++;
        else stat.tn++;
      }
    } else if (label === "unsafe") {
      if (blocksByDesc.size > 0) unsafeBlockedAny++;
      for (const stat of aggregate.values()) {
        if (blocksByDesc.has(stat.desc)) stat.tp++;
        else stat.fn++;
      }
    }
  }

  const fpr = safeN > 0 ? safeBlockedAny / safeN : 0;
  const fnr = unsafeN > 0 ? (unsafeN - unsafeBlockedAny) / unsafeN : 0;

  const report: EvalReport = {
    config: configPath ?? null,
    contracts: aggregate.size,
    totalTraces: files.length,
    safeTraces: safeN,
    unsafeTraces: unsafeN,
    unlabeled: otherN,
    fpr,
    fnr,
    perContract: Array.from(aggregate.values()),
  };

  // Baseline diff (optional).
  let baseline: EvalReport | undefined;
  if (baselineFile) {
    if (!existsSync(resolve(baselineFile))) {
      process.stderr.write(`Error: baseline ${baselineFile} not found\n`);
      process.exit(1);
    }
    baseline = JSON.parse(readFileSync(resolve(baselineFile), "utf-8")) as EvalReport;
  }

  if (asJson) {
    process.stdout.write(JSON.stringify(report, null, 2) + "\n");
  } else {
    process.stdout.write(`Corpus:        ${files.length} traces (${safeN} safe, ${unsafeN} unsafe, ${otherN} unlabeled)\n`);
    process.stdout.write(`Contracts:     ${report.contracts}\n`);
    process.stdout.write(`Overall FPR:   ${pct(fpr)}${diffPct(fpr, baseline?.fpr)}\n`);
    process.stdout.write(`Overall FNR:   ${pct(fnr)}${diffPct(fnr, baseline?.fnr)}\n\n`);
    process.stdout.write(`Per-contract:\n`);
    for (const s of report.perContract) {
      const cFpr = s.fp + s.tn > 0 ? s.fp / (s.fp + s.tn) : 0;
      const cFnr = s.tp + s.fn > 0 ? s.fn / (s.tp + s.fn) : 0;
      process.stdout.write(
        `  ${s.desc.split("\n")[0]}\n` +
          `    TP=${s.tp}  FP=${s.fp}  FN=${s.fn}  TN=${s.tn}` +
          `   FPR=${pct(cFpr)}  FNR=${pct(cFnr)}\n`,
      );
    }
  }

  if (writeBaseline) {
    writeFileSync(resolve(writeBaseline), JSON.stringify(report, null, 2), "utf-8");
    process.stderr.write(`✓ wrote baseline ${writeBaseline}\n`);
  }

  // CI gates.
  let gateFail = false;
  if (baseline && maxFprDelta !== undefined) {
    const d = fpr - baseline.fpr;
    if (d > maxFprDelta) {
      process.stderr.write(`✗ FPR rose ${(d * 100).toFixed(2)}pp (limit ${maxFprDelta * 100}pp)\n`);
      gateFail = true;
    }
  }
  if (baseline && maxFnrDelta !== undefined) {
    const d = fnr - baseline.fnr;
    if (d > maxFnrDelta) {
      process.stderr.write(`✗ FNR rose ${(d * 100).toFixed(2)}pp (limit ${maxFnrDelta * 100}pp)\n`);
      gateFail = true;
    }
  }
  if (gateFail) process.exit(1);
}
