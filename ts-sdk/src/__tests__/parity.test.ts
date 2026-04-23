/**
 * TS-side regression tests for known TS↔Python parity bugs.
 *
 * Each test asserts a behavior the *fixed* TS implementation must hold.
 * They were authored alongside the corresponding fixes in
 *   - core/patterns.ts  (boundedEventually n iterations -> n-1)
 *   - core/patterns.ts  (requiredStepsCompletion no extra X(...) wrapper)
 *   - index.ts          (Sponsio.guardBefore full state rollback on block)
 *
 * Run with ``npm run test:src`` (no build needed).
 */

import {
  Sponsio,
  evaluate,
  Atom, G, Implies, X, F, And,
  type Valuation,
} from "../index.js";
import {
  deadline,
  requiredStepsCompletion,
  loopDetection,
  mutualExclusion,
  mustPrecede,
  alwaysFollowedBy,
  noReversal,
  noDataLeak,
  confirmAfterSource,
  untrustedSourceGate,
} from "../core/patterns.js";

let passed = 0;
let failed = 0;

function assert(condition: boolean, msg: string): void {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.error(`  FAIL: ${msg}`);
  }
}

function trace(...steps: Array<Record<string, boolean | number>>): Valuation[] {
  return steps;
}

// ─────────────────────────────────────────────────────────────────────────
// N4: boundedEventually off-by-one
// ─────────────────────────────────────────────────────────────────────────
//
// Python ``_bounded_eventually(phi, n)`` builds an OR-chain of length n
// ("phi within the next n positions"). The previous TS loop ran n iterations
// → an OR-chain of length n+1, accepting one extra position. ``deadline`` is
// the user-visible carrier of this off-by-one.

function testBoundedEventuallyDeadline() {
  console.log("[N4 boundedEventually]");

  // deadline("X","Y", 1): after X, Y must hold at the *next* position.
  // Trace [X, Z]: pos 1 holds Z (no Y). With the fix this is FALSE
  // (constraint violated); the buggy version made it TRUE because
  // Y was permitted at pos 1 OR pos 2.
  const fOne = deadline("X", "Y", 1).formula;
  assert(
    evaluate(fOne, trace({ "called(X)": true }, { "called(Z)": true })) === false,
    "deadline(X,Y,1): Y missing at next step must violate (off-by-one fix)",
  );

  // Sanity: same formula, Y at the next position → satisfied.
  assert(
    evaluate(fOne, trace({ "called(X)": true }, { "called(Y)": true })) === true,
    "deadline(X,Y,1): Y at next step satisfies",
  );

  // deadline(X, Y, 2): Y allowed within 2 next positions.
  const fTwo = deadline("X", "Y", 2).formula;
  assert(
    evaluate(
      fTwo,
      trace({ "called(X)": true }, { "called(Z)": true }, { "called(Y)": true }),
    ) === true,
    "deadline(X,Y,2): Y at pos 2 (within 2 steps) satisfies",
  );
  assert(
    evaluate(
      fTwo,
      trace(
        { "called(X)": true },
        { "called(Z)": true },
        { "called(Z)": true },
        { "called(Y)": true },
      ),
    ) === false,
    "deadline(X,Y,2): Y at pos 3 (one beyond budget) violates",
  );
}

// ─────────────────────────────────────────────────────────────────────────
// N3: requiredStepsCompletion extra X(...) wrapper
// ─────────────────────────────────────────────────────────────────────────
//
// Python emits ``G(called(trigger) -> F(s1) ∧ F(s2) ...)`` with no outer
// X. The previous TS version added ``X(...)``, so a trigger fired at the
// last trace position passed vacuously via weak-X (X past end-of-trace = T).

function testRequiredStepsCompletion() {
  console.log("\n[N3 requiredStepsCompletion]");

  const f = requiredStepsCompletion("trigger", ["s1", "s2"]).formula;

  // Trigger fires at the last position before any step has happened.
  // Fix: contract violated (F-budget exhausted at trace end → false).
  // Buggy: weak-X past end → vacuously true.
  assert(
    evaluate(
      f,
      trace({ "called(trigger)": true }),
    ) === false,
    "requiredStepsCompletion: trigger at last step with no follow-ups must violate",
  );

  // All follow-ups present → satisfied.
  assert(
    evaluate(
      f,
      trace(
        { "called(trigger)": true },
        { "called(s1)": true },
        { "called(s2)": true },
      ),
    ) === true,
    "requiredStepsCompletion: all steps eventually present satisfies",
  );

  // F starts at the trigger position itself (parity with Python: no
  // off-by-one). A step that fires AT the trigger position should
  // count toward discharging the obligation.
  assert(
    evaluate(
      f,
      trace(
        { "called(trigger)": true, "called(s1)": true, "called(s2)": true },
      ),
    ) === true,
    "requiredStepsCompletion: steps coincident with trigger discharge obligations",
  );
}

// ─────────────────────────────────────────────────────────────────────────
// N5: Sponsio.guardBefore rollback was incomplete
// ─────────────────────────────────────────────────────────────────────────
//
// The buggy rollback only undid ``callCounts[toolName]`` and left
// ``consecutiveCounts``, ``lastTool``, ``callWithCounts``, ``tokenCounts``
// and ``delegationDepth`` corrupted. The fix snapshots / restores the whole
// state. We catch the leak via consecutive_count: a blocked call must not
// leak its ``lastTool`` mutation, otherwise a subsequent same-tool call
// resets the consecutive counter instead of incrementing it.

function testGuardBeforeRollback() {
  console.log("\n[N5 guardBefore rollback]");

  // Two contracts:
  //   1. mutex(safe, risky) — blocks ``risky`` once ``safe`` was called.
  //   2. loopDetection(safe, 3) — at most 3 consecutive ``safe`` calls.
  const guard = new Sponsio({
    contracts: [
      mutualExclusion("safe", "risky"),
      loopDetection("safe", 3),
    ],
  });

  for (let i = 0; i < 3; i++) {
    const r = guard.guardBefore("safe", {});
    assert(!r.blocked, `safe #${i + 1} should be allowed`);
  }

  // Hits the mutex → blocked. With the buggy rollback, ``lastTool`` would
  // become "risky" and ``consecutiveCounts.safe`` would be reset to 0.
  const blocked = guard.guardBefore("risky", {});
  assert(blocked.blocked, "risky should be blocked by mutex");

  // Now a 4th ``safe`` call. Fixed rollback: lastTool === "safe", so
  // consecutive_count(safe) increments to 4 → loopDetection(safe, 3)
  // blocks it. Buggy rollback: lastTool === "risky", so the counter
  // resets to 1 and the call is wrongly allowed.
  const fourth = guard.guardBefore("safe", {});
  assert(
    fourth.blocked,
    "4th safe must be blocked by loop_detection — blocked-call state must NOT have leaked",
  );

  // Sanity that the violation message is the loop-detection one, not
  // a stale mutex echo.
  if (fourth.blocked) {
    assert(
      fourth.message.includes("consecutive") ||
        fourth.message.includes("3 times"),
      `4th-safe block reason should reference loop_detection, got: ${fourth.message}`,
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────
// Bonus: confirm the TS NL parser still routes deadline phrasing the same
// way Python does (matters because cross-language scenarios may use it).
// ─────────────────────────────────────────────────────────────────────────

function testDeadlineNlParity() {
  console.log("\n[deadline NL parity]");

  // "after `X`, `Y` must occur within 1 step"
  // Python: deadline(actions[0], actions[1], 1) = deadline("X", "Y", 1).
  // TS NL: same convention after the parity fix.
  const guard = new Sponsio({
    contracts: ["after `X`, `Y` must occur within 1 step"],
  });

  // Smoke: contract parsed successfully and the underlying formula is
  // semantically G(called(X) -> X(called(Y))).
  // We use a trace that violates it to confirm the trigger-action
  // direction is correct (X is trigger, Y is the deadline).
  const internal = (guard as unknown as {
    _contracts: { formula: import("../core/formula.js").Formula }[];
  })._contracts;
  assert(
    internal.length === 1,
    `deadline NL: expected 1 parsed contract, got ${internal.length}`,
  );

  // Reuse the lower-level evaluator on the parsed formula.
  const f = internal[0].formula;
  assert(
    evaluate(f, trace({ "called(X)": true }, { "called(Z)": true })) === false,
    "deadline NL: parsed formula treats X as trigger and Y as the required next step",
  );
  assert(
    evaluate(f, trace({ "called(X)": true }, { "called(Y)": true })) === true,
    "deadline NL: Y at next step satisfies",
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Run
// ─────────────────────────────────────────────────────────────────────────

// ─────────────────────────────────────────────────────────────────────────
// Issue #14: degenerate pattern construction must throw at factory time
// (parity with Python ``_ensure_distinct`` / ``_ensure_non_empty``). A
// same-tool ``mustPrecede("A", "A")`` silently compiled to a tautology
// in both SDKs before the fix — the runtime would then cheerfully accept
// everything and the operator would never know their guard was dead.
// ─────────────────────────────────────────────────────────────────────────

function expectThrows(fn: () => unknown, matches: RegExp, label: string): void {
  try {
    fn();
    assert(false, `${label}: expected to throw, but did not`);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    assert(
      matches.test(msg),
      `${label}: error message should match ${matches}, got: ${msg}`,
    );
  }
}

function testDegeneratePatternRejection() {
  console.log("\n[degenerate-pattern rejection (Issue #14)]");

  expectThrows(
    () => mustPrecede("A", "A"),
    /must refer to different/,
    "mustPrecede(X, X) rejected",
  );
  expectThrows(
    () => alwaysFollowedBy("A", "A"),
    /must refer to different/,
    "alwaysFollowedBy(X, X) rejected",
  );
  expectThrows(
    () => noReversal("A", "A"),
    /must refer to different/,
    "noReversal(X, X) rejected",
  );
  expectThrows(
    () => mutualExclusion("A", "A"),
    /must refer to different/,
    "mutualExclusion(X, X) rejected",
  );
  expectThrows(
    () => noDataLeak("A", "A"),
    /must refer to different/,
    "noDataLeak(X, X) rejected",
  );
  expectThrows(
    () => deadline("A", "A", 3),
    /must refer to different/,
    "deadline(X, X, n) rejected",
  );
  expectThrows(
    () => deadline("A", "B", 0),
    /positive integer/,
    "deadline with non-positive steps rejected",
  );
  expectThrows(
    () => mustPrecede("", "B"),
    /non-empty string/,
    "empty tool name rejected",
  );
  expectThrows(
    () => confirmAfterSource("fetch", "fetch"),
    /must refer to different/,
    "confirmAfterSource(X, X) rejected",
  );
  expectThrows(
    () => untrustedSourceGate("fetch", "fetch"),
    /must refer to different/,
    "untrustedSourceGate(X, X) rejected",
  );
  expectThrows(
    () => requiredStepsCompletion("start", ["start", "cleanup"]),
    /cannot also appear/,
    "requiredStepsCompletion with trigger in steps rejected",
  );
  expectThrows(
    () => requiredStepsCompletion("start", ["a", "a"]),
    /duplicate/,
    "requiredStepsCompletion with duplicate step rejected",
  );
  expectThrows(
    () => requiredStepsCompletion("start", []),
    /must not be empty/,
    "requiredStepsCompletion with empty steps rejected",
  );

  // Sanity: distinct args still produce a formula.
  try {
    const f = mustPrecede("A", "B");
    assert(f.formula !== null, "mustPrecede(A, B) still works");
  } catch (e) {
    assert(false, `non-degenerate mustPrecede should not throw: ${e}`);
  }
}

console.log("=== TS↔Python Parity Regression Tests ===\n");
testBoundedEventuallyDeadline();
testRequiredStepsCompletion();
testGuardBeforeRollback();
testDeadlineNlParity();
testDegeneratePatternRejection();

console.log(`\n${"=".repeat(40)}`);
console.log(`Results: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);

// Suppress unused-import warnings for AST helpers re-exported for clarity:
void Atom; void G; void Implies; void X; void F; void And;
