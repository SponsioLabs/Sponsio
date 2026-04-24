/**
 * Quick tests for the expanded pattern library + parseRepr.
 */

import { Sponsio } from "../index.js";
import {
  mustConfirm, requiresPermission, boundedRetry, loopDetection,
  argLengthLimit, argValueRange, irreversibleOnce, tokenBudget,
  delegationDepthLimit, dangerousBashCommands,
  dryRunBeforeCommit, backupBeforeDestructive, auditAfter,
  approvalFreshness, sanitizedBeforeSink, duplicateCallLimit,
} from "../core/patterns.js";
import { parseRepr } from "../core/parser.js";

let passed = 0, failed = 0;
function assert(c: boolean, msg: string) {
  if (c) passed++;
  else { failed++; console.error(`FAIL: ${msg}`); }
}

// ── Pattern smoke tests ──
function testPatterns() {
  console.log("[Patterns]");

  // mustConfirm
  {
    const g = new Sponsio({ contracts: [mustConfirm("delete")], mode: "enforce", sessionLog: false });
    assert(g.guardBefore("delete").blocked, "mustConfirm blocks without confirm");
    g.reset();
    assert(!g.guardBefore("confirm_delete").blocked, "confirm_delete allowed");
    g.guardAfter("confirm_delete");
    assert(!g.guardBefore("delete").blocked, "delete allowed after confirm");
  }

  // boundedRetry
  {
    const g = new Sponsio({ contracts: [boundedRetry("retry", 2)], mode: "enforce", sessionLog: false });
    assert(!g.guardBefore("retry").blocked, "retry 1");
    g.guardAfter("retry");
    assert(!g.guardBefore("retry").blocked, "retry 2");
    g.guardAfter("retry");
    assert(g.guardBefore("retry").blocked, "retry 3 blocked");
  }

  // irreversibleOnce
  {
    const g = new Sponsio({ contracts: [irreversibleOnce("launch")], mode: "enforce", sessionLog: false });
    assert(!g.guardBefore("launch").blocked, "first launch ok");
    g.guardAfter("launch");
    assert(g.guardBefore("launch").blocked, "second launch blocked");
  }

  // loopDetection
  {
    const g = new Sponsio({ contracts: [loopDetection("poll", 3)], mode: "enforce", sessionLog: false });
    for (let i = 0; i < 3; i++) {
      assert(!g.guardBefore("poll").blocked, `poll ${i+1}`);
      g.guardAfter("poll");
    }
    assert(g.guardBefore("poll").blocked, "poll 4 blocked (loop)");
  }

  // workflow hygiene
  {
    const g = new Sponsio({ contracts: [dryRunBeforeCommit("plan", "apply")], mode: "enforce", sessionLog: false });
    assert(g.guardBefore("apply").blocked, "apply blocked before dry-run");
    g.reset();
    assert(!g.guardBefore("plan").blocked, "plan allowed");
    g.guardAfter("plan");
    assert(!g.guardBefore("apply").blocked, "apply allowed after dry-run");
  }

  {
    const g = new Sponsio({ contracts: [backupBeforeDestructive("snapshot", "drop")], mode: "enforce", sessionLog: false });
    assert(g.guardBefore("drop").blocked, "drop blocked before backup");
  }

  {
    const f = auditAfter("transfer", "audit_transfer");
    assert(f.patternName === "audit_after", "auditAfter carries patternName");
    assert(f.liveness, "auditAfter is liveness");
  }

  {
    const g = new Sponsio({ contracts: [approvalFreshness("approve", "deploy", 1)], mode: "enforce", sessionLog: false });
    assert(g.guardBefore("deploy").blocked, "deploy blocked before approval");
  }

  {
    const g = new Sponsio({ contracts: [sanitizedBeforeSink("web_fetch", "sanitize", "send_email")], mode: "enforce", sessionLog: false });
    assert(!g.guardBefore("web_fetch").blocked, "web_fetch allowed");
    g.guardAfter("web_fetch");
    assert(g.guardBefore("send_email").blocked, "sink blocked before sanitizer");
  }

  {
    const g = new Sponsio({ contracts: [duplicateCallLimit("search", "invoice-42", 1)], mode: "enforce", sessionLog: false });
    assert(!g.guardBefore("search", { query: "invoice-42" }).blocked, "first duplicate-pattern call allowed");
    g.guardAfter("search", "ok");
    assert(g.guardBefore("search", { query: "invoice-42" }).blocked, "second duplicate-pattern call blocked");
  }

  console.log("  pattern smoke tests: OK");
}

// ── parseRepr tests ──
function testParseRepr() {
  console.log("\n[parseRepr]");

  const cases = [
    "G(called('auth'))",
    "G((called('a') -> F(called('b'))))",
    "G((Var('count', 'bash') <= 5))",
    "(!(called('x')) U called('y'))",
    "G((called('auth') -> !(called('delete'))))",
  ];

  for (const c of cases) {
    try {
      const f = parseRepr(c);
      assert(f != null, `parse: ${c}`);
    } catch (e) {
      assert(false, `parse failed: ${c} -> ${e}`);
    }
  }

  console.log("  parseRepr tests: OK");
}

testPatterns();
testParseRepr();

console.log(`\n${"=".repeat(40)}`);
console.log(`Results: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
