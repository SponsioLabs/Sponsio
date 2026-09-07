/**
 * Hold the TypeScript NL parser to what the Python one does.
 *
 * The corpus is generated from this repo's own rule strings by
 * scripts/gen_nl_corpus.py, so it grows with the tests rather than being
 * a hand-picked list that stays convenient.
 *
 *     node scripts/check_nl_parity.mjs
 *
 * Exits non-zero on the first kind of divergence that matters: a rule
 * one runtime arms and the other drops, a rule the two compile to
 * different patterns, or the same pattern built from different
 * arguments. All three produce two runtimes disagreeing about a book
 * they both claim to enforce.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const { parseNl } = await import(
  join(root, "ts/packages/sdk/dist/core/nl-parser.js")
);

const corpus = JSON.parse(
  readFileSync(join(root, "tests/cross_language/nl_corpus.json"), "utf8"),
);

const dropped = [];
const wrongPattern = [];
const wrongArgs = [];

for (const c of corpus.cases) {
  let got = null;
  try {
    got = parseNl(c.rule);
  } catch {
    got = null;
  }
  if (!got) {
    dropped.push(`${c.pattern}  ${JSON.stringify(c.rule)}`);
    continue;
  }
  if (got.patternName !== c.pattern) {
    wrongPattern.push(
      `${c.pattern} -> ${got.patternName}  ${JSON.stringify(c.rule)}`,
    );
    continue;
  }
  const mine = JSON.stringify(got.args ?? null);
  const theirs = JSON.stringify(c.args);
  if (mine !== theirs) {
    wrongArgs.push(`${theirs} vs ${mine}  ${JSON.stringify(c.rule)}`);
  }
}

const bad = dropped.length + wrongPattern.length + wrongArgs.length;
console.log(`${corpus.cases.length - bad} / ${corpus.cases.length} agree`);

const report = (title, rows) => {
  if (!rows.length) return;
  console.log(`\n${title} (${rows.length})`);
  for (const r of rows) console.log(`  ${r}`);
};
report("dropped by TypeScript, armed by Python", dropped);
report("different pattern", wrongPattern);
report("same pattern, different arguments", wrongArgs);

process.exit(bad ? 1 : 0);
