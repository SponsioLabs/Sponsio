/**
 * Tokens shared across the render layer — palette, symbols, status
 * → color mapping. Mirrors ``sponsio/render/tokens.py`` enough to
 * keep the visual identity aligned across languages.
 *
 * ANSI codes are emitted directly (no Rich-equivalent dependency).
 * Output is ANSI-stripped automatically when stderr/stdout isn't a
 * TTY (handled in components.ts via the `useColor` flag).
 */

export const PALETTE = {
  brand: "36", // cyan
  rule: "2;36", // dim cyan
  fg: "39", // default foreground
  metadata: "2", // dim
  ok: "32", // green
  blocked: "31", // red
  warn: "33", // yellow
  active: "32", // green
  ready: "33", // yellow
  inactive: "2", // dim
};

export const STATUS = {
  ACTIVE: PALETTE.active,
  READY: PALETTE.ready,
  BLOCKED: PALETTE.blocked,
  "WOULD-BLOCK": PALETTE.warn,
  OBSERVED: PALETTE.warn,
  ALLOWED: PALETTE.ok,
  PASSED: PALETTE.ok,
  ERROR: PALETTE.warn,
} as Record<string, string>;

export const SYMBOLS = {
  logo: "◒◓",
  ruleHeavy: "━",
  ruleLight: "─",
  branchT: "├─",
  branchL: "└─",
  branchPipe: "│",
  branchEmpty: " ",
  bullet: "•",
  check: "✓",
  cross: "✗",
  arrowL: "←",
  arrowR: "→",
  gear: "⚙",
};

export function ansi(code: string, text: string, useColor: boolean): string {
  if (!useColor || !code) return text;
  return `\x1b[${code}m${text}\x1b[0m`;
}

export function stderrUseColor(): boolean {
  if (process.env.SPONSIO_NO_COLOR) return false;
  if (process.env.NO_COLOR) return false;
  if (process.env.FORCE_COLOR) return true;
  return !!(process.stderr as unknown as { isTTY?: boolean }).isTTY;
}

/** Truncate to ``max`` chars with an ellipsis when too long. */
export function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return s.slice(0, Math.max(0, max - 1)) + "…";
}

/** Right-pad a plain string to ``width`` (visible width — no ANSI). */
export function pad(s: string, width: number): string {
  if (s.length >= width) return s;
  return s + " ".repeat(width - s.length);
}
