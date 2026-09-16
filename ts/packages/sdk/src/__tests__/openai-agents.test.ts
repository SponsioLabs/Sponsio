import assert from "node:assert/strict";
import test from "node:test";
import { Sponsio } from "../index.js";
import { argBlacklist } from "../core/patterns.js";
import { wrapAgentsTools } from "../integrations/openai-agents.js";

/** The object ``tool()`` from @openai/agents returns: invoke(runContext, input). */
function agentsSdkTool(name: string, execute: (args: Record<string, unknown>) => unknown) {
  return {
    type: "function" as const,
    name,
    description: "",
    parameters: { type: "object", properties: {} },
    invoke: async (...args: unknown[]) => execute(JSON.parse(args[1] as string)),
  };
}

const runContext = { context: null, usage: {}, approvals: {} };

test("wrapAgentsTools reads the arguments from invoke's second parameter", async () => {
  const guard = new Sponsio({
    agentId: "oa_agents",
    contracts: [argBlacklist("issue_refund", "amount", ["^\\d{5,}$"])],
    mode: "enforce",
    sessionLog: false,
  });
  const ran: unknown[] = [];
  const refund = agentsSdkTool("issue_refund", (args) => {
    ran.push(args);
    return `refunded ${args.amount}`;
  });
  const [wrapped] = wrapAgentsTools([refund], guard);

  await assert.rejects(
    () => Promise.resolve(wrapped.invoke!(runContext, JSON.stringify({ amount: 99999 }))),
    /amount/,
  );
  assert.equal(ran.length, 0);

  const out = await wrapped.invoke!(runContext, JSON.stringify({ amount: 42 }));
  assert.equal(out, "refunded 42");
  assert.deepEqual(ran, [{ amount: 42 }]);
});

test("wrapAgentsTools still handles execute(args) shaped tools", async () => {
  const guard = new Sponsio({
    agentId: "oa_exec",
    contracts: ["tool `check_policy` must precede `issue_refund`"],
    mode: "enforce",
    sessionLog: false,
  });
  const refund = { name: "issue_refund", execute: async (args: unknown) => `ok ${JSON.stringify(args)}` };
  const [wrapped] = wrapAgentsTools([refund], guard);
  await assert.rejects(() => Promise.resolve(wrapped.execute!({ orderId: "1" })), /check_policy/);
});
