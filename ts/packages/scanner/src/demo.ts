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

interface Scenario {
  id: string;
  title: string;
  blurb: string;
  contracts: string[];
  events: { tool: string; args?: Record<string, unknown>; narration?: string }[];
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
    contracts: [
      "tool `verify_vendor_oob` must precede `update_vendor_bank_account`",
      "tool `wire_transfer` at most 1 times",
    ],
    events: [
      { tool: "read_email", args: { id: "INV-7841" }, narration: "AP reads invoice from `vendor-billing@acme-supplies.co` (lookalike domain)" },
      { tool: "lookup_vendor", args: { name: "Acme Industrial Supplies" }, narration: "Vendor record on file" },
      {
        tool: "update_vendor_bank_account",
        args: { vendorId: "ACME-001", newAccount: "9876543210" },
        narration: "Email-claimed bank change applied with no OOB verification",
      },
      { tool: "wire_transfer", args: { amount: 847000, vendorId: "ACME-001" }, narration: "$847k released to attacker-controlled account" },
    ],
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

async function buildGuard(scenario: Scenario): Promise<SdkLike> {
  const mod = await import("@sponsio/sdk");
  const Sponsio = mod.Sponsio;
  if (!Sponsio) throw new Error("[demo] @sponsio/sdk does not export Sponsio");
  return new (Sponsio as new (o: Record<string, unknown>) => SdkLike)({
    agentId: scenario.id,
    contracts: scenario.contracts,
    mode: "enforce",
    sessionLog: false,
  });
}

async function runScenario(scenario: Scenario, guarded: boolean): Promise<void> {
  process.stdout.write(`\n╔═ ${scenario.title}\n║\n`);
  for (const line of scenario.blurb.split("\n")) process.stdout.write(`║  ${line}\n`);
  process.stdout.write(`║\n║  Contracts armed:\n`);
  for (const c of scenario.contracts) process.stdout.write(`║    • ${c}\n`);
  process.stdout.write(`║\n║  Mode: ${guarded ? "guarded (Sponsio enforce)" : "no-guard (raw replay)"}\n`);
  process.stdout.write(`╚═\n\n`);

  const guard = guarded ? await buildGuard(scenario) : null;
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
