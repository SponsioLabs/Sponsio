/**
 * Cross-language parity test — TS side.
 *
 * Loads ``tests/cross_language/scenarios.json`` from the repo root
 * and replays each scenario through the TS Sponsio guard, asserting
 * every step's expected block/allow decision matches.
 *
 * Mirrors ``tests/cross_language/test_python.py`` so the same JSON
 * file gates both languages. Any drift in the TS deterministic core
 * (formula, evaluator, grounding, nl-parser, patterns) shows up
 * here.
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { Sponsio } from "../index.js";

interface Step {
  tool: string;
  args?: Record<string, unknown>;
  expect_blocked: boolean;
  reason?: string;
}
interface Scenario {
  name: string;
  contracts: string[];
  steps: Step[];
}

const here = dirname(fileURLToPath(import.meta.url));
// dist/__tests__/parity.scenarios.test.js -> ../../../../tests/cross_language/scenarios.json
// src/__tests__/parity.scenarios.test.ts  -> ../../../../tests/cross_language/scenarios.json
const candidates = [
  resolve(here, "..", "..", "..", "..", "..", "tests", "cross_language", "scenarios.json"),
  resolve(here, "..", "..", "..", "..", "tests", "cross_language", "scenarios.json"),
];
let scenariosPath: string | null = null;
for (const c of candidates) {
  try {
    readFileSync(c, "utf-8");
    scenariosPath = c;
    break;
  } catch {
    // try next
  }
}
if (!scenariosPath) {
  console.error("[parity.scenarios] cannot locate tests/cross_language/scenarios.json");
  process.exit(1);
}

const data = JSON.parse(readFileSync(scenariosPath, "utf-8")) as { scenarios: Scenario[] };

let passed = 0;
let failed = 0;

function assert(condition: boolean, msg: string): void {
  if (condition) passed++;
  else {
    failed++;
    console.error(`  FAIL: ${msg}`);
  }
}

console.log("=== Cross-language scenarios (TS) ===\n");
console.log(`Loading from ${scenariosPath}\n`);

for (const scenario of data.scenarios) {
  console.log(`--- ${scenario.name}`);
  const guard = new Sponsio({
    agentId: `xtest_${scenario.name}`,
    contracts: scenario.contracts,
    mode: "enforce",
    sessionLog: false,
  });
  for (let i = 0; i < scenario.steps.length; i++) {
    const step = scenario.steps[i];
    const r = guard.guardBefore(step.tool, step.args ?? {});
    const ok = r.blocked === step.expect_blocked;
    const tag = ok ? "✓" : "✗";
    const got = r.blocked ? "blocked" : "allowed";
    const want = step.expect_blocked ? "blocked" : "allowed";
    console.log(
      `  ${tag} step ${i + 1} ${step.tool}: want=${want} got=${got}${step.reason ? `  (${step.reason})` : ""}`,
    );
    assert(ok, `${scenario.name} step ${i + 1} (${step.tool}): want=${want} got=${got}`);
  }
}

console.log(`\n${"=".repeat(40)}`);
console.log(`Cross-language scenarios: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
