/**
 * LangGraph / LangChain.js Guard — HR Onboarding (TypeScript)
 *
 * Same scenario as the Python version (../python/langgraph_guard.py).
 * Shows wrapTools integration for LangChain.js / LangGraph.js.
 *
 * Usage:
 *   cd ts-sdk && npm install
 *   node ../examples/integrations/typescript/langgraph_guard.mjs
 */

import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const { Sponsio } = await import(resolve(__dirname, "..", "..", "..", "ts-sdk", "dist", "index.js"));

const CONTRACTS = [
  "tool `run_background_check` must precede `approve_candidate`",
  "tools `approve_candidate` and `reject_candidate` are mutually exclusive",
];

// Tool implementations
const tools = {
  run_background_check: (args) => `Background check passed for ${args.candidate_id}`,
  approve_candidate: (args) => `Candidate ${args.candidate_id} approved`,
  reject_candidate: (args) => `Candidate ${args.candidate_id} rejected: ${args.reason}`,
};

async function main() {
  console.log("=== LangGraph Guard (TypeScript) ===\n");

  const guard = new Sponsio({
    agentId: "hr_bot",
    contracts: CONTRACTS,
    mode: "enforce",
  });

  // Simulate what LangGraph's ToolNode would do
  const calls = [
    { tool: "approve_candidate", args: { candidate_id: "C-001" } },      // BLOCKED (no bg check)
    { tool: "run_background_check", args: { candidate_id: "C-001" } },   // OK
    { tool: "approve_candidate", args: { candidate_id: "C-001" } },      // OK (bg check done)
    { tool: "reject_candidate", args: { candidate_id: "C-001", reason: "changed mind" } },  // BLOCKED (mutual exclusion)
  ];

  for (const call of calls) {
    const result = guard.guardBefore(call.tool, call.args);

    if (result.blocked) {
      const reason = result.detViolations[0]?.message ?? result.message;
      console.log(`  [BLOCKED] ${call.tool}: ${reason}`);
    } else {
      const output = tools[call.tool](call.args);
      console.log(`  [OK]      ${call.tool}: ${output}`);
      await guard.guardAfter(call.tool, output);
    }
  }

  console.log("");
  guard.printSummary();
}

main().catch(console.error);
