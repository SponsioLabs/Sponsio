/**
 * Per-contract `mode:` overrides and yaml mode-source precedence —
 * the TS port of Python's tests/test_per_contract_mode.py.
 *
 * Both directions are pinned, same as Python: tighten one rule inside
 * an observing book (the feature people ask for), and loosen one rule
 * inside an enforcing book (how you ship a rule you are not yet sure
 * about without turning the whole book off).
 *
 * Run via ``npm test`` (compiled) or ``npx tsx`` for quick iteration.
 */

import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { Sponsio, loadSponsoConfig } from "../index.js";

let passed = 0;
let failed = 0;
function expect(cond: boolean, name: string): void {
  if (cond) {
    passed++;
  } else {
    failed++;
    console.error(`  FAIL: ${name}`);
  }
}

function writeYaml(body: string): string {
  const dir = mkdtempSync(join(tmpdir(), "sponsio-mode-"));
  const p = join(dir, "sponsio.yaml");
  writeFileSync(p, body, "utf-8");
  return p;
}

/* ------------------------------------------------------------------
 * yaml mode sources: top-level `mode:` is honoured, worst typo raises
 * ------------------------------------------------------------------*/

function testYamlModeSources(): void {
  console.log("[yaml mode sources]");

  // A file written exactly as the docs document it: bare top-level mode.
  const topLevel = writeYaml(
    "mode: enforce\nagents:\n  agent:\n    contracts:\n      - never call `rm`\n",
  );
  expect(
    loadSponsoConfig(topLevel, "agent").mode === "enforce",
    "bare top-level `mode:` reaches the loader result",
  );

  // runtime.mode outranks the top-level key.
  const both = writeYaml(
    "mode: enforce\nruntime:\n  mode: observe\nagents:\n  agent:\n    contracts:\n      - never call `rm`\n",
  );
  expect(
    loadSponsoConfig(both, "agent").mode === "observe",
    "runtime.mode outranks top-level `mode:`",
  );

  // defaults.mode sits between the two.
  const defaults = writeYaml(
    "mode: enforce\ndefaults:\n  mode: observe\nagents:\n  agent:\n    contracts:\n      - never call `rm`\n",
  );
  expect(
    loadSponsoConfig(defaults, "agent").mode === "observe",
    "defaults.mode outranks top-level `mode:`",
  );

  // An unrecognised value raises instead of being silently ignored.
  const typo = writeYaml("mode: enfroce\nagents:\n  agent:\n    contracts: []\n");
  let threw = false;
  try {
    loadSponsoConfig(typo, "agent");
  } catch {
    threw = true;
  }
  expect(threw, "typo in top-level `mode:` raises");
}

/* ------------------------------------------------------------------
 * direction 1: tighten one rule inside an observing book
 * ------------------------------------------------------------------*/

function testEnforceOneRuleInObservingBook(): void {
  console.log("[tighten one rule in an observing book]");

  const cfg = writeYaml(
    [
      "runtime:",
      "  mode: observe",
      "agents:",
      "  agent:",
      "    contracts:",
      "      - desc: no rm",
      "        mode: enforce",
      "        G:",
      "          pattern: rate_limit",
      "          args: [rm, 0]",
      "      - desc: no curl",
      "        G:",
      "          pattern: rate_limit",
      "          args: [curl, 0]",
    ].join("\n") + "\n",
  );

  const g = new Sponsio({ config: cfg, sessionLog: false });

  // The tightened rule blocks even though the book observes.
  const rm = g.guardBefore("rm", {});
  expect(rm.blocked === true, "enforce-mode contract blocks in observing book");

  // The untouched rule still only observes.
  const curl = g.guardBefore("curl", {});
  expect(curl.blocked === false, "global-observe contract does not block");
  expect(
    curl.detViolations.length === 1 &&
      curl.detViolations[0].message.includes("WOULD-BLOCK"),
    "observed violation reports WOULD-BLOCK",
  );
}

/* ------------------------------------------------------------------
 * direction 2: loosen one rule inside an enforcing book
 * ------------------------------------------------------------------*/

function testObserveOneRuleInEnforcingBook(): void {
  console.log("[loosen one rule in an enforcing book]");

  const cfg = writeYaml(
    [
      "runtime:",
      "  mode: enforce",
      "agents:",
      "  agent:",
      "    contracts:",
      "      - desc: no rm",
      "        mode: observe",
      "        G:",
      "          pattern: rate_limit",
      "          args: [rm, 0]",
      "      - desc: no curl",
      "        G:",
      "          pattern: rate_limit",
      "          args: [curl, 0]",
    ].join("\n") + "\n",
  );

  const g = new Sponsio({ config: cfg, sessionLog: false });

  // The loosened rule logs but does not block, and the trace keeps
  // the event (nothing to roll back).
  const rm = g.guardBefore("rm", {});
  expect(rm.blocked === false, "observe-mode contract does not block in enforcing book");
  expect(
    rm.detViolations.length === 1 &&
      rm.detViolations[0].message.includes("WOULD-BLOCK"),
    "loosened violation reports WOULD-BLOCK",
  );

  // The untouched rule still blocks.
  const curl = g.guardBefore("curl", {});
  expect(curl.blocked === true, "global-enforce contract still blocks");
}

/* ------------------------------------------------------------------
 * absent stays distinct from observe; typo on a contract raises
 * ------------------------------------------------------------------*/

function testAbsentAndTypo(): void {
  console.log("[absent vs typo]");

  // NL contract with a mode still routes (loader parses the NL so the
  // mode has a formula to ride on).
  const nl = writeYaml(
    [
      "runtime:",
      "  mode: observe",
      "agents:",
      "  agent:",
      "    contracts:",
      "      - desc: call `rm` at most 0 times",
      "        mode: enforce",
    ].join("\n") + "\n",
  );
  const g = new Sponsio({ config: nl, sessionLog: false });
  expect(
    g.guardBefore("rm", {}).blocked === true,
    "NL contract with mode: enforce blocks in observing book",
  );

  const typo = writeYaml(
    [
      "agents:",
      "  agent:",
      "    contracts:",
      "      - desc: no rm",
      "        mode: enforc",
      "        G:",
      "          pattern: rate_limit",
      "          args: [rm, 0]",
    ].join("\n") + "\n",
  );
  let threw = false;
  try {
    loadSponsoConfig(typo, "agent");
  } catch {
    threw = true;
  }
  expect(threw, "typo in per-contract `mode:` raises");
}

function main(): void {
  testYamlModeSources();
  testEnforceOneRuleInObservingBook();
  testObserveOneRuleInEnforcingBook();
  testAbsentAndTypo();

  console.log(`\n${"=".repeat(40)}`);
  console.log(`Results: ${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
}

main();
