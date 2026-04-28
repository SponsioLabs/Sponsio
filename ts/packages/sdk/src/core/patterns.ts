/**
 * Pattern library — pre-built LTL contract patterns.
 *
 * Port of sponsio/patterns/library.py (det patterns).
 * Each function returns a formula AST + description.
 *
 * 36 patterns across 7 categories:
 *   Core temporal (14): mustPrecede, alwaysFollowedBy, noReversal,
 *     requiresPermission, noDataLeak, mutualExclusion, rateLimit,
 *     idempotent, deadline, mustConfirm, cooldown, segregationOfDuty,
 *     boundedRetry, loopDetection
 *   Argument (5): argBlacklist, argAllowlist, scopeLimit, argLengthLimit, dataIntact
 *   OWASP (8): destructiveActionGate, untrustedSourceGate,
 *     requiredStepsCompletion, toolAllowlist, dangerousBashCommands,
 *     dangerousSqlVerbs, irreversibleOnce, confirmAfterSource
 *   Resource (3): tokenBudget, argValueRange, delegationDepthLimit
 *   Workflow hygiene (6): dryRunBeforeCommit, backupBeforeDestructive,
 *     auditAfter, approvalFreshness, sanitizedBeforeSink, duplicateCallLimit
 */

import {
  Formula, Atom, Not, And, Or, Implies,
  G, F, X, U,
  Le, Ge, Var, Const,
} from "./formula.js";

export interface DetFormula {
  formula: Formula;
  desc: string;
  patternName: string;
  liveness: boolean;
}

export interface AssumptionEnforcementPair {
  assumption: DetFormula;
  enforcement: DetFormula;
}

// --- Helpers ---

function called(tool: string): Atom {
  // Supports "tool:pattern" format — produces called_with atom.
  if (tool.includes(":")) {
    const [physical, pattern] = tool.split(":", 2);
    return new Atom("called_with", [physical, pattern]);
  }
  return new Atom("called", [tool]);
}

function countVar(tool: string): Var {
  if (tool.includes(":")) {
    const [physical, pattern] = tool.split(":", 2);
    return new Var("count_with", physical, pattern);
  }
  return new Var("count", tool);
}

function physicalTool(tool: string): string {
  return tool.includes(":") ? tool.split(":", 1)[0] : tool;
}

/**
 * Reject empty / whitespace-only tool names at factory time.
 *
 * An empty atom (``called()``) is never emitted by the grounding layer, so
 * the contract silently becomes a no-op. Parity with Python
 * ``_ensure_non_empty`` in ``sponsio/patterns/library.py``.
 */
function ensureNonEmpty(value: string, pattern: string, arg: string): void {
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(
      `${pattern}: argument \`${arg}\` must be a non-empty string ` +
        `(got ${JSON.stringify(value)}). An empty tool name silently disables ` +
        `the contract — this is almost never what you want.`,
    );
  }
}

/**
 * Reject degenerate ``f(x, x)`` construction.
 *
 * Parity with Python ``_ensure_distinct``. Two-arg temporal patterns
 * (``mustPrecede``, ``alwaysFollowedBy``, ``mutualExclusion``,
 * ``noReversal``, ``deadline``, …) collapse into a tautology or change
 * their meaning entirely when the two tools collide; almost always a
 * user typo. Surface it at construction time.
 */
function ensureDistinct(
  a: string,
  b: string,
  pattern: string,
  argA: string,
  argB: string,
): void {
  ensureNonEmpty(a, pattern, argA);
  ensureNonEmpty(b, pattern, argB);
  if (a === b) {
    throw new Error(
      `${pattern}: \`${argA}\` and \`${argB}\` must refer to different tools ` +
        `(got ${JSON.stringify(a)} for both). A same-tool pattern is either ` +
        `vacuously satisfied or silently degenerates into a different contract — ` +
        `use \`idempotent\` / \`rateLimit\` if you meant 'at most once' / 'at most N times'.`,
    );
  }
}

/** Bounded eventually: phi within N steps (parity with Python ``_bounded_eventually``).
 *
 * For ``n = 1`` the result is ``phi`` (current step only); for ``n = 2`` it is
 * ``phi ∨ X(phi)``; and so on. The loop runs ``n - 1`` times — running ``n``
 * times (the previous behavior) gave a strictly weaker contract that admitted
 * a violating step the Python evaluator would have caught.
 */
function boundedEventually(phi: Formula, n: number): Formula {
  let result: Formula = phi;
  for (let i = 0; i < n - 1; i++) {
    result = new Or(phi, new X(result));
  }
  return result;
}

/** Bounded never: phi false for next N steps. */
function boundedNever(phi: Formula, n: number): Formula {
  if (n <= 0) return new Not(new Atom("__never__"));
  let result: Formula = new Not(phi);
  for (let i = 1; i < n; i++) {
    result = new And(new Not(phi), new X(result));
  }
  return result;
}

function nextN(phi: Formula, n: number): Formula {
  let result: Formula = phi;
  for (let i = 0; i < n; i++) {
    result = new X(result);
  }
  return result;
}

function forbiddenUntil(until: Formula, forbidden: Formula): Formula {
  return new Or(new U(new Not(forbidden), until), new G(new Not(forbidden)));
}

// --- Core temporal patterns ---

export function mustPrecede(before: string, after: string): DetFormula {
  ensureDistinct(before, after, "mustPrecede", "before", "after");
  const f = new Or(
    new U(new Not(called(after)), called(before)),
    new G(new Not(called(after))),
  );
  return {
    formula: f,
    desc: `tool \`${before}\` must precede \`${after}\``,
    patternName: "must_precede",
    liveness: false,
  };
}

export function alwaysFollowedBy(trigger: string, response: string): DetFormula {
  ensureDistinct(trigger, response, "alwaysFollowedBy", "trigger", "response");
  const f = new G(new Implies(called(trigger), new F(called(response))));
  return {
    formula: f,
    desc: `\`${trigger}\` must always be followed by \`${response}\``,
    patternName: "always_followed_by",
    liveness: true,
  };
}

export function noReversal(commitment: string, contradiction: string): DetFormula {
  ensureDistinct(commitment, contradiction, "noReversal", "commitment", "contradiction");
  const f = new G(new Implies(called(commitment), new G(new Not(called(contradiction)))));
  return {
    formula: f,
    desc: `cannot call \`${contradiction}\` after \`${commitment}\``,
    patternName: "no_reversal",
    liveness: false,
  };
}

export function requiresPermission(tool: string, permission: string): DetFormula {
  const f = new G(new Implies(called(tool), new Atom("perm", [permission])));
  return {
    formula: f,
    desc: `\`${tool}\` requires permission \`${permission}\``,
    patternName: "requires_permission",
    liveness: false,
  };
}

export function noDataLeak(source: string, external: string): DetFormula {
  ensureDistinct(source, external, "noDataLeak", "source", "external");
  const f = new G(new Implies(
    new Atom("contains", [source]),
    new Not(new Atom("flow", [source, external])),
  ));
  return {
    formula: f,
    desc: `no data leak from \`${source}\` to \`${external}\``,
    patternName: "no_data_leak",
    liveness: false,
  };
}

export function mutualExclusion(a: string, b: string): DetFormula {
  ensureDistinct(a, b, "mutualExclusion", "a", "b");
  const f = new And(
    new G(new Implies(called(a), new G(new Not(called(b))))),
    new G(new Implies(called(b), new G(new Not(called(a))))),
  );
  return {
    formula: f,
    desc: `tools \`${a}\` and \`${b}\` are mutually exclusive`,
    patternName: "mutual_exclusion",
    liveness: false,
  };
}

export function rateLimit(tool: string, maxCalls: number): DetFormula {
  const f = new G(new Le(countVar(tool), new Const(maxCalls)));
  return {
    formula: f,
    desc: `tool \`${tool}\` at most ${maxCalls} times`,
    patternName: "rate_limit",
    liveness: false,
  };
}

export function idempotent(tool: string): DetFormula {
  return { ...rateLimit(tool, 1), patternName: "idempotent", desc: `\`${tool}\` at most once` };
}

export function deadline(trigger: string, action: string, steps: number): DetFormula {
  ensureDistinct(trigger, action, "deadline", "trigger", "action");
  if (!Number.isInteger(steps) || steps < 1) {
    throw new Error(
      `deadline: 'steps' must be a positive integer (got ${steps}). ` +
        `A non-positive deadline is unsatisfiable.`,
    );
  }
  const f = new G(new Implies(
    called(trigger),
    new X(boundedEventually(called(action), steps)),
  ));
  return {
    formula: f,
    desc: `\`${action}\` must occur within ${steps} steps of \`${trigger}\``,
    patternName: "deadline",
    liveness: true,
  };
}

export function mustConfirm(action: string): DetFormula {
  const confirm = `confirm_${action}`;
  const f = new Or(
    new U(new Not(called(action)), called(confirm)),
    new G(new Not(called(action))),
  );
  return {
    formula: f,
    desc: `\`${action}\` requires confirmation (\`${confirm}\`)`,
    patternName: "must_confirm",
    liveness: false,
  };
}

export function cooldown(action: string, steps: number): DetFormula {
  const f = new G(new Implies(
    called(action),
    new X(boundedNever(called(action), steps)),
  ));
  return {
    formula: f,
    desc: `\`${action}\` has a cooldown of ${steps} steps`,
    patternName: "cooldown",
    liveness: false,
  };
}

export function segregationOfDuty(a: string, b: string): DetFormula {
  // Validation runs inside mutualExclusion; re-raising with a
  // segregation-of-duty pattern name would be nicer, but keeping the
  // message consistent is sufficient and avoids duplicating the check.
  const me = mutualExclusion(a, b);
  return {
    ...me,
    patternName: "segregation_of_duty",
    desc: `\`${a}\` and \`${b}\` must be performed by different agents`,
  };
}

export function boundedRetry(action: string, maxRetries: number): DetFormula {
  const f = new G(new Le(countVar(action), new Const(maxRetries)));
  return {
    formula: f,
    desc: `\`${action}\` limited to ${maxRetries} retries`,
    patternName: "bounded_retry",
    liveness: false,
  };
}

export function loopDetection(action: string, maxConsecutive: number): DetFormula {
  // G(consecutive_count(action) <= max)
  const f = new G(new Le(new Var("consecutive_count", action), new Const(maxConsecutive)));
  return {
    formula: f,
    desc: `\`${action}\` max ${maxConsecutive} consecutive calls`,
    patternName: "loop_detection",
    liveness: false,
  };
}

// --- Workflow hygiene patterns ---

export function dryRunBeforeCommit(dryRun: string, commit: string): DetFormula {
  const base = mustPrecede(dryRun, commit);
  return {
    ...base,
    desc: `\`${dryRun}\` dry-run must precede \`${commit}\``,
    patternName: "dry_run_before_commit",
  };
}

export function backupBeforeDestructive(backup: string, action: string): DetFormula {
  const base = mustPrecede(backup, action);
  return {
    ...base,
    desc: `\`${backup}\` backup must precede destructive action \`${action}\``,
    patternName: "backup_before_destructive",
  };
}

export function auditAfter(action: string, audit: string): DetFormula {
  const base = alwaysFollowedBy(action, audit);
  return {
    ...base,
    desc: `\`${action}\` must be followed by audit step \`${audit}\``,
    patternName: "audit_after",
    liveness: true,
  };
}

export function approvalFreshness(approval: string, action: string, steps: number): DetFormula {
  ensureDistinct(approval, action, "approvalFreshness", "approval", "action");
  if (!Number.isInteger(steps) || steps < 1) {
    throw new Error(
      `approval_freshness: 'steps' must be a positive integer (got ${steps}).`,
    );
  }
  const approvalAtom = called(approval);
  const actionAtom = called(action);
  const closedWindow = forbiddenUntil(approvalAtom, actionAtom);
  const f = new And(
    closedWindow,
    new G(new Implies(approvalAtom, nextN(closedWindow, steps + 1))),
  );
  return {
    formula: f,
    desc: `\`${action}\` requires approval \`${approval}\` within ${steps} steps`,
    patternName: "approval_freshness",
    liveness: false,
  };
}

export function sanitizedBeforeSink(
  source: string,
  sanitizer: string,
  sink: string,
): DetFormula {
  ensureDistinct(source, sanitizer, "sanitizedBeforeSink", "source", "sanitizer");
  ensureDistinct(sanitizer, sink, "sanitizedBeforeSink", "sanitizer", "sink");
  ensureDistinct(source, sink, "sanitizedBeforeSink", "source", "sink");
  const f = new G(new Implies(
    called(source),
    new X(forbiddenUntil(called(sanitizer), called(sink))),
  ));
  return {
    formula: f,
    desc: `after \`${source}\`, \`${sanitizer}\` must precede \`${sink}\``,
    patternName: "sanitized_before_sink",
    liveness: false,
  };
}

export function duplicateCallLimit(
  tool: string,
  argsPattern: string,
  maxCount: number,
): DetFormula {
  ensureNonEmpty(tool, "duplicateCallLimit", "tool");
  ensureNonEmpty(argsPattern, "duplicateCallLimit", "argsPattern");
  if (!Number.isInteger(maxCount) || maxCount < 0) {
    throw new Error(
      `duplicate_call_limit: 'maxCount' must be a non-negative integer (got ${maxCount}).`,
    );
  }
  const f = new G(new Le(new Var("count_with", tool, argsPattern), new Const(maxCount)));
  return {
    formula: f,
    desc: `\`${tool}\` calls matching ${JSON.stringify(argsPattern)} at most ${maxCount} times`,
    patternName: "duplicate_call_limit",
    liveness: false,
  };
}

// --- Argument patterns ---

export function argBlacklist(tool: string, field: string, patterns: string[]): DetFormula {
  const physical = physicalTool(tool);
  let body: Formula = new Not(new Atom("arg_field_has", [physical, field, patterns[0]]));
  for (let i = 1; i < patterns.length; i++) {
    body = new And(body, new Not(new Atom("arg_field_has", [physical, field, patterns[i]])));
  }
  const f = new G(new Implies(called(tool), body));
  return {
    formula: f,
    desc: `\`${tool}\`.${field} must not match ${JSON.stringify(patterns)}`,
    patternName: "arg_blacklist",
    liveness: false,
  };
}

export function argAllowlist(tool: string, field: string, patterns: string[]): DetFormula {
  if (patterns.length === 0) {
    throw new Error(
      "arg_allowlist: 'patterns' must be non-empty. An empty allowlist " +
      "would block every call to the tool. Use tool_allowlist to ban " +
      "the tool itself, or arg_blacklist if you want to forbid specific patterns."
    );
  }
  const physical = physicalTool(tool);
  let body: Formula = new Atom("arg_field_has", [physical, field, patterns[0]]);
  for (let i = 1; i < patterns.length; i++) {
    body = new Or(body, new Atom("arg_field_has", [physical, field, patterns[i]]));
  }
  const f = new G(new Implies(called(tool), body));
  return {
    formula: f,
    desc: `\`${tool}\`.${field} must match one of ${JSON.stringify(patterns)}`,
    patternName: "arg_allowlist",
    liveness: false,
  };
}

export function scopeLimit(tool: string, allowedPaths: string[]): DetFormula {
  const physical = physicalTool(tool);
  const f = new G(new Implies(
    called(tool),
    new Atom("arg_paths_within", [physical, ...allowedPaths]),
  ));
  return {
    formula: f,
    desc: `\`${tool}\` restricted to paths: ${allowedPaths.join(", ")}`,
    patternName: "scope_limit",
    liveness: false,
  };
}

export function argLengthLimit(tool: string, param: string, maxChars: number): DetFormula {
  const physical = physicalTool(tool);
  const f = new G(new Implies(
    called(tool),
    new Not(new Atom("arg_length_exceeds", [physical, param, String(maxChars)])),
  ));
  return {
    formula: f,
    desc: `\`${tool}\`.${param} must not exceed ${maxChars} characters`,
    patternName: "arg_length_limit",
    liveness: false,
  };
}

export function dataIntact(boundTool: string, originalPaths: string[]): DetFormula {
  const f = new G(new Implies(
    new Atom("arg_has", ["bash", boundTool]),
    new Atom("arg_paths_within", ["bash", ...originalPaths]),
  ));
  return {
    formula: f,
    desc: `\`${boundTool}\` must use only original data from ${originalPaths.join(", ")}`,
    patternName: "data_intact",
    liveness: false,
  };
}

// --- OWASP Agentic Security patterns ---

export function destructiveActionGate(tool: string, approverRole: string = "approver"): DetFormula {
  const confirm = `confirm_${tool}`;
  // G(!called(tool)) ∨ ((!called(tool)) U (called(confirm) ∧ perm(role)))
  const f = new Or(
    new G(new Not(called(tool))),
    new U(
      new Not(called(tool)),
      new And(called(confirm), new Atom("perm", [approverRole])),
    ),
  );
  return {
    formula: f,
    desc: `\`${tool}\` is destructive and requires \`${approverRole}\` approval`,
    patternName: "destructive_action_gate",
    liveness: false,
  };
}

export function untrustedSourceGate(
  source: string,
  sink: string,
  confirm: string = "",
): AssumptionEnforcementPair {
  ensureDistinct(source, sink, "untrustedSourceGate", "source", "sink");
  const confirmAction = confirm || `confirm_${sink}`;
  return {
    assumption: {
      formula: called(source),
      desc: `\`${source}\` has been called (untrusted input)`,
      patternName: "untrusted_source_gate_assumption",
      liveness: false,
    },
    enforcement: mustPrecede(confirmAction, sink),
  };
}

export function requiredStepsCompletion(trigger: string, steps: string[]): DetFormula {
  // Parity with Python:
  //   G(called(trigger) → F(called(s1)) ∧ F(called(s2)) ∧ ...)
  // (No outer ``X(...)``: the obligation must be discharged starting from the
  // trigger step, and weak-X at end-of-trace must NOT vacuously satisfy a
  // trigger that fires at the last step before any step has happened.)
  ensureNonEmpty(trigger, "requiredStepsCompletion", "trigger");
  if (!steps || steps.length === 0) {
    throw new Error(
      "requiredStepsCompletion: 'steps' must not be empty. An empty checklist " +
        "is vacuously satisfied for every trigger.",
    );
  }
  const seen = new Set<string>();
  for (const r of steps) {
    ensureNonEmpty(r, "requiredStepsCompletion", "steps");
    if (r === trigger) {
      throw new Error(
        `requiredStepsCompletion: trigger ${JSON.stringify(trigger)} cannot also ` +
          `appear in steps — the trigger would be its own follow-up, making the ` +
          `constraint trivially satisfied.`,
      );
    }
    if (seen.has(r)) {
      throw new Error(
        `requiredStepsCompletion: steps contains a duplicate ${JSON.stringify(r)}. ` +
          `Deduplicate before building the contract.`,
      );
    }
    seen.add(r);
  }
  let body: Formula = new F(called(steps[0]));
  for (let i = 1; i < steps.length; i++) {
    body = new And(body, new F(called(steps[i])));
  }
  const f = new G(new Implies(called(trigger), body));
  return {
    formula: f,
    desc: `after \`${trigger}\`, all steps must complete: ${steps.join(", ")}`,
    patternName: "required_steps_completion",
    liveness: true,
  };
}

export function toolAllowlist(allowedTools: string[]): DetFormula {
  // ``called_any`` is emitted by the grounding layer for every
  // tool_call event (regardless of which tool). Requiring
  // ``G(called_any -> Or(called(a1), ..., called(aN)))`` encodes
  // "whenever any tool runs, it must be one of these".
  if (!Array.isArray(allowedTools) || allowedTools.length === 0) {
    throw new Error(
      "toolAllowlist requires a non-empty list of allowed tool names",
    );
  }
  const disjunction = allowedTools
    .map((t) => called(t))
    .reduce<Formula | null>((acc, atom) => (acc ? new Or(acc, atom) : atom), null)!;
  const formula = new G(new Implies(new Atom("called_any"), disjunction));
  return {
    formula,
    desc: `only allowed tools: ${allowedTools.join(", ")}`,
    patternName: "tool_allowlist",
    liveness: false,
  };
}

export function dangerousBashCommands(forbidden?: string[]): DetFormula {
  const defaults = [
    "sed -i", "rm -rf", "cp /app/data", "mv /app/data",
    "python -c", "chmod", "> /app", "tee /app",
  ];
  const cmds = forbidden ?? defaults;
  // G(count_with(bash, cmd) <= 0) for each cmd — AND them all
  let body: Formula = new Le(new Var("count_with", "bash", cmds[0]), new Const(0));
  for (let i = 1; i < cmds.length; i++) {
    body = new And(body, new Le(new Var("count_with", "bash", cmds[i]), new Const(0)));
  }
  const f = new G(body);
  return {
    formula: f,
    desc: `bash commands [${cmds.join(", ")}] are banned`,
    patternName: "dangerous_bash_commands",
    liveness: false,
  };
}

/**
 * Build a JS-regex-compatible case-insensitive pattern from a plain
 * word. JS regex doesn't support inline ``(?i)`` flags, so we expand
 * each letter into a character class (``DROP`` → ``[dD][rR][oO][pP]``).
 * Non-letter characters pass through. This keeps grounding.ts's
 * ``new RegExp(pattern)`` call case-sensitive for ``arg_blacklist``
 * (where the user authors literal regex) while letting safety-net
 * factories like ``dangerous_sql_verbs`` match lowercase too.
 */
function caseInsensitiveLiteral(word: string): string {
  let out = "";
  for (const ch of word) {
    if (ch >= "a" && ch <= "z") out += `[${ch}${ch.toUpperCase()}]`;
    else if (ch >= "A" && ch <= "Z") out += `[${ch.toLowerCase()}${ch}]`;
    else if ("\\^$.|?*+()[]{}".includes(ch)) out += `\\${ch}`;
    else out += ch;
  }
  return out;
}

export function dangerousSqlVerbs(tool: string = "execute_sql", forbidden?: string[]): DetFormula {
  const defaults = ["DROP", "TRUNCATE", "DELETE", "ALTER"];
  const verbs = forbidden ?? defaults;
  // Wrap each verb in a case-insensitive word pattern so ``drop table``
  // and ``DROP TABLE`` both match — the safety net would be useless
  // case-sensitive since LLMs emit either form.
  const verbPatterns = verbs.map((v) => `\\b${caseInsensitiveLiteral(v)}\\b`);
  let body: Formula = new Not(
    new Atom("arg_field_has", [tool, "query", verbPatterns[0]]),
  );
  for (let i = 1; i < verbPatterns.length; i++) {
    body = new And(
      body,
      new Not(new Atom("arg_field_has", [tool, "query", verbPatterns[i]])),
    );
  }
  const f = new G(new Implies(called(tool), body));
  return {
    formula: f,
    desc: `\`${tool}\` must not use SQL verbs [${verbs.join(", ")}]`,
    patternName: "dangerous_sql_verbs",
    liveness: false,
  };
}

export function irreversibleOnce(action: string): DetFormula {
  const f = new G(new Le(countVar(action), new Const(1)));
  return {
    formula: f,
    desc: `\`${action}\` is irreversible and may be called at most once`,
    patternName: "irreversible_once",
    liveness: false,
  };
}

export function confirmAfterSource(source: string, action: string): AssumptionEnforcementPair {
  ensureDistinct(source, action, "confirmAfterSource", "source", "action");
  const confirm = `confirm_${action}`;
  return {
    assumption: {
      formula: called(source),
      desc: `\`${source}\` has been called`,
      patternName: "confirm_after_source_assumption",
      liveness: false,
    },
    enforcement: mustPrecede(confirm, action),
  };
}

// --- Resource / delegation patterns ---

export function tokenBudget(maxTokens: number, scope: string = "total"): DetFormula {
  const f = new G(new Le(new Var("token_count", scope), new Const(maxTokens)));
  return {
    formula: f,
    desc: `session ${scope} tokens must not exceed ${maxTokens}`,
    patternName: "token_budget",
    liveness: false,
  };
}

export function argValueRange(
  tool: string,
  field: string,
  minVal?: number,
  maxVal?: number,
): DetFormula {
  if (minVal == null && maxVal == null) {
    throw new Error("argValueRange requires at least minVal or maxVal");
  }
  const physical = physicalTool(tool);
  const v = new Var("arg_numeric", physical, field);
  // Guard with called_with so the range check only fires when the tool is invoked.
  const guardAtom = called(tool);

  const parts: Formula[] = [];
  if (minVal != null) parts.push(new Ge(v, new Const(minVal)));
  if (maxVal != null) parts.push(new Le(v, new Const(maxVal)));
  const body: Formula = parts.length === 1 ? parts[0] : new And(parts[0], parts[1]);

  const f = new G(new Implies(guardAtom, body));

  let rangeStr: string;
  if (minVal != null && maxVal != null) rangeStr = `[${minVal}, ${maxVal}]`;
  else if (minVal != null) rangeStr = `>= ${minVal}`;
  else rangeStr = `<= ${maxVal}`;

  return {
    formula: f,
    desc: `\`${tool}\`.${field} must be in range ${rangeStr}`,
    patternName: "arg_value_range",
    liveness: false,
  };
}

export function delegationDepthLimit(maxDepth: number): DetFormula {
  const f = new G(new Le(new Var("delegation_depth"), new Const(maxDepth)));
  return {
    formula: f,
    desc: `delegation chain must not exceed depth ${maxDepth}`,
    patternName: "delegation_depth_limit",
    liveness: false,
  };
}
