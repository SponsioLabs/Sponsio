/**
 * LangGraph / LangChain.js Guard — HR Onboarding (TypeScript)
 *
 * Same scenario as the Python version (../python/langgraph_guard.py).
 * Demonstrates the wrapTools integration for LangChain.js / LangGraph.js.
 *
 * Build the SDK first, then run from repo root:
 *   cd ts/packages/sdk && npm install && npm run build && cd -
 *   node examples/integrations/typescript/langgraph_guard.mjs
 *
 * In real LangGraph code:
 *   import { ToolNode } from "@langchain/langgraph/prebuilt";
 *   const toolNode = new ToolNode(wrapTools(tools, guard));
 */

import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const sdkDist = resolve(__dirname, "..", "..", "..", "ts", "packages", "sdk", "dist");
const { Sponsio } = await import(resolve(sdkDist, "index.js"));
const { wrapTools } = await import(resolve(sdkDist, "integrations", "langchain.js"));

const CONTRACTS = [
  "tool `run_background_check` must precede `approve_candidate`",
  "tools `approve_candidate` and `reject_candidate` are mutually exclusive",
];

// Mock LangChain-style tools (have a .name and an .invoke method).
const tools = [
  {
    name: "run_background_check",
    invoke: async (args) => `Background check passed for ${args.candidate_id}`,
  },
  {
    name: "approve_candidate",
    invoke: async (args) => `Candidate ${args.candidate_id} approved`,
  },
  {
    name: "reject_candidate",
    invoke: async (args) =>
      `Candidate ${args.candidate_id} rejected: ${args.reason}`,
  },
];

async function main() {
  console.log("=== LangGraph / LangChain.js Guard (TypeScript) ===\n");

  const guard = new Sponsio({
    agentId: "hr_bot",
    contracts: CONTRACTS,
    mode: "enforce",
  });

  // wrapTools clones each tool and intercepts .invoke with guardBefore /
  // guardAfter. The original `tools` array is left untouched.
  const guarded = wrapTools(tools, guard);
  const byName = Object.fromEntries(guarded.map((t) => [t.name, t]));

  const calls = [
    { tool: "approve_candidate", args: { candidate_id: "C-001" } },                        // BLOCKED (no bg check)
    { tool: "run_background_check", args: { candidate_id: "C-001" } },                     // OK
    { tool: "approve_candidate", args: { candidate_id: "C-001" } },                        // OK
    { tool: "reject_candidate", args: { candidate_id: "C-001", reason: "changed mind" } }, // BLOCKED (mutual exclusion)
  ];

  for (const call of calls) {
    const output = await byName[call.tool].invoke(call.args);
    const tag = String(output).startsWith("BLOCKED") ? "[BLOCKED]" : "[OK]     ";
    console.log(`  ${tag} ${call.tool}: ${output}`);
  }

  console.log("");
  guard.printSummary();
}

main().catch(console.error);
