// A mistyped command must fail loudly.
//
// `sponsio valdate` used to fall through to the scanner, which found no
// files, printed `{"tools":[]}` and exited 0. The onboarding prompt tells
// coding agents to run these commands, so an agent that typos one reads a
// zero exit and moves on believing it worked.
//
// The scanner still has to accept what it is for: a path, a glob, a file.

import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";

const BIN = path.resolve(__dirname, "../../../bin/sponsio.cjs");

function run(args: string[]): { code: number; out: string; err: string } {
  try {
    const out = execFileSync(process.execPath, [BIN, ...args], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
    return { code: 0, out, err: "" };
  } catch (e) {
    const x = e as { status?: number; stdout?: string; stderr?: string };
    return { code: x.status ?? 1, out: x.stdout ?? "", err: x.stderr ?? "" };
  }
}

test("a mistyped command exits non-zero and names itself", () => {
  const r = run(["valdate"]);
  assert.notEqual(r.code, 0, `expected failure, got:\n${r.out}`);
  assert.match(r.err, /unknown command 'valdate'/);
  assert.match(r.err, /--help/);
});

test("a real command still runs", () => {
  const r = run(["patterns"]);
  assert.equal(r.code, 0, r.err);
  assert.match(r.out, /Sponsio patterns/);
});

test("a path is still a scan target, not a command", () => {
  const r = run([path.resolve(__dirname, "..")]);
  assert.equal(r.code, 0, r.err);
  assert.match(r.out, /"tools"/);
});

test("a glob is still a scan target", () => {
  const r = run(["src/**/*.ts"]);
  assert.equal(r.code, 0, r.err);
  assert.match(r.out, /"tools"/);
});

test("help lists the commands, not just scan flags", () => {
  const r = run(["--help"]);
  assert.equal(r.code, 0, r.err);
  assert.match(r.out, /COMMANDS:/);
  for (const cmd of ["init", "validate", "doctor", "report", "patterns"]) {
    assert.ok(r.out.includes(cmd), `help does not mention ${cmd}`);
  }
});
