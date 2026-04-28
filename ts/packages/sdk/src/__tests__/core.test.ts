/**
 * Cross-language tests (TypeScript side).
 *
 * Reads the same scenarios.json as Python tests.
 * Both must produce identical block/allow decisions.
 */

import { readFileSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";
import { Sponsio } from "../index.js";
import { sponsioHooks } from "../integrations/claude-agent.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCENARIOS: Array<{
  name: string;
  contracts: string[];
  steps: Array<{
    tool: string;
    args?: Record<string, unknown>;
    expect_blocked: boolean;
    reason: string;
  }>;
}> = JSON.parse(
  readFileSync(
    resolve(
      __dirname,
      "..",
      "..",
      "..",
      "..",
      "..",
      "tests",
      "cross_language",
      "scenarios.json",
    ),
    "utf-8",
  ),
).scenarios;

let passed = 0;
let failed = 0;

function assert(condition: boolean, msg: string) {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.error(`  FAIL: ${msg}`);
  }
}

async function testCoreGuard() {
  console.log("[Core guardBefore]");
  for (const scenario of SCENARIOS) {
    const guard = new Sponsio({ agentId: "xtest", contracts: scenario.contracts, mode: "enforce", sessionLog: false });
    for (let i = 0; i < scenario.steps.length; i++) {
      const step = scenario.steps[i];
      const result = guard.guardBefore(step.tool, step.args ?? {});
      assert(
        result.blocked === step.expect_blocked,
        `${scenario.name} step ${i} (${step.tool}): expected blocked=${step.expect_blocked}, got ${result.blocked}. ${step.reason}`,
      );
      if (!result.blocked) guard.guardAfter(step.tool, "ok");
    }
    console.log(`  ${scenario.name}: OK`);
  }
}

async function testClaudeHooks() {
  console.log("\n[Claude Agent hooks]");
  for (const scenario of SCENARIOS) {
    const guard = new Sponsio({ agentId: "xtest_hooks", contracts: scenario.contracts, mode: "enforce", sessionLog: false });
    const hooks = sponsioHooks(guard);
    const preHook = hooks.PreToolUse[0].hooks[0];
    const postHook = hooks.PostToolUse[0].hooks[0];

    for (let i = 0; i < scenario.steps.length; i++) {
      const step = scenario.steps[i];
      const input = {
        hook_event_name: "PreToolUse",
        tool_name: step.tool,
        tool_input: step.args ?? {},
        session_id: "test",
        cwd: "/tmp",
        tool_use_id: `id_${i}`,
        agent_id: "xtest",
        agent_type: "main",
      };

      const result = await preHook(input, `id_${i}`, {});
      const isDenied = (result as Record<string, unknown>)?.hookSpecificOutput
        ? ((result as { hookSpecificOutput: { permissionDecision?: string } }).hookSpecificOutput.permissionDecision === "deny")
        : false;

      assert(
        isDenied === step.expect_blocked,
        `${scenario.name} step ${i} (${step.tool}): expected blocked=${step.expect_blocked}, got denied=${isDenied}. ${step.reason}`,
      );

      if (!isDenied) {
        await postHook(
          { hook_event_name: "PostToolUse", tool_name: step.tool, tool_result: "ok" },
          `id_${i}`,
          {},
        );
      }
    }
    console.log(`  ${scenario.name}: OK`);
  }
}

async function testPerformance() {
  console.log("\n[Perf]");
  const guard = new Sponsio({
    agentId: "perf",
    contracts: ["tool `A` must precede `B`", "tool `B` at most 50 times"],
    mode: "enforce",
    sessionLog: false,
  });
  guard.guardBefore("A", {});
  guard.guardAfter("A", "ok");

  const start = Date.now();
  for (let i = 0; i < 1000; i++) {
    guard.guardBefore("B", { i });
    guard.guardAfter("B", "ok");
  }
  const elapsed = Date.now() - start;
  console.log(`  1000 checks in ${elapsed}ms (${(elapsed / 1000).toFixed(3)}ms/check)`);
  assert(elapsed < 5000, "1000 checks under 5s");
}

async function main() {
  console.log("=== Cross-Language Tests (TypeScript — native) ===\n");
  await testCoreGuard();
  await testClaudeHooks();
  await testPerformance();

  console.log(`\n${"=".repeat(40)}`);
  console.log(`Results: ${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
}

main().catch((e) => {
  console.error("FAILED:", e);
  process.exit(1);
});
