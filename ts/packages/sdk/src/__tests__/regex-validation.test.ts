import assert from "node:assert/strict";
import test from "node:test";
import { Sponsio } from "../index.js";
import { argBlacklist } from "../core/patterns.js";
import { Atom, G, Not } from "../core/formula.js";

test("a contract whose argument pattern is not a regex is refused at construction", () => {
  assert.throws(
    () =>
      new Sponsio({
        agentId: "rx",
        contracts: [argBlacklist("run_sql", "query", ["DROP("])],
        mode: "enforce",
        sessionLog: false,
      }),
    /not a valid regular expression/,
  );
});

test("a malformed llm_said pattern is refused at construction instead of never matching", () => {
  const bad = {
    formula: new G(new Not(new Atom("llm_said", ["(ssn"]))),
    desc: "no ssn",
    patternName: "custom",
    liveness: false,
  };
  assert.throws(
    () =>
      new Sponsio({
        agentId: "rx2",
        contracts: [bad],
        mode: "enforce",
        sessionLog: false,
      }),
    /not a valid regular expression/,
  );
});

test("valid patterns still construct", () => {
  const g = new Sponsio({
    agentId: "rx3",
    contracts: [argBlacklist("run_sql", "query", ["DROP\\s+TABLE"])],
    mode: "enforce",
    sessionLog: false,
  });
  assert.equal(g.guardBefore("run_sql", { query: "DROP TABLE x" }).stopOriginal, true);
});
