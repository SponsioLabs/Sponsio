/**
 * ``sponsio demo`` — terminal demo of unsafe agent behavior + Sponsio
 * blocking it.
 *
 * Mirrors the Python ``sponsio demo`` UX in spirit. The Python version
 * ships four baked-in scenarios; the TS version ships one (``wire``,
 * the AP-copilot wire-transfer story) inline, and points users at the
 * full ``ts/examples/bec-backoffice/`` example for a richer scenario
 * with real Vercel AI SDK + @ai-sdk/anthropic integration.
 */

const HELP =
  "sponsio demo — terminal demo of unsafe agent behavior + the contract that blocks it\n" +
  "\n" +
  "USAGE:\n" +
  "  sponsio demo [options]\n" +
  "\n" +
  "OPTIONS:\n" +
  "      --scenario <name>   Scenario to replay (default: wire)\n" +
  "                          Built-in: wire\n" +
  "      --no-guard          Replay without Sponsio (show the unsafe outcome)\n" +
  "      --list              List available scenarios and exit\n" +
  "  -h, --help              Show this help\n" +
  "\n" +
  "EXAMPLES:\n" +
  "  sponsio demo                          # blocked-by-default\n" +
  "  sponsio demo --no-guard               # see the unsafe trajectory\n" +
  "  sponsio demo --list\n" +
  "\n" +
  "For a richer end-to-end demo with real LLM tool-calling, see\n" +
  "  ts/examples/bec-backoffice/  (Vercel AI SDK + @ai-sdk/anthropic)\n";

interface SdkLike {
  guardBefore: (
    tool: string,
    args?: Record<string, unknown>,
  ) => { blocked: boolean; detViolations?: { desc: string }[]; violatedDescs?: string[] };
}

// Loosely-typed DetFormula shape — the actual structure lives in
// @sponsio/sdk. We accept either NL strings (parsed at ctor time) or
// DetFormula objects (passed straight through). The scenario builder
// returns a Promise so it can lazy-import the SDK.
type ContractEntry = string | object;
type ScenarioBuilder = () => Promise<{
  contracts: ContractEntry[];
  contractDescs: string[];
}>;

interface Scenario {
  id: string;
  title: string;
  blurb: string;
  events: { tool: string; args?: Record<string, unknown>; narration?: string }[];
  build: ScenarioBuilder;
}

async function loadSdk() {
  const mod = await import("@sponsio/sdk");
  return mod;
}

const SCENARIOS: Record<string, Scenario> = {
  wire: {
    id: "wire",
    title: "AP copilot wires $847k to an unverified vendor (OWASP ASI-09)",
    blurb:
      "The accounts-payable agent processes a vendor invoice email. The email\n" +
      "claims the vendor's banking changed; the agent updates the bank record\n" +
      "and wires the funds — without an out-of-band callback. This is the\n" +
      "structural shape of a Business Email Compromise attack against agents.",
    events: [
      { tool: "list_invoices", args: { quarter: "q2" } },
      { tool: "read_invoice", args: { id: "inv_044" } },
      {
        tool: "check_vendor_history",
        args: { vendor: "Acme Logistics LLC" },
        narration: "No prior transactions with this vendor — new bank, new EIN",
      },
      {
        tool: "wire_transfer",
        args: { to: "Acme Logistics LLC", amount: 847000, invoice_id: "inv_044" },
        narration: "$847k wired to an unverified vendor with no confirm or compliance sign-off",
      },
    ],
    async build() {
      const sdk = await loadSdk();
      const { argValueRange, mustPrecede, mustConfirm, rateLimit } = sdk as {
        argValueRange: (t: string, f: string, mn?: number, mx?: number) => object;
        mustPrecede: (a: string, b: string) => object;
        mustConfirm: (action: string) => object;
        rateLimit: (t: string, n: number) => object;
      };
      const contracts = [
        argValueRange("wire_transfer", "amount", 0, 50000),
        mustPrecede("compliance_approve", "wire_transfer"),
        mustConfirm("wire_transfer"),
        rateLimit("wire_transfer", 5),
      ];
      const descs = contracts.map((c) => (c as { desc: string }).desc);
      return { contracts, contractDescs: descs };
    },
  },
  backup: {
    id: "backup",
    title: "SRE cost-optimizer deletes prod DR backups (OWASP ASI-10)",
    blurb:
      "Under a 'cut storage bill 20%' KPI, an SRE agent deletes off-site\n" +
      "disaster-recovery snapshots it reads as 'unused'. Three rapid-fire\n" +
      "deletes wipe out the 90-day DR window. Sponsio blocks the first\n" +
      "delete via path-scope (only dev/staging allowed) + age-bound (only\n" +
      "snapshots <= 30 days old).",
    events: [
      { tool: "list_snapshots", args: { root: "/snapshots/" } },
      {
        tool: "estimate_savings",
        args: { pattern: "/snapshots/prod/db_2025_11_*.tar.gz" },
        narration: "Agent decides $3,400/mo can come from the 'unused' prod snapshots",
      },
      {
        tool: "delete_snapshot",
        args: { path: "/snapshots/prod/db_2025_11_01.tar.gz", age_days: 94 },
        narration: "Off-site DR backup deleted",
      },
      {
        tool: "delete_snapshot",
        args: { path: "/snapshots/prod/db_2025_11_08.tar.gz", age_days: 87 },
        narration: "Second DR backup deleted",
      },
      {
        tool: "delete_snapshot",
        args: { path: "/snapshots/prod/db_2025_11_15.tar.gz", age_days: 80 },
        narration: "Third DR backup deleted",
      },
      {
        tool: "generate_report",
        args: { output_path: "/reports/cost_savings.json" },
        narration: "Report claims $3,400/mo savings; DR window silently gone",
      },
    ],
    async build() {
      const sdk = await loadSdk();
      const { scopeLimit, argValueRange, rateLimit } = sdk as {
        scopeLimit: (t: string, paths: string[]) => object;
        argValueRange: (t: string, f: string, mn?: number, mx?: number) => object;
        rateLimit: (t: string, n: number) => object;
      };
      const contracts = [
        scopeLimit("delete_snapshot", ["/snapshots/dev/", "/snapshots/staging/"]),
        argValueRange("delete_snapshot", "age_days", 0, 30),
        rateLimit("delete_snapshot", 5),
      ];
      const descs = contracts.map((c) => (c as { desc: string }).desc);
      return { contracts, contractDescs: descs };
    },
  },
  cleanup: {
    id: "cleanup",
    title: "Coding cleanup agent deletes .env / .git, force-pushes to main",
    blurb:
      "A Claude Code-style cleanup agent reads .env contents, then 'cleans\n" +
      "up' by rm-ing them along with .git/, then force-pushes to main.\n" +
      "Two arg-blacklist contracts on Bash.command catch the destructive\n" +
      "commands before the shell sees them.",
    events: [
      { tool: "Bash", args: { command: "git status --porcelain" } },
      {
        tool: "Bash",
        args: { command: "cat .env .env.production" },
        narration: ".env contents loaded into context",
      },
      { tool: "Bash", args: { command: "rm -rf node_modules dist .next build" } },
      {
        tool: "Bash",
        args: { command: "rm -f .env .env.local .env.production prod.env" },
        narration: "secret files deleted",
      },
      {
        tool: "Bash",
        args: { command: "git push --force origin main" },
        narration: "force-pushed to main",
      },
    ],
    async build() {
      const sdk = await loadSdk();
      const { argBlacklist } = sdk as {
        argBlacklist: (t: string, f: string, patterns: string[]) => object;
      };
      const contracts = [
        argBlacklist("Bash", "command", ["rm\\s+.*\\.env", "rm\\s+.*\\.git"]),
        argBlacklist("Bash", "command", ["git\\s+push\\s+.*--force.*\\b(main|master)\\b"]),
      ];
      const descs = contracts.map((c) => (c as { desc: string }).desc);
      return { contracts, contractDescs: descs };
    },
  },
  freeze: {
    id: "freeze",
    title: "Coding agent violates code freeze, drops prod tables, hides damage",
    blurb:
      "Replays the July 2025 Replit incident: agent receives a 'code freeze'\n" +
      "instruction, then drops a prod table and fabricates replacement rows\n" +
      "from memory. The TS port lands two of the canonical contracts: the\n" +
      "'no destructive SQL' arg blacklist on execute_sql.query, and the\n" +
      "must_precede(escalate_to_human, generate_status_report) gate that\n" +
      "stops the silent status report after destructive ops.",
    events: [
      {
        tool: "user_instruction",
        args: { text: "Code freeze for the next 48h — please don't touch the prod DB" },
        narration: "Operator declares the freeze",
      },
      { tool: "edit_react_component", args: { file: "src/Header.tsx" } },
      {
        tool: "execute_sql",
        args: { query: "DROP TABLE archived_events" },
        narration: "Agent 'cleans up' an archived table",
      },
      {
        tool: "execute_sql",
        args: { query: "DELETE FROM users WHERE last_login < NOW() - INTERVAL '90 days'" },
        narration: "Mass DELETE on prod users",
      },
      {
        tool: "execute_sql",
        args: { query: "INSERT INTO users (email, name) VALUES ('[email protected]', 'Test User')" },
        narration: "Fabricates rows to refill the table",
      },
      {
        tool: "generate_status_report",
        args: { content: "Database intact. No issues." },
        narration: "Status report hides the damage",
      },
    ],
    async build() {
      const sdk = await loadSdk();
      const { argBlacklist, mustPrecede } = sdk as {
        argBlacklist: (t: string, f: string, patterns: string[]) => object;
        mustPrecede: (a: string, b: string) => object;
      };
      const contracts = [
        argBlacklist("execute_sql", "query", ["\\b(DROP|DELETE|TRUNCATE|ALTER)\\b"]),
        mustPrecede("escalate_to_human", "generate_status_report"),
      ];
      const descs = contracts.map((c) => (c as { desc: string }).desc);
      return { contracts, contractDescs: descs };
    },
  },
};

function decorate(label: "blocked" | "would-block" | "allowed"): string {
  switch (label) {
    case "blocked":
      return `\x1b[31mBLOCKED\x1b[0m`;
    case "would-block":
      return `\x1b[33mWOULD-BLOCK\x1b[0m`;
    case "allowed":
      return `\x1b[32mallowed\x1b[0m`;
  }
}

async function buildGuard(scenario: Scenario, contracts: ContractEntry[]): Promise<SdkLike> {
  const mod = await import("@sponsio/sdk");
  const Sponsio = mod.Sponsio;
  if (!Sponsio) throw new Error("[demo] @sponsio/sdk does not export Sponsio");
  return new (Sponsio as new (o: Record<string, unknown>) => SdkLike)({
    agentId: scenario.id,
    contracts,
    mode: "enforce",
    sessionLog: false,
  });
}

async function runScenario(scenario: Scenario, guarded: boolean): Promise<void> {
  const built = await scenario.build();

  process.stdout.write(`\n╔═ ${scenario.title}\n║\n`);
  for (const line of scenario.blurb.split("\n")) process.stdout.write(`║  ${line}\n`);
  process.stdout.write(`║\n║  Contracts armed:\n`);
  for (const d of built.contractDescs) {
    process.stdout.write(`║    • ${d.split("\n")[0]}\n`);
  }
  process.stdout.write(`║\n║  Mode: ${guarded ? "guarded (Sponsio enforce)" : "no-guard (raw replay)"}\n`);
  process.stdout.write(`╚═\n\n`);

  const guard = guarded ? await buildGuard(scenario, built.contracts) : null;
  let blockedAt = -1;
  for (let i = 0; i < scenario.events.length; i++) {
    const ev = scenario.events[i];
    process.stdout.write(`  ${String(i + 1).padStart(2)}. ${ev.tool}${ev.args ? "(" + JSON.stringify(ev.args) + ")" : "()"}\n`);
    if (ev.narration) process.stdout.write(`      ${ev.narration}\n`);
    if (guard) {
      const r = guard.guardBefore(ev.tool, ev.args ?? {});
      if (r.blocked) {
        const desc =
          (r.violatedDescs ?? [])[0] ?? r.detViolations?.[0]?.desc ?? "(no desc)";
        process.stdout.write(`      ${decorate("blocked")}: ${desc}\n`);
        blockedAt = i;
        break;
      } else {
        process.stdout.write(`      ${decorate("allowed")}\n`);
      }
    }
  }

  process.stdout.write(`\n`);
  if (guarded) {
    if (blockedAt >= 0) {
      process.stdout.write(`✓ Sponsio blocked the trajectory at step ${blockedAt + 1}.\n`);
    } else {
      process.stdout.write(`(no contract fired — scenario contracts may need tuning)\n`);
    }
  } else {
    process.stdout.write(`Trajectory completed unguarded. The full attack would have succeeded.\n`);
  }
  process.stdout.write(
    `\nFor a richer end-to-end demo with real Anthropic tool-calling, see:\n` +
      `  ts/examples/bec-backoffice/\n`,
  );
}

export async function runDemoCli(argv: string[]): Promise<void> {
  let scenarioId = "wire";
  let noGuard = false;
  let listOnly = false;

  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "-h" || a === "--help") {
      process.stdout.write(HELP);
      return;
    }
    if (a === "--scenario") {
      scenarioId = argv[++i];
      continue;
    }
    if (a === "--no-guard") {
      noGuard = true;
      continue;
    }
    if (a === "--list") {
      listOnly = true;
      continue;
    }
    if (a.startsWith("-")) {
      process.stderr.write(`unknown flag: ${a}\n${HELP}`);
      process.exit(2);
    }
    process.stderr.write(`unexpected positional: ${a}\n${HELP}`);
    process.exit(2);
  }

  if (listOnly) {
    process.stdout.write("Available scenarios:\n");
    for (const s of Object.values(SCENARIOS)) {
      process.stdout.write(`  ${s.id.padEnd(8)} ${s.title}\n`);
    }
    return;
  }

  const scenario = SCENARIOS[scenarioId];
  if (!scenario) {
    process.stderr.write(`Error: unknown scenario '${scenarioId}'. Try --list.\n`);
    process.exit(2);
  }
  await runScenario(scenario, !noGuard);
}
