import assert from "node:assert/strict";
import test from "node:test";
import { Sponsio } from "../index.js";
import { wrapTools } from "../integrations/langchain.js";

function tool(name: string, invoke: (input: Record<string, unknown>) => unknown) {
  return {
    name,
    invoke: async (input: unknown) => invoke(input as Record<string, unknown>),
  };
}

test("wrapTools blocks out-of-order calls and preserves allowed output", async () => {
  const guard = new Sponsio({
    agentId: "lc_test",
    contracts: ["tool `search_flights` must precede `book_flight`"],
    mode: "enforce",
    sessionLog: false,
  });

  const searchFlights = tool("search_flights", ({ to }) => `found ${to}`);
  const bookFlight = tool("book_flight", ({ flightId }) => `booked ${flightId}`);

  const [search, book] = wrapTools([searchFlights, bookFlight], guard);

  const blocked = await book.invoke({ flightId: "AA100" });
  assert.match(String(blocked), /BLOCKED by Sponsio/);

  assert.equal(await search.invoke({ to: "JFK" }), "found JFK");
  assert.equal(await book.invoke({ flightId: "AA100" }), "booked AA100");
});

test("wrapTools does not mutate original tool objects", async () => {
  const guard = new Sponsio({
    contracts: ["tool `A` must precede `B`"],
    mode: "enforce",
    sessionLog: false,
  });
  const original = tool("B", () => "ran");
  const [wrapped] = wrapTools([original], guard);

  assert.notEqual(wrapped, original);
  assert.equal(await original.invoke({}), "ran");
  assert.match(String(await wrapped.invoke({})), /BLOCKED by Sponsio/);
});

test("wrapTools preserves class instance prototypes", async () => {
  class MockTool {
    name = "B";

    describe(): string {
      return "prototype method";
    }

    async invoke(): Promise<string> {
      return "ran";
    }
  }

  const guard = new Sponsio({
    contracts: ["tool `A` must precede `B`"],
    mode: "enforce",
    sessionLog: false,
  });
  const original = new MockTool();
  const [wrapped] = wrapTools([original], guard);

  assert.notEqual(wrapped, original);
  assert.ok(wrapped instanceof MockTool);
  assert.equal(wrapped.describe(), "prototype method");
  assert.equal(await original.invoke(), "ran");
  assert.match(
    String(await (wrapped as unknown as { invoke: (i: unknown) => unknown }).invoke({})),
    /BLOCKED by Sponsio/,
  );
});

test("wrapTools accepts string input (LangChain v0 single-arg tools)", async () => {
  const guard = new Sponsio({
    contracts: ["tool `noop` must precede `echo`"],
    mode: "observe",
    sessionLog: false,
  });
  const echo = {
    name: "echo",
    invoke: async (input: unknown) => `got ${typeof input === "string" ? input : JSON.stringify(input)}`,
  };
  const [wrapped] = wrapTools([echo], guard);

  assert.equal(await wrapped.invoke("hello"), "got hello");
});
