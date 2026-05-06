/**
 * sponsio.yaml loader for the TypeScript runtime.
 *
 * Parses a Python-compatible sponsio.yaml into the shape the
 * {@link Sponsio} constructor expects: a list of NL-string contracts
 * plus runtime settings (mode, dashboard).
 *
 * Intentionally conservative — this loader understands the **subset**
 * of the yaml schema that makes sense in TS today:
 *
 *   - `runtime.mode` / `runtime.dashboard`
 *   - `agents.<id>.contracts[]` with either a plain-string `E:` field
 *     or a top-level `desc:` / direct-string entry. Structured
 *     `{ pattern, args }` forms that need pack-runtime support
 *     (token_budget, delegation_depth_limit, loop_detection, …)
 *     are *skipped with a warning* — the TS runtime does not ship
 *     pack infrastructure yet.
 *   - `include:` pack references are skipped with a warning for the
 *     same reason.
 *
 * The loader is therefore a **strict superset of inline TS usage**:
 * contracts that work when passed as `contracts: string[]` to the
 * ctor also work when loaded from yaml. More exotic Python-side
 * features (sto contracts, packs, LTL patterns) are logged and
 * ignored rather than crashing, so a user can share a single
 * sponsio.yaml between Python and TS without the TS side refusing
 * to start.
 */

import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import type { DetFormula } from "./patterns.js";
import {
  buildPatternByName,
  isAgPair,
  PatternFactoryError,
} from "./pattern-factory.js";
import { parseRepr, ParseError } from "./parser.js";
import { And, G, Implies, type Formula } from "./formula.js";
import { loadPackContracts } from "./pack-loader.js";
import { dirname, resolve as resolvePath } from "node:path";

// Use createRequire so we can lazily load the optional `yaml` package
// without breaking non-yaml users (ESM dynamic import would force the
// entire constructor path async, which we don't want).
const requireCjs = createRequire(import.meta.url);

export interface LoadedConfig {
  /**
   * Det contracts the TS SDK can consume. NL strings get run through
   * ``parseNl`` in the ctor; pre-built ``DetFormula`` entries (from
   * structured patterns, raw LTL, or A/G composition) are pushed
   * straight onto the contract list — no NL parsing required.
   */
  contracts: (string | DetFormula)[];
  /** Sto contract specs — compiled into evaluators by the ctor. */
  stoSpecs: StoContractSpec[];
  /** `judge:` block — provider / model / api_key / base_url / fallback. */
  judge?: JudgeConfigSpec;
  /** `enforce` | `observe` | undefined (fall through to default). */
  mode?: "enforce" | "observe";
  /** Dashboard URL or boolean from yaml, passed through unchanged. */
  dashboard?: string | boolean;
  /**
   * Contracts / packs we couldn't handle. Surfaced so the Sponsio
   * ctor can warn once without spamming per-contract log lines.
   */
  skipped: SkippedItem[];
}

export interface SkippedItem {
  kind: "pack" | "structured-contract" | "sto-contract" | "unknown-contract";
  detail: string;
}

export type StoAtomName =
  | "tone"
  | "llm_judge"
  | "relevance"
  | "semantic_pii_free"
  | "hallucination_free"
  | "scope_respect"
  | "metric_integrity"
  | "injection_free";

/**
 * Spec for a yaml-declared sto contract. The loader stays stateless:
 * it emits a description of the rule, and the Sponsio ctor wires up
 * an evaluator against the `judge:` client.
 */
export interface StoContractSpec {
  atom: StoAtomName;
  desc: string;
  /** Threshold — score below this counts as a violation. */
  threshold: number;
  /** ``tone``: the target tone. ``scope_respect``: the scope blurb.
   *  Other atoms ignore this. */
  args: string[];
  /** Free-form rubric for ``llm_judge``. */
  promptOverride?: string;
}

export interface JudgeConfigSpec {
  provider?: "openai";
  model?: string;
  apiKey?: string;
  baseUrl?: string;
  fallbackMode?: "allow" | "deny" | "skip";
}

type YamlLib = {
  parse: (src: string) => unknown;
};

function loadYamlLib(): YamlLib {
  try {
    return requireCjs("yaml") as YamlLib;
  } catch {
    throw new Error(
      "[sponsio] config loading requires the `yaml` package. " +
        "Install it with: npm install yaml",
    );
  }
}

/**
 * Load sponsio.yaml and project it onto the TS runtime's contract
 * surface. Reads synchronously so it can be called from a ctor.
 *
 * @param path       Path to sponsio.yaml (absolute or CWD-relative).
 * @param agentId    Which agent block to pull contracts from. Falls
 *                   back to `agents["*"]` (the wildcard block used by
 *                   packs) if `agentId` is missing.
 */
export function loadSponsoConfig(path: string, agentId: string): LoadedConfig {
  const yamlLib = loadYamlLib();

  let raw: string;
  try {
    raw = readFileSync(path, "utf-8");
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    throw new Error(`[sponsio] cannot read config ${path}: ${msg}`);
  }

  let parsed: unknown;
  try {
    parsed = yamlLib.parse(raw);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    throw new Error(`[sponsio] invalid yaml in ${path}: ${msg}`);
  }

  if (!isObject(parsed)) {
    throw new Error(
      `[sponsio] ${path}: expected a YAML mapping at the top level`,
    );
  }

  const skipped: SkippedItem[] = [];
  const contracts: string[] = [];
  const stoSpecs: StoContractSpec[] = [];

  const runtime = getObject(parsed["runtime"]);
  const mode = extractMode(runtime);
  const dashboard = extractDashboard(runtime);
  const judge = extractJudge(parsed["judge"]);

  const agents = getObject(parsed["agents"]);
  const agentBlock = agents
    ? (getObject(agents[agentId]) ?? getObject(agents["*"]) ?? null)
    : null;

  if (agentBlock) {
    extractAgentContracts(agentBlock, contracts, stoSpecs, skipped);
  }

  // `include:` resolution. Both placements are supported (agent-level
  // and top-level) for legacy reasons; agent-level takes precedence
  // since it scopes pack additions to a specific agent. Each pack's
  // contracts are projected through the same pipeline as user-yaml
  // contracts.
  const baseDir = dirname(resolvePath(path));
  const agentIncludes = agentBlock?.["include"];
  expandIncludes(agentIncludes, baseDir, contracts, stoSpecs, skipped);
  expandIncludes(parsed["include"], baseDir, contracts, stoSpecs, skipped);

  return { contracts, stoSpecs, judge, mode, dashboard, skipped };
}

function expandIncludes(
  raw: unknown,
  baseDir: string,
  detOut: (string | DetFormula)[],
  stoOut: StoContractSpec[],
  skipped: SkippedItem[],
): void {
  if (!Array.isArray(raw)) return;
  for (const spec of raw) {
    if (typeof spec !== "string") {
      skipped.push({ kind: "pack", detail: `include entry must be a string, got ${typeof spec}` });
      continue;
    }
    const items = loadPackContracts(spec, baseDir, new Set(), skipped);
    for (const item of items) {
      const projected = projectContract(item.raw);
      if (projected.kind === "nl") detOut.push(projected.value);
      else if (projected.kind === "det") detOut.push(projected.value);
      else if (projected.kind === "sto") stoOut.push(projected.value);
      else skipped.push(projected.reason);
    }
  }
}

function extractJudge(raw: unknown): JudgeConfigSpec | undefined {
  if (!isObject(raw)) return undefined;
  const provider = raw["provider"];
  const model = raw["model"];
  const apiKey = raw["api_key"];
  const baseUrl = raw["base_url"];
  const fallbackMode = raw["fallback_mode"];
  const spec: JudgeConfigSpec = {};
  if (provider === "openai") spec.provider = provider;
  if (typeof model === "string") spec.model = model;
  if (typeof apiKey === "string" && apiKey) spec.apiKey = apiKey;
  if (typeof baseUrl === "string" && baseUrl) spec.baseUrl = baseUrl;
  if (
    fallbackMode === "allow" ||
    fallbackMode === "deny" ||
    fallbackMode === "skip"
  ) {
    spec.fallbackMode = fallbackMode;
  }
  return Object.keys(spec).length > 0 ? spec : undefined;
}

/* -----------------------------------------------------------------
 * helpers
 * -----------------------------------------------------------------*/

function extractMode(runtime: Record<string, unknown> | null):
  | "enforce"
  | "observe"
  | undefined {
  if (!runtime) return undefined;
  const m = runtime["mode"];
  if (m === "enforce" || m === "observe") return m;
  return undefined;
}

function extractDashboard(
  runtime: Record<string, unknown> | null,
): string | boolean | undefined {
  if (!runtime) return undefined;
  const d = runtime["dashboard"];
  if (typeof d === "string" || typeof d === "boolean") return d;
  return undefined;
}

function extractAgentContracts(
  agent: Record<string, unknown>,
  detOut: (string | DetFormula)[],
  stoOut: StoContractSpec[],
  skipped: SkippedItem[],
): void {
  const raw = agent["contracts"];
  if (!Array.isArray(raw)) return;

  for (const entry of raw) {
    const projected = projectContract(entry);
    if (projected.kind === "nl") {
      detOut.push(projected.value);
    } else if (projected.kind === "det") {
      detOut.push(projected.value);
    } else if (projected.kind === "sto") {
      stoOut.push(projected.value);
    } else {
      skipped.push(projected.reason);
    }
  }
}

const STO_ATOMS = new Set<StoAtomName>([
  "tone",
  "llm_judge",
  "relevance",
  "semantic_pii_free",
  "hallucination_free",
  "scope_respect",
  "metric_integrity",
  "injection_free",
]);

type Projection =
  | { kind: "nl"; value: string }
  | { kind: "det"; value: DetFormula }
  | { kind: "sto"; value: StoContractSpec }
  | { kind: "skip"; reason: SkippedItem };

function projectContract(entry: unknown): Projection {
  // Simplest form: a bare string on the contracts list.
  if (typeof entry === "string") {
    return { kind: "nl", value: entry };
  }

  if (!isObject(entry)) {
    return {
      kind: "skip",
      reason: {
        kind: "unknown-contract",
        detail: `unexpected contract entry type: ${typeof entry}`,
      },
    };
  }

  // Canonical short-key ``G`` and long-key ``guarantee`` only — matches
  // Python's loader (no legacy alias support).
  const eField = entry["G"] ?? entry["guarantee"];
  const aField = entry["A"] ?? entry["assumption"];
  const desc = stringOr(entry["desc"], "");

  // Legacy ``sto: true`` flag without a structured pattern — caller
  // declared intent to run a stochastic rule but didn't pick an
  // atom. Skip with reason so the user knows to switch to
  // ``G: { pattern: tone | llm_judge, args, threshold }``.
  if (entry["sto"] === true || entry["type"] === "sto") {
    if (!(isObject(eField) && "pattern" in eField)) {
      return {
        kind: "skip",
        reason: {
          kind: "sto-contract",
          detail:
            desc ||
            "sto flag set without a `pattern:` (use pattern: tone | llm_judge)",
        },
      };
    }
  }

  // Sto takes priority — if ``G.pattern`` matches a known sto atom,
  // route straight to the sto spec builder (no A-side composition
  // is supported for sto today, matching Python).
  if (isObject(eField) && typeof eField["pattern"] === "string") {
    const pattern = String(eField["pattern"]);
    if (STO_ATOMS.has(pattern as StoAtomName)) {
      return projectSto(pattern as StoAtomName, eField, desc);
    }
  }

  // ``desc:`` as sole contract body — matches the old loader's
  // lossy-but-useful fallback when the user wrote only a human
  // description. Runs before ``buildConstraint`` so the "missing G"
  // error doesn't preempt it.
  if (eField === undefined && aField === undefined && desc) {
    return { kind: "nl", value: desc };
  }

  // Build the guarantee formula. Possible shapes:
  //  - string: NL
  //  - { pattern: X, args: [...] }: structured det
  //  - { ltl: "G(…)" }: raw LTL
  //  - list of any of the above: AND-combined
  const eBuilt = buildConstraint(eField, "G", desc);
  if (eBuilt.kind === "skip") return eBuilt;
  if (eBuilt.kind === "nl-string") {
    // No A-side composition for NL strings — the NL parser already
    // handles conditional forms ("if X then Y") via its own rules.
    if (aField !== undefined) {
      // User wrote an A alongside an NL E — we can't cleanly compose
      // without reparsing both sides, so fall through to NL-only with
      // a note in skipped so they know A was dropped.
      return { kind: "nl", value: eBuilt.value };
    }
    return { kind: "nl", value: eBuilt.value };
  }

  // We have a structured E formula. Compose with A if present.
  if (aField !== undefined) {
    const aBuilt = buildConstraint(aField, "A", desc);
    if (aBuilt.kind === "skip") return aBuilt;
    if (aBuilt.kind === "nl-string") {
      // NL on A side composes poorly with a structured E — would
      // need to re-parse, defer to Python. Surface as skipped.
      return {
        kind: "skip",
        reason: {
          kind: "structured-contract",
          detail: `${desc || "contract"}: mixed NL A with structured E not supported in TS`,
        },
      };
    }
    // Both sides structured: compose as ``G(A -> E)``. ``G`` is
    // required because ``evaluate`` runs from ``pos=0``; a bare
    // ``Implies(A, G)`` short-circuits to ``true`` whenever ``A`` is
    // false at step 0, regardless of later events. Lifting the
    // implication under ``G`` makes it fire at every state where the
    // assumption holds.
    const composed: DetFormula = {
      formula: new G(
        new Implies(aBuilt.formula.formula, eBuilt.formula.formula),
      ),
      desc: desc || `${aBuilt.formula.desc} => ${eBuilt.formula.desc}`,
      patternName: `${aBuilt.formula.patternName}__implies__${eBuilt.formula.patternName}`,
      liveness: eBuilt.formula.liveness,
    };
    return { kind: "det", value: composed };
  }

  // Plain structured E, no A.
  const naked: DetFormula = {
    formula: eBuilt.formula.formula,
    desc: desc || eBuilt.formula.desc,
    patternName: eBuilt.formula.patternName,
    liveness: eBuilt.formula.liveness,
  };
  return { kind: "det", value: naked };
}

type ConstraintResult =
  | { kind: "nl-string"; value: string }
  | { kind: "formula"; formula: DetFormula }
  | { kind: "skip"; reason: SkippedItem };

/** Parse one side (A or G) of a yaml contract into a DetFormula or
 *  an NL string. Lists are AND-combined pairwise. */
function buildConstraint(
  raw: unknown,
  side: "A" | "G",
  parentDesc: string,
): ConstraintResult {
  if (raw === undefined || raw === null) {
    if (side === "A") {
      // A is optional — caller falls through to plain-G path.
      return {
        kind: "skip",
        reason: {
          kind: "unknown-contract",
          detail: `${parentDesc || "contract"}: missing ${side}:`,
        },
      };
    }
    return {
      kind: "skip",
      reason: {
        kind: "unknown-contract",
        detail: `${parentDesc || "contract"}: missing G: / guarantee:`,
      },
    };
  }

  if (typeof raw === "string") {
    const trimmed = raw.trim();
    if (!trimmed) {
      return {
        kind: "skip",
        reason: {
          kind: "unknown-contract",
          detail: `${parentDesc || "contract"}: empty ${side}:`,
        },
      };
    }
    return { kind: "nl-string", value: trimmed };
  }

  if (Array.isArray(raw)) {
    // AND-combine every item; every item must itself be structured
    // (list of bare NL strings on the A/G side is unusual and would
    // compose ambiguously through parseNl — defer to Python).
    const parts: DetFormula[] = [];
    for (const item of raw) {
      const r = buildConstraint(item, side, parentDesc);
      if (r.kind !== "formula") {
        return {
          kind: "skip",
          reason: {
            kind: "structured-contract",
            detail: `${parentDesc || "contract"}: list-valued ${side} mixes shapes TS can't merge`,
          },
        };
      }
      parts.push(r.formula);
    }
    if (parts.length === 0) {
      return {
        kind: "skip",
        reason: {
          kind: "unknown-contract",
          detail: `${parentDesc || "contract"}: empty list ${side}:`,
        },
      };
    }
    const combined: Formula = parts
      .map((p) => p.formula)
      .reduce((acc, f) => (acc ? new And(acc, f) : f), undefined as Formula | undefined)!;
    return {
      kind: "formula",
      formula: {
        formula: combined,
        desc: parts.map((p) => p.desc).join(" ∧ "),
        patternName: "and_of_" + parts.map((p) => p.patternName).join("_"),
        liveness: parts.some((p) => p.liveness),
      },
    };
  }

  if (!isObject(raw)) {
    return {
      kind: "skip",
      reason: {
        kind: "unknown-contract",
        detail: `${parentDesc || "contract"}: ${side} must be string / mapping / list`,
      },
    };
  }

  // Per-clause ``desc:`` mirrors the Python ConstraintEntry.desc
  // field — without it the renderer is stuck using ``parentDesc``
  // for both A and G (same string for "assume" and "enforce" lines)
  // or, on the ltl path, the raw formula text. Per-clause desc lets
  // a contract distinguish "user declared a code freeze" (A) from
  // "no destructive SQL while the freeze is in effect" (G).
  const clauseDescRaw = raw["desc"];
  const clauseDesc =
    typeof clauseDescRaw === "string" && clauseDescRaw.trim()
      ? clauseDescRaw.trim()
      : parentDesc;

  // { ltl: "G(!called(foo))" } — raw formula, parsed via parseRepr.
  const ltl = raw["ltl"];
  if (typeof ltl === "string" && ltl.trim()) {
    try {
      const f = parseRepr(ltl);
      return {
        kind: "formula",
        formula: {
          formula: f,
          // Match Python's _compile_ltl: ``entry.desc or entry.ltl``.
          // Raw LTL text is ugly but consistent across language ports;
          // ``ltl(...)`` wrapping diverged silently from Python.
          desc: clauseDesc || ltl,
          patternName: "ltl",
          liveness: false,
        },
      };
    } catch (err) {
      const msg = err instanceof ParseError ? err.message : String(err);
      return {
        kind: "skip",
        reason: {
          kind: "structured-contract",
          detail: `${parentDesc || "contract"}: ${side}.ltl parse error — ${msg}`,
        },
      };
    }
  }

  // { pattern: <name>, args: [...] } — structured det pattern.
  if (typeof raw["pattern"] === "string") {
    const pattern = String(raw["pattern"]);
    const args = Array.isArray(raw["args"]) ? (raw["args"] as unknown[]) : [];
    try {
      const built = buildPatternByName(pattern, args);
      if (built === null) {
        return {
          kind: "skip",
          reason: {
            kind: "structured-contract",
            detail: parentDesc || `pattern=${pattern}`,
          },
        };
      }
      const det = isAgPair(built) ? built.guarantee : built;
      // ``desc:`` from the YAML overrides the pattern factory's
      // default — same precedence as Python's _compile_structured.
      const finalDet =
        typeof clauseDescRaw === "string" && clauseDescRaw.trim()
          ? { ...det, desc: clauseDescRaw.trim() }
          : det;
      return { kind: "formula", formula: finalDet };
    } catch (err) {
      const msg =
        err instanceof PatternFactoryError ? err.message : String(err);
      return {
        kind: "skip",
        reason: {
          kind: "structured-contract",
          detail: `${parentDesc || "contract"}: pattern=${pattern} — ${msg}`,
        },
      };
    }
  }

  // { nl: "..." } — rare, but Python accepts it. Treat as NL string.
  if (typeof raw["nl"] === "string" && raw["nl"].trim()) {
    return { kind: "nl-string", value: raw["nl"].trim() };
  }

  return {
    kind: "skip",
    reason: {
      kind: "unknown-contract",
      detail: `${parentDesc || "contract"}: ${side} has no pattern / ltl / nl`,
    },
  };
}

function projectSto(
  atom: StoAtomName,
  eField: Record<string, unknown>,
  desc: string,
): Projection {
  const rawThreshold = eField["threshold"];
  let threshold = 0.7;
  if (typeof rawThreshold === "number" && Number.isFinite(rawThreshold)) {
    threshold = clamp01(rawThreshold);
  }
  const args: string[] = [];
  const rawArgs = eField["args"];
  if (Array.isArray(rawArgs)) {
    for (const a of rawArgs) {
      if (typeof a === "string" || typeof a === "number") args.push(String(a));
    }
  }
  const promptOverride =
    typeof eField["prompt_override"] === "string"
      ? (eField["prompt_override"] as string)
      : undefined;

  // Per-atom arg requirements — tone/scope_respect need at least
  // one arg (the target tone / scope blurb). Other atoms run
  // unconditionally on the output (+ optional context fields set
  // via guard.setContext).
  if ((atom === "tone" || atom === "scope_respect") && args.length === 0) {
    const need = atom === "tone" ? "[target_tone]" : "[scope_description]";
    return {
      kind: "skip",
      reason: {
        kind: "sto-contract",
        detail: `${desc || atom} (missing args: expected ${need})`,
      },
    };
  }

  const defaultDesc =
    atom === "tone"
      ? `tone(${args[0]})`
      : atom === "scope_respect"
        ? `scope_respect(${args[0]})`
        : atom;

  const spec: StoContractSpec = {
    atom,
    desc: desc || defaultDesc,
    threshold,
    args,
  };
  if (promptOverride) spec.promptOverride = promptOverride;
  return { kind: "sto", value: spec };
}

function clamp01(n: number): number {
  if (n < 0) return 0;
  if (n > 1) return 1;
  return n;
}

function recordIncludes(raw: unknown, skipped: SkippedItem[]): void {
  if (!Array.isArray(raw)) return;
  for (const pack of raw) {
    if (typeof pack === "string") {
      skipped.push({ kind: "pack", detail: pack });
    }
  }
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function getObject(v: unknown): Record<string, unknown> | null {
  return isObject(v) ? v : null;
}

function stringOr(v: unknown, fallback: string): string {
  return typeof v === "string" ? v : fallback;
}
