/**
 * ``sponsio skill install`` — drop the universal SKILL.md
 * into Cursor / Claude Code / Codex skill directories so your
 * coding agent knows how to ``onboard`` / ``scan`` / ``refresh`` /
 * flip-to-enforce on every future project without re-pasting the
 * one-prompt setup.
 *
 * Parity with Python's ``sponsio skill install``. The shipped
 * SKILL.md is universal (covers both Python and TypeScript
 * workflows) and lives at ``<pkg>/skills/SKILL.md`` inside the
 * published ``@sponsio/scan-ts`` tarball. Available as a
 * cross-language path since either CLI can install it.
 */

import { promises as fs, existsSync } from "node:fs";
import { homedir } from "node:os";
import { join, dirname, resolve } from "node:path";

type Tool = "cursor" | "claude" | "codex" | "all";

interface SkillArgs {
  action: "install";
  tool: Tool;
  dest?: string;
  force: boolean;
  help: boolean;
}

const HELP =
  [
    "sponsio skill install — install the Sponsio Agent Skill",
    "",
    "USAGE:",
    "  sponsio skill install [options]",
    "",
    "OPTIONS:",
    "      --tool <name>  cursor | claude | codex | all  (default: all)",
    "      --dest <path>  Override install location (skips auto-detect)",
    "      --force        Overwrite any existing SKILL.md at the target",
    "  -h, --help         Show this help",
    "",
    "DESTINATIONS:",
    "  ~/.cursor/skills/sponsio/   — Cursor",
    "  ~/.claude/skills/sponsio/   — Claude Code",
    "  ~/.codex/skills/sponsio/    — Codex CLI",
  ].join("\n") + "\n";

function parseArgs(argv: string[]): SkillArgs {
  const action = argv[0];
  if (action !== "install") {
    throw new Error(`sponsio skill: unknown action '${action}' (expected 'install')`);
  }
  const a: SkillArgs = {
    action: "install",
    tool: "all",
    force: false,
    help: false,
  };
  for (let i = 1; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === "-h" || flag === "--help") a.help = true;
    else if (flag === "--tool") {
      const v = argv[++i];
      if (v !== "cursor" && v !== "claude" && v !== "codex" && v !== "all") {
        throw new Error(`--tool must be cursor|claude|codex|all, got ${v}`);
      }
      a.tool = v;
    } else if (flag === "--dest") a.dest = argv[++i];
    else if (flag === "--force") a.force = true;
    else throw new Error(`unknown flag: ${flag}`);
  }
  return a;
}

function destinationsForTool(tool: Tool): string[] {
  const home = homedir();
  const all = {
    cursor: join(home, ".cursor", "skills", "sponsio"),
    claude: join(home, ".claude", "skills", "sponsio"),
    codex: join(home, ".codex", "skills", "sponsio"),
  };
  return tool === "all"
    ? [all.cursor, all.claude, all.codex]
    : [all[tool]];
}

function resolveSkillSource(): string {
  // Published layout: ``<pkg>/skills/SKILL.md``. When running from
  // source (monorepo dev), ``__dirname`` is ``ts-scanner/dist`` so
  // ``../skills/SKILL.md`` lands correctly.
  return resolve(__dirname, "..", "skills", "SKILL.md");
}

export async function runSkillCli(argv: string[]): Promise<void> {
  let args: SkillArgs;
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

  const src = resolveSkillSource();
  if (!existsSync(src)) {
    process.stderr.write(
      `[sponsio] cannot locate SKILL.md at ${src} — did you build @sponsio/scan-ts?\n`,
    );
    process.exit(1);
  }

  const dests = args.dest ? [args.dest] : destinationsForTool(args.tool);
  let installed = 0;
  let skipped = 0;
  for (const dest of dests) {
    // ``--dest`` may point at either a directory or an explicit file
    // path; treat a trailing segment of ``SKILL.md`` as the file form.
    const targetIsFile =
      !!args.dest && dest.toLowerCase().endsWith("skill.md");
    const target = targetIsFile ? dest : join(dest, "SKILL.md");
    const targetDir = dirname(target);
    if (existsSync(target) && !args.force) {
      process.stdout.write(
        `  skip  ${target} (already exists — pass --force to overwrite)\n`,
      );
      skipped++;
      continue;
    }
    try {
      await fs.mkdir(targetDir, { recursive: true });
      await fs.copyFile(src, target);
      process.stdout.write(`  ok    ${target}\n`);
      installed++;
    } catch (err) {
      process.stderr.write(
        `  fail  ${target}: ${err instanceof Error ? err.message : err}\n`,
      );
    }
  }
  process.stdout.write(
    `\nInstalled ${installed}, skipped ${skipped}, out of ${dests.length} target(s).\n`,
  );
  if (installed === 0 && skipped === 0) process.exit(1);
}
