/**
 * ``sponsio onboard`` — TypeScript-first onboarding for
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

import { existsSync, readFileSync, realpathSync, unlinkSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join, resolve, dirname } from "path";
import { spawnSync } from "child_process";
import { dump } from "js-yaml";
import fgImport from "fast-glob";
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
  /**
   * After writing ``sponsio.yaml``, push it to the local dashboard at
   * ``--push-url`` so it surfaces on the Scan page + Contract Library.
   * Passed through to ``sponsio scan --push`` when Python is
   * available; silently skipped by the fallback path (no dashboard
   * client in the TS scanner).
   */
  push?: boolean;
  pushUrl?: string;
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
    if (opts.push) {
      args.push("--push");
      if (opts.pushUrl) args.push("--push-url", opts.pushUrl);
    }
    // Run from the project root so default ``-o sponsio.yaml``-relative
    // paths line up. We pass an *absolute* -o, so CWD is only for any
    // relative path inside the scan engine; inventory is a temp file
    // with no relative deps.
    //
    // Strip ``node_modules/.bin`` from PATH for the spawn — when this
    // CLI was invoked via ``npx sponsio onboard``, PATH starts with
    // node_modules/.bin/ where the TS bin lives.  Bare ``sponsio``
    // would then resolve to THIS binary (self-recursion: the JSON
    // tool inventory we just wrote gets re-interpreted as a tool-
    // glob pattern by ``runScanCli``, returning 0 tools and writing
    // an empty yaml).  Excluding node_modules forces resolution
    // through the user's broader PATH where the Python ``sponsio``
    // CLI lives.
    const pythonOnlyPath = (process.env.PATH ?? "")
      .split(":")
      .filter((p) => p && !p.includes("node_modules/.bin"))
      .join(":");
    const r = spawnSync("sponsio", args, {
      encoding: "utf-8",
      stdio: ["ignore", "pipe", "pipe"],
      cwd: root,
      env: { ...process.env, PATH: pythonOnlyPath },
    });
    if (r.error && (r.error as NodeJS.ErrnoException).code === "ENOENT") {
      method = "fallback";
      message =
        "sponsio (Python CLI) not on PATH — wrote a minimal det-only sponsio.yaml. " +
        "Install: pip install sponsio, then re-run for the same output as in a Python repo (packs, LLM, …).";
    } else if (r.status === 0) {
      // Indent forwarded subprocess output col-2 so it sits inside
      // the wizard's body margin (banner col-0, body col-2).  Without
      // this, Python ``sponsio scan``'s ``✓ Wrote …`` summary +
      // ``· AST scan`` bullets jutted out at col-0 while the TS
      // recap above and below them was col-2.  Operates per-line so
      // multi-line stderr / stdout chunks render correctly.
      const indent = (s: string): string =>
        s
          .split("\n")
          .map((ln) => (ln.length > 0 ? "  " + ln : ln))
          .join("\n");
      if (r.stderr) process.stderr.write(indent(r.stderr));
      if (r.stdout) process.stdout.write(indent(r.stdout));
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
      "# Generated by: sponsio onboard (Python CLI missing or `sponsio scan` errored)\n" +
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

function parseOnboardArgs(argv: string[]): OnboardOptions & { help: boolean; emitContext: boolean } {
  const out: OnboardOptions & { help: boolean; emitContext: boolean } = {
    target: ".",
    agent: "agent",
    mode: "observe",
    force: false,
    llm: false,
    pyNever: false,
    help: false,
    emitContext: false,
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
    } else if (a === "--mode") {
      // Parity with the Python CLI's ``sponsio onboard --mode``.
      // ``sponsio init`` dispatches with ``--mode <observe|enforce>``
      // for both Python and TS projects; without this branch the
      // TS scanner errored with ``unknown flag: --mode`` and the
      // wizard's apply step bailed mid-flight.
      const v = argv[++i];
      if (v !== "observe" && v !== "enforce") {
        process.stderr.write(
          `--mode must be 'observe' or 'enforce' (got: ${v ?? "<none>"})\n`,
        );
        process.exit(2);
      }
      out.mode = v;
    } else if (a === "--push") {
      out.push = true;
    } else if (a === "--no-push") {
      out.push = false;
    } else if (a === "--push-url") {
      out.pushUrl = argv[++i];
    } else if (a === "--emit-context") {
      out.emitContext = true;
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
  "sponsio onboard — static-scan JS/TS, write sponsio.yaml, print wrap snippet",
  "",
  "USAGE:",
  "  sponsio onboard [path] [options]     # path = project root (default: .)",
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
  "  --push             Push the generated yaml to the local dashboard (needs `sponsio serve --dev` and Python)",
  "  --push-url <url>   Dashboard URL (default: http://127.0.0.1:8000)",
  "  --force            Overwrite an existing sponsio.yaml",
  "  --py-never         Never call Python; always write the det-only fallback",
  "  --emit-context     Skip writing the yaml; emit structured project context",
  "                     (framework / tool inventory / packs / existing yaml /",
  "                      policy docs / wrap snippet) as JSON to stdout. Pair",
  "                     with `sponsio prompt onboard` to drive the IDE agent",
  "                     through contract authoring without a separate LLM call.",
  "  -h, --help         Show this help",
  "",
  "REQUIRES: npm `yaml` for `@sponsio/sdk` when using `config:` —",
  "  `npm install yaml` (pulled in automatically if you add `@sponsio/sdk` and follow its install line).",
  "",
].join("\n");

/**
 * Find files that look like agent entry points — the ones that import
 * the model/framework and call it. The IDE agent uses this to avoid
 * a second discovery pass. Conservative scan: top-level + a few
 * common dirs only.
 */
async function detectEntryFileCandidates(
  root: string,
  framework: TsOnboardFramework,
): Promise<{ path: string; reason: string }[]> {
  // Provider/framework signals to grep for, by detected framework.
  const signals: Record<string, RegExp[]> = {
    vercel: [
      /\bgenerateText\s*\(/,
      /from\s+["']ai["']/,
      /from\s+["']@ai-sdk\//,
    ],
    claude: [/from\s+["']@anthropic-ai\/claude-agent-sdk["']/, /from\s+["']claude-agent-sdk["']/],
    langgraph: [/from\s+["']@langchain\/langgraph["']/, /createReactAgent\s*\(/],
    mcp: [/from\s+["']@modelcontextprotocol\/sdk\//],
    google_adk: [/from\s+["']@google\/adk["']/],
    openai: [/from\s+["']openai["']/, /\bnew\s+OpenAI\s*\(/],
    none: [],
  };
  const fwSignals = signals[framework] ?? [];
  if (fwSignals.length === 0) return [];

  const patterns = ["*.ts", "*.tsx", "*.js", "*.jsx", "src/**/*.{ts,tsx,js,jsx}", "app/**/*.{ts,tsx,js,jsx}"];
  const files = await fgImport(patterns, {
    cwd: root,
    onlyFiles: true,
    ignore: ["**/node_modules/**", "**/dist/**", "**/build/**", "**/__tests__/**", "**/*.test.*", "**/*.spec.*"],
  });
  const out: { path: string; reason: string }[] = [];
  for (const rel of files) {
    let text: string;
    try {
      text = readFileSync(join(root, rel), "utf-8");
    } catch {
      continue;
    }
    const matched: string[] = [];
    for (const re of fwSignals) {
      if (re.test(text)) matched.push(re.source);
    }
    if (matched.length > 0) out.push({ path: rel, reason: `matches: ${matched.join(", ")}` });
  }
  // Prefer fewer, deeper signals first (more matches usually = real entry).
  out.sort((a, b) => b.reason.length - a.reason.length);
  return out.slice(0, 5);
}

/**
 * Discover root-level policy docs (security.md / policy.md and case
 * variants) and bundle them with the onboard context payload. Mirrors
 * Python's ``policy_docs`` field.
 */
function collectPolicyDocs(root: string): { path: string; content: string }[] {
  const docs: { path: string; content: string }[] = [];
  const candidates = ["security.md", "SECURITY.md", "policy.md", "POLICY.md"];
  const seen = new Set<string>();
  for (const name of candidates) {
    const p = join(root, name);
    if (!existsSync(p)) continue;
    try {
      const real = realpathSync(p);
      if (seen.has(real)) continue;
      seen.add(real);
      docs.push({ path: name, content: readFileSync(p, "utf-8") });
    } catch {
      // skip unreadable
    }
  }
  return docs;
}

/**
 * Run the deterministic stages of onboard (framework detection + AST
 * scan + pack auto-select + policy doc discovery) and emit the
 * structured payload as JSON. Mirrors Python's ``--emit-context``.
 */
async function runEmitContext(opts: OnboardOptions): Promise<void> {
  const { root, outPath, configRelForSnippet } = resolveOnboardPaths(opts.target);
  if (!existsSync(root)) {
    process.stderr.write(`[onboard] not a path: ${root}\n`);
    process.exit(1);
  }
  const framework = detectFramework(root);
  const patterns = defaultScanGlobs(root);
  const so = await scan(patterns, { cwd: root });
  const tools = so.tools.map((t) => t.function);
  const wrapSnippet = wrapSnippetFor(framework, opts.agent, configRelForSnippet);
  let existingYaml = "";
  if (existsSync(outPath)) {
    try {
      existingYaml = readFileSync(outPath, "utf-8");
    } catch {
      // ignore
    }
  }
  const entryCandidates = await detectEntryFileCandidates(root, framework);
  const payload = {
    framework: { name: framework, evidence: "from package.json dependencies" },
    agent_id: opts.agent,
    tool_inventory: tools,
    auto_selected_packs: ["sponsio:core/universal"],
    needs_workspace: false,
    existing_yaml: existingYaml,
    policy_docs: collectPolicyDocs(root),
    wrap_snippet: wrapSnippet,
    entry_file_candidates: entryCandidates,
    out_path: outPath,
    next_steps_hint:
      "Run `npx sponsio prompt onboard` to get the contract-authoring " +
      "prompt template, apply it to this JSON in your own LLM context, " +
      `then write the resulting YAML to ${outPath} via Edit/Write, ` +
      "and patch the agent entry file (see `entry_file_candidates`) " +
      "with the wrap_snippet. Validate with `npx sponsio validate`.",
  };
  process.stdout.write(JSON.stringify(payload, null, 2) + "\n");
}

/**
 * ``main`` for ``sponsio onboard ...`` — *sync entry from cli.ts*.
 */
export async function runOnboardCli(argv: string[]): Promise<void> {
  const p = parseOnboardArgs(argv);
  if (p.help) {
    process.stdout.write(ONBOARD_HELP);
    return;
  }
  if (p.emitContext) {
    await runEmitContext({
      target: p.target,
      agent: p.agent,
      mode: p.mode,
      force: p.force,
      llm: p.llm,
      pyNever: p.pyNever,
    });
    return;
  }
  const res = await runOnboard({
    target: p.target,
    agent: p.agent,
    mode: p.mode,
    force: p.force,
    push: p.push,
    pushUrl: p.pushUrl,
    llm: p.llm,
    pyNever: p.pyNever,
  });
  process.stdout.write("\n");
  // Match Python ``sponsio onboard``'s recap shape so the wizard
  // → onboard transition reads as one program.  The bullet-list
  // ``· framework: …`` block was redundant with the ``✓ wrote
  // <path>`` summary that ``sponsio scan`` already printed when
  // method === python; drop it and use Python's "Add this to
  // your agent entry point:" / "Next:" framing for the rest.
  if (res.method === "fallback") {
    // Fallback path didn't go through ``sponsio scan``, so it
    // didn't print the summary itself — emit one here in the
    // same shape Python uses (``✓ <path>`` + col-6 sub-keys
    // under the col-2 ✓ marker).
    process.stdout.write(`  ✓ ${res.outPath}\n`);
    process.stdout.write(`      tools:      ${res.toolCount}\n`);
    process.stdout.write(`      mode:       ${p.mode}\n`);
    process.stdout.write("      method:     det-only fallback yaml\n");
    process.stdout.write("\n");
  }
  process.stdout.write("  Add this to your agent entry point:\n\n");
  for (const line of res.wrapSnippet.split("\n")) {
    process.stdout.write(`    ${line}\n`);
  }
  process.stdout.write("\n  Next:\n");
  process.stdout.write(
    "    npx sponsio doctor                    # env health\n" +
      "    npx sponsio validate                  # parse check\n" +
      "    npx sponsio report --since 24h        # what would have been blocked\n" +
      "    npx sponsio mode enforce              # one-shot flip when ready\n",
  );
}
