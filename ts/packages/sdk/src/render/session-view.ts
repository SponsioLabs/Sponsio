/**
 * End-of-session trace-tree view — TS port of
 * ``sponsio/render/session_view.py``. Walks ``Sponsio.turnSpans()``
 * and renders the ``contracts armed`` + ``trace`` + ``VERDICT`` zones.
 *
 * Design B1 layout (see Python session_view.py docstring for the
 * canonical mockup):
 *
 *   ━━━ ◒◓ Sponsio ━━━ runtime contract enforcement ━━━
 *     session  sess_xxxx          agent  …            mode  ENFORCE
 *     tenant   …                  env    …            sdk   …
 *     contracts armed ──────────────────────────────────
 *       C1  …                                        READY
 *       …
 *     trace ────────────────────────────────────────────
 *       00.000  ├─ user_instruction "…"              + 0ms     mcp
 *       00.012  │  └─ ⚙ assume[C1] freeze declared   ✓
 *       00.012  │     contract C1 → ACTIVE
 *       …
 *               ✗ enforce[C1] destructive SQL …  BLOCKED  14µs
 *   ━━━ VERDICT ━━━ BLOCKED ━━━━━━━━━━━━━━━━━━━━━━━━━━
 *     headline · N violations · M warnings
 *     K checks   X% deterministic   N LLM calls
 *     → sponsio explain C1     sponsio replay sess_xxxx
 */
import {
  assumeLine,
  contractsTable,
  ctaLine,
  enforceViolationLine,
  eventLine,
  headerBanner,
  headerMeta,
  perfLine,
  sectionRule,
  stateTransitionLine,
  verdictBanner,
  verdictSummary,
} from "./components.js";
import { stderrUseColor } from "./tokens.js";
import type { AgentTurnSpan, SpanLike } from "../core/spans.js";
import type { DetFormula } from "../core/patterns.js";

export interface RenderOptions {
  agentId: string;
  mode: "observe" | "enforce";
  contracts: DetFormula[];
  turnSpans: AgentTurnSpan[];
  sessionId?: string;
  tenant?: string;
  env?: string;
  sdk?: string;
  ctas?: string[];
  /** Where to write. Defaults to stderr. */
  out?: NodeJS.WritableStream;
  /** Force ANSI on/off; defaults to TTY autodetect on `out`. */
  useColor?: boolean;
}

function shortAlias(_label: string, idx: number): string {
  return `C${idx + 1}`;
}

function detectSdk(): string {
  try {
    return `@sponsio/sdk@0.1`;
  } catch {
    return "—";
  }
}

function shortSessionId(seed: string): string {
  // Cheap deterministic 8-char hash (FNV-1a) — for visual id only.
  let h = 0x811c9dc5;
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return `sess_${h.toString(16).padStart(8, "0").slice(0, 8)}`;
}

function fmtUs(ms: number | null | undefined): string {
  if (ms == null) return "";
  const us = ms * 1000;
  if (us < 1000) return `${Math.round(us)}µs`;
  return `${us.toFixed(0)}µs`;
}

function fmtTs(elapsedMs: number): string {
  const sec = elapsedMs / 1000;
  return sec.toFixed(3);
}

interface ViolationCounters {
  blocked: number;
  observed: number;
}

function walkViolations(turnSpans: AgentTurnSpan[]): ViolationCounters {
  let blocked = 0;
  let observed = 0;
  for (const t of turnSpans) {
    if (t.blocked) blocked++;
    else if (t.detViolations > 0) observed++;
  }
  return { blocked, observed };
}

function perfStats(turnSpans: AgentTurnSpan[]): { totalChecks: number; lat: number[] } {
  let totalChecks = 0;
  const lat: number[] = [];
  for (const t of turnSpans) {
    for (const c of t.children) {
      if (c.spanType !== "sponsio.contract_check") continue;
      totalChecks++;
      const d = c.durationMs();
      if (d != null) lat.push(d * 1000); // µs
    }
  }
  return { totalChecks, lat };
}

function verdictStatus(b: ViolationCounters): "BLOCKED" | "WARN" | "ALLOWED" {
  if (b.blocked > 0) return "BLOCKED";
  if (b.observed > 0) return "WARN";
  return "ALLOWED";
}

function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) return 0;
  const idx = Math.min(sorted.length - 1, Math.floor(sorted.length * q));
  return sorted[idx];
}

function renderTurn(
  turn: AgentTurnSpan,
  sessionStart: number,
  isLast: boolean,
  contracts: DetFormula[],
  aliasMap: Record<string, string>,
  activated: Set<string>,
  useColor: boolean,
): string[] {
  const lines: string[] = [];
  const elapsedMs = turn.startTime - sessionStart;
  // First row: the tool call.
  lines.push(eventLine(fmtTs(elapsedMs), turn.action, "", "", isLast, useColor));

  for (const child of turn.children) {
    if (child.spanType !== "sponsio.contract_check") continue;
    const checkSpan = child as SpanLike & { contractName: string };
    const alias = aliasMap[checkSpan.contractName] ?? "?";
    for (const grand of checkSpan.children) {
      const kind = grand.spanType;
      if (kind === "sponsio.precondition") {
        const pre = grand as SpanLike & { result: boolean; formulaDesc: string };
        if (pre.result && !activated.has(alias)) {
          activated.add(alias);
          lines.push(assumeLine(alias, pre.formulaDesc, useColor));
          lines.push(stateTransitionLine(alias, "ACTIVE", useColor));
        }
      } else if (kind === "sponsio.guarantee") {
        const g = grand as SpanLike & { result: boolean; formulaDesc: string };
        if (!g.result) {
          let status = "BLOCKED";
          for (const inner of g.children) {
            if (inner.spanType === "sponsio.enforcement") {
              const e = inner as SpanLike & { resultAction: string };
              if (e.resultAction) status = e.resultAction.toUpperCase();
              break;
            }
          }
          lines.push(enforceViolationLine(alias, g.formulaDesc, status, useColor));
        }
      }
    }
  }
  return lines;
}

export function renderSession(opts: RenderOptions): void {
  const out = opts.out ?? process.stderr;
  const useColor = opts.useColor ?? stderrUseColor();
  const write = (s: string) => out.write(s + "\n");

  const sessionId = opts.sessionId ?? shortSessionId(`${opts.agentId}-${process.pid}`);
  const sdkLabel = opts.sdk ?? detectSdk();
  const counters = walkViolations(opts.turnSpans);
  const { totalChecks, lat } = perfStats(opts.turnSpans);
  const status = verdictStatus(counters);

  // 1. Top banner.
  write(headerBanner(useColor));
  write("");

  // 2. Header metadata grid.
  const pairs: [string, string][] = [
    ["session", sessionId],
    ["agent", opts.agentId],
    ["mode", opts.mode.toUpperCase()],
    ["tenant", opts.tenant ?? "—"],
    ["env", opts.env ?? "—"],
    ["sdk", sdkLabel],
  ];
  for (const r of headerMeta(pairs, useColor)) write("  " + r);
  write("");

  // 3. Contracts armed list.
  const aliasMap: Record<string, string> = {};
  if (opts.contracts.length > 0) {
    const rows: [string, string, string][] = [];
    opts.contracts.forEach((c, i) => {
      const alias = shortAlias(c.desc, i);
      const label = c.desc || "(unnamed)";
      aliasMap[label] = alias;
      // No assumption distinction in TS yet — every contract starts ACTIVE.
      rows.push([alias, label.split("\n")[0], "ACTIVE"]);
    });
    write("  " + sectionRule("contracts armed", useColor));
    for (const r of contractsTable(rows, useColor)) write("  " + r);
    write("");
  }

  // 4. Trace tree.
  if (opts.turnSpans.length > 0) {
    write("  " + sectionRule("trace", useColor));
    const sessionStart = opts.turnSpans[0].startTime;
    const activated = new Set<string>(Object.values(aliasMap)); // bare contracts are ACTIVE from step 0
    opts.turnSpans.forEach((turn, i) => {
      const isLast = i === opts.turnSpans.length - 1;
      for (const row of renderTurn(turn, sessionStart, isLast, opts.contracts, aliasMap, activated, useColor)) {
        write("  " + row);
      }
    });
    write("");
  }

  // 5. Verdict banner + summary.
  write(verdictBanner(status, useColor));
  write("");
  write("  " + verdictSummary(counters.blocked, counters.observed, opts.turnSpans.length, useColor));
  write("");

  // 6. Perf line.
  const detPct = totalChecks > 0 ? 100 : 100;
  write("  " + perfLine(totalChecks, detPct, 0, useColor));
  if (lat.length > 0) {
    const sorted = [...lat].sort((a, b) => a - b);
    const p50 = Math.round(quantile(sorted, 0.5));
    const p99 = Math.round(quantile(sorted, 0.99));
    const max_ = Math.round(sorted[sorted.length - 1]);
    write("  " + `p50  ${p50}µs   p99  ${p99}µs   max  ${max_}µs`);
  }
  write("");

  // 7. CTA footer.
  const ctas =
    opts.ctas ??
    (counters.blocked > 0
      ? [`sponsio explain ${Object.values(aliasMap)[0] ?? "C1"}`, `sponsio replay ${sessionId}`]
      : [`sponsio replay ${sessionId}`]);
  write("  " + ctaLine(ctas, useColor));
}
