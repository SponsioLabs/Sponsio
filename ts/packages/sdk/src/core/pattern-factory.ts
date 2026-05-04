/**
 * Structured-pattern factory — ``{ pattern: "must_precede", args: [...] }``
 * → ``DetFormula``. Bridges yaml-declared contracts to the pattern
 * library without going through NL parsing.
 *
 * Parity with Python's ``sponsio/config.py::_apply_pattern`` and the
 * pattern catalog in ``sponsio/patterns/library.py``. Every public det
 * factory in ``./patterns.ts`` is reachable here under its snake_case
 * yaml name; arg coercion is deliberately lenient (strings coerce
 * to numbers where the factory wants a number) so a yaml authored
 * by hand doesn't crash on a quoted ``"3"``.
 *
 * Atoms used in ``A:`` / assumption fields (``called``, ``called_with``)
 * are also exposed here — Python writes assumptions as structured
 * patterns too (see README canonical sample:
 * ``A: { pattern: called, args: [read, ".env"] }``), so the TS loader
 * needs to build them from the same shape.
 */

import {
  mustPrecede,
  alwaysFollowedBy,
  noReversal,
  requiresPermission,
  noDataLeak,
  mutualExclusion,
  rateLimit,
  idempotent,
  deadline,
  mustConfirm,
  cooldown,
  segregationOfDuty,
  boundedRetry,
  loopDetection,
  dryRunBeforeCommit,
  backupBeforeDestructive,
  auditAfter,
  approvalFreshness,
  sanitizedBeforeSink,
  duplicateCallLimit,
  argAllowlist,
  argBlacklist,
  scopeLimit,
  argLengthLimit,
  dataIntact,
  destructiveActionGate,
  untrustedSourceGate,
  requiredStepsCompletion,
  toolAllowlist,
  dangerousBashCommands,
  dangerousSqlVerbs,
  irreversibleOnce,
  confirmAfterSource,
  tokenBudget,
  argValueRange,
  delegationDepthLimit,
  type DetFormula,
  type AssumeGuaranteePair,
} from "./patterns.js";
import { Atom } from "./formula.js";

/** Error raised when a pattern name is unknown or args don't line up. */
export class PatternFactoryError extends Error {}

function asStr(v: unknown, field: string): string {
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  throw new PatternFactoryError(
    `expected string for ${field}, got ${typeof v}`,
  );
}

function asNum(v: unknown, field: string): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  throw new PatternFactoryError(
    `expected number for ${field}, got ${JSON.stringify(v)}`,
  );
}

function asStrList(v: unknown, field: string): string[] {
  if (!Array.isArray(v)) {
    throw new PatternFactoryError(
      `expected list of strings for ${field}, got ${typeof v}`,
    );
  }
  return v.map((x, i) => asStr(x, `${field}[${i}]`));
}

function needArg<T>(args: unknown[], i: number, field: string): T {
  if (i >= args.length) {
    throw new PatternFactoryError(`missing arg #${i} (${field})`);
  }
  return args[i] as T;
}

/**
 * Wrap a raw atom in a DetFormula so ``A: { pattern: called, args: […] }``
 * produces the same shape as the pattern factories.
 */
function atomAsFormula(
  atom: Atom,
  patternName: string,
  desc: string,
): DetFormula {
  return { formula: atom, desc, patternName, liveness: false };
}

/**
 * Build a ``DetFormula`` from a yaml-structured ``{ pattern, args }``
 * pair. Returns ``null`` for pattern names the TS runtime doesn't
 * know (pack-only runtime features, Python-only atoms); callers
 * surface those through the loader's ``skipped`` list.
 *
 * Args come in as YAML-parsed values: strings, numbers, nested
 * arrays. We coerce leniently so a hand-authored yaml doesn't
 * crash on ``args: ["3"]`` instead of ``args: [3]``.
 */
export function buildPatternByName(
  pattern: string,
  args: unknown[],
): DetFormula | AssumeGuaranteePair | null {
  switch (pattern) {
    // Atom-as-formula — used almost exclusively in A: / assumption.
    case "called": {
      const tool = asStr(needArg(args, 0, "tool"), "tool");
      // ``called, args: [read, ".env"]`` — second arg is an optional
      // content pattern that turns this into a called_with atom.
      if (args.length >= 2) {
        const contentPattern = asStr(args[1], "content_pattern");
        return atomAsFormula(
          new Atom("called_with", [tool, contentPattern]),
          "called_with",
          `called_with(${tool}, ${contentPattern})`,
        );
      }
      return atomAsFormula(
        new Atom("called", [tool]),
        "called",
        `called(${tool})`,
      );
    }
    case "called_with": {
      const tool = asStr(needArg(args, 0, "tool"), "tool");
      const contentPattern = asStr(
        needArg(args, 1, "content_pattern"),
        "content_pattern",
      );
      return atomAsFormula(
        new Atom("called_with", [tool, contentPattern]),
        "called_with",
        `called_with(${tool}, ${contentPattern})`,
      );
    }

    // Safety
    case "must_precede":
      return mustPrecede(
        asStr(needArg(args, 0, "before"), "before"),
        asStr(needArg(args, 1, "after"), "after"),
      );
    case "must_confirm":
      return mustConfirm(asStr(needArg(args, 0, "action"), "action"));
    case "requires_permission":
      return requiresPermission(
        asStr(needArg(args, 0, "tool"), "tool"),
        asStr(needArg(args, 1, "permission"), "permission"),
      );
    case "no_data_leak":
      return noDataLeak(
        asStr(needArg(args, 0, "source"), "source"),
        asStr(needArg(args, 1, "external"), "external"),
      );
    case "destructive_action_gate":
      return destructiveActionGate(
        asStr(needArg(args, 0, "tool"), "tool"),
        args.length >= 2 ? asStr(args[1], "approver_role") : undefined,
      );

    // Compliance
    case "no_reversal":
      return noReversal(
        asStr(needArg(args, 0, "commitment"), "commitment"),
        asStr(needArg(args, 1, "contradiction"), "contradiction"),
      );
    case "segregation_of_duty":
      return segregationOfDuty(
        asStr(needArg(args, 0, "a"), "a"),
        asStr(needArg(args, 1, "b"), "b"),
      );
    case "always_followed_by":
      return alwaysFollowedBy(
        asStr(needArg(args, 0, "trigger"), "trigger"),
        asStr(needArg(args, 1, "response"), "response"),
      );
    case "required_steps_completion":
      return requiredStepsCompletion(
        asStr(needArg(args, 0, "trigger"), "trigger"),
        asStrList(needArg(args, 1, "steps"), "steps"),
      );

    // Operational
    case "rate_limit":
      return rateLimit(
        asStr(needArg(args, 0, "tool"), "tool"),
        asNum(needArg(args, 1, "max_calls"), "max_calls"),
      );
    case "idempotent":
      return idempotent(asStr(needArg(args, 0, "tool"), "tool"));
    case "cooldown":
      return cooldown(
        asStr(needArg(args, 0, "action"), "action"),
        asNum(needArg(args, 1, "steps"), "steps"),
      );
    case "deadline":
      return deadline(
        asStr(needArg(args, 0, "trigger"), "trigger"),
        asStr(needArg(args, 1, "action"), "action"),
        asNum(needArg(args, 2, "steps"), "steps"),
      );
    case "bounded_retry":
      return boundedRetry(
        asStr(needArg(args, 0, "action"), "action"),
        asNum(needArg(args, 1, "max_retries"), "max_retries"),
      );
    case "loop_detection":
      return loopDetection(
        asStr(needArg(args, 0, "action"), "action"),
        asNum(needArg(args, 1, "max_consecutive"), "max_consecutive"),
      );
    case "dry_run_before_commit":
      return dryRunBeforeCommit(
        asStr(needArg(args, 0, "dry_run"), "dry_run"),
        asStr(needArg(args, 1, "commit"), "commit"),
      );
    case "backup_before_destructive":
      return backupBeforeDestructive(
        asStr(needArg(args, 0, "backup"), "backup"),
        asStr(needArg(args, 1, "action"), "action"),
      );
    case "audit_after":
      return auditAfter(
        asStr(needArg(args, 0, "action"), "action"),
        asStr(needArg(args, 1, "audit"), "audit"),
      );
    case "approval_freshness":
      return approvalFreshness(
        asStr(needArg(args, 0, "approval"), "approval"),
        asStr(needArg(args, 1, "action"), "action"),
        asNum(needArg(args, 2, "steps"), "steps"),
      );
    case "sanitized_before_sink":
      return sanitizedBeforeSink(
        asStr(needArg(args, 0, "source"), "source"),
        asStr(needArg(args, 1, "sanitizer"), "sanitizer"),
        asStr(needArg(args, 2, "sink"), "sink"),
      );
    case "duplicate_call_limit":
      return duplicateCallLimit(
        asStr(needArg(args, 0, "tool"), "tool"),
        asStr(needArg(args, 1, "args_pattern"), "args_pattern"),
        asNum(needArg(args, 2, "max_count"), "max_count"),
      );

    // Exclusion
    case "mutual_exclusion":
      return mutualExclusion(
        asStr(needArg(args, 0, "a"), "a"),
        asStr(needArg(args, 1, "b"), "b"),
      );
    case "tool_allowlist":
      return toolAllowlist(asStrList(needArg(args, 0, "allowed_tools"), "allowed_tools"));

    // Argument / Path
    case "arg_blacklist":
      return argBlacklist(
        asStr(needArg(args, 0, "tool"), "tool"),
        asStr(needArg(args, 1, "field"), "field"),
        asStrList(needArg(args, 2, "patterns"), "patterns"),
      );
    case "arg_allowlist":
      return argAllowlist(
        asStr(needArg(args, 0, "tool"), "tool"),
        asStr(needArg(args, 1, "field"), "field"),
        asStrList(needArg(args, 2, "patterns"), "patterns"),
      );
    case "scope_limit":
      return scopeLimit(
        asStr(needArg(args, 0, "tool"), "tool"),
        asStrList(needArg(args, 1, "allowed_paths"), "allowed_paths"),
      );
    case "arg_length_limit":
      return argLengthLimit(
        asStr(needArg(args, 0, "tool"), "tool"),
        asStr(needArg(args, 1, "param"), "param"),
        asNum(needArg(args, 2, "max_chars"), "max_chars"),
      );
    case "data_intact":
      return dataIntact(
        asStr(needArg(args, 0, "bound_tool"), "bound_tool"),
        asStrList(needArg(args, 1, "original_paths"), "original_paths"),
      );
    case "arg_value_range": {
      const tool = asStr(needArg(args, 0, "tool"), "tool");
      const field = asStr(needArg(args, 1, "field"), "field");
      const min = args.length >= 3 && args[2] != null ? asNum(args[2], "min") : undefined;
      const max = args.length >= 4 && args[3] != null ? asNum(args[3], "max") : undefined;
      return argValueRange(tool, field, min, max);
    }

    // Agentic Security
    case "untrusted_source_gate":
      return untrustedSourceGate(
        asStr(needArg(args, 0, "source"), "source"),
        asStr(needArg(args, 1, "sink"), "sink"),
        args.length >= 3 ? asStr(args[2], "confirm") : undefined,
      );
    case "confirm_after_source":
      return confirmAfterSource(
        asStr(needArg(args, 0, "source"), "source"),
        asStr(needArg(args, 1, "action"), "action"),
      );
    case "dangerous_bash_commands":
      return dangerousBashCommands(
        args.length >= 1 ? asStrList(args[0], "forbidden") : undefined,
      );
    case "dangerous_sql_verbs":
      return dangerousSqlVerbs(
        args.length >= 1 ? asStr(args[0], "tool") : undefined,
        args.length >= 2 ? asStrList(args[1], "forbidden") : undefined,
      );
    case "irreversible_once":
      return irreversibleOnce(asStr(needArg(args, 0, "action"), "action"));

    // Resource
    case "token_budget":
      return tokenBudget(
        asNum(needArg(args, 0, "max_tokens"), "max_tokens"),
        args.length >= 2 ? asStr(args[1], "scope") : undefined,
      );
    case "delegation_depth_limit":
      return delegationDepthLimit(asNum(needArg(args, 0, "max_depth"), "max_depth"));

    default:
      return null;
  }
}

/** Type guard: is this an A/G pair (``untrusted_source_gate`` / ``confirm_after_source``)? */
export function isAgPair(
  v: DetFormula | AssumeGuaranteePair,
): v is AssumeGuaranteePair {
  return (
    (v as AssumeGuaranteePair).assumption !== undefined &&
    (v as AssumeGuaranteePair).guarantee !== undefined
  );
}

/** All det pattern names this factory can build. Used by ``patterns`` CLI + tests. */
export const KNOWN_DET_PATTERNS: readonly string[] = Object.freeze([
  "called",
  "called_with",
  "must_precede",
  "must_confirm",
  "requires_permission",
  "no_data_leak",
  "destructive_action_gate",
  "no_reversal",
  "segregation_of_duty",
  "always_followed_by",
  "required_steps_completion",
  "rate_limit",
  "idempotent",
  "cooldown",
  "deadline",
  "bounded_retry",
  "loop_detection",
  "dry_run_before_commit",
  "backup_before_destructive",
  "audit_after",
  "approval_freshness",
  "sanitized_before_sink",
  "duplicate_call_limit",
  "mutual_exclusion",
  "tool_allowlist",
  "arg_blacklist",
  "arg_allowlist",
  "scope_limit",
  "arg_length_limit",
  "data_intact",
  "arg_value_range",
  "untrusted_source_gate",
  "confirm_after_source",
  "dangerous_bash_commands",
  "dangerous_sql_verbs",
  "irreversible_once",
  "token_budget",
  "delegation_depth_limit",
]);
