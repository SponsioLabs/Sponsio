/**
 * The two evidence obligations, and the entry that feeds them.
 *
 * `claimRequiresEvidence` and `underdeterminedMustClarify` were the last
 * two patterns Python had and TS did not. They are pure formulas over
 * three atoms the trace has to carry, so all three parts had to land
 * together: the atoms in grounding, the patterns, and `observeEvidence`.
 *
 * The verdicts here were checked against Python on the same four
 * verdict/action pairs and agree line for line.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { Sponsio } from "../index.js";

const RULEBOOK = `version: "1"
agents:
  bot:
    contracts:
      - desc: totals must verify
        G: {pattern: claim_requires_evidence, args: [items_sum_to_total]}
      - desc: ambiguous totals must be clarified
        G: {pattern: underdetermined_must_clarify, args: [items_sum_to_total]}
`;

function guard(): Sponsio {
  const dir = mkdtempSync(join(tmpdir(), "sponsio-evidence-"));
  const path = join(dir, "sponsio.yaml");
  writeFileSync(path, RULEBOOK);
  return new Sponsio({ config: path, agentId: "bot", mode: "enforce" });
}

test("a PASS releases", () => {
  const r = guard().observeEvidence("items_sum_to_total", "PASS", "release");
  assert.equal(r.stopOriginal, false);
});

test("any verdict but PASS violates the obligation", () => {
  // The point of `claim_requires_evidence`: not just MISMATCH. A claim
  // the service could not check is not a claim that checked out.
  for (const verdict of ["MISMATCH", "NO_EVIDENCE", "SOURCE_UNAVAILABLE"]) {
    const r = guard().observeEvidence("items_sum_to_total", verdict, "block");
    assert.equal(r.stopOriginal, true, `${verdict} should violate`);
  }
});

test("an ambiguous verdict released instead of clarified is a violation", () => {
  // `underdetermined_must_clarify` guards the policy wiring, not the
  // claim: an override that quietly releases an ambiguous claim trips it.
  const released = guard().observeEvidence(
    "items_sum_to_total",
    "UNDERDETERMINED",
    "release",
  );
  assert.equal(released.stopOriginal, true);
});

test("a predicate no contract names is transparent", () => {
  const r = guard().observeEvidence("date_valid", "MISMATCH", "block");
  assert.equal(r.stopOriginal, false, "contracts are per predicate");
});

test("observe mode records and does not stop", () => {
  const dir = mkdtempSync(join(tmpdir(), "sponsio-evidence-"));
  const path = join(dir, "sponsio.yaml");
  writeFileSync(path, RULEBOOK);
  const g = new Sponsio({ config: path, agentId: "bot", mode: "observe" });
  const r = g.observeEvidence("items_sum_to_total", "MISMATCH", "block");
  assert.equal(r.stopOriginal, false);
  assert.equal(r.detViolations.length, 1, "still recorded");
});
