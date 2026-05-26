/**
 * ``sponsio packs`` — informational listing of the built-in
 * pack library.
 *
 * Packs (pre-built bundles like ``sponsio:core/runaway``,
 * ``sponsio:capability/filesystem``) are Python-authored resource
 * files consumed by the Python runtime. The TS SDK doesn't ship
 * pack infra yet — pack ``include:`` entries get surfaced via
 * ``sponsio validate`` as skipped items. This command
 * documents what's available so a user can decide whether to
 * ``pip install sponsio`` alongside their TS agent (both languages
 * read the same ``sponsio.yaml``, so packs declared for Python
 * auto-apply whenever the Python runtime executes).
 */

interface Pack {
  name: string;
  rules: number;
  ruleType: "det" | "sto" | "mixed";
  description: string;
}

const PACKS: Pack[] = [
  {
    name: "sponsio:core/universal",
    rules: 0,
    ruleType: "det",
    description: "Empty stub in this build. The output-quality contracts (injection / jailbreak / toxic / PII / harm) moved to sponsio:core/llm_safety, which is not supported in this build (the engine is deterministic-only).",
  },
  {
    name: "sponsio:core/llm_safety",
    rules: 5,
    ruleType: "sto",
    description: "Not supported in this build (the engine is deterministic-only). LLM-judge safety net: injection_free / jailbreak_free / harmful / toxic_free / semantic_pii_free.",
  },
  {
    name: "sponsio:core/runaway",
    rules: 0,
    ruleType: "det",
    description: "Intentionally empty. Inline `token_budget` / `loop_detection` / `delegation_depth_limit` with project-specific numbers instead — see the file's header for the migration recipe.",
  },
  {
    name: "sponsio:capability/shell",
    rules: 11,
    ruleType: "det",
    description: "Any tool executing shell commands — dangerous verbs, force flags, rate caps.",
  },
  {
    name: "sponsio:capability/filesystem",
    rules: 9,
    ruleType: "det",
    description: "Any tool reading/writing files. Credential-path blacklist + read-before-edit + bootstrap-confirm + no_data_leak. (Workspace scoping moved to filesystem-strict.)",
  },
  {
    name: "sponsio:capability/filesystem-strict",
    rules: 4,
    ruleType: "det",
    description: "Opt-in. Workspace scope_limit on read/write/edit/apply_patch. Needs workspace:.  Add alongside filesystem when traces use absolute paths under one tree.",
  },
  {
    name: "sponsio:capability/credentials",
    rules: 8,
    ruleType: "det",
    description: "Block credential / secret / token paths from any tool that reads files (.env, *.pem, .ssh/id_*, ~/.aws/credentials, etc.).",
  },
  {
    name: "sponsio:capability/database",
    rules: 6,
    ruleType: "det",
    description: "Any tool issuing SQL — dangerous verbs (DROP/TRUNCATE/DELETE without WHERE), unparameterized queries, mass updates without quorum.",
  },
  {
    name: "sponsio:capability/self-modify",
    rules: 6,
    ruleType: "det",
    description: "Block agent-mediated Edit/Write/MultiEdit on the host's own ~/.sponsio/plugins/_host/sponsio.yaml. Stops self-modification of guard rules.",
  },
  {
    name: "sponsio:capability/subagent",
    rules: 5,
    ruleType: "det",
    description: "Subagent (Task-spawned) privilege boundary — stricter than the parent agent's rules.",
  },
  {
    name: "sponsio:capability/host-config-integrity",
    rules: 7,
    ruleType: "det",
    description: "Block tool-mediated mutation of host configs (~/.sponsio/, ~/.cursor/, ~/.claude/, project sponsio.yaml).",
  },
  {
    name: "sponsio:incident/openclaw",
    rules: 45,
    ruleType: "mixed",
    description: "Opt-in. CVE-derived rules for OpenClaw-style agents. Det rules (§1–§8) load in this build; sto rules (§0 LLM-output, §9 capability atoms) are not supported here (the engine is deterministic-only).",
  },
  {
    name: "sponsio:incident/subagent-escape",
    rules: 4,
    ruleType: "det",
    description: "Sub-agent reach-up defence. Denies Edit/Write/MultiEdit on ~/.sponsio/, project sponsio.yaml, .sponsiorc + Read on host rule lists (recon defence).",
  },
  {
    name: "sponsio:incident/claude-code-secret-bypass",
    rules: 7,
    ruleType: "det",
    description: "Mitigations for the Claude Code secret-bypass class (auto-allow paths leaking credentials).",
  },
  {
    name: "sponsio:incident/cursor-railway-wipe",
    rules: 3,
    ruleType: "det",
    description: "Cursor / Railway-style production-data wipe defence (gated destructive ops on remote infra).",
  },
  {
    name: "sponsio:incident/mcp-composition",
    rules: 12,
    ruleType: "det",
    description: "MCP cross-server composition attacks — untrusted-source-gate, no_data_leak across servers.",
  },
];

interface PacksArgs {
  format: "text" | "json";
  help: boolean;
}

const HELP =
  [
    "sponsio packs — list pre-built contract packs",
    "",
    "USAGE:",
    "  sponsio packs [options]",
    "",
    "OPTIONS:",
    "      --format <f>  'text' (default) or 'json'",
    "  -h, --help        Show this help",
    "",
    "NOTE:",
    "  Packs are executed by the Python runtime. The TS runtime honours",
    "  everything authored inline in the same sponsio.yaml, and 'validate'",
    "  reports pack includes as skipped items. For pack semantics, run",
    "  pip install sponsio alongside the TS agent.",
  ].join("\n") + "\n";

function parseArgs(argv: string[]): PacksArgs {
  const a: PacksArgs = { format: "text", help: false };
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === "-h" || flag === "--help") a.help = true;
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

export async function runPacksCli(argv: string[]): Promise<void> {
  let args: PacksArgs;
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
  if (args.format === "json") {
    process.stdout.write(JSON.stringify(PACKS, null, 2) + "\n");
    return;
  }
  const nameWidth = Math.max(...PACKS.map((p) => p.name.length));
  const lines: string[] = [];
  lines.push(`Sponsio packs — ${PACKS.length} available (Python runtime required)`);
  lines.push("");
  for (const p of PACKS) {
    lines.push(
      `  ${p.name.padEnd(nameWidth)}  (${p.rules} ${p.ruleType})`,
    );
    lines.push(`      ${p.description}`);
  }
  lines.push("");
  lines.push("To use: add under the agent's include: block, e.g.");
  lines.push("  agents:");
  lines.push("    my_agent:");
  lines.push("      include:");
  lines.push("        - sponsio:core/runaway");
  lines.push("");
  process.stdout.write(lines.join("\n"));
}
