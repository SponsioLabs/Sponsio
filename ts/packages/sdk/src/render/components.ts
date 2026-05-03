/**
 * Render primitives — banner / table / trace lines / verdict.
 *
 * Each function takes data and returns the lines to write (already
 * ANSI-styled when ``useColor`` is true). Composition happens in
 * ``session-view.ts``.
 */
import { PALETTE, STATUS, SYMBOLS, ansi, pad, truncate } from "./tokens.js";
import { serviceColor } from "./derive.js";

const RULE_WIDTH = 80;
const ALIAS_WIDTH = 4;
const STATUS_WIDTH = 8;

/** Top banner: ``━━━ ◒◓ Sponsio ━━━ tagline ━━━━━━━━━━``. */
export function headerBanner(useColor: boolean, tagline = "runtime contract enforcement"): string {
  const left = ansi(PALETTE.rule, "━━━ ", useColor);
  const logo = ansi(`1;${PALETTE.brand}`, `${SYMBOLS.logo} Sponsio`, useColor);
  const sep = ansi(PALETTE.rule, " ━━━ ", useColor);
  const tag = ansi(PALETTE.fg, tagline, useColor);
  // Compute fill so the line reaches RULE_WIDTH.
  const visibleLen = `━━━ ${SYMBOLS.logo} Sponsio ━━━ ${tagline} `.length;
  const fillLen = Math.max(0, RULE_WIDTH - visibleLen);
  const fill = ansi(PALETTE.rule, " " + SYMBOLS.ruleHeavy.repeat(fillLen), useColor);
  return `${left}${logo}${sep}${tag}${fill}`;
}

/** Bottom banner: ``━━━ VERDICT ━━━ BLOCKED ━━━━━━━━━━``. */
export function verdictBanner(status: string, useColor: boolean, label = "VERDICT"): string {
  const color = STATUS[status.toUpperCase()] ?? PALETTE.fg;
  const left = ansi(PALETTE.rule, "━━━ ", useColor);
  const lab = ansi(`1;${PALETTE.brand}`, label, useColor);
  const sep = ansi(PALETTE.rule, " ━━━ ", useColor);
  const stat = ansi(`1;${color}`, status.toUpperCase(), useColor);
  const visibleLen = `━━━ ${label} ━━━ ${status.toUpperCase()} `.length;
  const fillLen = Math.max(0, RULE_WIDTH - visibleLen);
  const fill = ansi(PALETTE.rule, " " + SYMBOLS.ruleHeavy.repeat(fillLen), useColor);
  return `${left}${lab}${sep}${stat}${fill}`;
}

/** Inside-zone divider: ``contracts armed ──────────────``. */
export function sectionRule(label: string, useColor: boolean): string {
  const lab = ansi(PALETTE.fg, `${label} `, useColor);
  const visibleLen = label.length + 1;
  const fillLen = Math.max(0, RULE_WIDTH - visibleLen);
  const fill = ansi(PALETTE.rule, SYMBOLS.ruleLight.repeat(fillLen), useColor);
  return `${lab}${fill}`;
}

/** Header metadata grid — three columns × N rows. */
export function headerMeta(pairs: [string, string][], useColor: boolean): string[] {
  const COLS = 3;
  const rows: string[][] = [];
  for (let i = 0; i < pairs.length; i += COLS) {
    rows.push(pairs.slice(i, i + COLS).map(([k, v]) => `${ansi(PALETTE.metadata, pad(k, 9), useColor)}${ansi(PALETTE.fg, v, useColor)}`));
  }
  return rows.map((r) => r.join(ansi("", "    ", useColor)));
}

export function contractsTable(rows: [string, string, string][], useColor: boolean): string[] {
  const out: string[] = [];
  // Compute name column width.
  const nameW = Math.min(56, Math.max(...rows.map(([, n]) => n.length)));
  for (const [alias, name, status] of rows) {
    const aliasCol = ansi(`1;${PALETTE.brand}`, pad(alias, ALIAS_WIDTH), useColor);
    const nameCol = ansi(PALETTE.fg, pad(truncate(name, nameW), nameW), useColor);
    const color = STATUS[status.toUpperCase()] ?? PALETTE.fg;
    const statusCol = ansi(`1;${color}`, status.toUpperCase().padStart(STATUS_WIDTH), useColor);
    out.push(`${aliasCol}  ${nameCol}  ${statusCol}`);
  }
  return out;
}

export interface TraceRow {
  /** Indent prefix already including any ``├─`` / ``└─`` / ``│`` leaders. */
  prefix: string;
  /** Body text already ansi-styled. */
  body: string;
}

export function eventLine(
  tsLabel: string,
  toolName: string,
  service: string,
  argsSummary: string,
  latency: string,
  isLast: boolean,
  useColor: boolean,
): string {
  const branch = isLast ? SYMBOLS.branchL : SYMBOLS.branchT;
  const ts = ansi(PALETTE.metadata, pad(tsLabel, 7), useColor);
  const br = ansi(PALETTE.metadata, branch, useColor);
  const tool = ansi(PALETTE.fg, toolName, useColor);
  const summary = argsSummary
    ? `(${ansi(PALETTE.metadata, argsSummary, useColor)})`
    : "()";
  const lat = latency ? `  ${ansi(PALETTE.metadata, latency, useColor)}` : "";
  const svc = service ? `   ${ansi(serviceColor(service), service, useColor)}` : "";
  return `${ts} ${br} ${tool}${summary}${lat}${svc}`;
}

export function assumeLine(alias: string, summary: string, useColor: boolean): string {
  const indent = "         ";
  const pipe = ansi(PALETTE.metadata, SYMBOLS.branchPipe, useColor);
  const tag = ansi(`1;${PALETTE.brand}`, alias, useColor);
  const text = ansi(PALETTE.fg, summary, useColor);
  return `${indent}${pipe} ${SYMBOLS.gear} assume[${tag}] ${text} ${ansi(PALETTE.ok, SYMBOLS.check, useColor)}`;
}

export function stateTransitionLine(alias: string, newState: string, useColor: boolean): string {
  const indent = "         ";
  const pipe = ansi(PALETTE.metadata, SYMBOLS.branchPipe, useColor);
  const tag = ansi(`1;${PALETTE.brand}`, alias, useColor);
  const color = STATUS[newState.toUpperCase()] ?? PALETTE.fg;
  return `${indent}${pipe}    contract ${tag} ${SYMBOLS.arrowR} ${ansi(`1;${color}`, newState.toUpperCase(), useColor)}`;
}

export function enforceViolationLine(
  alias: string,
  summary: string,
  status: string,
  useColor: boolean,
): string {
  const indent = "         ";
  const pipe = ansi(PALETTE.metadata, SYMBOLS.branchPipe, useColor);
  const cross = ansi(PALETTE.blocked, SYMBOLS.cross, useColor);
  const tag = ansi(`1;${PALETTE.brand}`, alias, useColor);
  const color = STATUS[status.toUpperCase()] ?? PALETTE.blocked;
  const stat = ansi(`1;${color}`, status.toUpperCase(), useColor);
  return `${indent}${pipe}    ${cross} enforce[${tag}] ${ansi(PALETTE.fg, truncate(summary, 50), useColor)} ${stat}`;
}

export function verdictSummary(
  blocked: number,
  warnings: number,
  totalTurns: number,
  useColor: boolean,
): string {
  const head =
    blocked > 0
      ? ansi(PALETTE.blocked, `${blocked} action${blocked === 1 ? "" : "s"} stopped pre-execution`, useColor)
      : warnings > 0
        ? ansi(PALETTE.warn, `${warnings} warning${warnings === 1 ? "" : "s"} (observe mode)`, useColor)
        : ansi(PALETTE.ok, `clean — no policy violations across ${totalTurns} turn${totalTurns === 1 ? "" : "s"}`, useColor);
  const tail = `${blocked} violation${blocked === 1 ? "" : "s"} ${ansi(PALETTE.metadata, SYMBOLS.bullet, useColor)} ${warnings} warning${warnings === 1 ? "" : "s"}`;
  return `${head}  ${ansi(PALETTE.metadata, SYMBOLS.bullet, useColor)} ${ansi(PALETTE.metadata, tail, useColor)}`;
}

export function perfLine(totalChecks: number, detPct: number, llmCalls: number, useColor: boolean): string {
  const pct = `${detPct.toFixed(0)}%`;
  return [
    `${ansi(PALETTE.metadata, "checks", useColor)}        ${ansi(PALETTE.fg, String(totalChecks), useColor)}`,
    `${ansi(PALETTE.metadata, "deterministic", useColor)} ${ansi(PALETTE.fg, pct, useColor)}`,
    `${ansi(PALETTE.metadata, "LLM calls", useColor)}     ${ansi(PALETTE.fg, String(llmCalls), useColor)}`,
  ].join("    ");
}

export function ctaLine(ctas: string[], useColor: boolean): string {
  return ctas.map((c) => `${ansi(PALETTE.metadata, SYMBOLS.arrowR, useColor)} ${ansi(PALETTE.fg, c, useColor)}`).join("     ");
}
