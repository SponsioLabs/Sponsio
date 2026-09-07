/**
 * What leaves the machine, and whether the other runtime would have sent
 * the same bytes.
 *
 * The privacy ladder is the thing a regulated customer is buying, so the
 * levels are checked against fixed expectations rather than against
 * themselves. The expectations are Python's own output, captured from
 * ``sponsio.bridge.privacy``: a level that quietly sent more here than
 * there would be a promise kept in one runtime and broken in the other.
 */

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import * as privacy from "../cloud/privacy.js";
import {
  argsPreview,
  slug,
  buildViewModel,
  newSessionId,
} from "../cloud/view-model.js";
import { CloudClient } from "../cloud/client.js";
import { parseNl } from "../core/nl-parser.js";
import { Sponsio } from "../index.js";

const PHI = { mrn: "MRN-88213", name: "Dana Whitfield" };

describe("privacy levels", () => {
  test("full sends the arguments, spaced as Python writes them", () => {
    assert.equal(
      argsPreview(PHI),
      '{"mrn": "MRN-88213", "name": "Dana Whitfield"}',
    );
  });

  test("shape keeps lengths and nothing else", () => {
    // Lengths survive because "the model passed a 40 000-character path"
    // is a debugging fact and not content.
    assert.equal(privacy.shape(PHI), '{"mrn": <str:9>, "name": <str:14>}');
  });

  test("hashed agrees with Python byte for byte", () => {
    // Captured from sponsio.bridge.privacy.digest. The digest is an
    // equality token, so a divergence means the two runtimes disagree
    // about whether two calls were the same call. It diverged once, on
    // JSON spacing: JSON.stringify omits the spaces Python writes.
    assert.equal(privacy.digest(PHI), "sha256:b3a18e0127a4");
  });

  test("key order does not change the digest", () => {
    assert.equal(privacy.digest({ a: 1, b: 2 }), privacy.digest({ b: 2, a: 1 }));
  });

  test("metadata and tool_calls send no arguments at all", () => {
    for (const level of ["metadata", "tool_calls"] as const) {
      assert.equal(privacy.preview(PHI, level, argsPreview(PHI)), "");
    }
  });

  test("only tool_calls drops a whole step", () => {
    assert.equal(privacy.keepsStep("output", "metadata"), true);
    assert.equal(privacy.keepsStep("output", "tool_calls"), false);
    assert.equal(privacy.keepsStep("tool_call", "tool_calls"), true);
  });

  test("an unrecognised level fails closed", () => {
    // Far more likely a typo in a deployment that meant to tighten than
    // a request to loosen.
    const before = process.env.SPONSIO_PRIVACY;
    process.env.SPONSIO_PRIVACY = "metadataa";
    try {
      assert.equal(privacy.resolve(), "tool_calls");
    } finally {
      if (before === undefined) delete process.env.SPONSIO_PRIVACY;
      else process.env.SPONSIO_PRIVACY = before;
    }
  });

  test("the environment beats the argument", () => {
    // The person who decides what may leave a machine is its operator,
    // not the author of the agent running on it.
    const before = process.env.SPONSIO_PRIVACY;
    process.env.SPONSIO_PRIVACY = "hashed";
    try {
      assert.equal(privacy.resolve("full"), "hashed");
    } finally {
      if (before === undefined) delete process.env.SPONSIO_PRIVACY;
      else process.env.SPONSIO_PRIVACY = before;
    }
  });

  test("the run says what it is not sending", () => {
    // A dashboard showing empty arguments has to be able to tell "the
    // agent passed none" from "this deployment declined to send them".
    const d = privacy.describe("tool_calls");
    assert.deepEqual(d, {
      level: "tool_calls",
      sendsArguments: false,
      sendsArgumentShapes: false,
      sendsOutputText: false,
      sendsClaimValues: false,
      sendsOutputLane: false,
    });
  });
});

describe("the run the cloud stores", () => {
  test("a violation carries the rule's identity, not just its sentence", () => {
    // The cloud groups findings by (pattern, args) and derives a stable
    // id from them. Uploaded without these, a rule reworded loses its
    // own history.
    const g = new Sponsio({
      agentId: "refund-bot",
      contracts: ["tool `check_policy` must precede `issue_refund`"],
      mode: "observe",
    });
    g.guardBefore("issue_refund", { amount: 40 });
    const vm = g.viewModel() as any;
    const v = vm.steps[0].verdict.violations[0];
    assert.equal(v.pattern, "must_precede");
    assert.deepEqual(v.args, ["check_policy", "issue_refund"]);
    assert.equal(v.contractId, "tool-check-policy-must-precede-issue-refund");
  });

  test("the output lane is recorded too", () => {
    // It builds no span tree, so it was enforced locally and never
    // uploaded: at `full` a TypeScript run showed half of what a Python
    // one showed.
    const g = new Sponsio({
      agentId: "a",
      contracts: ["response must not contain emails"],
      mode: "observe",
    });
    g.observeResponse("write to dana@example.com");
    const vm = g.viewModel() as any;
    assert.equal(vm.steps.filter((s: any) => s.type === "output").length, 1);
  });

  test("tool_calls drops the output lane and keeps the actions", () => {
    const before = process.env.SPONSIO_PRIVACY;
    process.env.SPONSIO_PRIVACY = "tool_calls";
    try {
      const g = new Sponsio({ agentId: "a", contracts: [], mode: "observe" });
      g.guardBefore("pay", { amount: 1 });
      g.observeResponse("anything at all");
      const vm = g.viewModel() as any;
      assert.equal(vm.steps.length, 1);
      assert.equal(vm.steps[0].type, "tool_call");
      assert.equal(vm.steps[0].argsPreview, "");
    } finally {
      if (before === undefined) delete process.env.SPONSIO_PRIVACY;
      else process.env.SPONSIO_PRIVACY = before;
    }
  });

  test("a fresh session gets a fresh id", () => {
    // The server upserts by this key, so two runs sharing one silently
    // become one and the older is overwritten with no error.
    const g = new Sponsio({ agentId: "a", contracts: [], mode: "observe" });
    const first = (g.viewModel() as any).session.id;
    g.resetSession();
    assert.notEqual((g.viewModel() as any).session.id, first);
  });

  test("uploading without a key is a no, not a throw", () => {
    // Losing telemetry must never change what an agent does.
    const before = process.env.SPONSIO_API_KEY;
    delete process.env.SPONSIO_API_KEY;
    try {
      const g = new Sponsio({ agentId: "a", contracts: [], mode: "observe" });
      return g.uploadSession().then((ok) => assert.equal(ok, false));
    } finally {
      if (before !== undefined) process.env.SPONSIO_API_KEY = before;
    }
  });

  test("the contract id is Python's slug", () => {
    assert.equal(slug("tool `a` must precede `b`"), "tool-a-must-precede-b");
    assert.equal(slug("!!!"), "rule");
  });

  test("an empty run is still a well-formed run", () => {
    const vm = buildViewModel({
      agentId: "a",
      mode: "observe",
      sessionId: "run-1",
      startedAt: 0,
      contracts: [],
      spans: [],
      privacyLevel: "full",
    }) as any;
    assert.equal(vm.summary.totalSteps, 0);
    assert.equal(vm.summary.passRate, 1);
  });
});

describe("what an untrusted rule string cannot do", () => {
  test("a very long sentence is truncated, not chewed on", () => {
    // Several keyword regexes are quadratic on adversarial input, and on
    // a platform whose customers supply their own overlay rules that
    // string is not fully trusted. Python has always truncated at 10k;
    // this side did not, so the same rulebook that cost Python
    // milliseconds could hang a TypeScript agent.
    const nasty = "1".repeat(200_000) + " steps";
    const started = Date.now();
    parseNl(nasty);
    assert.ok(
      Date.now() - started < 2_000,
      "parsing a 200k-character 'rule' should not take seconds",
    );
  });

  test("a base url of slashes does not go quadratic", () => {
    const started = Date.now();
    new CloudClient({ apiKey: "x", baseUrl: "https://x" + "/".repeat(100_000) });
    assert.ok(Date.now() - started < 1_000);
  });

  test("run ids come from the CSPRNG", () => {
    // The server upserts by this id, so a collision silently merges two
    // runs. Math.random is seeded from a clock a booting fleet shares.
    const seen = new Set<string>();
    for (let i = 0; i < 5_000; i++) seen.add(newSessionId());
    assert.equal(seen.size, 5_000);
    assert.match(newSessionId(), /^run-[0-9a-f]{16}$/);
  });
});
