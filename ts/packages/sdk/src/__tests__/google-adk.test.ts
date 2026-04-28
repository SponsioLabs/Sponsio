import assert from "node:assert/strict";
import test from "node:test";
import { Sponsio } from "../index.js";
import { wrapGoogleAdkTools } from "../integrations/google-adk.js";

function tool(name: string, execute: (args: Record<string, unknown>) => unknown) {
  return {
    name,
    execute: (...args: unknown[]) => execute(args[0] as Record<string, unknown>),
  };
}

test("wrapGoogleAdkTools blocks out-of-order calls and preserves allowed output", async () => {
  const guard = new Sponsio({
    agentId: "adk_test",
    contracts: ["tool `search_flights` must precede `book_flight`"],
    mode: "enforce",
    sessionLog: false,
  });

  const searchFlights = tool("search_flights", ({ to }) => ({
    status: "success",
    report: `found ${to}`,
  }));
  const bookFlight = tool("book_flight", ({ flightId }) => ({
    status: "success",
    confirmation: flightId,
  }));

  const [search, book] = wrapGoogleAdkTools([searchFlights, bookFlight], guard);

  const blocked = await book.execute({ flightId: "AA100" });
  assert.equal((blocked as Record<string, unknown>).status, "error");
  assert.match(
    String((blocked as Record<string, unknown>).error_message),
    /BLOCKED by contract/,
  );

  assert.deepEqual(await search.execute({ to: "JFK" }), {
    status: "success",
    report: "found JFK",
  });
  assert.deepEqual(await book.execute({ flightId: "AA100" }), {
    status: "success",
    confirmation: "AA100",
  });
});

test("wrapGoogleAdkTools does not mutate original tool objects", async () => {
  const guard = new Sponsio({
    contracts: ["tool `A` must precede `B`"],
    mode: "enforce",
    sessionLog: false,
  });
  const original = tool("B", () => "ran");
  const [wrapped] = wrapGoogleAdkTools([original], guard);

  assert.notEqual(wrapped, original);
  assert.equal(await original.execute({}), "ran");
  assert.equal((await wrapped.execute({}) as Record<string, unknown>).status, "error");
});

test("wrapGoogleAdkTools preserves class instance prototypes", async () => {
  class MockFunctionTool {
    name = "B";

    describe(): string {
      return "prototype method";
    }

    execute(): string {
      return "ran";
    }
  }

  const guard = new Sponsio({
    contracts: ["tool `A` must precede `B`"],
    mode: "enforce",
    sessionLog: false,
  });
  const original = new MockFunctionTool();
  const [wrapped] = wrapGoogleAdkTools([original], guard);

  assert.notEqual(wrapped, original);
  assert.ok(wrapped instanceof MockFunctionTool);
  assert.equal(wrapped.describe(), "prototype method");
  assert.equal(await original.execute(), "ran");
  const blocked = await (wrapped as unknown as { execute: (arg: unknown) => unknown }).execute({});
  assert.equal((blocked as Record<string, unknown>).status, "error");
});
