import assert from "node:assert/strict";
import test from "node:test";
import { Sponsio } from "../index.js";
import { sponsioHooks } from "../integrations/claude-agent.js";

function guard() {
  return new Sponsio({
    agentId: "claude",
    contracts: ["tool `check_policy` must precede `issue_refund`"],
    mode: "enforce",
    sessionLog: false,
  });
}

test("PreToolUse denies a refused call", async () => {
  const hooks = sponsioHooks(guard());
  const pre = hooks.PreToolUse[0].hooks[0];
  const out = (await pre({ tool_name: "issue_refund", tool_input: {} }, "t1", null)) as any;
  assert.equal(out.hookSpecificOutput.permissionDecision, "deny");
});

test("PreToolUse denies when the guard itself throws", async () => {
  const g = guard();
  (g as any).guardBefore = () => {
    throw new Error("evaluator exploded");
  };
  const pre = sponsioHooks(g).PreToolUse[0].hooks[0];
  const out = (await pre({ tool_name: "issue_refund", tool_input: {} }, "t1", null)) as any;
  assert.equal(out.hookSpecificOutput.permissionDecision, "deny");
  assert.match(out.hookSpecificOutput.permissionDecisionReason, /evaluator exploded/);
});

test("PostToolUse reads tool_response", async () => {
  const g = guard();
  const seen: string[] = [];
  const original = g.guardAfter.bind(g);
  (g as any).guardAfter = async (name: string, output: string) => {
    seen.push(output);
    return original(name, output);
  };
  const post = sponsioHooks(g).PostToolUse[0].hooks[0];
  await post({ tool_name: "check_policy", tool_response: { stdout: "policy ok" } }, "t1", null);
  assert.equal(seen.length, 1);
  assert.match(seen[0], /policy ok/);
});
