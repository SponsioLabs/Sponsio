import * as path from "path";
import { describe, expect, it } from "vitest";
import { scan } from "../src/index";

const FIXTURES = path.resolve(__dirname, "fixtures");

function byName(tools: Awaited<ReturnType<typeof scan>>["tools"]) {
  return Object.fromEntries(tools.map((t) => [t.function.name, t]));
}

describe("Vercel AI SDK extractor", () => {
  it("extracts tools defined via ``tool({...})``", async () => {
    const { tools } = await scan([path.join(FIXTURES, "vercel.ts")]);
    const map = byName(tools);

    expect(Object.keys(map).sort()).toEqual(
      ["deleteAccount", "listOrders", "lookupCustomer"].sort()
    );

    const lookup = map["lookupCustomer"];
    expect(lookup.function.description).toContain("Fetch a customer");
    expect(lookup.function.parameters.properties).toHaveProperty("user_id");
    expect(lookup.function.parameters.properties.user_id.type).toBe("string");
    // .optional() → absent from required[]
    expect(lookup.function.parameters.required).toContain("user_id");
    expect(lookup.function.parameters.required).not.toContain(
      "include_archived"
    );
  });

  it("converts z.enum to enum JSON schema", async () => {
    const { tools } = await scan([path.join(FIXTURES, "vercel.ts")]);
    const map = byName(tools);
    const status = map["listOrders"].function.parameters.properties.status;
    expect(status.type).toBe("string");
    expect(status.enum).toEqual(["pending", "shipped", "delivered"]);
  });

  it("recognises z.number().int() as integer", async () => {
    const { tools } = await scan([path.join(FIXTURES, "vercel.ts")]);
    const map = byName(tools);
    const limit = map["listOrders"].function.parameters.properties.limit;
    expect(limit.type).toBe("integer");
  });
});

describe("LangChain.js extractor", () => {
  it("extracts ``new DynamicStructuredTool({...})``", async () => {
    const { tools } = await scan([path.join(FIXTURES, "langchain.ts")]);
    const map = byName(tools);

    expect(map).toHaveProperty("issue_refund");
    expect(map["issue_refund"].function.parameters.properties).toHaveProperty(
      "order_id"
    );
    expect(map["issue_refund"].function.parameters.properties).toHaveProperty(
      "amount"
    );
  });

  it("extracts ``new DynamicTool({...})`` without schema", async () => {
    const { tools } = await scan([path.join(FIXTURES, "langchain.ts")]);
    const map = byName(tools);
    expect(map).toHaveProperty("check_policy");
    expect(map["check_policy"].function.parameters).toEqual({
      type: "object",
      properties: {},
    });
  });

  it("infers tool name from variable assignment when no name key is set", async () => {
    const { tools } = await scan([path.join(FIXTURES, "langchain.ts")]);
    const map = byName(tools);
    expect(map).toHaveProperty("transferFunds");
  });
});

describe("Generic fallback extractor", () => {
  it("extracts ``createTool({...})`` calls", async () => {
    const { tools } = await scan([path.join(FIXTURES, "generic.ts")]);
    const map = byName(tools);
    expect(map).toHaveProperty("lookup_order");
    expect(map["lookup_order"].function.description).toContain("Look up");
    expect(
      map["lookup_order"].function.parameters.properties
    ).toHaveProperty("order_id");
    expect(map["lookup_order"].function.parameters.required).toContain(
      "order_id"
    );
  });

  it("extracts dotted member access like ``Sponsio.tool({...})``", async () => {
    const { tools } = await scan([path.join(FIXTURES, "generic.ts")]);
    const map = byName(tools);
    expect(map).toHaveProperty("cancel_order");
    // Used the ``schema`` key, not ``parameters``
    expect(
      map["cancel_order"].function.parameters.properties
    ).toHaveProperty("order_id");
  });

  it("extracts Google ADK ``new FunctionTool({...})`` tools", async () => {
    const { tools } = await scan([path.join(FIXTURES, "generic.ts")]);
    const map = byName(tools);
    expect(map).toHaveProperty("search_flights");
    expect(map["search_flights"].function.description).toContain(
      "Search available flights"
    );
    expect(
      map["search_flights"].function.parameters.properties
    ).toHaveProperty("origin");
    expect(
      map["search_flights"].function.parameters.properties
    ).toHaveProperty("destination");
  });

  it("ignores call sites that lack the tool object shape", async () => {
    // ``createTool({ id: 5 })`` only has one TOOL_SHAPE_KEY hit, so
    // it should NOT appear in the output — guards against false
    // positives polluting the inventory.
    const { tools } = await scan([path.join(FIXTURES, "generic.ts")]);
    const map = byName(tools);
    expect(map).not.toHaveProperty("notATool");
    // No anonymous tools should slip through either.
    expect(tools.every((t) => t.function.name.length > 0)).toBe(true);
  });

  it("extracts methods decorated with ``@tool``", async () => {
    const { tools } = await scan([path.join(FIXTURES, "generic.ts")]);
    const map = byName(tools);
    expect(map).toHaveProperty("getCustomer");
    // JSDoc → description when no decorator config is provided
    expect(map["getCustomer"].function.description).toContain(
      "Fetch customer profile"
    );
  });

  it("``@tool({...})`` config wins over JSDoc description", async () => {
    const { tools } = await scan([path.join(FIXTURES, "generic.ts")]);
    const map = byName(tools);
    expect(map).toHaveProperty("refund");
    expect(map["refund"].function.description).toContain(
      "config.description"
    );
    expect(map["refund"].function.parameters.properties).toHaveProperty(
      "amount"
    );
  });

  it("provenance is labelled ``generic`` / ``generic_decorator``", async () => {
    const { provenance } = await scan([path.join(FIXTURES, "generic.ts")]);
    const labels = new Set(Object.values(provenance).map((p) => p.extractor));
    expect(labels.has("generic")).toBe(true);
    expect(labels.has("generic_decorator")).toBe(true);
  });

  it("framework-specific extractor wins over generic for the same call site", async () => {
    // Vercel's ``tool({...})`` would also match the generic pattern.
    // The dedupe layer (keyed on name+file+line) keeps the first
    // hit, which is the framework-specific one — verify that the
    // Vercel fixture's tools all carry the ``vercel_ai`` label.
    const { provenance } = await scan([path.join(FIXTURES, "vercel.ts")]);
    const labels = new Set(Object.values(provenance).map((p) => p.extractor));
    expect(labels.has("vercel_ai")).toBe(true);
    expect(labels.has("generic")).toBe(false);
  });
});

describe("Output shape (OpenAI function-calling)", () => {
  it("produces tools with {type: 'function', function: {name, description, parameters}}", async () => {
    const { tools } = await scan([FIXTURES + "/*.ts"]);
    expect(tools.length).toBeGreaterThan(0);
    for (const t of tools) {
      expect(t.type).toBe("function");
      expect(t.function.name).toBeTruthy();
      expect(t.function.parameters.type).toBe("object");
      expect(t.function.parameters.properties).toBeDefined();
    }
  });

  it("provenance maps record filepath + extractor", async () => {
    const { provenance } = await scan([FIXTURES + "/*.ts"]);
    const values = Object.values(provenance);
    expect(values.length).toBeGreaterThan(0);
    const extractors = new Set(values.map((p) => p.extractor));
    expect(extractors.has("vercel_ai")).toBe(true);
    expect(extractors.has("langchain_js")).toBe(true);
  });
});
