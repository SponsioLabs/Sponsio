/**
 * ``sponsio replay`` — re-render a recorded session log.
 *
 * Mirrors the Python ``sponsio replay`` UX: take a session id, file
 * stem, or direct path; read ``~/.sponsio/sessions/<agent>/*.jsonl``;
 * pretty-print each event in chronological order. Also supports
 * ``--list`` to browse available sessions.
 *
 * The TS implementation skips Python's AgentTurnSpan reconstruction
 * (that infra lives only on the Python side); instead it produces a
 * flat per-event view that's still readable and covers the audit-trail
 * use case.
 */
import { readdirSync, readFileSync, existsSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join, isAbsolute, basename } from "node:path";

const HELP =
  "sponsio replay — re-render a recorded session\n" +
  "\n" +
  "USAGE:\n" +
  "  sponsio replay [session] [options]\n" +
  "\n" +
  "ARGUMENTS:\n" +
  "  [session]    short id, filename stem, or direct path to a jsonl log\n" +
  "\n" +
  "OPTIONS:\n" +
  "      --list         List available sessions and exit\n" +
  "      --agent <id>   Override the agent id derived from the session path\n" +
  "      --json         Emit raw event JSON (one per line) instead of pretty\n" +
  "  -h, --help         Show this help\n" +
  "\n" +
  "EXAMPLES:\n" +
  "  sponsio replay --list\n" +
  "  sponsio replay 20260501_002216_83674\n" +
  "  sponsio replay /path/to/log.jsonl\n";

interface SessionEvent {
  ts?: number;
  agent_id?: string;
  action?: string;
  pipeline?: string;
  constraint?: string;
  result?: { action?: string; message?: string; score?: number };
}

interface SessionFile {
  agent: string;
  stem: string;
  path: string;
  mtime: number;
  size: number;
}

function sessionsRoot(): string {
  return join(homedir(), ".sponsio", "sessions");
}

function listSessions(): SessionFile[] {
  const root = sessionsRoot();
  if (!existsSync(root)) return [];
  const out: SessionFile[] = [];
  for (const agent of readdirSync(root)) {
    const dir = join(root, agent);
    if (!statSync(dir).isDirectory()) continue;
    for (const f of readdirSync(dir)) {
      if (!f.endsWith(".jsonl")) continue;
      const path = join(dir, f);
      const st = statSync(path);
      out.push({ agent, stem: f.replace(/\.jsonl$/, ""), path, mtime: st.mtimeMs, size: st.size });
    }
  }
  out.sort((a, b) => b.mtime - a.mtime);
  return out;
}

function resolveSession(query: string): SessionFile | null {
  if (existsSync(query)) {
    const path = isAbsolute(query) ? query : join(process.cwd(), query);
    const st = statSync(path);
    return {
      agent: basename(join(path, "..")),
      stem: basename(path).replace(/\.jsonl$/, ""),
      path,
      mtime: st.mtimeMs,
      size: st.size,
    };
  }
  const all = listSessions();
  const exact = all.find((s) => s.stem === query);
  if (exact) return exact;
  const prefix = all.filter((s) => s.stem.startsWith(query));
  if (prefix.length === 1) return prefix[0];
  if (prefix.length > 1) {
    process.stderr.write(`ambiguous session id '${query}' — matches:\n`);
    for (const s of prefix.slice(0, 5)) process.stderr.write(`  ${s.agent}/${s.stem}\n`);
    process.exit(2);
  }
  return null;
}

function fmtTs(ts: number | undefined): string {
  if (typeof ts !== "number") return "?".padEnd(19);
  return new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 19);
}

function decorate(action: string): string {
  switch (action.toLowerCase()) {
    case "blocked":
      return `\x1b[31m${action.toUpperCase()}\x1b[0m`;
    case "would-block":
    case "would_block":
      return `\x1b[33m${action.toUpperCase()}\x1b[0m`;
    case "allowed":
    case "passed":
      return `\x1b[32m${action.toLowerCase()}\x1b[0m`;
    default:
      return action;
  }
}

export async function runReplayCli(argv: string[]): Promise<void> {
  let listOnly = false;
  let asJson = false;
  let agentOverride: string | undefined;
  let session: string | null = null;

  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "-h" || a === "--help") {
      process.stdout.write(HELP);
      return;
    }
    if (a === "--list") {
      listOnly = true;
      continue;
    }
    if (a === "--json") {
      asJson = true;
      continue;
    }
    if (a === "--agent") {
      agentOverride = argv[++i];
      continue;
    }
    if (a.startsWith("-")) {
      process.stderr.write(`unknown flag: ${a}\n${HELP}`);
      process.exit(2);
    }
    if (session !== null) {
      process.stderr.write(`extra positional argument: ${a}\n${HELP}`);
      process.exit(2);
    }
    session = a;
  }

  if (listOnly) {
    const all = listSessions();
    if (all.length === 0) {
      process.stdout.write("(no sessions in ~/.sponsio/sessions)\n");
      return;
    }
    process.stdout.write(`Found ${all.length} session(s):\n\n`);
    for (const s of all.slice(0, 50)) {
      const dt = new Date(s.mtime).toISOString().slice(0, 19).replace("T", " ");
      const kb = (s.size / 1024).toFixed(1);
      process.stdout.write(`  ${dt}  ${s.agent.padEnd(20)} ${s.stem}  (${kb} KB)\n`);
    }
    if (all.length > 50) process.stdout.write(`  … +${all.length - 50} more\n`);
    return;
  }

  if (!session) {
    process.stderr.write(`missing session id (or pass --list)\n\n${HELP}`);
    process.exit(2);
  }

  const file = resolveSession(session);
  if (!file) {
    process.stderr.write(`Error: no session matched '${session}'. Try --list.\n`);
    process.exit(2);
  }

  const text = readFileSync(file.path, "utf-8");
  const lines = text.split(/\r?\n/).filter(Boolean);

  if (asJson) {
    for (const line of lines) process.stdout.write(line + "\n");
    return;
  }

  const agent = agentOverride ?? file.agent;
  process.stdout.write(`Session: ${file.stem}\n`);
  process.stdout.write(`Agent:   ${agent}\n`);
  process.stdout.write(`Source:  ${file.path}\n`);
  process.stdout.write(`Events:  ${lines.length}\n\n`);

  for (const line of lines) {
    let ev: SessionEvent;
    try {
      ev = JSON.parse(line);
    } catch {
      continue;
    }
    const ts = fmtTs(ev.ts);
    const pipeline = (ev.pipeline ?? "?").padEnd(3);
    const action = decorate(ev.result?.action ?? "?");
    const tool = ev.action ?? "?";
    const constraint = ev.constraint ?? "";
    const score = typeof ev.result?.score === "number" ? ` score=${ev.result.score.toFixed(2)}` : "";
    process.stdout.write(`  ${ts}  ${pipeline}  ${action}  ${tool}${score}\n`);
    if (constraint) process.stdout.write(`    └─ ${constraint}\n`);
  }
}
