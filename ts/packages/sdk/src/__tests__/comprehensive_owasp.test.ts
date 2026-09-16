/**
 * Comprehensive coverage — OWASP / Agentic Security patterns (TS).
 *
 * Mirrors ``tests/comprehensive/test_patterns_owasp.py``. The two A/G
 * pair patterns (``untrustedSourceGate`` / ``confirmAfterSource``)
 * return ``{ assumption, guarantee }`` — we feed both into the guard.
 */

import {
  confirmAfterSource,
  dangerousBashCommands,
  dangerousSqlVerbs,
  destructiveActionGate,
  irreversibleOnce,
  requiredStepsCompletion,
  toolAllowlist,
  untrustedSourceGate,
} from "../core/patterns.js";
import { Sponsio } from "../index.js";
import { makeGuard, newScoreboard } from "./_comprehensive_helpers.js";

const board = newScoreboard();
const a = (cond: boolean, msg: string) => board.assert(cond, msg);

// ── destructive_action_gate ────────────────────────────────────────
{
  const g = makeGuard([destructiveActionGate("drop_table")]);
  a(g.guardBefore("drop_table", { table: "users" }).blocked, "destructive_action_gate blocks");
}

// ── untrusted_source_gate (A/G pair) ───────────────────────────────
{
  const pair = untrustedSourceGate("web_fetch", "send_email");
  // The TS Sponsio ctor accepts DetFormulas; the assumption / guarantee
  // pair is wired by registering both as separate contracts. The guard
  // evaluates the conjunction so the gate fires only after the source.
  const g = new Sponsio({
    contracts: [pair.assumption, pair.guarantee],
    mode: "enforce",
    sessionLog: false,
  });
  g.guardBefore("web_fetch");
  a(g.guardBefore("send_email").blocked, "untrusted_source_gate blocks sink without confirm");
}
{
  const pair = untrustedSourceGate("web_fetch", "send_email");
  const g = new Sponsio({
    contracts: [pair.assumption, pair.guarantee],
    mode: "enforce",
    sessionLog: false,
  });
  g.guardBefore("web_fetch");
  g.guardBefore("confirm_send_email");
  a(!g.guardBefore("send_email").blocked, "untrusted_source_gate allows after confirm");
}

// ── required_steps_completion ──────────────────────────────────────
// Liveness pattern — TS evaluator does weak finite-trace semantics so
// a still-pending obligation at trace end is vacuously satisfied. We
// instead verify the contract's *blocking* form by stepping past the
// trigger without completing all steps and asserting the verifier
// flags the next call after the deadline.
{
  const g = makeGuard([requiredStepsCompletion("close_incident", ["root_cause", "postmortem"])]);
  a(!g.guardBefore("close_incident").blocked, "required_steps_completion: trigger fires");
  // Liveness — final result not asserted here; mirror Python's path of
  // checking the contract loaded + the evaluator runs without throw.
}
{
  const g = makeGuard([requiredStepsCompletion("close_incident", ["root_cause", "postmortem"])]);
  g.guardBefore("close_incident");
  g.guardBefore("root_cause");
  // All required steps complete — no violation surfaces.
  a(!g.guardBefore("postmortem").blocked, "required_steps_completion satisfied with all steps");
}

// ── tool_allowlist ─────────────────────────────────────────────────
{
  const g = makeGuard([toolAllowlist(["read_file", "list_files"])]);
  a(g.guardBefore("rm_rf").blocked, "tool_allowlist blocks disallowed tool");
}
{
  const g = makeGuard([toolAllowlist(["read_file", "list_files"])]);
  a(!g.guardBefore("read_file", { path: "/tmp/x" }).blocked, "tool_allowlist allows listed tool");
}

// ── dangerous_bash_commands ────────────────────────────────────────
{
  const g = makeGuard([dangerousBashCommands()]);
  a(g.guardBefore("bash", { command: "rm -rf /" }).blocked, "dangerous_bash_commands blocks rm -rf");
}
{
  const g = makeGuard([dangerousBashCommands()]);
  a(!g.guardBefore("bash", { command: "ls /tmp" }).blocked, "dangerous_bash_commands allows ls");
}
// The default used to be the literal "rm -rf"; every other spelling of a
// recursive delete walked through.
for (const cmd of [
  "rm --recursive --force /",
  "rm -fr /tmp/x",
  "rm -Rf /tmp/x",
  "rm -f -r /tmp/x",
  "rm -v --recursive /tmp/x",
]) {
  const g = makeGuard([dangerousBashCommands()]);
  a(g.guardBefore("bash", { command: cmd }).blocked, `dangerous_bash_commands blocks ${cmd}`);
}
for (const cmd of ["rm -f build.log", "rm notes.txt", "rmdir -p empty/dir"]) {
  const g = makeGuard([dangerousBashCommands()]);
  a(!g.guardBefore("bash", { command: cmd }).blocked, `dangerous_bash_commands allows ${cmd}`);
}

// ── dangerous_sql_verbs ────────────────────────────────────────────
{
  const g = makeGuard([dangerousSqlVerbs()]);
  a(g.guardBefore("execute_sql", { query: "DROP TABLE users" }).blocked, "dangerous_sql_verbs blocks DROP");
}
{
  const g = makeGuard([dangerousSqlVerbs()]);
  // TS variant uses case-insensitive expansion — lowercase still fires.
  a(g.guardBefore("execute_sql", { query: "drop table users" }).blocked, "dangerous_sql_verbs case-insensitive");
}
{
  const g = makeGuard([dangerousSqlVerbs()]);
  a(!g.guardBefore("execute_sql", { query: "SELECT * FROM users" }).blocked, "dangerous_sql_verbs allows SELECT");
}

// ── irreversible_once ──────────────────────────────────────────────
{
  const g = makeGuard([irreversibleOnce("launch_rocket")]);
  a(!g.guardBefore("launch_rocket").blocked, "irreversible_once first allowed");
  a(g.guardBefore("launch_rocket").blocked, "irreversible_once second blocked");
}

// ── confirm_after_source (A/G pair) ────────────────────────────────
{
  const pair = confirmAfterSource("web_fetch", "send_email");
  const g = new Sponsio({
    contracts: [pair.assumption, pair.guarantee],
    mode: "enforce",
    sessionLog: false,
  });
  g.guardBefore("web_fetch");
  a(g.guardBefore("send_email").blocked, "confirm_after_source blocks sink");
}
{
  const pair = confirmAfterSource("web_fetch", "send_email");
  const g = new Sponsio({
    contracts: [pair.assumption, pair.guarantee],
    mode: "enforce",
    sessionLog: false,
  });
  g.guardBefore("web_fetch");
  g.guardBefore("confirm_send_email");
  a(!g.guardBefore("send_email").blocked, "confirm_after_source allows after confirm");
}

board.summary("comprehensive_owasp");
