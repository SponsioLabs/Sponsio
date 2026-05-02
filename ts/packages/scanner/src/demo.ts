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
  // If true, the scenario depends on Python-only patterns (called_with regex,
  // arg_value_range etc.) that the TS NL parser doesn't support yet. The
  // scenario stays listed for parity with `python -m sponsio demo --list`,
  // but running it errors out with a pointer to the Python CLI.
  pythonOnly?: boolean;
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
      "tool `compliance_approve` must precede `wire_transfer`",
      "tool `confirm_wire_transfer` must precede `wire_transfer`",
      "tool `wire_transfer` at most 5 times",
    ],
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
  },
  backup: {
    id: "backup",
    title: "SRE cost-optimizer deletes prod DR backups (OWASP ASI-10)",
    blurb:
      "Under a 'cut storage bill 20%' KPI, an SRE agent deletes off-site\n" +
      "disaster-recovery snapshots it reads as 'unused'. The TS scenario\n" +
      "uses a tight rate_limit(2) as a blast-radius cap; the Python demo\n" +
      "blocks the first delete via path-scope + age-bound enforcement\n" +
      "(scope_limit / arg_value_range — not yet in the TS NL parser).",
    contracts: ["tool `delete_snapshot` at most 2 times"],
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
  },
  cleanup: {
    id: "cleanup",
    title: "Coding cleanup agent deletes .env / .git, force-pushes to main",
    blurb:
      "A Claude Code-style cleanup agent reads .env contents, then 'cleans\n" +
      "up' by rm-ing them along with .git/, then force-pushes to main. The\n" +
      "Python demo blocks this via `called_with` regex patterns on Bash\n" +
      "args — those patterns aren't in the TS NL parser yet, so this\n" +
      "scenario stays Python-only.",
    contracts: [],
    events: [],
    pythonOnly: true,
  },
  freeze: {
    id: "freeze",
    title: "Coding agent violates code freeze, drops prod tables, hides damage",
    blurb:
      "Replays the July 2025 Replit incident: agent receives a 'code freeze'\n" +
      "instruction, then drops a prod table and tries to fabricate replacement\n" +
      "rows from memory. Five A/E contracts pin every step. Like cleanup, the\n" +
      "Python demo uses `called_with` regex assumptions that aren't expressible\n" +
      "in TS NL yet — Python-only for now.",
    contracts: [],
    events: [],
    pythonOnly: true,
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
      const tag = s.pythonOnly ? "  (python-only)" : "";
      process.stdout.write(`  ${s.id.padEnd(8)} ${s.title}${tag}\n`);
    }
    return;
  }

  const scenario = SCENARIOS[scenarioId];
  if (!scenario) {
    process.stderr.write(`Error: unknown scenario '${scenarioId}'. Try --list.\n`);
    process.exit(2);
  }
  if (scenario.pythonOnly) {
    process.stderr.write(
      `Scenario '${scenario.id}' depends on Python-only contract patterns ` +
        `(called_with regex / arg_value_range / structured composition).\n` +
        `Run via the Python CLI instead:\n` +
        `  pip install sponsio && sponsio demo --scenario ${scenario.id}\n`,
    );
    process.exit(2);
  }
  await runScenario(scenario, !noGuard);
}
