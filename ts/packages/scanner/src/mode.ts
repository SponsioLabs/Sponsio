/**
 * ``sponsio mode`` — flip a sponsio.yaml between observe and enforce.
 *
 * Mirrors the Python ``sponsio mode`` command: a single in-place edit
 * of the ``mode:`` line, with surrounding comments preserved. Works
 * for both the TS-onboard layout (``runtime: { mode: ... }``) and
 * the Python-onboard layout (``defaults: { mode: ... }``) — the
 * regex matches any ``mode:`` line at any indentation.
 */
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";

const HELP =
  "sponsio mode — flip sponsio.yaml between observe and enforce\n" +
  "\n" +
  "USAGE:\n" +
  "  sponsio mode <observe|enforce> [options]\n" +
  "\n" +
  "OPTIONS:\n" +
  "  -c, --config <file>  Path to sponsio.yaml (default: sponsio.yaml)\n" +
  "  -h, --help           Show this help\n" +
  "\n" +
  "EXAMPLES:\n" +
  "  sponsio mode enforce\n" +
  "  sponsio mode observe -c contracts/sponsio.yaml\n";

export async function runModeCli(argv: string[]): Promise<void> {
  let target: "observe" | "enforce" | null = null;
  let configPath = "sponsio.yaml";

  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "-h" || a === "--help") {
      process.stdout.write(HELP);
      return;
    }
    if (a === "-c" || a === "--config") {
      configPath = argv[++i];
      continue;
    }
    if (a === "observe" || a === "enforce") {
      target = a;
      continue;
    }
    process.stderr.write(`unknown argument: ${a}\n${HELP}`);
    process.exit(2);
  }

  if (!target) {
    process.stderr.write(`missing target mode (observe|enforce)\n\n${HELP}`);
    process.exit(2);
  }

  const path = resolve(configPath);
  if (!existsSync(path)) {
    process.stderr.write(`✗ ${path} not found — run 'sponsio onboard .' first\n`);
    process.exit(1);
  }

  const text = readFileSync(path, "utf8");
  const re = /^(\s*mode:\s*)(observe|enforce)(\s*(?:#.*)?)$/m;
  const match = text.match(re);
  if (!match) {
    process.stderr.write(
      `✗ no \`mode:\` line found in ${path} — edit by hand or re-run \`sponsio onboard --force\`\n`,
    );
    process.exit(1);
  }
  if (match[2] === target) {
    process.stdout.write(`✓ ${path} is already \`mode: ${target}\` (no change)\n`);
    return;
  }
  const newText = text.replace(re, (_full, p1: string, _p2: string, p3: string) => `${p1}${target}${p3}`);
  writeFileSync(path, newText, "utf8");
  process.stdout.write(`✓ ${path} → mode: ${target}\n`);
}
