import assert from "node:assert/strict";
import test from "node:test";
import { Sponsio } from "../index.js";
import { ArgValue, Const, G, Gt, Not } from "../core/formula.js";

/**
 * A numeric cap must see the shapes a model actually writes. Treating
 * "$5,000" as "not a number" let it past a cap that stopped "5000", and
 * the overflow literal "1e400" used to block on Python while passing
 * here — the same contract, two answers.
 */
function capGuard() {
  const cap = {
    formula: new G(new Not(new Gt(new ArgValue("pay", "amount"), new Const(1000)))),
    desc: "amount must not exceed 1000",
    patternName: "custom",
    liveness: false,
  } as any;
  return new Sponsio({
    agentId: "numeric",
    contracts: [cap],
    mode: "enforce",
    sessionLog: false,
  });
}

test("a cap blocks formatted currency above the limit", () => {
  for (const amount of [5000, "5000", "$5,000", "5,000", "5000 USD", "$1,234.50"]) {
    assert.equal(
      capGuard().guardBefore("pay", { amount }).blocked,
      true,
      `expected ${JSON.stringify(amount)} to be blocked`,
    );
  }
});

test("a cap blocks an overflow magnitude, matching Python", () => {
  assert.equal(capGuard().guardBefore("pay", { amount: "1e400" }).blocked, true);
});

test("a cap still admits formatted values under the limit", () => {
  for (const amount of [10, "10", "$10", "10 USD"]) {
    assert.equal(
      capGuard().guardBefore("pay", { amount }).blocked,
      false,
      `expected ${JSON.stringify(amount)} to be allowed`,
    );
  }
});

test("an ambiguous decimal comma is not guessed at", () => {
  // "5,50" may be five and a half; reading it as 550 would silently
  // change the magnitude, so it stays uncoerced and the cap cannot act.
  assert.equal(capGuard().guardBefore("pay", { amount: "5,50" }).blocked, false);
});
