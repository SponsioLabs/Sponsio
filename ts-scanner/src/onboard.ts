/**
 * ``sponsio-scan-ts onboard`` — TypeScript-first onboarding for
 * Node.js agent codebases.
 *
 * 1. Static-scan the tree for tool definitions (same engine as
 *    ``scan``).
 * 2. Write a temp ``.json`` inventory and, when the Python ``sponsio``
 *    CLI is on ``PATH``, run
 *    ``sponsio scan <inventory.json> -o <sponsio.yaml>`` for the
 *    same inference / starter-pack / pack behaviour as
 *    ``sponsio onboard .`` in a Python project.
 * 3. If ``sponsio`` is missing, write a *minimal* ``sponsio.yaml``
 *    (observe mode + name-heuristic NL ``E:`` strings that the TS
 *    runtime can parse) so ``new Sponsio({ config: ... })`` works
 *    without a Python install.
 *
 * The TS SDK has no LTL / pack / sto runtime — the conservative NL
 * fallback is intentionally det-only. Users who install ``sponsio``
 * get the full Python output (including includes / structured rules)
 * and the same file still loads in TS (unsupported entries are
 * skipped with a one-time warning in ``@sponsio/sdk``).
 */

import { existsSync, readFileSync, unlinkSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join, resolve, dirname } from "path";
import { spawnSync } from "child_process";
import { dump } from "js-yaml";
import { scan } from "./index";

/* ----------------------------------------------------------------- */

export type TsOnboardFramework =
  | "langgraph"
  | "vercel"
  | "openai"
  | "mcp"
  | "claude"
  | "google_adk"
  | "none";

export interface OnboardOptions {
  /** Project root; ``sponsio.yaml`` is written under this path (unless a ``.yaml`` file path is given as target). */
  target: string;
  /** Agent id; default ``"agent"`` to match ``sponsio scan --agent`` / ``sponsio onboard``. */
  agent: string;
  mode: "observe" | "enforce";
  /** Overwrite an existing ``sponsio.yaml`` at the same path. */
  force: boolean;
  /** Pass ``--llm`` through to ``sponsio scan`` (requires a provider from env; see ``docs/cli.md``). */
  llm: boolean;
  /**
   * Never shell out to Python; always use the det-only NL fallback
   * (for CI, or to guarantee zero Python in the build graph).
   */
  pyNever: boolean;
}

export interface OnboardResult {
  outPath: string;
  root: string;
  agent: string;
  /** ``python`` = ``sponsio scan <json>`` succeeded. ``fallback`` = wrote minimal yaml without ``sponsio``. */
  method: "python" | "fallback";
  toolCount: number;
  /** Single-line user-facing message (for stderr in the CLI). */
  message: string;
  wrapSnippet: string;
}

/* ----------------------------------------------------------------- */

const IRREV_RE = /delete|drop|wipe|purge|destroy|deploy|refund|execute|shell|subprocess|sql|send_email|issue_refund|transfer|approve_payment/i;

/**
 * Suggest 1–3 deterministic ``E:`` strings that the TS ``parseNl`` can
 * compile — conservative defaults when the Python CLI is missing.
 */
export function suggestDetNlContracts(names: string[]): { E: string }[] {
  const nset = new Set(names);
  const out: { E: string }[] = [];
  for (const n of names) {
    if (IRREV_RE.test(n)) {
      if (nset.has("confirm_with_user") || nset.has("check_policy") || nset.has("verify")) {
        const gate = nset.has("confirm_with_user")
          ? "confirm_with_user"
          : nset.has("check_policy")
            ? "check_policy"
            : "verify";
        out.push({ E: `tool \`${gate}\` must precede \`${n}\`` });
      } else {
        out.push({ E: `tool \`${n}\` at most once` });
      }
    }
    if (out.length >= 3) break;
  }
  if (out.length === 0) {
    const c = names.find((x) => /send|email|message|post|notify|webhook|slack|sms/i.test(x));
    if (c) out.push({ E: `tool \`${c}\` at most 5 times` });
  }
  return out;
}

/**
 * Heuristic framework id from the nearest ``package.json`` in
 * ``root`` — used for the printed TypeScript wrap snippet only.
 */
export function detectFramework(root: string): TsOnboardFramework {
  const pkgPath = join(root, "package.json");
  if (!existsSync(pkgPath)) return "none";
  let raw: { dependencies?: Record<string, string>; devDependencies?: Record<string, string> };
  try {
    raw = JSON.parse(readFileSync(pkgPath, "utf-8")) as {
      dependencies?: Record<string, string>;
      devDependencies?: Record<string, string>;
    };
  } catch {
    return "none";
  }
  const d = { ...raw.dependencies, ...raw.devDependencies };
  if (d["@modelcontextprotocol/sdk"]) return "mcp";
  if (d["@langchain/langgraph"] || d["@langchain/core"]) return "langgraph";
  if (d["@anthropic-ai/claude-agent-sdk"] || d["claude-agent-sdk"]) return "claude";
  if (d["@google/adk"]) return "google_adk";
  if (d["ai"] || d["@ai-sdk/openai"]) return "vercel";
  if (d["openai"]) return "openai";
  return "none";
}

function wrapSnippetFor(framework: TsOnboardFramework, agent: string, configRel: string): string {
  const cfg = JSON.stringify(configRel);
  const ag = JSON.stringify(agent);
  switch (framework) {
    case "langgraph":
    case "mcp":
      return [
        `import { Sponsio } from "@sponsio/sdk";`,
        `import { wrapTools } from "@sponsio/sdk/langchain";`,
        `import { ToolNode } from "@langchain/langgraph/prebuilt";`,
        ``,
        `const guard = new Sponsio({ config: ${cfg}, agentId: ${ag} });`,
        `const toolNode = new ToolNode(wrapTools(tools, guard));`,
      ].join("\n");
    case "claude":
      return [
        `import { ClaudeSDKClient } from "claude-agent-sdk";`,
        `import { Sponsio } from "@sponsio/sdk";`,
        `import { sponsioHooks } from "@sponsio/sdk/claude-agent";`,
        ``,
        `const guard = new Sponsio({ config: ${cfg}, agentId: ${ag} });`,
        `const client = new ClaudeSDKClient({ hooks: sponsioHooks(guard) });`,
      ].join("\n");
    case "google_adk":
      return [
        `import { Sponsio } from "@sponsio/sdk";`,
        `import { wrapGoogleAdkTools } from "@sponsio/sdk/google-adk";`,
        ``,
        `const guard = new Sponsio({ config: ${cfg}, agentId: ${ag} });`,
        `const guardedTools = wrapGoogleAdkTools(tools, guard);`,
        `// pass guardedTools to new LlmAgent({ tools: guardedTools, ... })`,
      ].join("\n");
    case "none":
      return [
        `import { Sponsio } from "@sponsio/sdk";`,
        ``,
        `const guard = new Sponsio({ config: ${cfg}, agentId: ${ag} });`,
        ``,
        `// Wrap your tool loop:`,
        `const check = guard.guardBefore(toolName, toolArgs);`,
        `if (check.allowed) {`,
        `  const output = await runTool(toolName, toolArgs);`,
        `  await guard.guardAfter(toolName, output);`,
        `} else {`,
        `  // feed check.detViolations[0].message back to the model`,
        `}`,
      ].join("\n");
    case "vercel":
      return [
        `import { Sponsio } from "@sponsio/sdk";`,
        `import { sponsioMiddleware } from "@sponsio/sdk/vercel-ai";`,
        `import { wrapLanguageModel } from "ai";`,
        ``,
        `const guard = new Sponsio({ config: ${cfg}, agentId: ${ag} });`,
        `// const model = wrapLanguageModel({ model, middleware: sponsioMiddleware(guard) })`,
      ].join("\n");
    case "openai":
      return [
        `import OpenAI from "openai";`,
        `import { Sponsio } from "@sponsio/sdk";`,
        `import { wrapOpenAI } from "@sponsio/sdk/openai";`,
        ``,
        `const guard = new Sponsio({ config: ${cfg}, agentId: ${ag} });`,
        `const client = wrapOpenAI(new OpenAI(), guard);`,
      ].join("\n");
  }
}

function resolveOnboardPaths(
  targetArg: string
): { root: string; outPath: string; configRelForSnippet: string } {
  const abs = resolve(targetArg);
  if (abs.endsWith(".yaml") || abs.endsWith(".yml")) {
    return {
      root: dirname(abs),
      outPath: abs,
      configRelForSnippet: "sponsio.yaml",
    };
  }
  return {
    root: abs,
    outPath: join(abs, "sponsio.yaml"),
    configRelForSnippet: "sponsio.yaml",
  };
}

function defaultScanGlobs(root: string): string[] {
  if (existsSync(join(root, "src"))) {
    return [
      "src/**/*.ts",
      "src/**/*.tsx",
      "src/**/*.js",
      "src/**/*.jsx",
    ];
  }
  return ["**/*.{ts,tsx,js,jsx}"];
}

/**
 * Assemble a minimal, TS-runtime-friendly ``sponsio.yaml`` when the
 * ``sponsio`` CLI is unavailable.
 */
export function buildFallbackPayload(
  toolNames: string[],
  inv: { tools: unknown[] },
  agent: string,
  mode: "observe" | "enforce"
): Record<string, unknown> {
  const contracts = suggestDetNlContracts(toolNames);
  return {
    version: 1,
    runtime: { mode },
    tools: inv.tools ?? [],
    agents: {
      [agent]: { contracts: contracts.length ? contracts : [] },
    },
  };
}

/**
 * The full ``onboard`` operation — used by the CLI and (optionally)
 * library callers.
 */
export async function runOnboard(opts: OnboardOptions): Promise<OnboardResult> {
  const { root, outPath, configRelForSnippet } = resolveOnboardPaths(opts.target);
  if (!existsSync(root)) {
    throw new Error(`[onboard] not a path: ${root}`);
  }
  if (existsSync(outPath) && !opts.force) {
    throw new Error(
      `[onboard] ${outPath} already exists. Pass --force to overwrite, or delete the file.`
    );
  }
  if (opts.force && existsSync(outPath)) {
    try {
      unlinkSync(outPath);
    } catch (e) {
      const m = e instanceof Error ? e.message : String(e);
      throw new Error(`[onboard] cannot remove ${outPath}: ${m}`);
    }
  }

  const patterns = defaultScanGlobs(root);
  const so = await scan(patterns, { cwd: root });
  const toolNames = so.tools.map((t) => t.function.name);
  const invJson = { tools: so.tools };
  if (so.diagnostics.length) {
    for (const d of so.diagnostics) {
      process.stderr.write(
        `[${d.level}] ${d.filepath}:${d.line}  ${d.message}\n`
      );
    }
  }

  const absTmp = join(
    tmpdir(),
    `sponsio-onboard-${process.pid}-${Date.now()}.json`
  );
  writeFileSync(absTmp, JSON.stringify(invJson) + "\n", "utf-8");

  let method: "python" | "fallback" = "fallback";
  let message = "";

  if (!opts.pyNever) {
    const args = [
      "scan",
      absTmp,
      "-o",
      outPath,
      "-a",
      opts.agent,
    ];
    if (opts.llm) args.push("--llm");
    // Run from the project root so default ``-o sponsio.yaml``-relative
    // paths line up. We pass an *absolute* -o, so CWD is only for any
    // relative path inside the scan engine; inventory is a temp file
    // with no relative deps.
    const r = spawnSync("sponsio", args, {
      encoding: "utf-8",
      stdio: ["ignore", "pipe", "pipe"],
      cwd: root,
      env: { ...process.env },
    });
    if (r.error && (r.error as NodeJS.ErrnoException).code === "ENOENT") {
      method = "fallback";
      message =
        "sponsio (Python CLI) not on PATH — wrote a minimal det-only sponsio.yaml. " +
        "Install: pip install sponsio, then re-run for the same output as in a Python repo (packs, LLM, …).";
    } else if (r.status === 0) {
      if (r.stderr) process.stderr.write(r.stderr);
      if (r.stdout) process.stdout.write(r.stdout);
      method = "python";
    } else {
      const errMsg = (r.error as Error)?.message ?? r.stderr?.slice(0, 2000) ?? "";
      if (r.stderr) process.stderr.write(r.stderr);
      process.stderr.write(
        `[onboard] sponsio scan failed (${r.status}): ${errMsg}\n` +
          "  Falling back to a minimal `sponsio.yaml` (det-only NL rules).\n"
      );
      method = "fallback";
      message = "sponsio scan failed — used minimal det-only yaml fallback instead.";
    }
  }

  if (method === "fallback") {
    const payload = buildFallbackPayload(
      toolNames,
      invJson,
      opts.agent,
      opts.mode
    );
    const header =
      "# Generated by: sponsio-scan-ts onboard (Python CLI missing or `sponsio scan` errored)\n" +
      "# Re-run with `pip install sponsio` on PATH for full `sponsio scan` / pack inference.\n";
    writeFileSync(
      outPath,
      header + dump(payload, { lineWidth: 100, noRefs: true }),
      "utf-8"
    );
  }

  try {
    unlinkSync(absTmp);
  } catch {
    // best-effort temp cleanup
  }

  const fw = detectFramework(root);
  const wrapSnippet = wrapSnippetFor(
    fw,
    opts.agent,
    configRelForSnippet
  );

  if (method === "fallback" && message) {
    process.stderr.write(`[onboard] ${message}\n`);
  }

  return {
    outPath,
    root,
    agent: opts.agent,
    method,
    toolCount: toolNames.length,
    message,
    wrapSnippet,
  };
}

/* ----------------------------------------------------------------- */

function parseOnboardArgs(argv: string[]): OnboardOptions & { help: boolean } {
  const out: OnboardOptions & { help: boolean } = {
    target: ".",
    agent: "agent",
    mode: "observe",
    force: false,
    llm: false,
    pyNever: false,
    help: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "-h" || a === "--help") {
      out.help = true;
    } else if (a === "--force") {
      out.force = true;
    } else if (a === "--llm") {
      out.llm = true;
    } else if (a === "--py-never" || a === "--no-python") {
      out.pyNever = true;
    } else if (a === "--agent" || a === "-a") {
      out.agent = argv[++i] ?? "agent";
    } else if (a.startsWith("-")) {
      process.stderr.write(`unknown flag: ${a}\n`);
      process.exit(2);
    } else {
      out.target = a;
    }
  }
  return out;
}

const ONBOARD_HELP = [
  "sponsio-scan-ts onboard — static-scan JS/TS, write sponsio.yaml, print wrap snippet",
  "",
  "USAGE:",
  "  sponsio-scan-ts onboard [path] [options]     # path = project root (default: .)",
  "",
  "  When the ``sponsio`` CLI (Python) is on PATH, runs the same",
  "  ``sponsio scan <tool-inventory.json>`` pass as a manual",
  "  ``npx @sponsio/scan-ts | sponsio`` pipeline — so you get the",
  "  same starter contracts / packs as ``sponsio onboard .`` in a",
  "  Python repo. Without ``sponsio``, writes a det-only minimal yaml",
  "  (observe mode) that ``new Sponsio({ config: 'sponsio.yaml' })``",
  "  can still load in TypeScript.",
  "",
  "OPTIONS:",
  "  -a, --agent <id>   Agent id in yaml (default: agent, matches `sponsio scan`)",
  "  --llm              Pass --llm to `sponsio scan` (needs API keys; see docs/cli.md#provider-matrix)",
  "  --force            Overwrite an existing sponsio.yaml",
  "  --py-never         Never call Python; always write the det-only fallback",
  "  -h, --help         Show this help",
  "",
  "REQUIRES: npm `yaml` for `@sponsio/sdk` when using `config:` —",
  "  `npm install yaml` (pulled in automatically if you add `@sponsio/sdk` and follow its install line).",
  "",
].join("\n");

/**
 * ``main`` for ``sponsio-scan-ts onboard ...`` — *sync entry from cli.ts*.
 */
export async function runOnboardCli(argv: string[]): Promise<void> {
  const p = parseOnboardArgs(argv);
  if (p.help) {
    process.stdout.write(ONBOARD_HELP);
    return;
  }
  const res = await runOnboard({
    target: p.target,
    agent: p.agent,
    mode: p.mode,
    force: p.force,
    llm: p.llm,
    pyNever: p.pyNever,
  });
  process.stdout.write("\n");
  process.stdout.write("· framework: " + detectFramework(res.root) + " (heuristic from package.json)\n");
  process.stdout.write("· method:    " + res.method + (res.method === "python" ? " (sponsio scan)" : " (det-only fallback yaml)") + "\n");
  process.stdout.write("· tools:     " + res.toolCount + "\n");
  process.stdout.write("· wrote:     " + res.outPath + "\n");
  process.stdout.write("\nAdd to your agent entry file:\n\n");
  process.stdout.write(res.wrapSnippet + "\n");
  process.stdout.write(
    "\nNext steps (TypeScript):\n" +
      "  npx sponsio-scan-ts validate        # parse check + det/sto counts\n" +
      "  npx sponsio-scan-ts doctor          # env health\n" +
      "  # run your agent, then…\n" +
      "  npx sponsio-scan-ts report --since 24h\n" +
      "  # once false positives are pruned:\n" +
      "  export SPONSIO_MODE=enforce\n",
  );
}

