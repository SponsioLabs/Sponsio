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

// Use createRequire so we can lazily load the optional `yaml` package
// without breaking non-yaml users (ESM dynamic import would force the
// entire constructor path async, which we don't want).
const requireCjs = createRequire(import.meta.url);

export interface LoadedConfig {
  /** NL-string contracts the TS SDK can actually parse. */
  contracts: string[];
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

  const runtime = getObject(parsed["runtime"]);
  const mode = extractMode(runtime);
  const dashboard = extractDashboard(runtime);

  const agents = getObject(parsed["agents"]);
  const agentBlock = agents
    ? (getObject(agents[agentId]) ?? getObject(agents["*"]) ?? null)
    : null;

  if (agentBlock) {
    extractAgentContracts(agentBlock, contracts, skipped);
  }

  // `include:` may live at the agent or top level in some packs;
  // either way we don't support it yet, so note it.
  const agentIncludes = agentBlock?.["include"];
  recordIncludes(agentIncludes, skipped);
  recordIncludes(parsed["include"], skipped);

  return { contracts, mode, dashboard, skipped };
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
  out: string[],
  skipped: SkippedItem[],
): void {
  const raw = agent["contracts"];
  if (!Array.isArray(raw)) return;

  for (const entry of raw) {
    const nl = projectContract(entry);
    if (nl.ok) {
      out.push(nl.value);
    } else {
      skipped.push(nl.reason);
    }
  }
}

type Projection =
  | { ok: true; value: string }
  | { ok: false; reason: SkippedItem };

function projectContract(entry: unknown): Projection {
  // Simplest form: a bare string on the contracts list.
  if (typeof entry === "string") {
    return { ok: true, value: entry };
  }

  if (!isObject(entry)) {
    return {
      ok: false,
      reason: {
        kind: "unknown-contract",
        detail: `unexpected contract entry type: ${typeof entry}`,
      },
    };
  }

  // If sto pipeline fields are present, the TS runtime can't evaluate.
  if ("sto" in entry || entry["type"] === "sto") {
    return {
      ok: false,
      reason: {
        kind: "sto-contract",
        detail: stringOr(entry["desc"], "<no desc>"),
      },
    };
  }

  const eField = entry["E"];
  const desc = stringOr(entry["desc"], "");

  // Preferred: `E:` as a plain NL string — exactly what
  // ``sponsio onboard`` writes for user-authored contracts.
  if (typeof eField === "string" && eField.trim()) {
    return { ok: true, value: eField.trim() };
  }

  // Structured `{ pattern, args }` needs per-pattern support
  // (token_budget, delegation_depth_limit, loop_detection, …).
  // Those are pack-runtime features we haven't ported to TS.
  if (isObject(eField) && "pattern" in eField) {
    return {
      ok: false,
      reason: {
        kind: "structured-contract",
        detail: desc || `pattern=${String(eField["pattern"])}`,
      },
    };
  }

  // Fall back to the `desc:` field as an NL hint. This is lossy
  // (desc is for humans, not the parser) but it's what a user who
  // wrote `- desc: "must call check_policy before issue_refund"`
  // would reasonably expect.
  if (desc) {
    return { ok: true, value: desc };
  }

  return {
    ok: false,
    reason: {
      kind: "unknown-contract",
      detail: "contract has neither an NL `E:` nor a `desc:` field",
    },
  };
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
