/**
 * Tests for the new P0 surface: config-loader, session-log, and
 * the observe/enforce/env mode-precedence logic in ``Sponsio``.
 *
 * Run via ``npm test`` (compiled) or ``npx tsx`` for quick iteration.
 */

import { strict as assert } from "node:assert";
import {
  mkdtempSync,
  readFileSync,
  writeFileSync,
  existsSync,
  readdirSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  Sponsio,
  SessionLogger,
  rotateSessions,
  loadSponsoConfig,
} from "../index.js";

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

function mkTmpDir(prefix: string): string {
  return mkdtempSync(join(tmpdir(), prefix));
}

function writeYaml(dir: string, body: string): string {
  const p = join(dir, "sponsio.yaml");
  writeFileSync(p, body, "utf-8");
  return p;
}

function countLogLines(path: string): number {
  if (!existsSync(path)) return 0;
  const body = readFileSync(path, "utf-8");
  return body.split("\n").filter((l) => l.length > 0).length;
}

/* ------------------------------------------------------------------
 * config-loader
 * ------------------------------------------------------------------*/

function testConfigLoader(): void {
  console.log("[config-loader]");

  // -- NL-string E: is the canonical shape `sponsio onboard` emits. ---
  {
    const dir = mkTmpDir("sponsio-cfg-");
    const p = writeYaml(
      dir,
      `
version: "1"
runtime:
  mode: enforce
agents:
  coding_agent:
    contracts:
      - desc: "confirm before destructive delete"
        G: "must call \`confirm_delete\` before \`delete\`"
      - desc: "loop guard"
        G: "tool \`delete\` at most 3 times"
`.trimStart(),
    );

    const cfg = loadSponsoConfig(p, "coding_agent");
    expect(cfg.contracts.length === 2, "loads two NL contracts");
    expect(cfg.mode === "enforce", "picks up runtime.mode");
    expect(cfg.skipped.length === 0, "no skipped items on a clean yaml");
  }

  // -- structured/packs/sto entries get skipped (with reason). --------
  {
    const dir = mkTmpDir("sponsio-cfg-");
    const p = writeYaml(
      dir,
      `
version: "1"
agents:
  bot:
    include:
      - sponsio:core/runaway
    contracts:
      - desc: "token budget"
        G:
          pattern: token_budget
          args: [200000, "total"]
      - desc: "tone"
        sto: true
        G: "response must be empathetic"
      - G: "must call \`A\` before \`B\`"
`.trimStart(),
    );

    const cfg = loadSponsoConfig(p, "bot");
    // token_budget is a built-in det pattern → kept as a compiled
    // DetFormula. NL entry also kept. The sto-flag entry is skipped
    // (no pattern:). The ``sponsio:core/runaway`` include is now
    // resolved by the pack loader; runaway is an empty stub so it
    // contributes 0 contracts and adds nothing to ``skipped``.
    expect(cfg.contracts.length === 2, "keeps token_budget + NL-string");
    expect(
      cfg.contracts.some(
        (c) => typeof c === "object" && c.patternName === "token_budget",
      ),
      "token_budget compiled via pattern factory",
    );
    expect(
      !cfg.skipped.some((s) => s.kind === "pack"),
      "pack include resolved (no longer skipped)",
    );
    expect(
      cfg.skipped.some((s) => s.kind === "sto-contract"),
      "flags the sto contract",
    );
  }

  // -- agent-id fallback: `*` block + desc fallback. ------------------
  {
    const dir = mkTmpDir("sponsio-cfg-");
    const p = writeYaml(
      dir,
      `
version: "1"
agents:
  "*":
    contracts:
      - desc: "must call \`A\` before \`B\`"
`.trimStart(),
    );

    const cfg = loadSponsoConfig(p, "anything");
    expect(cfg.contracts.length === 1, "falls back to wildcard agent block");
    const first = cfg.contracts[0];
    expect(
      typeof first === "string" && first.includes("must call"),
      "uses desc when E is absent",
    );
  }

  // -- missing file raises a readable error. --------------------------
  {
    let msg = "";
    try {
      loadSponsoConfig("/nope/does-not-exist.yaml", "x");
    } catch (e) {
      msg = (e as Error).message;
    }
    expect(msg.includes("cannot read config"), "missing file error");
  }
}

/* ------------------------------------------------------------------
 * Sponsio — mode precedence + observe semantics + yaml loading
 * ------------------------------------------------------------------*/

function testModePrecedence(): void {
  console.log("\n[mode precedence]");

  const original = process.env.SPONSIO_MODE;

  // default: observe
  {
    delete process.env.SPONSIO_MODE;
    const g = new Sponsio({ contracts: [], sessionLog: false });
    expect(g.mode === "observe", "default mode is observe");
  }

  // ctor beats default
  {
    delete process.env.SPONSIO_MODE;
    const g = new Sponsio({
      contracts: [],
      mode: "enforce",
      sessionLog: false,
    });
    expect(g.mode === "enforce", "ctor arg beats default");
  }

  // env beats ctor (Python parity — ops flipping production)
  {
    process.env.SPONSIO_MODE = "observe";
    const g = new Sponsio({
      contracts: [],
      mode: "enforce",
      sessionLog: false,
    });
    expect(g.mode === "observe", "env beats ctor arg");
  }

  // unknown env ignored (falls back to next in chain)
  {
    process.env.SPONSIO_MODE = "banana";
    const g = new Sponsio({
      contracts: [],
      mode: "enforce",
      sessionLog: false,
    });
    expect(g.mode === "enforce", "unknown env value falls through");
  }

  // restore env
  if (original === undefined) {
    delete process.env.SPONSIO_MODE;
  } else {
    process.env.SPONSIO_MODE = original;
  }
}

function testObserveModeDoesNotBlock(): void {
  console.log("\n[observe mode]");

  const dir = mkTmpDir("sponsio-log-");
  const g = new Sponsio({
    contracts: ["tool `confirm` must precede `delete`"],
    mode: "observe",
    sessionLogBaseDir: dir,
  });

  // delete without confirm — would block in enforce, must pass in observe.
  const r = g.guardBefore("delete", {});
  expect(!r.blocked, "observe mode: violation does not block");
  expect(r.allowed, "observe mode: returns allowed=true");
  expect(
    g.violations.length === 1,
    "observe mode: internal violations list still captures it",
  );

  // And the would-block was written to the session log.
  const agentDir = join(dir, "agent");
  expect(existsSync(agentDir), "agent dir created");
  const files = readdirSync(agentDir).filter((f) => f.endsWith(".jsonl"));
  expect(files.length === 1, "exactly one session log file");
  const logPath = join(agentDir, files[0]);
  const lines = readFileSync(logPath, "utf-8")
    .trim()
    .split("\n")
    .map((l) => JSON.parse(l));
  expect(
    lines.some((rec) => rec.action === "observed"),
    "observed record present",
  );
}

function testEnforceModeBlocksAndLogs(): void {
  console.log("\n[enforce mode]");

  const dir = mkTmpDir("sponsio-log-");
  const g = new Sponsio({
    agentId: "bot",
    contracts: ["tool `confirm` must precede `delete`"],
    mode: "enforce",
    sessionLogBaseDir: dir,
  });

  const blocked = g.guardBefore("delete", {});
  expect(blocked.blocked, "enforce mode: violation blocks");

  const agentDir = join(dir, "bot");
  const files = readdirSync(agentDir).filter((f) => f.endsWith(".jsonl"));
  const logPath = join(agentDir, files[0]);
  const lines = readFileSync(logPath, "utf-8")
    .trim()
    .split("\n")
    .map((l) => JSON.parse(l));
  expect(
    lines.some((rec) => rec.action === "blocked"),
    "blocked record present in enforce mode",
  );
}

function testConfigDrivenCtor(): void {
  console.log("\n[config: ctor]");

  const dir = mkTmpDir("sponsio-cfg-");
  const yamlPath = writeYaml(
    dir,
    `
version: "1"
runtime:
  mode: enforce
agents:
  bot:
    contracts:
      - desc: "confirm first"
        G: "must call \`confirm\` before \`delete\`"
`.trimStart(),
  );

  delete process.env.SPONSIO_MODE;
  const g = new Sponsio({
    agentId: "bot",
    config: yamlPath,
    sessionLogBaseDir: mkTmpDir("sponsio-log-"),
  });

  expect(g.mode === "enforce", "yaml runtime.mode honoured");
  const r = g.guardBefore("delete", {});
  expect(r.blocked, "yaml-loaded contract actually blocks");
}

/* ------------------------------------------------------------------
 * session-log — sanitization + rotation
 * ------------------------------------------------------------------*/

function testSessionLoggerBasics(): void {
  console.log("\n[session-log]");

  const dir = mkTmpDir("sponsio-log-");
  const logger = new SessionLogger("coding_agent", {
    baseDir: dir,
    timestamp: "20260101_120000",
    skipRotation: true,
  });

  logger.log({
    ts: 1700000000,
    agent_id: "coding_agent",
    action: "allowed",
    pipeline: "det",
    constraint: "coding_agent.delete",
    result: { action: "allowed", message: "" },
  });
  logger.log({
    ts: 1700000001,
    agent_id: "coding_agent",
    action: "blocked",
    pipeline: "det",
    constraint: "confirm first",
    result: { action: "blocked", message: "BLOCKED: ..." },
  });

  expect(
    countLogLines(logger.path) === 2,
    "session log appends two jsonl records",
  );
  expect(
    logger.path.includes("coding_agent"),
    "path includes sanitized agent id",
  );
}

function testSessionLoggerSanitizesAgentId(): void {
  const dir = mkTmpDir("sponsio-log-");
  const logger = new SessionLogger("../../etc/pwd", {
    baseDir: dir,
    skipRotation: true,
  });
  // The path must not escape `dir`.
  expect(
    logger.path.startsWith(dir),
    "malicious agent id does not escape base dir",
  );
}

function testRotateSessions(): void {
  const dir = mkTmpDir("sponsio-rotate-");
  const agentDir = join(dir, "bot");
  writeFileSync(join(dir, "stub.txt"), "", "utf-8"); // ignored, not jsonl
  // mkdir via logger then manually drop an ancient file
  const logger = new SessionLogger("bot", {
    baseDir: dir,
    timestamp: "20260101_120000",
    skipRotation: true,
  });
  logger.log({
    ts: 1700000000,
    agent_id: "bot",
    action: "allowed",
    pipeline: "det",
    constraint: "bot.noop",
    result: { action: "allowed", message: "" },
  });

  const removed = rotateSessions(dir, /*keepDays*/ 7, /*maxMB*/ 100);
  expect(
    removed.length === 0,
    "rotate keeps fresh files when under budget",
  );
  expect(existsSync(logger.path), "fresh log survives rotation");
  // Avoid referencing agentDir directly — just make sure rotation visited it.
  expect(readdirSync(agentDir).length >= 1, "agent dir still populated");
}

/* ------------------------------------------------------------------
 * main
 * ------------------------------------------------------------------*/

async function main(): Promise<void> {
  console.log("=== config / mode / session-log ===\n");
  testConfigLoader();
  testModePrecedence();
  testObserveModeDoesNotBlock();
  testEnforceModeBlocksAndLogs();
  testConfigDrivenCtor();
  testSessionLoggerBasics();
  testSessionLoggerSanitizesAgentId();
  testRotateSessions();

  console.log(`\n${"=".repeat(40)}`);
  console.log(`Results: ${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
}

main();
