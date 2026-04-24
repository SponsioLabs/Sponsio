/**
 * Vanilla Guard — Banking Transfers (TypeScript)
 *
 * Same scenario as the Python version (../python/vanilla_guard.py).
 * Shows direct guardBefore / guardAfter usage with the native TS SDK.
 *
 * Demonstrates:
 *   - fluent ``contract(desc).assume().enforce()`` builder (TS parity
 *     with Python's ``from sponsio import contract``)
 *   - ``result.detViolations[0].message`` for structured feedback
 *   - ``await guard.guardAfter(...)`` (awaited so sto atoms would run)
 *   - ``guard.printSummary()`` for ad-hoc review
 *
 * Usage:
 *   cd ts-sdk && npm install && npm run build
 *   node ../examples/integrations/typescript/vanilla_guard.mjs
 */

import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const { Sponsio, contract } = await import(
  resolve(__dirname, "..", "..", "..", "ts-sdk", "dist", "index.js")
);

const CONTRACTS = [
  // Conditional A/E — the assumption fires the enforcement.
  contract("identity check before transfer")
    .assume("called `transfer_funds`")
    .enforce("must call `verify_identity` before `transfer_funds`"),
  // Unconditional rate limit — no .assume(), only .enforce().
  contract("transfer rate limit").enforce("tool `transfer_funds` at most 3 times"),
];

async function main() {
  console.log("=== Vanilla Guard (TypeScript) ===\n");

  const guard = new Sponsio({
    agentId: "bank_bot",
    contracts: CONTRACTS,
    mode: "enforce",
  });

  const plannedCalls = [
    { tool: "lookup_account", args: { account_id: "ACC-001" } },
    { tool: "transfer_funds", args: { to: "ACC-002", amount: 500 } }, // BLOCKED
    { tool: "verify_identity", args: { account_id: "ACC-001" } },
    { tool: "transfer_funds", args: { to: "ACC-002", amount: 500 } },
    { tool: "transfer_funds", args: { to: "ACC-003", amount: 200 } },
    { tool: "transfer_funds", args: { to: "ACC-004", amount: 100 } },
    { tool: "transfer_funds", args: { to: "ACC-005", amount: 50 } }, // BLOCKED (limit)
  ];

  for (const call of plannedCalls) {
    const check = guard.guardBefore(call.tool, call.args);

    if (check.blocked) {
      // Prefer detViolations[0].message over `check.message` when
      // you want to feed the reason back to the model — mirrors the
      // Python `check.det_violations[0].message` pattern.
      const reason = check.detViolations[0]?.message ?? check.message;
      console.log(`  [BLOCKED] ${call.tool}: ${reason}`);
      continue;
    }

    console.log(`  [OK]      ${call.tool}(${JSON.stringify(call.args)})`);
    await guard.guardAfter(call.tool, "ok");
  }

  console.log("");
  guard.printSummary();
}

main().catch(console.error);
