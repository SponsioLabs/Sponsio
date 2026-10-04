import assert from "node:assert/strict";
import test from "node:test";
import { Sponsio } from "../index.js";
import { ArgValue, Const, G, Gt, Not } from "../core/formula.js";
import {
  argBlacklist,
  argLengthLimit,
  argValueRange,
  loopDetection,
  mustPrecede,
  rateLimit,
  type DetFormula,
} from "../core/patterns.js";
import { numericArg } from "../core/grounding.js";
import { canonicalTool, toolAliases } from "../core/tool-names.js";

/**
 * TS port of the "cannot evaluate is not the same as not violated" fixes
 * (Python: tests/test_fail_open_layer.py, PR #180 and follow-ups). Each
 * case used to report a rule as satisfied when the rule never looked at
 * the call.
 */

function guard(contracts: DetFormula[]) {
  return new Sponsio({ agentId: "failopen", contracts, mode: "enforce", sessionLog: false });
}

// ── 1. Tool-name canonicalisation ──────────────────────────────────────

test("canonicalTool strips and case-folds, but keeps an MCP prefix", () => {
  assert.equal(canonicalTool("  Issue_Refund \n"), "issue_refund");
  assert.equal(canonicalTool("mcp__Finance__Issue_Refund"), "mcp__finance__issue_refund");
  // Python's casefold, not just toLowerCase.
  assert.equal(canonicalTool("STRASSE_ß"), "strasse_ss");
});

test("toolAliases answers to every spelling, raw name first", () => {
  assert.deepEqual(toolAliases("mcp__finance__Issue_Refund "), [
    "mcp__finance__Issue_Refund ",
    "mcp__finance__Issue_Refund",
    "mcp__finance__issue_refund",
    "Issue_Refund",
    "issue_refund",
  ]);
  assert.deepEqual(toolAliases("issue_refund"), ["issue_refund"]);
  // The server segment has no "__"; the tool segment may.
  assert.ok(toolAliases("mcp__a_b__c__d").includes("c__d"));
});

test("mustPrecede binds every spelling of the guarded tool", () => {
  for (const name of ["issue_refund", "Issue_Refund", "issue_refund ", "mcp__finance__issue_refund"]) {
    const r = guard([mustPrecede("check_policy", "issue_refund")]).guardBefore(name, { id: 1 });
    assert.equal(r.blocked, true, `expected ${JSON.stringify(name)} to be blocked`);
  }
  const g = guard([mustPrecede("check_policy", "issue_refund")]);
  assert.equal(g.guardBefore("mcp__finance__Check_Policy", { id: 1 }).blocked, false);
  assert.equal(g.guardBefore("Issue_Refund", { id: 1 }).blocked, false);
});

test("a contract authored in mixed case still binds the call", () => {
  const r = guard([mustPrecede("Check_Policy", "Issue_Refund")]).guardBefore("issue_refund", { id: 1 });
  assert.equal(r.blocked, true);
});

test("rateLimit counts calls across spellings", () => {
  const g = guard([rateLimit("issue_refund", 2)]);
  assert.equal(g.guardBefore("issue_refund", { id: 1 }).blocked, false);
  assert.equal(g.guardBefore("Issue_Refund", { id: 2 }).blocked, false);
  assert.equal(g.guardBefore("mcp__finance__issue_refund", { id: 3 }).blocked, true);
});

test("loopDetection treats spellings of one tool as the same tool", () => {
  const g = guard([loopDetection("search", 2)]);
  assert.equal(g.guardBefore("search", { q: "a" }).blocked, false);
  assert.equal(g.guardBefore("Search", { q: "b" }).blocked, false);
  assert.equal(g.guardBefore("SEARCH ", { q: "c" }).blocked, true);
  // A different tool breaks the run for every alias.
  const h = guard([loopDetection("search", 2)]);
  h.guardBefore("Search", { q: "a" });
  h.guardBefore("search", { q: "b" });
  h.guardBefore("fetch", { url: "x" });
  assert.equal(h.guardBefore("SEARCH", { q: "c" }).blocked, false);
});

test("argument rules bind case and MCP variants", () => {
  for (const name of ["Bash", "bash", "BASH ", "mcp__shell__Bash"]) {
    const r = guard([argBlacklist("Bash", "command", ["rm -rf"])]).guardBefore(name, {
      command: "rm -rf /",
    });
    assert.equal(r.blocked, true, `expected ${JSON.stringify(name)} to be blocked`);
  }
  const lim = guard([argLengthLimit("Write", "content", 5)]);
  assert.equal(lim.guardBefore("mcp__fs__write", { content: "far too long" }).blocked, true);
});

// ── 2. Missing arguments refused ───────────────────────────────────────

test("a call with no arguments is refused when a rule reads them", () => {
  for (const args of [{}, undefined, null]) {
    const r = guard([argBlacklist("Bash", "command", ["rm -rf"])]).guardBefore(
      "Bash",
      args as unknown as Record<string, unknown>,
    );
    assert.equal(r.blocked, true, `expected args=${JSON.stringify(args)} to be blocked`);
    assert.equal(r.allowed, false);
    assert.equal(r.detViolations[0]?.ruleId, "args:unevaluable");
  }
  const r = guard([argLengthLimit("write", "content", 10)]).guardBefore("Write", {});
  assert.equal(r.detViolations[0]?.ruleId, "args:unevaluable");
});

test("an MCP-prefixed call with no arguments is refused too", () => {
  const r = guard([argBlacklist("Bash", "command", ["rm -rf"])]).guardBefore("mcp__shell__Bash", {});
  assert.equal(r.detViolations[0]?.ruleId, "args:unevaluable");
});

test("a tool no rule reads the arguments of still runs without them", () => {
  const g = guard([argBlacklist("Bash", "command", ["rm -rf"]), rateLimit("list_files", 5)]);
  assert.equal(g.guardBefore("list_files", {}).blocked, false);
});

test("a refused call leaves no trace behind", () => {
  const g = guard([argBlacklist("Bash", "command", ["rm -rf"]), rateLimit("Bash", 1)]);
  assert.equal(g.guardBefore("Bash", {}).blocked, true);
  assert.equal(g.guardBefore("Bash", { command: "ls" }).blocked, false);
});

test("SPONSIO_ALLOW_MISSING_ARGS=1 restores the permissive behaviour", () => {
  const prev = process.env.SPONSIO_ALLOW_MISSING_ARGS;
  process.env.SPONSIO_ALLOW_MISSING_ARGS = "1";
  try {
    const g = guard([argBlacklist("Bash", "command", ["rm -rf"])]);
    assert.equal(g.guardBefore("Bash", {}).blocked, false);
    const n = guard([argValueRange("pay", "amount", undefined, 1000)]);
    assert.equal(n.guardBefore("pay", { amount: "five thousand" }).blocked, false);
  } finally {
    if (prev === undefined) delete process.env.SPONSIO_ALLOW_MISSING_ARGS;
    else process.env.SPONSIO_ALLOW_MISSING_ARGS = prev;
  }
});

// ── 3. arg_numeric normalisation ───────────────────────────────────────

test("numericArg reads the shapes a model writes", () => {
  assert.equal(numericArg(5000), 5000);
  assert.equal(numericArg(true), 1);
  assert.equal(numericArg(false), 0);
  assert.equal(numericArg("5000"), 5000);
  assert.equal(numericArg("$5,000"), 5000);
  assert.equal(numericArg("5,000"), 5000);
  assert.equal(numericArg("5000 USD"), 5000);
  assert.equal(numericArg("1e400"), Infinity);
  assert.equal(numericArg("five thousand"), undefined);
  assert.equal(numericArg("5,50"), undefined);
  assert.equal(numericArg(null), undefined);
  assert.equal(numericArg({ v: 1 }), undefined);
});

test("argValueRange: formatted amounts are bounded like plain ones", () => {
  const cases: [unknown, boolean][] = [
    [500, false],
    ["500", false],
    ["$500", false],
    [5000, true],
    ["5000", true],
    ["$5,000", true],
    ["5,000", true],
    ["5000 USD", true],
    ["1e400", true],
  ];
  for (const [amount, blocked] of cases) {
    const r = guard([argValueRange("pay", "amount", undefined, 1000)]).guardBefore("pay", { amount });
    assert.equal(r.blocked, blocked, `amount=${JSON.stringify(amount)}`);
    if (blocked) assert.notEqual(r.detViolations[0]?.ruleId, "args:unevaluable");
  }
});

// ── 4. Unreadable numeric argument refused ─────────────────────────────

test("argValueRange: an unreadable amount is refused, an absent one is not", () => {
  const r = guard([argValueRange("pay", "amount", undefined, 1000)]).guardBefore("pay", {
    amount: "five thousand",
  });
  assert.equal(r.blocked, true);
  assert.equal(r.detViolations[0]?.ruleId, "args:unevaluable");

  const viaMcp = guard([argValueRange("pay", "amount", undefined, 1000)]).guardBefore(
    "mcp__bank__Pay",
    { amount: "lots" },
  );
  assert.equal(viaMcp.detViolations[0]?.ruleId, "args:unevaluable");

  const absent = guard([argValueRange("pay", "amount", undefined, 1000)]).guardBefore("pay", {
    note: "x",
  });
  assert.equal(absent.blocked, false);

  const nulled = guard([argValueRange("pay", "amount", undefined, 1000)]).guardBefore("pay", {
    amount: null,
  });
  assert.equal(nulled.blocked, false);
});

// ── 5. Non-numeric value against a numeric comparison warns once ───────

test("a numeric guard meeting a non-numeric value warns, once per value", () => {
  const cap = {
    formula: new G(new Not(new Gt(new ArgValue("pay", "amount"), new Const(1000)))),
    desc: "amount must not exceed 1000",
    patternName: "custom",
    liveness: false,
  } as DetFormula;
  const seen: string[] = [];
  const orig = console.warn;
  console.warn = (...parts: unknown[]) => {
    seen.push(parts.map(String).join(" "));
  };
  try {
    const g = guard([cap]);
    g.guardBefore("pay", { amount: "a great many dollars" });
    g.guardBefore("pay", { amount: "a great many dollars" });
  } finally {
    console.warn = orig;
  }
  const hits = seen.filter((s) => s.includes("non-numeric value"));
  assert.equal(hits.length, 1);
  assert.match(hits[0], /a great many dollars/);
});
