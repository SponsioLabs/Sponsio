/**
 * ``sponsio check`` — replay a trace file through the SDK and report
 * which contracts would have blocked.
 *
 * Mirrors the Python ``sponsio check`` command. Loads a trace from
 * disk, builds a ``Sponsio`` guard from inline NL contracts or a
 * sponsio.yaml config, replays each event through ``guardBefore``,
 * and prints a per-contract pass/fail summary.
 *
 * Supported trace formats (sniffed from content, like Python):
 *   - Native single-file JSON: ``{"events": [{"action": "...", "args": {...}}, ...]}``
 *   - Native event JSONL: one ``{"action": "...", "args": {...}}`` per line
 *   - Session-log JSONL: one MonitorEvent per line (decisions, not raw
 *     events — used to extract the underlying tool calls)
 *
 * OTLP/JSON is not yet supported; pipe through Python's
 * ``sponsio export`` if you have OTLP traces.
 */
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import * as yaml from "js-yaml";
import { extractOtlpEvents, isOtlpPayload } from "./otlp";

const HELP =
  "sponsio check — replay a trace file through the engine, report violations\n" +
  "\n" +
  "USAGE:\n" +
  "  sponsio check --trace <file> [contracts...] [options]\n" +
  "\n" +
  "OPTIONS:\n" +
  "  -t, --trace <file>     Trace file (.json or .jsonl) — required\n" +
  "  -c, --config <file>    sponsio.yaml; loads contracts + agent id\n" +
  "  -a, --agent <id>       Agent id (with --config); defaults to single-agent\n" +
  "      --json             Output as JSON\n" +
  "  -h, --help             Show this help\n" +
  "\n" +
  "Pass either positional NL contract strings or --config, not both.\n" +
  "\n" +
  "EXAMPLES:\n" +
  "  sponsio check -t trace.json \"tool `A` must precede `B`\"\n" +
  "  sponsio check -t run.jsonl --config sponsio.yaml --agent bot\n" +
  "  sponsio check -t trace.json --config sponsio.yaml --json\n";

interface RawEvent {
  tool: string;
  args?: Record<string, unknown>;
}

interface SdkLike {
  guardBefore: (
    tool: string,
    args?: Record<string, unknown>,
  ) => { blocked: boolean; message?: string; violatedDescs?: string[]; detViolations?: { desc: string }[] };
  contractDescs: () => string[];
}

function sniffEvents(text: string, path: string): RawEvent[] {
  const trimmed = text.trimStart();
  if (trimmed.startsWith("{")) {
    let data: unknown;
    try {
      data = JSON.parse(text);
    } catch (e) {
      throw new Error(`[check] cannot parse ${path} as JSON: ${(e as Error).message}`);
    }
    if (isOtlpPayload(data)) {
      return extractOtlpEvents(data);
    }
    if (data && typeof data === "object" && Array.isArray((data as { events?: unknown }).events)) {
      return ((data as { events: unknown[] }).events).map(coerceEvent).filter(Boolean) as RawEvent[];
    }
    throw new Error(`[check] ${path}: expected {"events": [...]} or {"resourceSpans": [...]}`);
  }
  if (trimmed.startsWith("[")) {
    let data: unknown;
    try {
      data = JSON.parse(text);
    } catch (e) {
      throw new Error(`[check] cannot parse ${path} as JSON array: ${(e as Error).message}`);
    }
    if (!Array.isArray(data)) throw new Error(`[check] ${path}: expected JSON array`);
    return (data as unknown[]).map(coerceEvent).filter(Boolean) as RawEvent[];
  }
  // JSONL
  const out: RawEvent[] = [];
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) continue;
    try {
      const obj = JSON.parse(line);
      const ev = coerceEvent(obj);
      if (ev) out.push(ev);
    } catch {
      // skip malformed line
    }
  }
  return out;
}

function coerceEvent(raw: unknown): RawEvent | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  // Session-log shape: pipeline + result + action — extract tool from action.
  // Native shape: action: <tool>, args: {...}
  // ToolEvent (TS SDK) shape: tool: <name>, args: {...}
  const tool = (r.tool as string) ?? (r.action as string);
  if (typeof tool !== "string") return null;
  const args = (r.args as Record<string, unknown>) ?? {};
  return { tool, args };
}

interface ContractStat {
  desc: string;
  blockedCount: number;
  firstBlockAt?: number;
}

async function loadGuard(opts: {
  inline: string[];
  configPath?: string;
  agentId?: string;
}): Promise<{ guard: SdkLike; contractDescs: string[] }> {
  // Lazy require so the scanner CLI works even when @sponsio/sdk isn't
  // installed in the user's project for non-check commands.
  const mod = await import("@sponsio/sdk");
  const Sponsio = mod.Sponsio;
  if (!Sponsio) throw new Error("[check] @sponsio/sdk does not export Sponsio — upgrade the SDK.");

  const ctorOpts: Record<string, unknown> = {
    mode: "enforce",
    sessionLog: false,
  };
  if (opts.configPath) {
    const cfgAbs = resolve(opts.configPath);
    ctorOpts.config = cfgAbs;
    if (opts.agentId) {
      ctorOpts.agentId = opts.agentId;
    } else {
      // Auto-select when the yaml has exactly one agent — matches the
      // Python `sponsio check` UX. Falls back to the SDK's default
      // ("agent") if the yaml is missing or malformed.
      try {
        const data = yaml.load(readFileSync(cfgAbs, "utf-8")) as Record<string, unknown>;
        const agents = data?.agents as Record<string, unknown> | undefined;
        const ids = agents ? Object.keys(agents) : [];
        if (ids.length === 1) ctorOpts.agentId = ids[0];
      } catch {
        // ignore — let the SDK ctor surface the real error
      }
    }
  } else {
    ctorOpts.agentId = "check";
    ctorOpts.contracts = opts.inline;
  }
  const guard = new (Sponsio as new (o: Record<string, unknown>) => SdkLike)(ctorOpts);
  return { guard, contractDescs: guard.contractDescs() };
}

export async function runCheckCli(argv: string[]): Promise<void> {
  let tracePath: string | undefined;
  let configPath: string | undefined;
  let agentId: string | undefined;
  let asJson = false;
  const inline: string[] = [];

  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "-h" || a === "--help") {
      process.stdout.write(HELP);
      return;
    }
    if (a === "-t" || a === "--trace") {
      tracePath = argv[++i];
      continue;
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
    if (a.startsWith("-")) {
      process.stderr.write(`unknown flag: ${a}\n${HELP}`);
      process.exit(2);
    }
    inline.push(a);
  }

  if (!tracePath) {
    process.stderr.write(`Error: --trace is required\n\n${HELP}`);
    process.exit(2);
  }
  if (configPath && inline.length > 0) {
    process.stderr.write(`Error: cannot use both --config and positional contracts\n`);
    process.exit(2);
  }
  if (!configPath && inline.length === 0) {
    process.stderr.write(`Error: pass --config or at least one positional contract\n\n${HELP}`);
    process.exit(2);
  }
  if (agentId && !configPath) {
    process.stderr.write(`Error: --agent requires --config\n`);
    process.exit(2);
  }

  if (!existsSync(resolve(tracePath))) {
    process.stderr.write(`Error: trace file ${tracePath} does not exist\n`);
    process.exit(1);
  }
  const text = readFileSync(resolve(tracePath), "utf-8");
  const events = sniffEvents(text, tracePath);
  if (events.length === 0) {
    process.stderr.write(`Warning: trace is empty (no events extracted)\n`);
    process.exit(0);
  }

  const { guard, contractDescs } = await loadGuard({ inline, configPath, agentId });
  const stats: Map<string, ContractStat> = new Map();
  for (const d of contractDescs) stats.set(d, { desc: d, blockedCount: 0 });

  let totalBlocks = 0;
  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    const r = guard.guardBefore(ev.tool, ev.args ?? {});
    if (r.blocked) {
      totalBlocks++;
      const descs = (r.violatedDescs ?? r.detViolations?.map((v) => v.desc) ?? []);
      for (const d of descs) {
        const s = stats.get(d) ?? { desc: d, blockedCount: 0 };
        if (s.blockedCount === 0) s.firstBlockAt = i;
        s.blockedCount += 1;
        stats.set(d, s);
      }
    }
  }

  if (asJson) {
    process.stdout.write(
      JSON.stringify(
        {
          trace: tracePath,
          events: events.length,
          totalBlocks,
          perContract: Array.from(stats.values()),
        },
        null,
        2,
      ) + "\n",
    );
    return;
  }

  process.stdout.write(`Trace:    ${tracePath}\n`);
  process.stdout.write(`Events:   ${events.length}\n`);
  process.stdout.write(`Contracts: ${contractDescs.length}\n`);
  process.stdout.write(`Blocks:   ${totalBlocks}\n\n`);
  if (stats.size === 0) {
    process.stdout.write("(no contracts compiled)\n");
    return;
  }
  process.stdout.write(`Per-contract:\n`);
  for (const s of stats.values()) {
    const tag = s.blockedCount === 0 ? "\x1b[32mPASS\x1b[0m" : "\x1b[31mFAIL\x1b[0m";
    process.stdout.write(`  ${tag}  ${s.desc.split("\n")[0]}\n`);
    if (s.blockedCount > 0) {
      process.stdout.write(`        first block at event #${s.firstBlockAt}, total ${s.blockedCount}\n`);
    }
  }
}
