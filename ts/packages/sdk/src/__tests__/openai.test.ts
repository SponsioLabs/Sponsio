import assert from "node:assert/strict";
import test from "node:test";
import { Sponsio } from "../index.js";
import { wrapOpenAI } from "../integrations/openai.js";

function guard(mode: "enforce" | "observe" = "enforce") {
  return new Sponsio({
    agentId: "oa",
    contracts: ["tool `check_policy` must precede `issue_refund`"],
    mode,
    sessionLog: false,
  });
}

function chatResponse(...calls: Array<[string, string]>) {
  return {
    choices: [
      {
        message: {
          content: null as string | null,
          tool_calls: calls.map(([name, args], i) => ({
            id: `c${i}`,
            type: "function",
            function: { name, arguments: args },
          })),
        },
      },
    ],
  };
}

function fakeClient(chat: unknown, responses?: unknown) {
  const calls: unknown[] = [];
  const client: Record<string, unknown> = {
    chat: {
      completions: {
        create: async (params: unknown) => {
          calls.push(params);
          return chat;
        },
        parse: async () => chat,
      },
    },
  };
  if (responses) client.responses = { create: async () => responses };
  return { client, calls };
}

test("wrapOpenAI strips a refused tool call and explains it", async () => {
  const { client } = fakeClient(chatResponse(["issue_refund", "{}"]));
  const c = wrapOpenAI(client, guard()) as any;
  const out = await c.chat.completions.create({ model: "m", messages: [] });
  assert.equal(out.choices[0].message.tool_calls, null);
  assert.match(out.choices[0].message.content, /\[BLOCKED\] issue_refund/);
});

test("wrapOpenAI guards parse() too", async () => {
  const { client } = fakeClient(chatResponse(["issue_refund", "{}"]));
  const c = wrapOpenAI(client, guard()) as any;
  const out = await c.chat.completions.parse({ model: "m", messages: [] });
  assert.equal(out.choices[0].message.tool_calls, null);
});

test("wrapOpenAI refuses stream: true instead of passing the stream through unchecked", async () => {
  const { client, calls } = fakeClient(chatResponse(["issue_refund", "{}"]));
  const c = wrapOpenAI(client, guard()) as any;
  await assert.rejects(
    () => c.chat.completions.create({ model: "m", messages: [], stream: true }),
    /stream/,
  );
  assert.equal(calls.length, 0);
});

test("wrapOpenAI guards the Responses API output", async () => {
  const responses = {
    output: [
      { type: "message", role: "assistant", content: [{ type: "output_text", text: "Refunding." }] },
      { type: "function_call", name: "issue_refund", arguments: "{}", call_id: "c1" },
    ],
  };
  const { client } = fakeClient(chatResponse(), responses);
  const c = wrapOpenAI(client, guard()) as any;
  const out = await c.responses.create({ model: "m", input: "x" });
  assert.deepEqual(out.output.map((i: { type: string }) => i.type), ["message"]);
  assert.match(out.output[0].content[0].text, /\[BLOCKED\] issue_refund/);
});

test("malformed arguments are refused in enforce mode without recording a phantom call", async () => {
  const g = guard();
  const { client } = fakeClient(chatResponse(["check_policy", "{not json"]));
  const c = wrapOpenAI(client, g) as any;
  const out = await c.chat.completions.create({ model: "m", messages: [] });
  assert.equal(out.choices[0].message.tool_calls, null);
  assert.match(out.choices[0].message.content, /malformed JSON/);
  // The dropped check_policy never ran, so issue_refund must still be refused.
  assert.equal(g.guardBefore("issue_refund", {}).stopOriginal, true);
});

test("malformed arguments pass through in observe mode", async () => {
  const { client } = fakeClient(chatResponse(["check_policy", "{not json"]));
  const c = wrapOpenAI(client, guard("observe")) as any;
  const out = await c.chat.completions.create({ model: "m", messages: [] });
  assert.equal(out.choices[0].message.tool_calls.length, 1);
});
