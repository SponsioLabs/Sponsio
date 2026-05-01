/**
 * Stochastic Guard — Empathetic Customer Replies (TypeScript)
 *
 * Shows the minimal TS sto pipeline (``tone`` atom + ``llm_judge``).
 * Mirrors the Python sto_* examples under ../python/sto_*_guard.py.
 *
 * What you get on top of det contracts:
 *   - ``guardAfter`` now evaluates stochastic rules against the tool
 *     output and returns ``{ stoViolations: [...] }``.
 *   - Violations route to ``blocked`` in enforce mode, or log-only in
 *     observe mode (same precedence rules as det).
 *   - Session JSONL gains ``pipeline: "sto"`` rows with the judge
 *     score, readable by ``sponsio report`` / ``sponsio-scan-ts report``.
 *
 * Usage — live (needs OpenAI credentials):
 *   export OPENAI_API_KEY=...
 *   node ../examples/integrations/typescript/sto_tone_guard.mjs
 *
 * Usage — offline (default, stub judge so CI can run it):
 *   node ../examples/integrations/typescript/sto_tone_guard.mjs
 */

import { resolve, dirname } from "path";
import { fileURLToPath } from "url";
import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const { Sponsio } = await import(
  resolve(__dirname, "..", "..", "..", "ts", "packages", "sdk", "dist", "index.js")
);

const USE_LIVE = !!process.env.OPENAI_API_KEY && process.env.USE_MOCK !== "1";

// Stub judge: deterministic offline scoring so the example always
// runs. Replaced by a real OpenAI call when OPENAI_API_KEY is set.
const stubJudge = {
  async complete(prompt) {
    const p = prompt.toLowerCase();
    // crude heuristic: any of these markers → high empathy score
    const empatheticMarkers = ["sorry", "understand", "thank", "help", "happy"];
    const curtMarkers = ["no.", "denied", "unable"];
    const empatheticHit = empatheticMarkers.some((m) => p.includes(m));
    const curtHit = curtMarkers.some((m) => p.includes(m));
    if (curtHit) return "0.2";
    return empatheticHit ? "0.9" : "0.5";
  },
};

async function main() {
  console.log("=== Sto Guard — Tone (TypeScript) ===");
  console.log(`mode: ${USE_LIVE ? "LIVE (OpenAI)" : "OFFLINE (stub judge)"}\n`);

  // Write a minimal sponsio.yaml so this example doubles as a
  // validate-me-with-the-CLI artefact:
  //   npx sponsio-scan-ts validate <path printed below>
  const tmpDir = mkdtempSync(join(tmpdir(), "sponsio-sto-"));
  const yamlPath = join(tmpDir, "sponsio.yaml");
  writeFileSync(
    yamlPath,
    `version: 1
runtime:
  mode: enforce
judge:
  provider: openai
  model: gpt-4o-mini
  fallback_mode: allow
agents:
  support_bot:
    contracts:
      - desc: "replies must be empathetic"
        E:
          pattern: tone
          args: [empathetic]
          threshold: 0.7
      - desc: "replies must not leak PII"
        E:
          pattern: llm_judge
          prompt_override: |
            Score 0–1: does the response avoid leaking any personally
            identifiable information (emails, phone numbers, SSNs,
            home addresses)? 1.0 = fully clean, 0.0 = clear leak.
          threshold: 0.8
`,
  );
  console.log(`wrote ${yamlPath}`);

  const guard = new Sponsio({
    agentId: "support_bot",
    config: yamlPath,
    sessionLog: false,
    judge: USE_LIVE ? undefined : stubJudge,
  });

  // Candidate tool outputs — in a real agent these come from your
  // ``reply``/``send_message`` tool return value.
  const outputs = [
    { text: "I'm sorry you had trouble. Thank you for your patience — I'll fix this right away.", expected: "pass" },
    { text: "No.", expected: "fail(tone)" },
    { text: "Your refund has been issued to jane.doe@example.com.", expected: "fail(pii)" },
  ];

  for (const { text, expected } of outputs) {
    console.log(`\n  output: ${JSON.stringify(text)}`);
    console.log(`  expected: ${expected}`);
    const check = await guard.guardAfter("reply", text);
    if (check.blocked) {
      console.log(`  [BLOCKED]`);
      for (const v of check.stoViolations) console.log(`    ${v.message}`);
    } else if (check.stoViolations.length > 0) {
      console.log(`  [WOULD-BLOCK] (observe mode would only log)`);
      for (const v of check.stoViolations) console.log(`    ${v.message}`);
    } else {
      console.log(`  [OK]`);
    }
  }

  console.log("");
  guard.printSummary();
}

main().catch(console.error);
