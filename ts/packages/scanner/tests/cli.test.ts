/**
 * End-to-end CLI tests for ``--config``.
 *
 * We spawn the compiled ``bin/scan.js`` as a subprocess (not
 * programmatically invoke ``main()``) because:
 *   1. The CLI calls ``process.exit`` on parse errors, and we want
 *      to observe that exit code without bringing down the test
 *      runner.
 *   2. Subprocess boundary exercises the JSON I/O path the way a
 *      real user uses it: ``sponsio-scan-ts ... | sponsio scan``.
 *
 * The build step must have run first (``npm run build``) — the test
 * imports from ``dist/``, not ``src/``, so it catches any runtime
 * regression that TS's type-only checks would miss.
 */

import { spawnSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

const CLI = path.resolve(__dirname, "..", "bin", "scan.js");
const FIXTURES = path.resolve(__dirname, "fixtures");

let tmpDir: string;
beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "sponsio-scan-ts-cli-"));
});
afterEach(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

interface RunResult {
  status: number | null;
  stdout: string;
  stderr: string;
  parsed?: any;
}

function run(args: string[], env: Record<string, string> = {}): RunResult {
  const res = spawnSync("node", [CLI, ...args], {
    encoding: "utf8",
    // ``...process.env`` to inherit PATH etc. — tests don't sandbox
    // env; individual test cases add what they need and restore via
    // afterEach's fresh tmpDir.
    env: { ...process.env, ...env },
  });
  const out: RunResult = {
    status: res.status,
    stdout: res.stdout ?? "",
    stderr: res.stderr ?? "",
  };
  try {
    out.parsed = JSON.parse(out.stdout);
  } catch {
    /* stdout isn't JSON (help text, errors) */
  }
  return out;
}

// ---------------------------------------------------------------------------
// Config-file discovery + error handling
// ---------------------------------------------------------------------------

describe("sponsio-scan-ts --config", () => {
  it("fails with exit code 2 and a clear message when --config points nowhere", () => {
    const res = run(["--config", path.join(tmpDir, "missing.yaml")]);
    expect(res.status).toBe(2);
    expect(res.stderr).toMatch(/\[config\].*not found/);
  });

  it("fails cleanly on malformed YAML (not a TypeError)", () => {
    const cfg = path.join(tmpDir, "broken.yaml");
    fs.writeFileSync(cfg, "scan:\n  patterns:\n    - [unclosed\n");
    const res = run(["--config", cfg]);
    expect(res.status).toBe(2);
    expect(res.stderr).toMatch(/\[config\] invalid YAML/);
  });

  it("warns on stderr about unset env vars but still produces output", () => {
    // Use a highly unique name so the test passes regardless of
    // what's in the test-runner env.  Note: we CANNOT "unset" a var
    // by passing it as empty string — ``process.env[name]`` would
    // then return ``""`` and the loader's ``!== undefined`` check
    // would treat it as set-but-empty (see the comment block in
    // ``config.ts`` for why we distinguish).
    const uniqueName = `SPONSIO_NEVER_SET_${Date.now()}_${Math.random()
      .toString(36)
      .slice(2)}`;
    const cfg = path.join(tmpDir, "sponsio.yaml");
    fs.writeFileSync(
      cfg,
      [
        "extractor:",
        "  provider: openai",
        `  api_key: \${${uniqueName}}`,
      ].join("\n")
    );
    const res = run(["--config", cfg, FIXTURES + "/*.ts"]);
    expect(res.status).toBe(0);
    expect(res.stderr).toMatch(new RegExp(`warning.*${uniqueName}`));
    expect(res.parsed).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// YAML-sourced defaults + CLI override precedence
// ---------------------------------------------------------------------------

describe("--config defaults + CLI precedence", () => {
  it("uses scan.patterns from YAML when no CLI patterns are passed", () => {
    const cfg = path.join(tmpDir, "sponsio.yaml");
    fs.writeFileSync(
      cfg,
      [
        "scan:",
        `  patterns: ["${FIXTURES}/vercel.ts"]`,
      ].join("\n")
    );
    const res = run(["--config", cfg]);
    expect(res.status).toBe(0);
    // Only vercel fixture → only those tool names appear.  If the
    // fallback ``src/**`` pattern leaked in, we'd see unrelated
    // tools from this repo's own src dir.
    const names = res.parsed.tools.map((t: any) => t.function.name);
    expect(names).toContain("lookupCustomer");
    expect(names).not.toContain("lookup_order"); // from generic.ts
  });

  it("CLI pattern overrides scan.patterns in YAML", () => {
    const cfg = path.join(tmpDir, "sponsio.yaml");
    fs.writeFileSync(
      cfg,
      [
        "scan:",
        `  patterns: ["${FIXTURES}/vercel.ts"]`,
      ].join("\n")
    );
    const res = run(["--config", cfg, `${FIXTURES}/langchain.ts`]);
    expect(res.status).toBe(0);
    const names = res.parsed.tools.map((t: any) => t.function.name);
    // CLI won → we see langchain fixture's tools, not vercel's.
    expect(names).toContain("issue_refund");
    expect(names).not.toContain("lookupCustomer");
  });

  it("scan.out from YAML directs output to a file", () => {
    const cfg = path.join(tmpDir, "sponsio.yaml");
    const outFile = path.join(tmpDir, "tools.json");
    fs.writeFileSync(
      cfg,
      [
        "scan:",
        `  out: ${outFile}`,
        `  patterns: ["${FIXTURES}/vercel.ts"]`,
      ].join("\n")
    );
    const res = run(["--config", cfg]);
    expect(res.status).toBe(0);
    // stdout should NOT contain JSON (it went to the file)
    expect(res.parsed).toBeUndefined();
    expect(fs.existsSync(outFile)).toBe(true);
    const written = JSON.parse(fs.readFileSync(outFile, "utf8"));
    expect(written.tools.length).toBeGreaterThan(0);
  });

  it("--out on CLI wins over scan.out in YAML", () => {
    const cfg = path.join(tmpDir, "sponsio.yaml");
    const yamlOut = path.join(tmpDir, "yaml.json");
    const cliOut = path.join(tmpDir, "cli.json");
    fs.writeFileSync(
      cfg,
      [
        "scan:",
        `  out: ${yamlOut}`,
        `  patterns: ["${FIXTURES}/vercel.ts"]`,
      ].join("\n")
    );
    const res = run(["--config", cfg, "--out", cliOut]);
    expect(res.status).toBe(0);
    expect(fs.existsSync(cliOut)).toBe(true);
    expect(fs.existsSync(yamlOut)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Extractor passthrough — what lets Python skip a second --config
// ---------------------------------------------------------------------------

describe("extractor passthrough in output JSON", () => {
  it("embeds parsed extractor under _extractor with env vars resolved", () => {
    const cfg = path.join(tmpDir, "sponsio.yaml");
    fs.writeFileSync(
      cfg,
      [
        "extractor:",
        "  provider: openai",
        "  model: gpt-4o",
        "  api_key: ${SPONSIO_PASSTHROUGH_KEY}",
        "scan:",
        `  patterns: ["${FIXTURES}/vercel.ts"]`,
      ].join("\n")
    );
    const res = run(
      ["--config", cfg],
      { SPONSIO_PASSTHROUGH_KEY: "sk-passthrough-xyz" }
    );
    expect(res.status).toBe(0);
    expect(res.parsed._extractor).toEqual({
      provider: "openai",
      model: "gpt-4o",
      api_key: "sk-passthrough-xyz",
    });
  });

  it("omits _extractor when no extractor section exists", () => {
    const cfg = path.join(tmpDir, "sponsio.yaml");
    fs.writeFileSync(
      cfg,
      ["scan:", `  patterns: ["${FIXTURES}/vercel.ts"]`].join("\n")
    );
    const res = run(["--config", cfg]);
    expect(res.status).toBe(0);
    expect(res.parsed._extractor).toBeUndefined();
  });
});
