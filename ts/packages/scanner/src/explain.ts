/**
 * ``sponsio explain`` — explain a contract: source, last block, hints.
 *
 * Mirrors the Python ``sponsio explain`` command. Resolves a query
 * (``Cn`` alias or desc substring) against the yaml's contracts and
 * prints a structured summary plus the most recent session-log event
 * for that contract.
 */
import { readFileSync, existsSync, readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { resolve, join } from "node:path";
import * as yaml from "js-yaml";

const HELP =
  "sponsio explain — explain a contract: source, last violation, hints\n" +
  "\n" +
  "USAGE:\n" +
  "  sponsio explain <query> [options]\n" +
  "\n" +
  "ARGUMENTS:\n" +
  "  <query>    Either an alias (e.g. C1) or a substring of the desc/E text\n" +
  "\n" +
  "OPTIONS:\n" +
  "  -c, --config <file>   Path to sponsio.yaml (default: ./sponsio.yaml or $SPONSIO_CONFIG)\n" +
  "  -a, --agent <id>      Agent id (required if yaml has multiple agents)\n" +
  "      --format <f>      text | json (default: text)\n" +
  "  -h, --help            Show this help\n" +
  "\n" +
  "EXAMPLES:\n" +
  "  sponsio explain C1\n" +
  "  sponsio explain \"verify_vendor\"\n" +
  "  sponsio explain C2 --format json\n";

interface ContractEntry {
  E?: string | Record<string, unknown>;
  A?: string | Record<string, unknown>;
  desc?: string;
}

interface SessionEvent {
  ts: number;
  agent_id: string;
  action: string;
  pipeline: string;
  constraint?: string;
  result: { action: string; message: string };
}

function resolveConfigPath(explicit?: string): string {
  if (explicit) return resolve(explicit);
  if (process.env.SPONSIO_CONFIG) return resolve(process.env.SPONSIO_CONFIG);
  const cwd = resolve("sponsio.yaml");
  if (existsSync(cwd)) return cwd;
  process.stderr.write("Error: no config found. Pass --config or create ./sponsio.yaml.\n");
  process.exit(2);
}

function loadAgents(configPath: string): Record<string, { contracts?: ContractEntry[] }> {
  const text = readFileSync(configPath, "utf-8");
  const data = yaml.load(text) as Record<string, unknown>;
  return (data.agents as Record<string, { contracts?: ContractEntry[] }>) ?? {};
}

function resolveContract(
  query: string,
  contracts: ContractEntry[],
): { contract: ContractEntry; idx: number } | null {
  const aliasMatch = /^C(\d+)$/.exec(query);
  if (aliasMatch) {
    const i = parseInt(aliasMatch[1], 10) - 1;
    if (i >= 0 && i < contracts.length) return { contract: contracts[i], idx: i };
    return null;
  }
  const lower = query.toLowerCase();
  for (let i = 0; i < contracts.length; i++) {
    const c = contracts[i];
    const haystack = (
      (c.desc ?? "") +
      " " +
      (typeof c.E === "string" ? c.E : JSON.stringify(c.E ?? "")) +
      " " +
      (typeof c.A === "string" ? c.A : JSON.stringify(c.A ?? ""))
    ).toLowerCase();
    if (haystack.includes(lower)) return { contract: c, idx: i };
  }
  return null;
}

function summariseE(c: ContractEntry): string {
  if (typeof c.E === "string") return c.E;
  if (c.E && typeof c.E === "object") {
    const e = c.E as Record<string, unknown>;
    if (typeof e.pattern === "string") {
      const args = Array.isArray(e.args) ? e.args.join(", ") : "";
      return `${e.pattern}(${args})`;
    }
    return JSON.stringify(e);
  }
  return "(no E)";
}

function findLastEvent(agentId: string, contractText: string): SessionEvent | null {
  const dir = join(homedir(), ".sponsio", "sessions", agentId);
  if (!existsSync(dir)) return null;
  const files = readdirSync(dir)
    .filter((f) => f.endsWith(".jsonl"))
    .map((f) => ({ f, mtime: statSync(join(dir, f)).mtimeMs }))
    .sort((a, b) => b.mtime - a.mtime);
  const needle = contractText.toLowerCase();
  for (const { f } of files) {
    const text = readFileSync(join(dir, f), "utf-8");
    const lines = text.split(/\r?\n/).filter(Boolean);
    for (let i = lines.length - 1; i >= 0; i--) {
      try {
        const ev = JSON.parse(lines[i]) as SessionEvent;
        if ((ev.constraint ?? "").toLowerCase().includes(needle)) return ev;
      } catch {
        // skip malformed line
      }
    }
  }
  return null;
}

function renderText(args: {
  agentId: string;
  configPath: string;
  contract: ContractEntry;
  idx: number;
  last: SessionEvent | null;
  total: number;
}) {
  const { agentId, configPath, contract, idx, last, total } = args;
  const lines: string[] = [];
  lines.push(`Contract C${idx + 1} of ${total} (agent: ${agentId})`);
  lines.push(`Source: ${configPath}`);
  if (contract.desc) lines.push(`\nDesc:\n  ${contract.desc.split("\n").join("\n  ")}`);
  lines.push(`\nEnforcement (E):`);
  lines.push(`  ${summariseE(contract)}`);
  if (contract.A !== undefined) {
    lines.push(`\nAssumption (A):`);
    lines.push(`  ${typeof contract.A === "string" ? contract.A : JSON.stringify(contract.A)}`);
  }
  lines.push(`\nLast event for this contract:`);
  if (last) {
    const dt = new Date(last.ts * 1000).toISOString();
    lines.push(`  ${dt}  ${last.result.action.toUpperCase()}  on ${last.action}`);
    lines.push(`  ${last.result.message}`);
  } else {
    lines.push("  (no matching session-log events found in ~/.sponsio/sessions)");
  }
  return lines.join("\n") + "\n";
}

export async function runExplainCli(argv: string[]): Promise<void> {
  let query: string | null = null;
  let configFlag: string | undefined;
  let agentFlag: string | undefined;
  let format: "text" | "json" = "text";

  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "-h" || a === "--help") {
      process.stdout.write(HELP);
      return;
    }
    if (a === "-c" || a === "--config") {
      configFlag = argv[++i];
      continue;
    }
    if (a === "-a" || a === "--agent") {
      agentFlag = argv[++i];
      continue;
    }
    if (a === "--format") {
      const v = argv[++i];
      if (v !== "text" && v !== "json") {
        process.stderr.write(`--format must be text|json (got ${v})\n`);
        process.exit(2);
      }
      format = v;
      continue;
    }
    if (a.startsWith("-")) {
      process.stderr.write(`unknown flag: ${a}\n${HELP}`);
      process.exit(2);
    }
    if (query !== null) {
      process.stderr.write(`extra positional argument: ${a}\n${HELP}`);
      process.exit(2);
    }
    query = a;
  }

  if (!query) {
    process.stderr.write(`missing query\n\n${HELP}`);
    process.exit(2);
  }

  const configPath = resolveConfigPath(configFlag);
  const agents = loadAgents(configPath);
  const agentIds = Object.keys(agents);
  if (agentIds.length === 0) {
    process.stderr.write(`Error: ${configPath} has no agents block.\n`);
    process.exit(2);
  }
  let agentId: string;
  if (agentFlag) {
    if (!(agentFlag in agents)) {
      process.stderr.write(
        `Error: agent '${agentFlag}' not in config (available: ${agentIds.join(", ")}).\n`,
      );
      process.exit(2);
    }
    agentId = agentFlag;
  } else if (agentIds.length === 1) {
    agentId = agentIds[0];
  } else {
    process.stderr.write(
      `Error: config has ${agentIds.length} agents — pass --agent (available: ${agentIds.join(", ")}).\n`,
    );
    process.exit(2);
  }

  const contracts = agents[agentId].contracts ?? [];
  if (contracts.length === 0) {
    process.stderr.write(`Error: no contracts compiled for agent '${agentId}'.\n`);
    process.exit(2);
  }

  const found = resolveContract(query, contracts);
  if (!found) {
    process.stderr.write(`Error: no contract matched '${query}'. Available:\n`);
    for (let i = 0; i < contracts.length; i++) {
      const c = contracts[i];
      const label = c.desc ?? summariseE(c);
      process.stderr.write(`  C${i + 1}  ${label.split("\n")[0]}\n`);
    }
    process.exit(2);
  }

  const needle = (found.contract.desc ?? summariseE(found.contract)).split("\n")[0];
  const last = findLastEvent(agentId, needle);

  if (format === "json") {
    process.stdout.write(
      JSON.stringify(
        {
          agent: agentId,
          source: configPath,
          alias: `C${found.idx + 1}`,
          contract: found.contract,
          last_event: last,
        },
        null,
        2,
      ) + "\n",
    );
    return;
  }
  process.stdout.write(
    renderText({
      agentId,
      configPath,
      contract: found.contract,
      idx: found.idx,
      last,
      total: contracts.length,
    }),
  );
}
