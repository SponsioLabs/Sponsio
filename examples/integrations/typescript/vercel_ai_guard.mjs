/**
 * Vercel AI SDK Guard — Content Publishing (TypeScript)
 *
 * Same scenario as the Python version (../python/vercel_ai_guard.py).
 * Shows sponsioMiddleware integration for Vercel AI SDK.
 *
 * Usage:
 *   cd ts-sdk && npm install
 *   node ../examples/integrations/typescript/vercel_ai_guard.mjs
 */

import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const { Sponsio } = await import(resolve(__dirname, "..", "..", "..", "ts-sdk", "dist", "index.js"));

const CONTRACTS = [
  "tool `review_content` must precede `publish_post`",
  "tool `send_newsletter` at most 1 times",
];

// Tool implementations
const tools = {
  review_content: (args) => `Content "${args.title}" reviewed: approved`,
  publish_post: (args) => `Post "${args.title}" published`,
  send_newsletter: (args) => `Newsletter sent to ${args.subscribers} subscribers`,
};

async function main() {
  console.log("=== Vercel AI SDK Guard (TypeScript) ===\n");

  const guard = new Sponsio({
    agentId: "publisher",
    contracts: CONTRACTS,
    mode: "enforce",
  });

  // Simulate what the Vercel AI middleware would do
  const calls = [
    { tool: "publish_post", args: { title: "Breaking News" } },          // BLOCKED
    { tool: "review_content", args: { title: "Breaking News" } },         // OK
    { tool: "publish_post", args: { title: "Breaking News" } },           // OK
    { tool: "send_newsletter", args: { subscribers: 1000 } },             // OK
    { tool: "send_newsletter", args: { subscribers: 2000 } },             // BLOCKED (rate limit)
  ];

  for (const call of calls) {
    const result = guard.guardBefore(call.tool, call.args);

    if (result.blocked) {
      console.log(`  [BLOCKED] ${call.tool}: ${result.message}`);
    } else {
      const output = tools[call.tool](call.args);
      console.log(`  [OK]      ${call.tool}: ${output}`);
      guard.guardAfter(call.tool, output);
    }
  }

  console.log("\n" + guard.summary());
}

main().catch(console.error);
