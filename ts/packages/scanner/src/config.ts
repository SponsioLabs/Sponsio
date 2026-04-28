/**
 * ``sponsio.yaml`` loader for ``@sponsio/scan-ts``.
 *
 * Parity feature with the Python side's ``sponsio/config.py``.  The
 * goal is *semantic* parity — same YAML file works for both
 * scanners, same env-var interpolation rules, same precedence
 * (CLI flag > YAML > built-in default) — not byte-level parity of
 * the parsed dataclasses (the TS scanner only needs a narrow slice
 * of the schema).
 *
 * The three things we read:
 *
 *   1. ``scan:`` — optional section for TS-scanner-specific
 *      knobs: patterns, ignore list, output path, provenance
 *      inclusion.  Lets a repo put all scanner config in one
 *      file instead of sprinkling CLI flags across workflows.
 *   2. ``extractor:`` — *passthrough* (not interpreted here).
 *      We embed it in the output JSON as ``_extractor`` metadata
 *      so a downstream ``sponsio scan tools.json`` picks up the
 *      same LLM config without needing ``--config`` a second
 *      time.  Mirrors the Python ``ExtractorSection``.
 *   3. ``${ENV_VAR}`` / ``${ENV_VAR:-default}`` — interpolated
 *      on load with EXACTLY the same regex and semantics as
 *      Python (see ``_ENV_VAR_RE`` in ``sponsio/config.py``).
 *
 * Sections we *don't* parse: contracts, agents, judge, defaults.
 * Those are Python-runtime concerns; touching them here would
 * duplicate the Python schema and invite drift.
 */

import * as fs from "fs";
import * as path from "path";
import * as yaml from "js-yaml";

/**
 * Parse-time LLM config — passed through to the output JSON for
 * Python-side consumption.  All fields optional because partial
 * configs are common (e.g. ``provider`` + ``model`` with the
 * api_key coming from an env var later).
 */
export interface ExtractorSection {
  provider?: string;
  model?: string;
  api_key?: string;
  base_url?: string;
}

/**
 * TS-scanner-specific config knobs.  Everything optional; CLI flags
 * still override whatever we find here.
 */
export interface ScanSection {
  patterns?: string[];
  ignore?: string[];
  out?: string;
  provenance?: boolean;
}

export interface SponsioConfig {
  /** Raw extractor section — unvalidated, emitted verbatim so any
   *  future field the Python side adds flows through without a
   *  TS-side schema change. */
  extractor: ExtractorSection;
  scan: ScanSection;
  /** Env-var references that appeared in the file but were unset
   *  AND had no default.  The loader keeps going (shell-like
   *  semantics: the var expands to empty) but surfaces these for
   *  CLI-time warnings — silent empty strings are a classic
   *  "api_key: " source of head-scratching. */
  missingEnvVars: string[];
  /** Absolute path the file was loaded from — used in error
   *  messages and tests. */
  sourcePath: string;
}

export class ConfigError extends Error {
  constructor(message: string, public readonly sourcePath?: string) {
    super(message);
    this.name = "ConfigError";
  }
}

/**
 * Bash-style: ``${VAR}`` or ``${VAR:-default}``.  We deliberately
 * do NOT support the bare ``$VAR`` shorthand because YAML strings
 * routinely contain naked dollar signs (template vars, regex,
 * currency) and we don't want to accidentally munch those.  Same
 * regex as Python's ``_ENV_VAR_RE`` — keep them synchronized.
 */
const ENV_VAR_RE = /\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}/g;

/**
 * Recursively expand ``${VAR}`` / ``${VAR:-default}`` inside the
 * parsed YAML tree.  Operates on a deep-cloned copy (we could
 * mutate, but a clone makes the function safe to call on the raw
 * parse result without surprising the caller if they also hold a
 * reference).
 *
 * @param value Any JSON-serialisable value (string/array/object/primitive).
 * @param missing Out-param: receives the names of unset-no-default env vars.
 *                Shared across recursion so one flat list comes back.
 */
function interpolateEnv(value: unknown, missing: Set<string>): unknown {
  if (typeof value === "string") {
    return value.replace(ENV_VAR_RE, (_match, name: string, def?: string) => {
      const resolved = process.env[name];
      if (resolved !== undefined) return resolved;
      if (def !== undefined) return def;
      missing.add(name);
      return "";
    });
  }
  if (Array.isArray(value)) {
    return value.map((v) => interpolateEnv(v, missing));
  }
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) {
      out[k] = interpolateEnv(v, missing);
    }
    return out;
  }
  return value;
}

/**
 * Load and parse ``sponsio.yaml``.
 *
 * Design notes:
 *   * We *don't* validate the full Python schema — only the two
 *     sections we care about.  Everything else (``contracts``,
 *     ``agents``, ``judge``) sails through untouched so a
 *     single file can be authoritative for both Python and TS.
 *   * Missing file → ``ConfigError`` (not silently-empty config),
 *     because the user explicitly passed ``--config <path>`` and
 *     typos are the #1 failure mode.
 *   * Malformed YAML → ``ConfigError`` with the parser's line/col
 *     where available (js-yaml's default YAMLException already
 *     includes this in its ``reason`` field).
 */
export function loadConfig(configPath: string): SponsioConfig {
  const abs = path.resolve(configPath);
  if (!fs.existsSync(abs)) {
    throw new ConfigError(
      `sponsio.yaml not found: ${abs}\n` +
        `  (pass an absolute or cwd-relative path to --config)`,
      abs
    );
  }

  let raw: string;
  try {
    raw = fs.readFileSync(abs, "utf8");
  } catch (err) {
    throw new ConfigError(
      `cannot read ${abs}: ${(err as Error).message}`,
      abs
    );
  }

  let parsed: unknown;
  try {
    parsed = yaml.load(raw);
  } catch (err) {
    throw new ConfigError(
      `invalid YAML in ${abs}: ${(err as Error).message}`,
      abs
    );
  }

  if (parsed === null || parsed === undefined) {
    // Empty file is legal in YAML — treat as no-config-at-all, not
    // an error.  Users sometimes ``touch sponsio.yaml`` before
    // filling it in.
    return {
      extractor: {},
      scan: {},
      missingEnvVars: [],
      sourcePath: abs,
    };
  }
  if (typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new ConfigError(
      `top level of ${abs} must be a mapping, got ${typeof parsed}`,
      abs
    );
  }

  const missing = new Set<string>();
  const interpolated = interpolateEnv(parsed, missing) as Record<
    string,
    unknown
  >;

  return {
    extractor: normaliseExtractor(interpolated.extractor, abs),
    scan: normaliseScan(interpolated.scan, abs),
    missingEnvVars: [...missing].sort(),
    sourcePath: abs,
  };
}

function normaliseExtractor(raw: unknown, source: string): ExtractorSection {
  if (raw === undefined || raw === null) return {};
  if (typeof raw !== "object" || Array.isArray(raw)) {
    throw new ConfigError(
      `'extractor' must be a mapping in ${source}, got ${
        Array.isArray(raw) ? "array" : typeof raw
      }`,
      source
    );
  }
  const r = raw as Record<string, unknown>;
  const out: ExtractorSection = {};
  for (const key of ["provider", "model", "api_key", "base_url"] as const) {
    const v = r[key];
    if (v === undefined || v === null) continue;
    if (typeof v !== "string") {
      throw new ConfigError(
        `'extractor.${key}' must be a string in ${source}, got ${typeof v}`,
        source
      );
    }
    // Filter empty strings — the interpolator turns unset vars
    // into ``""`` and emitting ``api_key: ""`` in the output
    // downstream would look like an explicit blanking rather than
    // "not set".  Treat empty as absent.
    if (v !== "") out[key] = v;
  }
  return out;
}

function normaliseScan(raw: unknown, source: string): ScanSection {
  if (raw === undefined || raw === null) return {};
  if (typeof raw !== "object" || Array.isArray(raw)) {
    throw new ConfigError(
      `'scan' must be a mapping in ${source}, got ${
        Array.isArray(raw) ? "array" : typeof raw
      }`,
      source
    );
  }
  const r = raw as Record<string, unknown>;
  const out: ScanSection = {};

  const asStringArray = (v: unknown, key: string): string[] => {
    if (!Array.isArray(v)) {
      throw new ConfigError(
        `'scan.${key}' must be an array of strings in ${source}`,
        source
      );
    }
    for (const item of v) {
      if (typeof item !== "string") {
        throw new ConfigError(
          `'scan.${key}' items must be strings in ${source}`,
          source
        );
      }
    }
    return v as string[];
  };

  if (r.patterns !== undefined) out.patterns = asStringArray(r.patterns, "patterns");
  if (r.ignore !== undefined) out.ignore = asStringArray(r.ignore, "ignore");
  if (r.out !== undefined) {
    if (typeof r.out !== "string") {
      throw new ConfigError(`'scan.out' must be a string in ${source}`, source);
    }
    // Empty string (from an unset ``${OUT_FILE}``) is treated as
    // unset — same rationale as extractor fields above.
    if (r.out !== "") out.out = r.out;
  }
  if (r.provenance !== undefined) {
    if (typeof r.provenance !== "boolean") {
      throw new ConfigError(
        `'scan.provenance' must be a boolean in ${source}, got ${typeof r.provenance}`,
        source
      );
    }
    out.provenance = r.provenance;
  }
  return out;
}
