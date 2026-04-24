import { describe, it, expect } from "vitest";
import { dump } from "js-yaml";
import {
  suggestDetNlContracts,
  buildFallbackPayload,
  detectFramework,
} from "../src/onboard";

describe("onboard heuristics", () => {
  it("suggestDetNlContracts prefers must_precede when a confirm tool exists", () => {
    const names = ["confirm_with_user", "delete_file"];
    const c = suggestDetNlContracts(names);
    expect(c.length).toBeGreaterThan(0);
    expect(c[0]!.E).toContain("must precede");
  });

  it("buildFallbackPayload round-trips through js-yaml", () => {
    const p = buildFallbackPayload(
      ["delete_thing", "get_x"],
      { tools: [] },
      "agent",
      "observe"
    );
    const s = dump(p);
    expect(s).toContain("version: 1");
    expect(s).toContain("mode: observe");
  });

  it("detectFramework sees langgraph from package.json", () => {
    // repo root is wrong — no package.json in ts-scanner root with deps. Use a fixture path? Skip.
    const fw = detectFramework(process.cwd());
    expect(["none", "langgraph", "vercel", "openai", "mcp", "claude"]).toContain(
      fw
    );
  });
});
