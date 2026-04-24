/**
 * ``sponsio-scan-ts report`` — minimal shadow-mode report for TS projects.
 *
 * Reads ``~/.sponsio/sessions/<agent_id>/*.jsonl`` (the shared
 * session log written by both the Python and TypeScript runtimes)
 * and prints a markdown summary of blocks / would-have-blocks over
 * a time window.  Keeps the TypeScript-first track self-contained
 * so ``npm`` users can answer "what did my agent try to do yesterday?"
 * without first installing the Python CLI.
 *
 * Usage:
 *
 *   sponsio-scan-ts report                              # last 7d, all agents
 *   sponsio-scan-ts report --agent support_bot --since 24h
 *   sponsio-scan-ts report --since all --format json
 *
 * Accepts both Python-canonical action values (``blocked`` / ``observed``
 * / ``allowed``) and the TS logger's shorthand (``block`` / ``observe_log``
 * / ``allow``).  Files with unknown shapes are skipped silently.
 */

import { promises as fs } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const SINCE_RE = /^(\d+)([smhd])$/;

interface ReportEvent {
  ts: number;
  agentId: string;
  constraint: string;
  action: "blocked" | "observed" | "allowed" | "other";
  message: string;
}

interface AgentSummary {
  agentId: string;
  total: number;
  blocks: number;
  observed: number;
  allowed: number;
  /** Violations grouped by constraint desc, newest first. */
  perConstraint: Array<{
    constraint: string;
    blocked: number;
    observed: number;
    latestMessage: string;
    latestTs: number;
  }>;
}

interface ReportArgs {
  since: string;
  agent?: string;
  baseDir?: string;
  format: "markdown" | "json";
  help: boolean;
}

const HELP =
  [
    "sponsio-scan-ts report — shadow-mode summary of session logs",
    "",
    "USAGE:",
    "  sponsio-scan-ts report [options]",
    "",
    "OPTIONS:",
    "      --since <spec>    Time window (default: 7d). Accepts 'all', '30m', '24h', '7d'.",
    "      --agent <id>      Only include this agent_id",
    "      --base-dir <path> Override session log dir (default: ~/.sponsio/sessions)",
    "      --format <fmt>    'markdown' (default) or 'json'",
    "  -h, --help            Show this help",
    "",
    "EXAMPLES:",
    "  sponsio-scan-ts report",
    "  sponsio-scan-ts report --since 24h --agent support_bot",
    "  sponsio-scan-ts report --since all --format json",
  ].join("\n") + "\n";

function parseArgs(argv: string[]): ReportArgs {
  const a: ReportArgs = { since: "7d", format: "markdown", help: false };
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === "-h" || flag === "--help") a.help = true;
    else if (flag === "--since") a.since = argv[++i];
    else if (flag === "--agent") a.agent = argv[++i];
    else if (flag === "--base-dir") a.baseDir = argv[++i];
    else if (flag === "--format") {
      const v = argv[++i];
      if (v !== "markdown" && v !== "json") {
        throw new Error(`--format must be 'markdown' or 'json', got ${v}`);
      }
      a.format = v;
    } else {
      throw new Error(`unknown flag: ${flag}`);
    }
  }
  return a;
}

function parseSince(spec: string, now: number = Date.now() / 1000): number {
  if (!spec || spec.toLowerCase() === "all") return 0;
  const m = SINCE_RE.exec(spec.trim().toLowerCase());
  if (!m) {
    throw new Error(
      `Invalid --since value: ${spec}. Expected 'all' or a duration like '30m', '24h', '7d'.`,
    );
  }
  const n = parseInt(m[1], 10);
  const unit = m[2];
  const mult = unit === "s" ? 1 : unit === "m" ? 60 : unit === "h" ? 3600 : 86400;
  return now - n * mult;
}

function normalizeAction(raw: unknown): ReportEvent["action"] {
  if (typeof raw !== "string") return "other";
  // Accept both Python canonical values and the TS shorthand so the
  // same JSONL works against either CLI.
  switch (raw) {
    case "blocked":
    case "block":
      return "blocked";
    case "observed":
    case "observe_log":
      return "observed";
    case "allowed":
    case "allow":
    case "warned":
      return "allowed";
    default:
      return "other";
  }
}

async function* walkSessionFiles(
  base: string,
  agent: string | undefined,
): AsyncGenerator<string> {
  let agentDirs: string[];
  try {
    agentDirs = await fs.readdir(base);
  } catch {
    return;
  }
  for (const dir of agentDirs) {
    if (agent && dir !== agent) continue;
    const full = join(base, dir);
    let inner: string[];
    try {
      const st = await fs.stat(full);
      if (!st.isDirectory()) continue;
      inner = await fs.readdir(full);
    } catch {
      continue;
    }
    for (const f of inner) {
      if (f.endsWith(".jsonl")) yield join(full, f);
    }
  }
}

async function loadEvents(
  base: string,
  agent: string | undefined,
  sinceTs: number,
): Promise<ReportEvent[]> {
  const out: ReportEvent[] = [];
  for await (const path of walkSessionFiles(base, agent)) {
    let text: string;
    try {
      text = await fs.readFile(path, "utf8");
    } catch {
      continue;
    }
    for (const line of text.split("\n")) {
      if (!line.trim()) continue;
      let rec: Record<string, unknown>;
      try {
        rec = JSON.parse(line);
      } catch {
        continue;
      }
      const ts = Number(rec.ts ?? 0);
      if (!Number.isFinite(ts) || ts < sinceTs) continue;
      const result = (rec.result ?? {}) as Record<string, unknown>;
      out.push({
        ts,
        agentId: String(rec.agent_id ?? ""),
        constraint: String(rec.constraint ?? ""),
        action: normalizeAction(result.action),
        message: String(result.message ?? ""),
      });
    }
  }
  return out;
}

function aggregate(events: ReportEvent[]): AgentSummary[] {
  const byAgent = new Map<string, ReportEvent[]>();
  for (const e of events) {
    const list = byAgent.get(e.agentId) ?? [];
    list.push(e);
    byAgent.set(e.agentId, list);
  }
  const summaries: AgentSummary[] = [];
  for (const [agentId, evs] of byAgent) {
    let blocks = 0;
    let observed = 0;
    let allowed = 0;
    const perConstraint = new Map<
      string,
      {
        constraint: string;
        blocked: number;
        observed: number;
        latestMessage: string;
        latestTs: number;
      }
    >();
    for (const e of evs) {
      if (e.action === "blocked") blocks++;
      else if (e.action === "observed") observed++;
      else if (e.action === "allowed") allowed++;

      if (e.action === "blocked" || e.action === "observed") {
        const key = e.constraint || "(unknown)";
        const row = perConstraint.get(key) ?? {
          constraint: key,
          blocked: 0,
          observed: 0,
          latestMessage: "",
          latestTs: 0,
        };
        if (e.action === "blocked") row.blocked++;
        else row.observed++;
        if (e.ts > row.latestTs) {
          row.latestTs = e.ts;
          row.latestMessage = e.message;
        }
        perConstraint.set(key, row);
      }
    }
    summaries.push({
      agentId,
      total: evs.length,
      blocks,
      observed,
      allowed,
      perConstraint: [...perConstraint.values()].sort(
        (a, b) => b.latestTs - a.latestTs,
      ),
    });
  }
  return summaries.sort((a, b) => a.agentId.localeCompare(b.agentId));
}

function renderMarkdown(summaries: AgentSummary[], since: string): string {
  if (summaries.length === 0) {
    return `# Sponsio report\n\nNo session events in window (--since ${since}).\n`;
  }
  const lines: string[] = [];
  lines.push(`# Sponsio report`);
  lines.push(`_Window: last ${since} · ${summaries.length} agent(s)_`);
  lines.push("");
  for (const s of summaries) {
    lines.push(`## ${s.agentId}`);
    lines.push(
      `- total events: **${s.total}** (allowed ${s.allowed} · ` +
        `would-block ${s.observed} · blocked ${s.blocks})`,
    );
    if (s.perConstraint.length === 0) {
      lines.push(`- no violations in window`);
    } else {
      lines.push(`- violations by contract:`);
      for (const c of s.perConstraint) {
        const parts: string[] = [];
        if (c.blocked) parts.push(`${c.blocked} blocked`);
        if (c.observed) parts.push(`${c.observed} would-block`);
        lines.push(`  - \`${c.constraint}\` — ${parts.join(" · ")}`);
        if (c.latestMessage) {
          lines.push(`    latest: ${c.latestMessage}`);
        }
      }
    }
    lines.push("");
  }
  return lines.join("\n");
}

function renderJson(summaries: AgentSummary[]): string {
  return JSON.stringify({ agents: summaries }, null, 2) + "\n";
}

export async function runReportCli(argv: string[]): Promise<void> {
  let args: ReportArgs;
  try {
    args = parseArgs(argv);
  } catch (err) {
    process.stderr.write(
      `${err instanceof Error ? err.message : String(err)}\n`,
    );
    process.exit(2);
  }
  if (args.help) {
    process.stdout.write(HELP);
    return;
  }
  const sinceTs = parseSince(args.since);
  const base = args.baseDir ?? join(homedir(), ".sponsio", "sessions");
  const events = await loadEvents(base, args.agent, sinceTs);
  const summaries = aggregate(events);
  const out =
    args.format === "json"
      ? renderJson(summaries)
      : renderMarkdown(summaries, args.since);
  process.stdout.write(out);
}

// Exported for tests.
export const _internals = {
  parseSince,
  normalizeAction,
  aggregate,
  renderMarkdown,
  renderJson,
};
