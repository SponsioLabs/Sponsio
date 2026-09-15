import assert from "node:assert/strict";
import test from "node:test";
import { Sponsio } from "../index.js";
import { sponsioMiddleware } from "../integrations/vercel-ai.js";

process.env.SPONSIO_NO_BANNER = "1";

function guard() {
  return new Sponsio({
    agentId: "vercel",
    contracts: ["tool `check_policy` must precede `issue_refund`"],
    mode: "enforce",
    sessionLog: false,
  });
}

test("wrapGenerate drops a refused tool-call part from an ai v5+ content array", async () => {
  const mw = sponsioMiddleware(guard());
  const result = await mw.wrapGenerate({
    params: {},
    doGenerate: async () => ({
      content: [
        { type: "text", text: "Refunding." },
        { type: "tool-call", toolCallId: "1", toolName: "issue_refund", input: JSON.stringify({ orderId: "o1" }) },
      ],
      finishReason: "tool-calls",
    }),
  });
  assert.equal(result.content.filter((p: { type: string }) => p.type === "tool-call").length, 0);
  assert.match(result.content.at(-1).text, /Sponsio blocked: issue_refund/);
  assert.equal(result.finishReason, "stop");
});

test("wrapGenerate leaves an allowed v5 result byte-identical", async () => {
  const mw = sponsioMiddleware(guard());
  const original = {
    content: [{ type: "tool-call", toolCallId: "1", toolName: "check_policy", input: "{}" }],
    finishReason: "tool-calls",
  };
  const result = await mw.wrapGenerate({ params: {}, doGenerate: async () => original });
  assert.equal(result, original);
});

test("wrapGenerate still handles the ai v4 toolCalls shape", async () => {
  const mw = sponsioMiddleware(guard());
  const result = await mw.wrapGenerate({
    params: {},
    doGenerate: async () => ({
      text: "",
      toolCalls: [{ toolCallType: "function", toolCallId: "1", toolName: "issue_refund", args: "{}" }],
      finishReason: "tool-calls",
    }),
  });
  assert.deepEqual(result.toolCalls, []);
  assert.match(result.text, /Sponsio blocked/);
  assert.equal(result.finishReason, "stop");
});

test("wrapStream gates tool-call parts before streamText can execute them", async () => {
  const mw = sponsioMiddleware(guard());
  const parts = [
    { type: "text-start", id: "t1" },
    { type: "text-delta", id: "t1", delta: "Refunding." },
    { type: "text-end", id: "t1" },
    { type: "tool-input-start", id: "1", toolName: "issue_refund" },
    { type: "tool-input-end", id: "1" },
    { type: "tool-call", toolCallId: "1", toolName: "issue_refund", input: "{}" },
    { type: "finish", finishReason: "tool-calls", usage: {} },
  ];
  const stream = new ReadableStream({
    start(controller) {
      for (const p of parts) controller.enqueue(p);
      controller.close();
    },
  });
  const out = await mw.wrapStream({ doStream: async () => ({ stream, rawCall: {} }) });
  const seen: Array<{ type: string; [k: string]: unknown }> = [];
  const reader = out.stream.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    seen.push(value);
  }
  assert.equal(seen.filter((p) => p.type === "tool-call").length, 0);
  assert.ok(seen.some((p) => p.type === "text-delta" && String(p.delta).includes("Sponsio blocked")));
  assert.equal(seen.at(-1)!.type, "finish");
  assert.equal(seen.at(-1)!.finishReason, "stop");
  assert.equal(out.rawCall !== undefined, true);
});

test("wrapStream passes allowed tool calls through untouched", async () => {
  const mw = sponsioMiddleware(guard());
  const parts = [
    { type: "tool-call", toolCallId: "1", toolName: "check_policy", input: "{}" },
    { type: "finish", finishReason: "tool-calls", usage: {} },
  ];
  const stream = new ReadableStream({
    start(controller) {
      for (const p of parts) controller.enqueue(p);
      controller.close();
    },
  });
  const out = await mw.wrapStream({ doStream: async () => ({ stream }) });
  const seen: Array<{ type: string; finishReason?: string }> = [];
  const reader = out.stream.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    seen.push(value);
  }
  assert.deepEqual(seen.map((p) => p.type), ["tool-call", "finish"]);
  assert.equal(seen[1].finishReason, "tool-calls");
});
