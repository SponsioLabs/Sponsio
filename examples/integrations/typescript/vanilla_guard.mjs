/**
 * Vanilla Guard — Banking Transfers (TypeScript)
 *
 * Same scenario as the Python version (../vanilla_guard.py).
 * Shows direct guard_before/guard_after usage via Pyodide.
 *
 * Usage:
 *   cd ts-sdk && npm install
 *   node ../examples/integrations/typescript/vanilla_guard.mjs
 */

import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const { Sponsio } = await import(resolve(__dirname, "..", "..", "..", "ts-sdk", "dist", "index.js"));

const CONTRACTS = [
  "tool `verify_identity` must precede `transfer_funds`",
  "tool `transfer_funds` at most 2 times",
];

async function main() {
  console.log("=== Vanilla Guard (TypeScript) ===\n");

  const guard = new Sponsio({
    agentId: "bank_bot",
    contracts: CONTRACTS,
    mode: "enforce",
  });

  // Simulate a banking agent session
  const calls = [
    { tool: "transfer_funds", args: { amount: 500, to: "acct_789" } },  // BLOCKED
    { tool: "verify_identity", args: { user: "alice" } },                // OK
    { tool: "transfer_funds", args: { amount: 500, to: "acct_789" } },  // OK
    { tool: "transfer_funds", args: { amount: 200, to: "acct_456" } },  // OK (2nd)
    { tool: "transfer_funds", args: { amount: 100, to: "acct_123" } },  // BLOCKED (limit)
  ];

  for (const call of calls) {
    const result = guard.guardBefore(call.tool, call.args);

    if (result.blocked) {
      console.log(`  [BLOCKED] ${call.tool}: ${result.message}`);
    } else {
      console.log(`  [OK]      ${call.tool}(${JSON.stringify(call.args)})`);
      guard.guardAfter(call.tool, "ok");
    }
  }

  console.log("\n" + guard.summary());
}

main().catch(console.error);
