/**
 * Google ADK Guard - Travel Booking (TypeScript)
 *
 * Shows ``wrapGoogleAdkTools(tools, guard)`` for @google/adk FunctionTool
 * objects. Mock mode uses the same object shape: name + execute.
 *
 * Usage:
 *   cd ts-sdk && npm install && npm run build
 *   node ../examples/integrations/typescript/google_adk_guard.mjs
 */

import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const { Sponsio } = await import(
  resolve(__dirname, "..", "..", "..", "ts-sdk", "dist", "index.js")
);
const { wrapGoogleAdkTools } = await import(
  resolve(
    __dirname,
    "..",
    "..",
    "..",
    "ts-sdk",
    "dist",
    "integrations",
    "google-adk.js",
  )
);

function functionTool(name, execute) {
  return { name, execute };
}

const searchFlights = functionTool("search_flights", ({ origin, destination }) => {
  return { status: "success", report: `Found ${origin} to ${destination} from $320` };
});

const bookFlight = functionTool("book_flight", ({ flightId }) => {
  return { status: "success", confirmation: `Booked ${flightId}` };
});

const chargePayment = functionTool("charge_payment", ({ amount }) => {
  return { status: "success", receipt: `Charged $${amount}` };
});

const CONTRACTS = [
  "tool `search_flights` must precede `book_flight`",
  "tool `charge_payment` at most 1 times",
];

async function main() {
  console.log("=== Google ADK Guard (TypeScript) ===\n");

  const guard = new Sponsio({
    agentId: "travel_agent",
    contracts: CONTRACTS,
    mode: "enforce",
  });

  // ======== Wrap before handing tools to LlmAgent ========
  const guarded = wrapGoogleAdkTools(
    [searchFlights, bookFlight, chargePayment],
    guard,
  );
  // In a real ADK agent:
  //   import { LlmAgent } from "@google/adk";
  //   export const rootAgent = new LlmAgent({ name: "travel", tools: guarded, ... });
  // =======================================================

  const [searchG, bookG, chargeG] = guarded;
  const script = [
    { tool: bookG, args: { flightId: "AA100" } },
    { tool: searchG, args: { origin: "SFO", destination: "JFK" } },
    { tool: bookG, args: { flightId: "AA100" } },
    { tool: chargeG, args: { amount: 320 } },
    { tool: chargeG, args: { amount: 10 } },
  ];

  for (const step of script) {
    const out = await step.tool.execute(step.args);
    const status = out.status === "error" ? "BLOCKED" : "OK";
    console.log(`  [${status}] ${step.tool.name}: ${JSON.stringify(out)}`);
  }

  console.log("");
  guard.printSummary();
}

main().catch(console.error);
