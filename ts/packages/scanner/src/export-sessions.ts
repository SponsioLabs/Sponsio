/**
 * ``sponsio export-sessions`` — ship session-log events to OTLP file.
 *
 * Mirrors the Python ``sponsio export-sessions`` command's file-output
 * mode. Reads ``~/.sponsio/sessions/<agent>/*.jsonl``, converts each
 * MonitorEvent row into an OTLP span, and writes them as OTLP-JSONL
 * (one resourceSpans-wrapped object per line, ready for ingestion by
 * any OTLP/JSON sink).
 *
 * HTTP POST to a remote OTLP/HTTP endpoint is intentionally not
 * implemented — keep the TS scanner self-contained and avoid the
 * auth/header surface. For HTTP delivery, pipe the JSONL output
 * through curl or use the Python CLI.
 */
import { readFileSync, writeFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { resolve, join } from "node:path";

const HELP =
  "sponsio export-sessions — session log -> OTLP-JSONL file\n" +
  "\n" +
  "USAGE:\n" +
  "  sponsio export-sessions --to <file> [options]\n" +
  "\n" +
  "OPTIONS:\n" +
  "      --to <file>       Output OTLP-JSONL file (required)\n" +
  "      --since <window>  '24h' / '7d' / 'all' (default: 24h)\n" +
  "      --agent <id>      Filter to this agent (default: all agents)\n" +
  "  -h, --help            Show this help\n" +
  "\n" +
  "EXAMPLES:\n" +
  "  sponsio export-sessions --to audit.jsonl\n" +
  "  sponsio export-sessions --since 7d --agent backoffice --to acme-audit.jsonl\n" +
  "  sponsio export-sessions --since all --to full-audit.jsonl\n" +
  "\n" +
  "Tip: pipe the result to curl for OTLP/HTTP delivery, e.g.\n" +
  "  curl -X POST https://otlp.example.com/v1/traces -H 'Content-Type: application/json' --data-binary @audit.jsonl\n";

interface MonitorEvent {
  ts: number;
  agent_id?: string;
  action?: string;
  pipeline?: string;
  constraint?: string;
  result?: { action?: string; message?: string; score?: number };
}

interface OtlpAttr {
  key: string;
  value: { stringValue?: string; intValue?: string; doubleValue?: number; boolValue?: boolean };
}

function attr(key: string, value: unknown): OtlpAttr {
  if (typeof value === "boolean") return { key, value: { boolValue: value } };
  if (typeof value === "number") {
    if (Number.isInteger(value)) return { key, value: { intValue: String(value) } };
    return { key, value: { doubleValue: value } };
  }
  return { key, value: { stringValue: typeof value === "string" ? value : String(value) } };
}

function parseSince(s: string): number | null {
  if (s === "all") return null;
  const m = /^(\d+)\s*(s|m|h|d|w)$/.exec(s.trim());
  if (!m) return null;
  const n = parseInt(m[1], 10);
  const mult = { s: 1, m: 60, h: 3600, d: 86400, w: 604800 }[m[2]] ?? 0;
  return Date.now() / 1000 - n * mult;
}

function eventToSpan(ev: MonitorEvent): unknown {
  const tsNs = BigInt(Math.round((ev.ts ?? 0) * 1e9));
  const endNs = tsNs + 1_000_000n;
  const attrs: OtlpAttr[] = [];
  if (ev.action) attrs.push(attr("sponsio.action", ev.action));
  if (ev.pipeline) attrs.push(attr("sponsio.pipeline", ev.pipeline));
  if (ev.constraint) attrs.push(attr("sponsio.constraint", ev.constraint));
  if (ev.result?.action) attrs.push(attr("sponsio.result", ev.result.action));
  if (ev.result?.message) attrs.push(attr("sponsio.message", ev.result.message));
  if (typeof ev.result?.score === "number") attrs.push(attr("sponsio.score", ev.result.score));
  return {
    resourceSpans: [
      {
        resource: {
          attributes: [attr("service.name", ev.agent_id ?? "agent")],
        },
        scopeSpans: [
          {
            scope: { name: "sponsio" },
            spans: [
              {
                traceId: "0".repeat(32),
                spanId: tsNs.toString(16).padStart(16, "0").slice(0, 16),
                name: ev.action ?? "monitor_event",
                startTimeUnixNano: tsNs.toString(),
                endTimeUnixNano: endNs.toString(),
                status: { code: ev.result?.action === "blocked" ? 2 : 1 },
                attributes: attrs,
              },
            ],
          },
        ],
      },
    ],
  };
}

export async function runExportSessionsCli(argv: string[]): Promise<void> {
  let toFile: string | undefined;
  let since = "24h";
  let agentFilter: string | undefined;

  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "-h" || a === "--help") {
      process.stdout.write(HELP);
      return;
    }
    if (a === "--to") {
      toFile = argv[++i];
      continue;
    }
    if (a === "--since") {
      since = argv[++i];
      continue;
    }
    if (a === "--agent") {
      agentFilter = argv[++i];
      continue;
    }
    if (a.startsWith("-")) {
      process.stderr.write(`unknown flag: ${a}\n${HELP}`);
      process.exit(2);
    }
    process.stderr.write(`unexpected positional: ${a}\n${HELP}`);
    process.exit(2);
  }

  if (!toFile) {
    process.stderr.write(`Error: --to <file> required\n\n${HELP}`);
    process.exit(2);
  }

  const cutoff = parseSince(since);
  if (cutoff === null && since !== "all") {
    process.stderr.write(`Error: --since must be 'all' or a duration like 24h / 7d\n`);
    process.exit(2);
  }

  const root = join(homedir(), ".sponsio", "sessions");
  if (!existsSync(root)) {
    process.stderr.write(`No sessions found under ${root}\n`);
    process.exit(0);
  }

  const lines: string[] = [];
  let totalEvents = 0;
  let agentsScanned = 0;
  for (const agent of readdirSync(root)) {
    if (agentFilter && agent !== agentFilter) continue;
    const dir = join(root, agent);
    if (!statSync(dir).isDirectory()) continue;
    agentsScanned++;
    for (const f of readdirSync(dir)) {
      if (!f.endsWith(".jsonl")) continue;
      const path = join(dir, f);
      const text = readFileSync(path, "utf-8");
      for (const line of text.split(/\r?\n/)) {
        if (!line.trim()) continue;
        let ev: MonitorEvent;
        try {
          ev = JSON.parse(line);
        } catch {
          continue;
        }
        if (cutoff !== null && (ev.ts ?? 0) < cutoff) continue;
        if (!ev.agent_id) ev.agent_id = agent;
        lines.push(JSON.stringify(eventToSpan(ev)));
        totalEvents++;
      }
    }
  }

  writeFileSync(resolve(toFile), lines.join("\n") + (lines.length ? "\n" : ""), "utf-8");
  process.stdout.write(
    `✓ wrote ${totalEvents} OTLP span(s) from ${agentsScanned} agent(s) to ${toFile}\n` +
      `  window: ${since}${agentFilter ? `, agent: ${agentFilter}` : ""}\n`,
  );
}
