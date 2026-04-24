/**
 * OpenAI Agents SDK Guard — Travel Booking (TypeScript)
 *
 * Counterpart to Python's ``agents_sdk_guard.py``. Shows
 * ``wrapAgentsTools(tools, guard)`` — the native TS adapter for the
 * ``@openai/agents`` package.
 *
 * ``wrapAgentsTools`` is non-destructive: it returns new tool
 * objects with their ``execute`` function intercepted. The guard
 * runs ``guardBefore`` prior to the tool body and ``guardAfter`` on
 * the result — so det + sto contracts both fire at the action
 * boundary.
 *
 * Usage:
 *   cd ts-sdk && npm install && npm run build
 *   node ../examples/integrations/typescript/openai_agents_guard.mjs
 */

import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const { Sponsio, contract } = await import(
  resolve(__dirname, "..", "..", "..", "ts-sdk", "dist", "index.js")
);
const { wrapAgentsTools } = await import(
  resolve(
    __dirname,
    "..",
    "..",
    "..",
    "ts-sdk",
    "dist",
    "integrations",
    "openai-agents.js",
  )
);

// Shape a fake Agents SDK tool as `@openai/agents` does: a name,
// parameters (omitted here), and an async ``execute``.
function tool(name, execute) {
  return { name, execute };
}

const searchFlights = tool("search_flights", async ({ from, to }) => {
  return `2 options found: ${from} → ${to}, from $320`;
});

const bookFlight = tool("book_flight", async ({ flight_id }) => {
  return `Booked ${flight_id}: confirmation ABC123`;
});

const chargePayment = tool("charge_payment", async ({ amount }) => {
  return `Charged $${amount}`;
});

const CONTRACTS = [
  // Classic pipeline gate.
  contract("must search before booking")
    .assume("called `book_flight`")
    .enforce("must call `search_flights` before `book_flight`"),
  // Charge only after a booking confirmation exists.
  contract("must book before charging")
    .assume("called `charge_payment`")
    .enforce("must call `book_flight` before `charge_payment`"),
  // Rate cap to contain a runaway loop.
  contract("charge cap").enforce("tool `charge_payment` at most 1 times"),
];

async function main() {
  console.log("=== OpenAI Agents SDK Guard (TypeScript) ===\n");

  const guard = new Sponsio({
    agentId: "travel_agent",
    contracts: CONTRACTS,
    mode: "enforce",
  });

  // ======== Wrap before handing tools to the Agent ========
  const guarded = wrapAgentsTools(
    [searchFlights, bookFlight, chargePayment],
    guard,
  );
  // In a real agent:
  //   import { Agent, run } from "@openai/agents";
  //   const agent = new Agent({ name: "travel", tools: guarded });
  //   await run(agent, "Book the cheapest SFO → JFK flight");
  // ========================================================

  const [searchG, bookG, chargeG] = guarded;

  // Simulate what the Agents SDK runner does: a sequence of tool
  // invocations, some of which the model will attempt out of order.
  const script = [
    { tool: bookG, args: { flight_id: "AA100" } }, // BLOCKED — must search first
    { tool: searchG, args: { from: "SFO", to: "JFK" } },
    { tool: bookG, args: { flight_id: "AA100" } },
    { tool: chargeG, args: { amount: 320 } },
    { tool: chargeG, args: { amount: 10 } }, // BLOCKED — rate cap
  ];

  for (const step of script) {
    try {
      const out = await step.tool.execute(step.args);
      console.log(`  [OK]      ${step.tool.name}: ${out}`);
    } catch (err) {
      console.log(`  [BLOCKED] ${step.tool.name}: ${err.message}`);
    }
  }

  console.log("");
  guard.printSummary();
}

main().catch(console.error);
