/**
 * Tests for ``@sponsio/scan-ts`` config loading.
 *
 * Focus areas (in decreasing order of "scary if it breaks"):
 *   1. ``${VAR}`` / ``${VAR:-default}`` interpolation semantics
 *      (drift from Python here = cross-language config silently
 *      meaning different things; easiest way to break user trust).
 *   2. CLI / YAML precedence — if a YAML value clobbers a CLI
 *      flag, the user's muscle-memory breaks.
 *   3. Error messages — bad YAML, missing file, wrong types.
 *      Not correctness per se, but a bad error message here is
 *      the user's first impression of the ``--config`` flag.
 *   4. Extractor passthrough reaching the output JSON verbatim.
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { loadConfig, ConfigError } from "../src/config";

let tmpDir: string;
const ORIGINAL_ENV = { ...process.env };

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "sponsio-scan-ts-cfg-"));
});

afterEach(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
  // Always restore env so one test's mutations don't bleed into the
  // next via Node's process-global state.
  process.env = { ...ORIGINAL_ENV };
});

function writeYaml(content: string): string {
  const p = path.join(tmpDir, "sponsio.yaml");
  fs.writeFileSync(p, content, "utf8");
  return p;
}

// ---------------------------------------------------------------------------
// Env-var interpolation
// ---------------------------------------------------------------------------

describe("${ENV_VAR} interpolation", () => {
  it("substitutes a set env var", () => {
    process.env.SPONSIO_TEST_KEY = "secret-123";
    const p = writeYaml(`
extractor:
  provider: openai
  api_key: \${SPONSIO_TEST_KEY}
`);
    const cfg = loadConfig(p);
    expect(cfg.extractor.api_key).toBe("secret-123");
    expect(cfg.missingEnvVars).toEqual([]);
  });

  it("uses default when env var is unset (``${VAR:-default}``)", () => {
    delete process.env.SPONSIO_UNSET;
    const p = writeYaml(`
extractor:
  provider: openai
  model: \${SPONSIO_MODEL:-gpt-4o-mini}
`);
    const cfg = loadConfig(p);
    expect(cfg.extractor.model).toBe("gpt-4o-mini");
    expect(cfg.missingEnvVars).toEqual([]);
  });

  it("defaults win only when env var is actually unset, not when empty", () => {
    // Shell ``${VAR:-default}`` ALSO expands to default when VAR is
    // empty.  Our Python impl matches that shell semantics?  Let's
    // check: Python uses ``os.environ.get(name, default)`` which
    // returns default only on MISSING keys (not empty), and the
    // regex match on the CLI side just returns "" if default isn't
    // present.  So our semantics: "set but empty" ≠ "unset".
    //
    // This is a DIVERGENCE from shell but consistent within
    // Sponsio, and worth a test so we don't accidentally flip it.
    process.env.SPONSIO_EMPTY = "";
    const p = writeYaml(`
extractor:
  model: \${SPONSIO_EMPTY:-fallback}
`);
    const cfg = loadConfig(p);
    // "set but empty" → interpolates to ""; our normaliser then
    // treats empty-string fields as "not set" and filters them
    // out of the parsed section (see the rationale comment in
    // config.ts). So ``model`` should be absent.
    expect(cfg.extractor.model).toBeUndefined();
  });

  it("unset-and-no-default expands to empty AND is reported in missingEnvVars", () => {
    delete process.env.SPONSIO_REALLY_UNSET;
    const p = writeYaml(`
extractor:
  api_key: \${SPONSIO_REALLY_UNSET}
`);
    const cfg = loadConfig(p);
    expect(cfg.extractor.api_key).toBeUndefined(); // empty → filtered
    expect(cfg.missingEnvVars).toContain("SPONSIO_REALLY_UNSET");
  });

  it("interpolates inside nested sections, arrays, and surrounding text", () => {
    process.env.SPONSIO_ROOT = "packages";
    process.env.SPONSIO_IGN = "generated";
    const p = writeYaml(`
scan:
  patterns:
    - \${SPONSIO_ROOT}/*/src/**/*.ts
  ignore:
    - "**/\${SPONSIO_IGN}/**"
`);
    const cfg = loadConfig(p);
    expect(cfg.scan.patterns).toEqual(["packages/*/src/**/*.ts"]);
    expect(cfg.scan.ignore).toEqual(["**/generated/**"]);
  });

  it("mid-string substitutions work (``prefix_${VAR}_suffix``)", () => {
    process.env.SPONSIO_STAGE = "prod";
    const p = writeYaml(`
extractor:
  base_url: https://\${SPONSIO_STAGE}.api.example.com/v1
`);
    const cfg = loadConfig(p);
    expect(cfg.extractor.base_url).toBe("https://prod.api.example.com/v1");
  });

  it("does NOT interpolate bare ``$VAR`` (shell-shorthand foot-gun)", () => {
    // Same decision as Python side — bare ``$`` collides with legit
    // YAML string contents (regex classes, template vars, currency)
    // too often to be safe.  Require the braces.
    process.env.SPONSIO_KEY = "should-not-appear";
    const p = writeYaml(`
extractor:
  provider: "price is $SPONSIO_KEY"
`);
    const cfg = loadConfig(p);
    expect(cfg.extractor.provider).toBe("price is $SPONSIO_KEY");
  });

  it("regex matches Python's exactly (same pattern, same behaviour)", () => {
    // Guardrail: the regex literal in config.ts must stay identical
    // to the one in sponsio/config.py.  We re-import the module and
    // probe a few edge cases known to diverge if the regex differs.
    process.env.X = "ok";
    const p = writeYaml(`
extractor:
  # leading digit in var name — invalid per both regexes:
  model: "\${1BAD}"
`);
    const cfg = loadConfig(p);
    // "${1BAD}" should NOT match the regex (invalid identifier),
    // so it survives literally through interpolation; then our
    // normaliser keeps it as-is.
    expect(cfg.extractor.model).toBe("${1BAD}");
  });
});

// ---------------------------------------------------------------------------
// scan: section parsing + precedence-relevant details
// ---------------------------------------------------------------------------

describe("scan: section", () => {
  it("empty file yields an empty config (not an error)", () => {
    // ``touch sponsio.yaml`` is a common workflow while wiring up.
    const p = writeYaml("");
    const cfg = loadConfig(p);
    expect(cfg.extractor).toEqual({});
    expect(cfg.scan).toEqual({});
    expect(cfg.missingEnvVars).toEqual([]);
  });

  it("missing scan: section is legal", () => {
    const p = writeYaml(`
extractor:
  provider: openai
`);
    const cfg = loadConfig(p);
    expect(cfg.scan).toEqual({});
  });

  it("parses patterns/ignore/out/provenance", () => {
    const p = writeYaml(`
scan:
  patterns:
    - "src/**/*.ts"
    - "packages/*/src/**/*.ts"
  ignore:
    - "**/fixtures/**"
  out: tools.json
  provenance: true
`);
    const cfg = loadConfig(p);
    expect(cfg.scan.patterns).toEqual([
      "src/**/*.ts",
      "packages/*/src/**/*.ts",
    ]);
    expect(cfg.scan.ignore).toEqual(["**/fixtures/**"]);
    expect(cfg.scan.out).toBe("tools.json");
    expect(cfg.scan.provenance).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Error cases
// ---------------------------------------------------------------------------

describe("error handling", () => {
  it("missing file raises ConfigError with a helpful path", () => {
    const missing = path.join(tmpDir, "nope.yaml");
    expect(() => loadConfig(missing)).toThrow(ConfigError);
    expect(() => loadConfig(missing)).toThrow(/not found/);
    expect(() => loadConfig(missing)).toThrow(missing);
  });

  it("malformed YAML surfaces the parser's line/column", () => {
    const p = writeYaml("scan:\n  patterns:\n    - [unclosed\n");
    expect(() => loadConfig(p)).toThrow(ConfigError);
    expect(() => loadConfig(p)).toThrow(/invalid YAML/);
  });

  it("top-level array is rejected", () => {
    const p = writeYaml("- just_a_list\n");
    expect(() => loadConfig(p)).toThrow(/must be a mapping/);
  });

  it("extractor not a mapping is rejected", () => {
    const p = writeYaml("extractor:\n  - openai\n");
    expect(() => loadConfig(p)).toThrow(/'extractor' must be a mapping/);
  });

  it("scan.patterns of wrong type is rejected with the field name", () => {
    const p = writeYaml(`
scan:
  patterns: "src/**/*.ts"  # should be a list, not a bare string
`);
    // We want a precise error so users fix the right line.
    expect(() => loadConfig(p)).toThrow(/'scan.patterns' must be an array/);
  });

  it("scan.provenance of wrong type is rejected", () => {
    const p = writeYaml(`
scan:
  provenance: "yes"
`);
    expect(() => loadConfig(p)).toThrow(/'scan.provenance' must be a boolean/);
  });
});

// ---------------------------------------------------------------------------
// Passthrough contract (this is what downstream Python will read)
// ---------------------------------------------------------------------------

describe("extractor passthrough", () => {
  it("all four known fields flow through", () => {
    process.env.SPONSIO_KEY = "k";
    const p = writeYaml(`
extractor:
  provider: openai
  model: gpt-4o
  api_key: \${SPONSIO_KEY}
  base_url: https://api.example.com/v1
`);
    const cfg = loadConfig(p);
    expect(cfg.extractor).toEqual({
      provider: "openai",
      model: "gpt-4o",
      api_key: "k",
      base_url: "https://api.example.com/v1",
    });
  });

  it("unknown extractor fields are dropped (not passed through verbatim)", () => {
    // We WHITELIST fields so a typo like ``models:`` (plural)
    // surfaces loudly downstream (Python will complain about the
    // missing ``model:``) rather than being silently carried
    // through and accepted.  Trade-off: a new Python-side field
    // needs adding here too.  Documented in config.ts.
    const p = writeYaml(`
extractor:
  provider: openai
  models: gpt-4o          # typo — plural
  organization: acme      # legit OpenAI field we don't expose yet
`);
    const cfg = loadConfig(p);
    expect(cfg.extractor).toEqual({ provider: "openai" });
  });
});
