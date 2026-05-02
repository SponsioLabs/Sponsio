/**
 * ``sponsio patterns`` — browse the deterministic pattern
 * library + built-in sto atoms. Parity with Python's ``sponsio patterns``.
 *
 * Output is grouped the same way the main README tabulates them so
 * users can eyeball what's available without clicking through to
 * docs. Uses raw strings (no SDK import) to keep ``--help`` instant
 * and avoid a hard dependency on the SDK when the scanner is
 * consumed standalone (e.g. in a monorepo where the SDK isn't
 * linked).
 */

interface PatternRow {
  name: string;
  category:
    | "Safety"
    | "Compliance"
    | "Operational"
    | "Exclusion"
    | "Argument / Path"
    | "Agentic Security"
    | "Resource"
    | "Sto";
  nlExample: string;
}

const ROWS: PatternRow[] = [
  // Safety
  { name: "must_precede", category: "Safety", nlExample: "tool `check_policy` must precede `issue_refund`" },
  { name: "must_confirm", category: "Safety", nlExample: "must confirm before `delete_account`" },
  { name: "requires_permission", category: "Safety", nlExample: "tool `wire_transfer` requires `manager` permission" },
  { name: "no_data_leak", category: "Safety", nlExample: "output of `read_env` must not reach `send_email`" },
  { name: "destructive_action_gate", category: "Safety", nlExample: "`drop_table` requires approver" },

  // Compliance
  { name: "no_reversal", category: "Compliance", nlExample: "no reversal of `approve_loan` after approval" },
  { name: "segregation_of_duty", category: "Compliance", nlExample: "`create_payment` and `approve_payment` are segregated by role" },
  { name: "always_followed_by", category: "Compliance", nlExample: "`issue_refund` must be followed by `notify_customer`" },
  { name: "required_steps_completion", category: "Compliance", nlExample: "`close_ticket` requires steps [verify, document, notify]" },

  // Operational
  { name: "rate_limit", category: "Operational", nlExample: "tool `send_email` at most 5 times" },
  { name: "idempotent", category: "Operational", nlExample: "tool `create_user` is idempotent" },
  { name: "cooldown", category: "Operational", nlExample: "cooldown 10 steps between `send_sms`" },
  { name: "deadline", category: "Operational", nlExample: "after `start_job`, `finalize` within 20 steps" },
  { name: "bounded_retry", category: "Operational", nlExample: "`retry_payment` bounded to 3 retries" },
  { name: "loop_detection", category: "Operational", nlExample: "no more than 5 consecutive `poll_status`" },

  // Exclusion
  { name: "mutual_exclusion", category: "Exclusion", nlExample: "tools `approve` and `reject` are mutually exclusive" },
  { name: "tool_allowlist", category: "Exclusion", nlExample: "only tools [search, summarize] may be called" },

  // Argument / Path
  { name: "arg_blacklist", category: "Argument / Path", nlExample: "`exec` args must not contain 'rm -rf'" },
  { name: "scope_limit", category: "Argument / Path", nlExample: "`read_file` path must be under /workspace" },
  { name: "arg_length_limit", category: "Argument / Path", nlExample: "`prompt` arg `text` ≤ 10000 chars" },
  { name: "data_intact", category: "Argument / Path", nlExample: "`edit_file` must preserve original structure" },
  { name: "arg_value_range", category: "Argument / Path", nlExample: "`transfer` amount in [1, 10000]" },

  // Agentic Security
  { name: "untrusted_source_gate", category: "Agentic Security", nlExample: "after `fetch_url`, no `exec` without confirmation" },
  { name: "confirm_after_source", category: "Agentic Security", nlExample: "after `read_email`, confirm before `send_email`" },
  { name: "dangerous_bash_commands", category: "Agentic Security", nlExample: "no `rm -rf /` or similar" },
  { name: "dangerous_sql_verbs", category: "Agentic Security", nlExample: "`execute_sql` must not use DROP/TRUNCATE" },
  { name: "irreversible_once", category: "Agentic Security", nlExample: "`force_push` at most once" },

  // Resource
  { name: "token_budget", category: "Resource", nlExample: "total token budget 200k" },
  { name: "delegation_depth_limit", category: "Resource", nlExample: "max delegation depth 3" },

  // Sto (built into the TS SDK)
  { name: "tone",               category: "Sto", nlExample: "E: { pattern: tone, args: [empathetic], threshold: 0.7 }" },
  { name: "llm_judge",          category: "Sto", nlExample: "E: { pattern: llm_judge, prompt_override: '…', threshold: 0.8 }" },
  { name: "relevance",          category: "Sto", nlExample: "needs guard.setContext({ query })" },
  { name: "semantic_pii_free",  category: "Sto", nlExample: "catches paraphrased PII that regex misses" },
  { name: "hallucination_free", category: "Sto", nlExample: "needs guard.setContext({ source })" },
  { name: "scope_respect",      category: "Sto", nlExample: "E: { pattern: scope_respect, args: ['only customer billing'] }" },
  { name: "metric_integrity",   category: "Sto", nlExample: "catches silently-altered numbers / flipped pass-fails" },
  { name: "injection_free",     category: "Sto", nlExample: "detects prompt-injection payloads in tool output" },
];

interface PatternsArgs {
  format: "text" | "json";
  category?: string;
  help: boolean;
}

const HELP =
  [
    "sponsio patterns — list det patterns + sto atoms",
    "",
    "USAGE:",
    "  sponsio patterns [options]",
    "",
    "OPTIONS:",
    "      --category <name>  Filter by category (Safety, Compliance, Sto …)",
    "      --format <f>       'text' (default) or 'json'",
    "  -h, --help             Show this help",
  ].join("\n") + "\n";

function parseArgs(argv: string[]): PatternsArgs {
  const a: PatternsArgs = { format: "text", help: false };
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === "-h" || flag === "--help") a.help = true;
    else if (flag === "--category") a.category = argv[++i];
    else if (flag === "--format") {
      const v = argv[++i];
      if (v !== "text" && v !== "json") throw new Error(`--format must be 'text' or 'json'`);
      a.format = v;
    } else {
      throw new Error(`unknown flag: ${flag}`);
    }
  }
  return a;
}

export async function runPatternsCli(argv: string[]): Promise<void> {
  let args: PatternsArgs;
  try {
    args = parseArgs(argv);
  } catch (err) {
    process.stderr.write(`${err instanceof Error ? err.message : String(err)}\n`);
    process.exit(2);
  }
  if (args.help) {
    process.stdout.write(HELP);
    return;
  }
  const rows = args.category
    ? ROWS.filter((r) => r.category.toLowerCase() === args.category!.toLowerCase())
    : ROWS;
  if (args.format === "json") {
    process.stdout.write(JSON.stringify(rows, null, 2) + "\n");
    return;
  }
  // Group by category, preserve declaration order within each group.
  const byCat = new Map<string, PatternRow[]>();
  for (const r of rows) {
    const list = byCat.get(r.category) ?? [];
    list.push(r);
    byCat.set(r.category, list);
  }
  const lines: string[] = [];
  lines.push(`Sponsio patterns — ${rows.length} total (${byCat.size} categor${byCat.size === 1 ? "y" : "ies"})`);
  lines.push("");
  for (const [cat, list] of byCat) {
    lines.push(`## ${cat} (${list.length})`);
    for (const r of list) {
      lines.push(`  ${r.name.padEnd(26)} ${r.nlExample}`);
    }
    lines.push("");
  }
  process.stdout.write(lines.join("\n"));
}
