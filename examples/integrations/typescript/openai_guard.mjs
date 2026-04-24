/**
 * OpenAI SDK Guard — Database Admin (TypeScript)
 *
 * Same scenario as the Python version (../python/openai_guard.py).
 * Shows ``patchOpenAI(client, guard)`` — the TS alias for Python's
 * ``patch_openai``. Mutates the client in place; every subsequent
 * ``client.chat.completions.create(...)`` is contract-guarded.
 *
 * Usage:
 *   cd ts-sdk && npm install && npm run build
 *   node ../examples/integrations/typescript/openai_guard.mjs
 */

import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const { Sponsio } = await import(
  resolve(__dirname, "..", "..", "..", "ts-sdk", "dist", "index.js")
);
// patchOpenAI is re-exported alongside wrapOpenAI — both mutate the
// client in place and return it. Pick the name that reads best next
// to the Python snippet you're porting.
const { patchOpenAI } = await import(
  resolve(
    __dirname,
    "..",
    "..",
    "..",
    "ts-sdk",
    "dist",
    "integrations",
    "openai.js",
  )
);

const CONTRACTS = [
  "tool `preview_query` must precede `execute_query`",
  "tool `execute_query` at most 3 times",
];

async function main() {
  console.log("=== OpenAI SDK Guard (TypeScript) ===\n");

  const guard = new Sponsio({
    agentId: "db_admin",
    contracts: CONTRACTS,
    mode: "enforce",
  });

  // In a real agent:
  //   import OpenAI from "openai";
  //   const client = patchOpenAI(new OpenAI(), guard);
  //   await client.chat.completions.create({ ... });  // auto-guarded
  //
  // Here we demonstrate the guard itself — a minimal fake response
  // drives the same code path patchOpenAI installs internally.
  const fakeClient = {
    chat: {
      completions: {
        create: async () => ({
          choices: [
            {
              message: {
                tool_calls: [
                  {
                    function: {
                      name: "execute_query",
                      arguments: JSON.stringify({ sql: "DROP TABLE users" }),
                    },
                  },
                  {
                    function: {
                      name: "preview_query",
                      arguments: JSON.stringify({ sql: "SELECT * FROM users" }),
                    },
                  },
                ],
                content: "",
              },
            },
          ],
        }),
      },
    },
  };

  const guarded = patchOpenAI(fakeClient, guard);
  const response = await guarded.chat.completions.create({
    model: "gpt-4o",
    messages: [],
  });

  const msg = response.choices[0].message;
  const surviving = msg.tool_calls?.map((t) => t.function.name) ?? [];
  console.log(`  tool_calls after guard: ${JSON.stringify(surviving)}`);
  if (msg.content) console.log(`  guard notes: ${msg.content}`);

  console.log("");
  guard.printSummary();
}

main().catch(console.error);
