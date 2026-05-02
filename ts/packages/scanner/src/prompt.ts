/**
 * ``sponsio prompt`` — print the agent-facing prompt template for a
 * sponsio workflow (onboard / scan / refresh).
 *
 * Mirrors the Python ``sponsio prompt`` command. The same three .md
 * files live in ``ts/packages/scanner/prompts/`` (mirrored from
 * ``sponsio/prompts/``); this command just reads and prints the right
 * one. Used by the Sponsio skill to drive contract authoring without
 * a separate LLM API call.
 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

const FLOWS = new Set(["onboard", "scan", "refresh"]);

const HELP =
  "sponsio prompt — print the contract-authoring prompt for a workflow\n" +
  "\n" +
  "USAGE:\n" +
  "  sponsio prompt <flow>\n" +
  "\n" +
  "ARGUMENTS:\n" +
  "  <flow>      onboard | scan | refresh\n" +
  "\n" +
  "EXAMPLES:\n" +
  "  sponsio prompt onboard\n" +
  "  sponsio prompt scan | pbcopy\n";

function locatePromptsDir(): string {
  // CommonJS build (dist/) walks back to the package root: dist/ → ../prompts.
  // From src/ during dev: src/ → ../prompts.
  const candidates = [join(__dirname, "..", "prompts"), join(__dirname, "..", "..", "prompts")];
  for (const c of candidates) {
    if (existsSync(join(c, "onboard.md"))) return c;
  }
  throw new Error(`[prompt] cannot locate prompts/ directory (looked in: ${candidates.join(", ")})`);
}

export async function runPromptCli(argv: string[]): Promise<void> {
  if (argv.length === 0 || argv[0] === "-h" || argv[0] === "--help") {
    process.stdout.write(HELP);
    if (argv.length === 0) process.exit(2);
    return;
  }
  const flow = argv[0];
  if (!FLOWS.has(flow)) {
    process.stderr.write(`unknown flow: ${flow}\n${HELP}`);
    process.exit(2);
  }
  const promptsDir = locatePromptsDir();
  const text = readFileSync(join(promptsDir, `${flow}.md`), "utf-8");
  process.stdout.write(text);
}
