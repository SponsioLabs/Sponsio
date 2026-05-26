/**
 * ``sponsio validate`` — CI-friendly config check.
 *
 * Mirrors the first half of Python's ``sponsio validate`` command:
 * parse sponsio.yaml, surface structural errors, and report what
 * the TS runtime can / cannot handle. Exits non-zero when the file
 * doesn't load or when --strict is set and the loader had to skip
 * any unsupported yaml items.
 *
 * Usage:
 *
 *   sponsio validate                    # validates ./sponsio.yaml
 *   sponsio validate ./config.yaml
 *   sponsio validate --agent support_bot
 *   sponsio validate --strict           # non-zero on any skip
 *   sponsio validate --format json      # machine-readable
 */

import { existsSync } from "node:fs";
import { resolve } from "node:path";

interface ValidateArgs {
  path: string;
  agent?: string;
  strict: boolean;
  format: "text" | "json";
  help: boolean;
}

const HELP =
  [
    "sponsio validate — parse sponsio.yaml and report what the TS runtime will use",
    "",
    "USAGE:",
    "  sponsio validate [path] [options]",
    "",
    "ARGUMENTS:",
    "  [path]            Path to sponsio.yaml (default: ./sponsio.yaml)",
    "",
    "OPTIONS:",
    "      --agent <id>  Which agent block to project (default: first one found)",
    "      --strict      Exit non-zero if the loader had to skip anything",
    "      --format <f>  'text' (default) or 'json'",
    "  -h, --help        Show this help",
    "",
    "EXIT CODES:",
    "  0  config loads and the TS runtime can honour every declared contract",
    "  1  yaml / schema error — config does not load at all",
    "  2  loaded with warnings (unsupported yaml items skipped); only with --strict",
  ].join("\n") + "\n";

function parseArgs(argv: string[]): ValidateArgs {
  const a: ValidateArgs = {
    path: "sponsio.yaml",
    strict: false,
    format: "text",
    help: false,
  };
  const positional: string[] = [];
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === "-h" || flag === "--help") a.help = true;
    else if (flag === "--agent") a.agent = argv[++i];
    else if (flag === "--strict") a.strict = true;
    else if (flag === "--format") {
      const v = argv[++i];
      if (v !== "text" && v !== "json") {
        throw new Error(`--format must be 'text' or 'json', got ${v}`);
      }
      a.format = v;
    } else if (flag.startsWith("-")) {
      throw new Error(`unknown flag: ${flag}`);
    } else {
      positional.push(flag);
    }
  }
  if (positional.length > 1) {
    throw new Error(
      `validate accepts at most one positional path, got ${positional.length}`,
    );
  }
  if (positional[0]) a.path = positional[0];
  return a;
}

interface ValidateResult {
  path: string;
  agent: string;
  mode: string | undefined;
  detContracts: number;
  stoContracts: number;
  judgeConfigured: boolean;
  skipped: Array<{ kind: string; detail: string }>;
  unparseableNl: string[];
  error?: string;
}

export async function runValidateCli(argv: string[]): Promise<void> {
  let args: ValidateArgs;
  try {
    args = parseArgs(argv);
  } catch (err) {
    process.stderr.write(
      `${err instanceof Error ? err.message : String(err)}\n`,
    );
    process.exit(2);
  }
  if (args.help) {
    process.stdout.write(HELP);
    return;
  }

  const resolved = resolve(process.cwd(), args.path);
  if (!existsSync(resolved)) {
    process.stderr.write(`[sponsio] config not found: ${resolved}\n`);
    process.exit(1);
  }

  // Lazy-load: avoids paying for the ts-sdk import on `--help`.
  let result: ValidateResult;
  try {
    // Import through the dist of ``@sponsio/sdk`` when the scanner
    // ships on npm, and through a relative path when we're running
    // in-repo. ``require.resolve`` lets us tolerate either.
    const sdk = await loadSdk();
    const loaded = sdk.loadSponsoConfig(resolved, args.agent ?? "agent");
    // ``loaded.contracts`` is ``(string | DetFormula)[]``:
    //  - strings are NL that still needs ``parseNl`` (some rule
    //    factories might reject, count those separately);
    //  - objects are already-compiled DetFormula entries (structured
    //    patterns, raw LTL, A/G composition) — those are det-valid by
    //    construction.
    const unparseable: string[] = [];
    let preCompiled = 0;
    for (const c of loaded.contracts) {
      if (typeof c === "string") {
        try {
          if (!sdk.parseNl(c)) unparseable.push(c);
        } catch {
          unparseable.push(c);
        }
      } else {
        preCompiled++;
      }
    }
    result = {
      path: resolved,
      agent: args.agent ?? "agent",
      mode: loaded.mode,
      detContracts:
        preCompiled +
        (loaded.contracts.filter((c) => typeof c === "string").length -
          unparseable.length),
      stoContracts: loaded.stoSpecs?.length ?? 0,
      judgeConfigured: !!loaded.judge,
      skipped: loaded.skipped ?? [],
      unparseableNl: unparseable,
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    const error = { path: resolved, error: msg };
    if (args.format === "json") {
      process.stdout.write(JSON.stringify(error, null, 2) + "\n");
    } else {
      process.stderr.write(`[sponsio] validate failed: ${msg}\n`);
    }
    process.exit(1);
  }

  if (args.format === "json") {
    process.stdout.write(JSON.stringify(result, null, 2) + "\n");
  } else {
    process.stdout.write(renderText(result));
  }

  const hasWarnings =
    result.skipped.length > 0 ||
    result.unparseableNl.length > 0 ||
    // Sto contracts are not supported in this build (the engine is
    // deterministic-only). They are rejected at load time. Surface as
    // a strict-mode warning so CI catches it.
    result.stoContracts > 0;
  if (args.strict && hasWarnings) process.exit(2);
}

function renderText(r: ValidateResult): string {
  const lines: string[] = [];
  lines.push(`✓ loaded ${r.path}`);
  lines.push(`  agent:            ${r.agent}`);
  lines.push(`  runtime.mode:     ${r.mode ?? "(unset — falls to observe)"}`);
  lines.push(`  det contracts:    ${r.detContracts}`);
  lines.push(
    `  sto contracts:    ${r.stoContracts}${r.stoContracts > 0 ? " (not supported in this build)" : ""}`,
  );
  lines.push(
    `  judge configured: ${r.judgeConfigured ? "yes (not supported in this build)" : "no"}`,
  );
  if (r.unparseableNl.length) {
    lines.push("");
    lines.push(`! ${r.unparseableNl.length} contract(s) failed NL parsing:`);
    for (const c of r.unparseableNl) lines.push(`    - ${c}`);
  }
  if (r.skipped.length) {
    lines.push("");
    lines.push(`! ${r.skipped.length} yaml item(s) skipped (Python-only):`);
    for (const s of r.skipped) lines.push(`    - [${s.kind}] ${s.detail}`);
  }
  lines.push("");
  return lines.join("\n");
}

type SdkMod = {
  loadSponsoConfig: (
    p: string,
    agent: string,
  ) => {
    // NL string or pre-built DetFormula (has ``patternName`` / ``desc``).
    contracts: Array<string | { desc: string; patternName: string }>;
    stoSpecs?: unknown[];
    judge?: unknown;
    mode?: string;
    skipped?: Array<{ kind: string; detail: string }>;
  };
  parseNl: (text: string) => unknown | null;
};

async function loadSdk(): Promise<SdkMod> {
  // Resolve order: installed ``@sponsio/sdk`` > sibling repo path
  // (useful during local dev when ``scan-ts`` runs via npm link but
  // the SDK isn't hoisted). The bare specifier goes through a
  // function-wrapped require so TS doesn't complain when the SDK
  // isn't a declared dep of this package.
  const dynamicImport = new Function(
    "s",
    "return import(s)",
  ) as (s: string) => Promise<SdkMod>;
  try {
    return await dynamicImport("@sponsio/sdk");
  } catch {
    const devPath = resolve(__dirname, "..", "..", "ts-sdk", "dist", "index.js");
    return await dynamicImport(devPath);
  }
}
