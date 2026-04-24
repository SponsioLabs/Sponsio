/**
 * OpenAI SDK Guard — Database Admin (TypeScript)
 *
 * Same scenario as the Python version (../openai_guard.py).
 * Shows wrapOpenAI integration via Pyodide.
 *
 * Usage:
 *   cd ts-sdk && npm install
 *   node ../examples/integrations/typescript/openai_guard.mjs
 */

import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const { Sponsio } = await import(resolve(__dirname, "..", "..", "..", "ts-sdk", "dist", "index.js"));

const CONTRACTS = [
  "tool `preview_query` must precede `execute_query`",
  "tool `execute_query` at most 3 times",
];

async function main() {
  console.log("=== OpenAI SDK Guard (TypeScript) ===\n");

  const guard = new Sponsio({
    agentId: "db_admin",
    contracts: CONTRACTS,
    mode: "enforce",
  });

  // Simulate tool calls (in real usage, wrapOpenAI intercepts the OpenAI client)
  const calls = [
    { tool: "execute_query", args: { sql: "DROP TABLE users" } },  // BLOCKED
    { tool: "preview_query", args: { sql: "SELECT * FROM users" } }, // OK
    { tool: "execute_query", args: { sql: "SELECT * FROM users" } }, // OK
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
