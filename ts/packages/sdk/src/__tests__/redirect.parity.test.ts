/**
 * The TS half of the redirect parity check. Reads the same rulebook and
 * the same answer key as `tests/cross_language/test_redirect_parity.py`,
 * so a verdict difference fails on whichever side drifted.
 *
 * The two runtimes disagreed about every `redirect_to_safe` verdict and
 * nothing caught it: `scenarios.json` carries inline NL contracts, and
 * `redirect_to_safe` has no plain-English form, so it was never in there.
 *
 * TS used to report a redirect as `blocked: true` with no way to learn the
 * substitute, because a compiled contract carried no strategy: the formula
 * `G(!called(unsafe))` is indistinguishable from a plain ban.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { Sponsio } from "../index.js";

// ESM: no `__dirname`. Same derivation as the other tests here.
const HERE = path.dirname(fileURLToPath(import.meta.url));
const CROSS = path.resolve(HERE, "../../../../../tests/cross_language");
const RULEBOOK = path.join(CROSS, "redirect_parity.yaml");

interface Case {
  tool: string;
  why: string;
  expect: {
    blocked: boolean;
    allowed: boolean;
    redirected: boolean;
    redirectedTo: string | null;
    stopOriginal: boolean;
  };
}

const CASES: Case[] = JSON.parse(
  readFileSync(path.join(CROSS, "redirect_expected.json"), "utf8"),
);

test("every verdict matches the shared answer key", async () => {
  const guard = new Sponsio({
    config: RULEBOOK,
    agentId: "bot",
    mode: "enforce",
  });

  for (const c of CASES) {
    const r = await guard.guardBefore(c.tool, {});
    const got = {
      blocked: r.blocked,
      allowed: r.allowed,
      redirected: r.redirected,
      // Python writes `null` where TS leaves the key off.
      redirectedTo: r.redirectedTo ?? null,
      stopOriginal: r.stopOriginal,
    };
    assert.deepEqual(got, c.expect, `${c.tool}: ${c.why}`);
  }
});

test("stopOriginal is the gate, not blocked", async () => {
  // The distinction this whole file exists for: on a redirect, a caller
  // reading `blocked` runs the exact call the contract forbade.
  const guard = new Sponsio({
    config: RULEBOOK,
    agentId: "bot",
    mode: "enforce",
  });
  const r = await guard.guardBefore("rm_rf", {});
  assert.equal(r.blocked, false, "a redirect is not a hard block");
  assert.equal(r.stopOriginal, true, "but the original call must not run");
  assert.equal(r.redirectedTo, "trash", "and the substitute is named");
});
