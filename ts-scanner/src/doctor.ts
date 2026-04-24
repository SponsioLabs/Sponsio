/**
 * ``sponsio-scan-ts doctor`` — pre-flight env + config sanity check.
 *
 * Parity with Python's ``sponsio doctor``, tuned to the TS runtime:
 * verifies node version, the ``yaml`` peer dep, the session log dir,
 * ``SPONSIO_MODE`` value, and (if ``sponsio.yaml`` is present in CWD)
 * that it parses cleanly. Exits non-zero when any check fails so CI
 * can gate on it.
 */

import { existsSync, accessSync, constants, statSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { createRequire } from "node:module";

interface Check {
  label: string;
  status: "ok" | "warn" | "fail";
  detail: string;
}

interface DoctorArgs {
  format: "text" | "json";
  help: boolean;
}

const HELP =
  [
    "sponsio-scan-ts doctor — env + config health check",
    "",
    "USAGE:",
    "  sponsio-scan-ts doctor [options]",
    "",
    "OPTIONS:",
    "      --format <f>  'text' (default) or 'json'",
    "  -h, --help        Show this help",
    "",
    "EXIT CODES:",
    "  0  all checks green",
    "  2  at least one 'fail' — TS runtime won't start cleanly until fixed",
  ].join("\n") + "\n";

function parseArgs(argv: string[]): DoctorArgs {
  const a: DoctorArgs = { format: "text", help: false };
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === "-h" || flag === "--help") a.help = true;
    else if (flag === "--format") {
      const v = argv[++i];
      if (v !== "text" && v !== "json") throw new Error(`--format must be 'text' or 'json'`);
      a.format = v;
    } else {
      throw new Error(`unknown flag: ${flag}`);
    }
  }
  return a;
}

function checkNodeVersion(): Check {
  const v = process.versions.node.split(".").map((n) => parseInt(n, 10));
  if (v[0] >= 18) {
    return {
      label: "node runtime",
      status: "ok",
      detail: `node ${process.versions.node}`,
    };
  }
  return {
    label: "node runtime",
    status: "fail",
    detail: `node ${process.versions.node} < 18 — the TS SDK uses global fetch`,
  };
}

function checkYamlDep(): Check {
  try {
    const require = createRequire(__filename);
    const yaml = require("yaml") as { parse: unknown };
    if (typeof yaml.parse !== "function") {
      return { label: "yaml peer dep", status: "fail", detail: "require('yaml') returned unexpected shape" };
    }
    return { label: "yaml peer dep", status: "ok", detail: "require('yaml') works" };
  } catch (e) {
    return {
      label: "yaml peer dep",
      status: "fail",
      detail: `yaml package missing — install with: npm install yaml (${e instanceof Error ? e.message : e})`,
    };
  }
}

function checkSessionDir(): Check {
  const dir = join(homedir(), ".sponsio", "sessions");
  try {
    mkdirSync(dir, { recursive: true });
    accessSync(dir, constants.W_OK);
    return {
      label: "session log dir",
      status: "ok",
      detail: `${dir} writable`,
    };
  } catch (e) {
    return {
      label: "session log dir",
      status: "warn",
      detail: `${dir} not writable (${e instanceof Error ? e.message : e}) — sessionLog:false when constructing guard`,
    };
  }
}

function checkSponsoMode(): Check {
  const raw = process.env.SPONSIO_MODE;
  if (!raw) {
    return {
      label: "SPONSIO_MODE",
      status: "ok",
      detail: "unset (falls through to ctor / yaml / default 'observe')",
    };
  }
  if (raw === "enforce" || raw === "observe") {
    return { label: "SPONSIO_MODE", status: "ok", detail: raw };
  }
  return {
    label: "SPONSIO_MODE",
    status: "fail",
    detail: `unknown value '${raw}' — expected 'enforce' | 'observe'`,
  };
}

function checkYamlParses(): Check {
  const path = resolve(process.cwd(), "sponsio.yaml");
  if (!existsSync(path)) {
    return {
      label: "sponsio.yaml",
      status: "warn",
      detail: `${path} not found — run 'sponsio-scan-ts onboard .' to create one`,
    };
  }
  try {
    const st = statSync(path);
    if (st.size === 0) {
      return {
        label: "sponsio.yaml",
        status: "warn",
        detail: `${path} is empty`,
      };
    }
    const require = createRequire(__filename);
    const yaml = require("yaml") as { parse: (s: string) => unknown };
    const fs = require("fs") as typeof import("fs");
    const raw = fs.readFileSync(path, "utf-8");
    const parsed = yaml.parse(raw);
    if (typeof parsed !== "object" || parsed === null) {
      return {
        label: "sponsio.yaml",
        status: "fail",
        detail: `${path}: top-level is not a mapping`,
      };
    }
    return {
      label: "sponsio.yaml",
      status: "ok",
      detail: `${path} parses (use 'validate' for det/sto counts)`,
    };
  } catch (e) {
    return {
      label: "sponsio.yaml",
      status: "fail",
      detail: `${path}: ${e instanceof Error ? e.message : e}`,
    };
  }
}

function render(checks: Check[]): string {
  const width = Math.max(...checks.map((c) => c.label.length));
  const out: string[] = [];
  for (const c of checks) {
    const marker = c.status === "ok" ? "✓" : c.status === "warn" ? "⚠" : "✗";
    out.push(`  ${marker} ${c.label.padEnd(width)}  ${c.detail}`);
  }
  return out.join("\n") + "\n";
}

export async function runDoctorCli(argv: string[]): Promise<void> {
  let args: DoctorArgs;
  try {
    args = parseArgs(argv);
  } catch (err) {
    process.stderr.write(`${err instanceof Error ? err.message : String(err)}\n`);
    process.exit(2);
  }
  if (args.help) {
    process.stdout.write(HELP);
    return;
  }

  const checks = [
    checkNodeVersion(),
    checkYamlDep(),
    checkSessionDir(),
    checkSponsoMode(),
    checkYamlParses(),
  ];
  if (args.format === "json") {
    process.stdout.write(JSON.stringify(checks, null, 2) + "\n");
  } else {
    process.stdout.write("Sponsio doctor\n");
    process.stdout.write(render(checks));
  }
  if (checks.some((c) => c.status === "fail")) process.exit(2);
}
