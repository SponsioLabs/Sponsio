/**
 * Claude Agent SDK Guard — Customer Service (TypeScript)
 *
 * Same scenario as the Python version (../claude_agent_guard.py).
 * Shows the zero-wrapping hooks integration via Pyodide.
 *
 * Usage:
 *   cd ts-sdk && npm install
 *   node ../examples/integrations/typescript/claude_agent_guard.mjs
 */

import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

// Adjust path to load from ts-sdk/src
const __dirname = dirname(fileURLToPath(import.meta.url));
const { Sponsio } = await import(resolve(__dirname, "..", "..", "..", "ts-sdk", "dist", "index.js"));
const { sponsioHooks } = await import(resolve(__dirname, "..", "..", "..", "ts-sdk", "dist", "integrations", "claude-agent.js"));

const CONTRACTS = [
  "tool `check_policy` must precede `issue_refund`",
  "tool `issue_refund` at most 1 times",
];

// Tool implementations
const tools = {
  check_policy: (args) => `Order ${args.order_id}: eligible for refund`,
  issue_refund: (args) => `Refund issued for order ${args.order_id}`,
  send_email: (args) => `Email sent to ${args.to}`,
};

async function main() {
  console.log("=== Claude Agent SDK Guard (TypeScript) ===\n");

  // ======== Add Sponsio: 2 lines ========
  const guard = new Sponsio({
    agentId: "support_bot",
    contracts: CONTRACTS,
    mode: "enforce",
  });
  const hooks = sponsioHooks(guard);
  // ======================================

  const preHook = hooks.PreToolUse[0].hooks[0];
  const postHook = hooks.PostToolUse[0].hooks[0];

  // Simulate agent tool calls
  const calls = [
    { tool: "issue_refund", args: { order_id: "#W456" } },   // should DENY
    { tool: "check_policy", args: { order_id: "#W456" } },   // should allow
    { tool: "issue_refund", args: { order_id: "#W456" } },   // should allow
    { tool: "issue_refund", args: { order_id: "#W789" } },   // should DENY (rate limit)
  ];

  for (const call of calls) {
    const input = {
      hook_event_name: "PreToolUse",
      tool_name: call.tool,
      tool_input: call.args,
      session_id: "demo",
      cwd: "/tmp",
      tool_use_id: `id_${call.tool}`,
      agent_id: "support_bot",
      agent_type: "main",
    };

    const result = await preHook(input, input.tool_use_id, {});

    if (result?.hookSpecificOutput?.permissionDecision === "deny") {
      const reason = result.hookSpecificOutput.permissionDecisionReason;
      console.log(`  [DENIED] ${call.tool}: ${reason}`);
    } else {
      const output = tools[call.tool](call.args);
      console.log(`  [OK]     ${call.tool}: ${output}`);

      await postHook(
        { hook_event_name: "PostToolUse", tool_name: call.tool, tool_result: output },
        input.tool_use_id,
        {}
      );
    }
  }

  console.log("\n" + guard.summary());
}

main().catch(console.error);
