/**
 * ``sponsio init`` — generate a starter ``sponsio.yaml`` skeleton.
 *
 * Mirrors the Python ``sponsio init`` command's non-interactive flag
 * surface: ``--mode``, ``--provider``, ``--judge-fallback``,
 * ``--no-sample``, ``--force``. The TS implementation is non-
 * interactive only — flags are required, no prompts. Use ``onboard``
 * if you want framework detection + tool scanning.
 *
 * Output is a minimal yaml the TS SDK can load: ``runtime.mode`` plus
 * an ``agents.<id>`` block with a sample contract (unless --no-sample).
 */
import { writeFileSync, existsSync, unlinkSync, readFileSync, mkdirSync, readdirSync, statSync } from "node:fs";
import { resolve, join, dirname, relative } from "node:path";
import * as yaml from "js-yaml";

const HELP =
  "sponsio init — generate a starter sponsio.yaml skeleton\n" +
  "\n" +
  "USAGE:\n" +
  "  sponsio init [target] [options]    # target = directory or yaml path\n" +
  "\n" +
  "OPTIONS:\n" +
  "  -a, --agent <id>                  Agent identifier (default: agent)\n" +
  "      --mode <observe|enforce>      Runtime mode (default: observe)\n" +
  "      --provider <name>             LLM provider hint for downstream\n" +
  "                                    `scan --llm` (openai|anthropic|gemini|none)\n" +
  "      --judge-fallback <a|d|s>      allow|deny|skip on judge failure\n" +
  "      --no-sample                   Don't include a starter contract\n" +
  "      --with-example                Skip the wizard and drop a runnable\n" +
  "                                    scaffold (sponsio.yaml + traces/)\n" +
  "                                    into the target dir; pair with\n" +
  "                                    `sponsio eval traces/`\n" +
  "      --force                       Overwrite existing sponsio.yaml\n" +
  "  -h, --help                        Show this help\n" +
  "\n" +
  "EXAMPLES:\n" +
  "  sponsio init\n" +
  "  sponsio init --mode enforce --no-sample\n" +
  "  sponsio init . --with-example\n" +
  "  sponsio init src/ --provider gemini --judge-fallback allow --force\n";

interface InitOptions {
  target: string;
  agent: string;
  mode: "observe" | "enforce";
  provider: "openai" | "anthropic" | "gemini" | "none";
  judgeFallback: "allow" | "deny" | "skip";
  noSample: boolean;
  withExample: boolean;
  force: boolean;
}

function parseArgs(argv: string[]): InitOptions {
  const opts: InitOptions = {
    target: ".",
    agent: "agent",
    mode: "observe",
    provider: "none",
    judgeFallback: "allow",
    noSample: false,
    withExample: false,
    force: false,
  };
  let positional = false;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "-h" || a === "--help") {
      process.stdout.write(HELP);
      process.exit(0);
    }
    if (a === "-a" || a === "--agent") {
      opts.agent = argv[++i];
      continue;
    }
    if (a === "--mode") {
      const v = argv[++i];
      if (v !== "observe" && v !== "enforce") {
        process.stderr.write(`--mode must be observe|enforce (got ${v})\n`);
        process.exit(2);
      }
      opts.mode = v;
      continue;
    }
    if (a === "--provider") {
      const v = argv[++i];
      if (!["openai", "anthropic", "gemini", "none"].includes(v)) {
        process.stderr.write(`--provider must be openai|anthropic|gemini|none (got ${v})\n`);
        process.exit(2);
      }
      opts.provider = v as InitOptions["provider"];
      continue;
    }
    if (a === "--judge-fallback") {
      const v = argv[++i];
      if (!["allow", "deny", "skip"].includes(v)) {
        process.stderr.write(`--judge-fallback must be allow|deny|skip (got ${v})\n`);
        process.exit(2);
      }
      opts.judgeFallback = v as InitOptions["judgeFallback"];
      continue;
    }
    if (a === "--no-sample") {
      opts.noSample = true;
      continue;
    }
    if (a === "--with-example") {
      opts.withExample = true;
      continue;
    }
    if (a === "--force") {
      opts.force = true;
      continue;
    }
    if (!positional && !a.startsWith("-")) {
      opts.target = a;
      positional = true;
      continue;
    }
    process.stderr.write(`unknown argument: ${a}\n${HELP}`);
    process.exit(2);
  }
  return opts;
}

function resolveOutPath(target: string): string {
  const abs = resolve(target);
  return abs.endsWith(".yaml") || abs.endsWith(".yml") ? abs : resolve(abs, "sponsio.yaml");
}

function locateInitExamplesDir(): string | null {
  // dist/init.js -> ../init_examples; src/init.ts -> ../init_examples
  const candidates = [join(__dirname, "..", "init_examples"), join(__dirname, "..", "..", "init_examples")];
  for (const c of candidates) {
    const evalDir = join(c, "eval");
    try {
      if (statSync(evalDir).isDirectory()) return c;
    } catch {
      // try next
    }
  }
  return null;
}

function copyDir(src: string, dst: string): { copied: number; skipped: number } {
  let copied = 0;
  let skipped = 0;
  for (const entry of readdirSync(src, { withFileTypes: true })) {
    const sp = join(src, entry.name);
    const dp = join(dst, entry.name);
    if (entry.isDirectory()) {
      mkdirSync(dp, { recursive: true });
      const r = copyDir(sp, dp);
      copied += r.copied;
      skipped += r.skipped;
    } else if (entry.isFile()) {
      if (existsSync(dp)) {
        skipped++;
      } else {
        writeFileSync(dp, readFileSync(sp));
        copied++;
      }
    }
  }
  return { copied, skipped };
}

function dropExampleScaffold(opts: InitOptions): void {
  const root = locateInitExamplesDir();
  if (!root) {
    process.stderr.write(
      `Error: cannot locate init_examples/ directory. ` +
        `Expected alongside the scanner package's dist/. ` +
        `(Override with SPONSIO_INIT_EXAMPLES_DIR if running from a non-standard layout.)\n`,
    );
    process.exit(1);
  }
  const overrideRoot = process.env.SPONSIO_INIT_EXAMPLES_DIR;
  const sourceRoot = overrideRoot && existsSync(overrideRoot) ? overrideRoot : root;
  const exampleDir = join(sourceRoot, "eval");

  const targetAbs = resolve(opts.target);
  const targetDir = targetAbs.endsWith(".yaml") || targetAbs.endsWith(".yml") ? dirname(targetAbs) : targetAbs;
  mkdirSync(targetDir, { recursive: true });

  // Bail if sponsio.yaml exists and --force not set.
  const targetYaml = join(targetDir, "sponsio.yaml");
  if (existsSync(targetYaml) && !opts.force) {
    process.stderr.write(`✗ ${targetYaml} already exists. Pass --force to overwrite.\n`);
    process.exit(1);
  }
  if (opts.force && existsSync(targetYaml)) unlinkSync(targetYaml);

  const result = copyDir(exampleDir, targetDir);
  process.stdout.write(
    `✓ Wrote scaffold to ${targetDir}\n` +
      `  ${result.copied} file(s) copied${result.skipped > 0 ? `, ${result.skipped} skipped (already exist; pass --force for full overwrite of yaml)` : ""}\n` +
      `\nNext:\n` +
      `  cd ${relative(process.cwd(), targetDir) || "."}\n` +
      `  sponsio eval traces/ --config sponsio.yaml\n`,
  );
}

export async function runInitCli(argv: string[]): Promise<void> {
  const opts = parseArgs(argv);

  if (opts.withExample) {
    dropExampleScaffold(opts);
    return;
  }

  const outPath = resolveOutPath(opts.target);

  if (existsSync(outPath) && !opts.force) {
    process.stderr.write(
      `✗ ${outPath} already exists. Pass --force to overwrite, or delete the file.\n`,
    );
    process.exit(1);
  }
  if (opts.force && existsSync(outPath)) {
    unlinkSync(outPath);
  }

  const contracts: { E: string }[] = opts.noSample
    ? []
    : [{ E: "tool `check_policy` must precede `issue_refund`" }];

  const payload: Record<string, unknown> = {
    version: 1,
    runtime: { mode: opts.mode },
    judge: { fallback_mode: opts.judgeFallback, circuit_breaker: true },
    agents: {
      [opts.agent]: { contracts },
    },
  };
  if (opts.provider !== "none") {
    payload.extractor = { provider: opts.provider };
  }

  const header =
    "# Generated by: sponsio init\n" +
    "# Starter sponsio.yaml — edit contracts: to add deterministic rules.\n" +
    "# See `sponsio patterns` for the supported NL pattern catalog.\n\n";
  writeFileSync(outPath, header + yaml.dump(payload, { lineWidth: 100 }), "utf-8");
  process.stdout.write(`✓ wrote ${outPath} (mode: ${opts.mode}, agent: ${opts.agent})\n`);
  if (contracts.length === 0) {
    process.stdout.write(
      `  tip: add at least one contract under agents.${opts.agent}.contracts:\n` +
        `       - E: "tool \`<gate>\` must precede \`<action>\`"\n`,
    );
  }
  process.stdout.write(
    `\nNext: run \`sponsio scan <src>\` to mine contracts from your code, or\n` +
      `      edit the file by hand and validate with \`sponsio validate\`.\n`,
  );
}
