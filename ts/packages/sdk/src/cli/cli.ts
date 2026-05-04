#!/usr/bin/env node
/**
 * ``sponsio`` — CLI frontend for ``@sponsio/sdk``.
 *
 * Usage:
 *
 *   npx @sponsio/sdk ./src
 *   npx @sponsio/sdk "src/**\/*.ts" --out tools.json
 *   npx @sponsio/sdk --config sponsio.yaml
 *   npx @sponsio/sdk ./src --pretty
 *
 * Writes the OpenAI function-calling JSON inventory to stdout (or to
 * ``--out <file>``) so you can pipe into ``sponsio scan`` on the
 * Python side:
 *
 *   npx @sponsio/sdk ./src > tools.json
 *   sponsio scan tools.json --out sponsio.yaml
 *
 * When ``--config sponsio.yaml`` is passed, the CLI also reads the
 * ``scan:`` section (for defaults) and ``extractor:`` (for LLM
 * passthrough in the output JSON, so the Python-side scan doesn't
 * need ``--config`` a second time).  CLI flags always win over YAML.
 */

import { promises as fs } from "fs";
import { loadConfig, ConfigError, type SponsioConfig } from "./config";
import { runOnboardCli } from "./onboard";
import { runReportCli } from "./report";
import { runValidateCli } from "./validate";
import { runPatternsCli } from "./patterns";
import { runDoctorCli } from "./doctor";
import { runPacksCli } from "./packs";
import { runSkillCli } from "./skill";
import { runModeCli } from "./mode";
import { runInitCli } from "./init";
import { runScanCli } from "./scan";
import { runPromptCli } from "./prompt";
import { runExplainCli } from "./explain";
import { runReplayCli } from "./replay";
import { runCheckCli } from "./check";
import { runEvalCli } from "./eval";
import { runExportCli } from "./export";
import { runExportSessionsCli } from "./export-sessions";
import { runDemoCli } from "./demo";
import { scan } from "./index";

interface CliArgs {
  patterns: string[];
  out?: string;
  pretty: boolean;
  help: boolean;
  version: boolean;
  includeProvenance: boolean;
  configPath?: string;
}

// Authored as a plain-string array (not a template literal) because
// the help text contains literal ``${VAR}`` tokens that describe the
// env-var interpolation syntax.  Inside a template literal those
// would try to evaluate as JS expressions (and ``\$`` escapes are
// inconsistently handled across TS-target combos).  Joining plain
// strings is dull but unambiguous.
const HELP =
  [
    "sponsio — scan TypeScript/JavaScript for agent tool definitions",
    "",
    "USAGE:",
    "  sponsio <patterns...> [options]",
    "",
    "ARGUMENTS:",
    '  <patterns...>       Files or glob patterns to scan (default: "src/**/*.{ts,tsx,js,jsx}")',
    "",
    "OPTIONS:",
    "  -o, --out <file>    Write output to <file> instead of stdout",
    "  -c, --config <yaml> Read defaults from sponsio.yaml's ``scan:`` section and",
    "                      embed ``extractor:`` config into the output for Python.",
    "      --pretty        Pretty-print the emitted JSON",
    "      --provenance    Include per-tool source-file provenance in output",
    "  -h, --help          Show this help",
    "  -v, --version       Show version",
    "",
    "EXAMPLES:",
    "  sponsio ./src",
    '  sponsio "src/tools/**/*.ts" --out tools.json',
  "  sponsio --config sponsio.yaml --pretty",
  "  sponsio ./src --out /tmp/inv.json && sponsio scan /tmp/inv.json -o sponsio.yaml",
  "  sponsio onboard .",
  "  sponsio init --mode observe",
  "  sponsio scan ./src --append",
  "  sponsio mode enforce",
  "  sponsio check -t trace.json --config sponsio.yaml",
  "  sponsio eval traces/ --config sponsio.yaml --json",
  "  sponsio explain C1",
  "  sponsio prompt onboard",
  "  sponsio replay --list",
  "  sponsio export run.json --to traces/ --label safe",
  "  sponsio export-sessions --to audit.jsonl",
  "  sponsio demo --scenario wire",
  "  sponsio report --since 24h",
  "  sponsio validate ./sponsio.yaml",
  "  sponsio patterns",
  "  sponsio doctor",
  "  sponsio skill install",
    "",
    "SUBCOMMANDS:",
    "  onboard [.]                Generate sponsio.yaml + print integration snippet",
    "  init [target]              Skeleton sponsio.yaml (no scan)",
    "  scan <patterns…>           AST-scan source -> sponsio.yaml with contracts",
    "  mode <observe|enforce>     Flip the mode line in sponsio.yaml",
    "  check -t <trace> …         Replay a trace file, report contract pass/fail",
    "  eval <traces/> …           Replay labelled corpus, report FPR/FNR + diff",
    "  explain <Cn|substr>        Explain a contract: source, last violation",
    "  prompt <onboard|scan|…>    Print agent-facing contract-authoring template",
    "  replay [session|--list]    Pretty-print a session log",
    "  export <src> --to <dir>    Sponsio-native trace -> OTLP/JSON",
    "  export-sessions --to <f>   Session log -> OTLP-JSONL",
    "  report [--since 24h …]     Summarize ~/.sponsio/sessions/ into markdown/json",
    "  validate [path]            Parse sponsio.yaml + report contract counts (sto contracts flagged as Cloud-only)",
    "  patterns [--category …]    List deterministic pattern templates available in TS",
    "  packs                      List the built-in pack library (Python-executed)",
    "  doctor                     Env + config health check",
    "  demo [--scenario wire]     Terminal demo of unsafe agent + Sponsio block",
    "  skill install              Drop SKILL.md into Cursor/Claude/Codex skill dirs",
    "",
    "CONFIG FILE SHAPE:",
    "  # sponsio.yaml",
    "  scan:",
    '    patterns:  ["src/**/*.ts", "packages/*/src/**/*.ts"]',
    '    ignore:    ["**/generated/**"]',
    '    out:       "tools.json"',
    "    provenance: true",
    "  extractor:",
    "    provider: openai",
    "    model:    gpt-4o",
    "    api_key:  ${OPENAI_API_KEY}      # ${VAR} / ${VAR:-default} supported",
  ].join("\n") + "\n";

function parseArgs(argv: string[]): CliArgs {
  const args: CliArgs = {
    patterns: [],
    pretty: false,
    help: false,
    version: false,
    includeProvenance: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "-h" || a === "--help") args.help = true;
    else if (a === "-v" || a === "--version") args.version = true;
    else if (a === "--pretty") args.pretty = true;
    else if (a === "--provenance") args.includeProvenance = true;
    else if (a === "-o" || a === "--out") {
      args.out = argv[++i];
    } else if (a === "-c" || a === "--config") {
      args.configPath = argv[++i];
    } else if (a.startsWith("-")) {
      process.stderr.write(`unknown flag: ${a}\n`);
      process.exit(2);
    } else {
      args.patterns.push(a);
    }
  }
  return args;
}

async function main() {
  const raw = process.argv.slice(2);
  if (raw[0] === "onboard") {
    try {
      await runOnboardCli(raw.slice(1));
    } catch (err) {
      process.stderr.write(`${err instanceof Error ? err.stack ?? err.message : err}\n`);
      process.exit(1);
    }
    return;
  }
  if (raw[0] === "report") {
    try {
      await runReportCli(raw.slice(1));
    } catch (err) {
      process.stderr.write(`${err instanceof Error ? err.stack ?? err.message : err}\n`);
      process.exit(1);
    }
    return;
  }
  if (raw[0] === "validate") {
    try {
      await runValidateCli(raw.slice(1));
    } catch (err) {
      process.stderr.write(`${err instanceof Error ? err.stack ?? err.message : err}\n`);
      process.exit(1);
    }
    return;
  }
  if (raw[0] === "patterns") {
    try {
      await runPatternsCli(raw.slice(1));
    } catch (err) {
      process.stderr.write(`${err instanceof Error ? err.stack ?? err.message : err}\n`);
      process.exit(1);
    }
    return;
  }
  if (raw[0] === "packs") {
    try {
      await runPacksCli(raw.slice(1));
    } catch (err) {
      process.stderr.write(`${err instanceof Error ? err.stack ?? err.message : err}\n`);
      process.exit(1);
    }
    return;
  }
  if (raw[0] === "doctor") {
    try {
      await runDoctorCli(raw.slice(1));
    } catch (err) {
      process.stderr.write(`${err instanceof Error ? err.stack ?? err.message : err}\n`);
      process.exit(1);
    }
    return;
  }
  if (raw[0] === "skill") {
    try {
      await runSkillCli(raw.slice(1));
    } catch (err) {
      process.stderr.write(`${err instanceof Error ? err.stack ?? err.message : err}\n`);
      process.exit(1);
    }
    return;
  }
  if (raw[0] === "mode") {
    try {
      await runModeCli(raw.slice(1));
    } catch (err) {
      process.stderr.write(`${err instanceof Error ? err.stack ?? err.message : err}\n`);
      process.exit(1);
    }
    return;
  }
  if (raw[0] === "init") {
    try {
      await runInitCli(raw.slice(1));
    } catch (err) {
      process.stderr.write(`${err instanceof Error ? err.stack ?? err.message : err}\n`);
      process.exit(1);
    }
    return;
  }
  if (raw[0] === "scan") {
    try {
      await runScanCli(raw.slice(1));
    } catch (err) {
      process.stderr.write(`${err instanceof Error ? err.stack ?? err.message : err}\n`);
      process.exit(1);
    }
    return;
  }
  if (raw[0] === "prompt") {
    try {
      await runPromptCli(raw.slice(1));
    } catch (err) {
      process.stderr.write(`${err instanceof Error ? err.stack ?? err.message : err}\n`);
      process.exit(1);
    }
    return;
  }
  if (raw[0] === "explain") {
    try {
      await runExplainCli(raw.slice(1));
    } catch (err) {
      process.stderr.write(`${err instanceof Error ? err.stack ?? err.message : err}\n`);
      process.exit(1);
    }
    return;
  }
  if (raw[0] === "replay") {
    try {
      await runReplayCli(raw.slice(1));
    } catch (err) {
      process.stderr.write(`${err instanceof Error ? err.stack ?? err.message : err}\n`);
      process.exit(1);
    }
    return;
  }
  if (raw[0] === "check") {
    try {
      await runCheckCli(raw.slice(1));
    } catch (err) {
      process.stderr.write(`${err instanceof Error ? err.stack ?? err.message : err}\n`);
      process.exit(1);
    }
    return;
  }
  if (raw[0] === "eval") {
    try {
      await runEvalCli(raw.slice(1));
    } catch (err) {
      process.stderr.write(`${err instanceof Error ? err.stack ?? err.message : err}\n`);
      process.exit(1);
    }
    return;
  }
  if (raw[0] === "export") {
    try {
      await runExportCli(raw.slice(1));
    } catch (err) {
      process.stderr.write(`${err instanceof Error ? err.stack ?? err.message : err}\n`);
      process.exit(1);
    }
    return;
  }
  if (raw[0] === "export-sessions") {
    try {
      await runExportSessionsCli(raw.slice(1));
    } catch (err) {
      process.stderr.write(`${err instanceof Error ? err.stack ?? err.message : err}\n`);
      process.exit(1);
    }
    return;
  }
  if (raw[0] === "demo") {
    try {
      await runDemoCli(raw.slice(1));
    } catch (err) {
      process.stderr.write(`${err instanceof Error ? err.stack ?? err.message : err}\n`);
      process.exit(1);
    }
    return;
  }

  const args = parseArgs(raw);

  if (args.help) {
    process.stdout.write(HELP);
    return;
  }
  if (args.version) {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const pkg = require("../package.json");
    process.stdout.write(`${pkg.version}\n`);
    return;
  }

  // ---------------------------------------------------------------
  // Resolve config (if any).  Precedence: CLI flag > YAML > default.
  // We load the YAML *first* so CLI flags can overwrite whatever we
  // parsed; the opposite order (CLI then YAML) would silently undo
  // ``--out foo.json`` when the YAML also set ``scan.out``.
  // ---------------------------------------------------------------
  let config: SponsioConfig | undefined;
  if (args.configPath) {
    try {
      config = loadConfig(args.configPath);
    } catch (err) {
      if (err instanceof ConfigError) {
        process.stderr.write(`[config] ${err.message}\n`);
        process.exit(2);
      }
      throw err;
    }

    // Warn about unset env-var references.  We don't fail on these
    // — shell semantics says a missing var expands to empty, and
    // aborting a pure AST scan because an ``OPENAI_API_KEY`` env
    // isn't exported would be actively unhelpful (the scanner
    // itself doesn't use that key; only the downstream Python
    // extractor does).  But a warning means users find out before
    // the Python side yells about ``api_key: ""``.
    if (config.missingEnvVars.length) {
      process.stderr.write(
        `[config] warning: ${config.missingEnvVars.length} ` +
          `env var(s) referenced in ${config.sourcePath} are unset ` +
          `and have no default, expanded to empty string: ` +
          config.missingEnvVars.join(", ") +
          `\n`
      );
    }
  }

  // ---------------------------------------------------------------
  // Apply precedence for each knob.
  // ---------------------------------------------------------------
  const effectivePatterns =
    args.patterns.length > 0
      ? args.patterns
      : config?.scan.patterns && config.scan.patterns.length > 0
      ? config.scan.patterns
      : ["src/**/*.{ts,tsx,js,jsx}"];

  const effectiveOut = args.out ?? config?.scan.out;

  // Provenance is a boolean flag so we can't use ``??`` to mean
  // "CLI not provided" — but since CLI parsing only sets
  // includeProvenance to ``true`` on --provenance (never back to
  // false from a flag), the YAML value only matters when the flag
  // wasn't passed.  Keep this explicit so future additions (e.g. a
  // ``--no-provenance`` flag) don't accidentally tangle precedence.
  const effectiveProvenance = args.includeProvenance
    ? true
    : config?.scan.provenance === true;

  // ---------------------------------------------------------------
  // If a bare directory was passed, expand it to a glob.  Kept here
  // (not in the config loader) because ``scan.patterns`` in YAML is
  // authored with globs already — only CLI args get the fast-path
  // directory expansion.
  // ---------------------------------------------------------------
  const expanded = await Promise.all(
    effectivePatterns.map(async (p) => {
      try {
        const stat = await fs.stat(p);
        if (stat.isDirectory()) {
          return `${p.replace(/\/$/, "")}/**/*.{ts,tsx,js,jsx}`;
        }
      } catch {
        // not a real path — treat as a glob
      }
      return p;
    })
  );

  const scanOptions = config?.scan.ignore ? { ignore: config.scan.ignore } : {};
  const result = await scan(expanded, scanOptions);

  // ---------------------------------------------------------------
  // Build the output payload.  The extractor passthrough goes on a
  // ``_extractor`` key with a leading underscore so Python's tool
  // inventory loader (which accepts any top-level dict with a
  // ``tools:`` key) ignores it as metadata rather than trying to
  // interpret it as a tool.  The Python ``sponsio scan`` side will
  // read ``_extractor`` in a follow-up PR.
  // ---------------------------------------------------------------
  const basePayload = effectiveProvenance
    ? result
    : { tools: result.tools };

  const payload: Record<string, unknown> = { ...basePayload };
  if (config && Object.keys(config.extractor).length > 0) {
    payload._extractor = config.extractor;
  }

  const json = args.pretty
    ? JSON.stringify(payload, null, 2)
    : JSON.stringify(payload);

  if (effectiveOut) {
    await fs.writeFile(effectiveOut, json + "\n", "utf8");
    process.stderr.write(
      `wrote ${result.tools.length} tools to ${effectiveOut}\n`
    );
  } else {
    process.stdout.write(json + "\n");
  }

  if (result.diagnostics.length) {
    for (const d of result.diagnostics) {
      process.stderr.write(
        `[${d.level}] ${d.filepath}:${d.line}  ${d.message}\n`
      );
    }
  }
}

main().catch((err) => {
  process.stderr.write(`${err.stack ?? err.message ?? err}\n`);
  process.exit(1);
});
