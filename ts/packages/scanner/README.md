# @sponsio/scan-ts

Static scanner for TypeScript/JavaScript agent tool definitions.
Produces an OpenAI function-calling JSON inventory that
[`sponsio scan`](https://github.com/sponsio-labs/sponsio) consumes to
propose runtime contracts (`must_precede`, `idempotent`, `no_data_leak`,
`arg_blacklist`, …).

> Companion to the Python-side AST scanner. Use this one when your
> agent lives in a Node.js codebase — LangChain.js, Vercel AI SDK,
> LangGraph.js, or anything using JSON-Schema-shaped tools.

## Install

```bash
npm i -D @sponsio/scan-ts
# or run without installing:
npx @sponsio/scan-ts ./src
```

## CLI

```bash
# Scan a directory — writes JSON to stdout
npx @sponsio/scan-ts ./src > tools.json

# …or to a file
npx @sponsio/scan-ts ./src --out tools.json --pretty

# Pipe into `sponsio scan` (writes a temp file — `sponsio` has no --stdin)
npx @sponsio/scan-ts ./src --out /tmp/sponsio-tools.json
sponsio scan /tmp/sponsio-tools.json -o sponsio.yaml
#
# Or use the bundled one-shot (same idea + optional `sponsio` on PATH)
npx sponsio-scan-ts onboard .
```

`onboard` is implemented in [`src/onboard.ts`](src/onboard.ts): run the static scan, then `sponsio scan` on a temp tool-inventory file when the Python CLI is on `PATH` (or emit a det-only `sponsio.yaml` as a fallback). The published npm binary is `sponsio-scan-ts` (package `@sponsio/scan-ts`).

Flags:

| Flag                  | Meaning                                                             |
| --------------------- | ------------------------------------------------------------------- |
| `-o, --out <f>`       | Write to `<f>` instead of stdout                                    |
| `-c, --config <yaml>` | Read defaults from `sponsio.yaml` (see [Config file](#config-file)) |
| `--pretty`            | Pretty-print emitted JSON                                           |
| `--provenance`        | Include per-tool file/line provenance in output                     |

## Config file

Pass `--config sponsio.yaml` to share config with the Python side and
stop repeating glob patterns and LLM credentials across two tools.

```yaml
# sponsio.yaml
scan:
  patterns:   ["src/**/*.ts", "packages/*/src/**/*.ts"]
  ignore:     ["**/generated/**"]
  out:        "tools.json"
  provenance: true

extractor:
  provider: openai
  model:    gpt-4o
  api_key:  ${OPENAI_API_KEY}       # ${VAR} / ${VAR:-default} supported
  base_url: https://api.example.com/v1
```

Precedence is **CLI flag > YAML > built-in default** — passing
`--out foo.json` always wins over a `scan.out:` in the file, so you
can keep the YAML as the declarative default and override per-run.

Env interpolation rules (identical to the Python-side `sponsio.yaml`
loader so one file means the same thing in both languages):

* `${VAR}` — unset and no default → empty string, with a warning on
  stderr naming every unresolved variable (fails loud, doesn't abort).
* `${VAR:-default}` — unset → `default`.
* `$VAR` (no braces) — **not** interpolated, treated as a literal.
  Deliberate: too many YAML strings legitimately contain bare
  dollar signs.

The `extractor:` block is passed through verbatim to the output
JSON under a top-level `_extractor` key. A downstream
`sponsio scan tools.json` picks that up and skips re-reading the
YAML — useful when the scan step runs in CI and the LLM step runs
elsewhere with different file-system access.

## Supported shapes

| Framework                | Pattern                                          |
| ------------------------ | ------------------------------------------------ |
| Vercel AI SDK            | `tool({ description, parameters, execute })`     |
| LangChain.js / LangGraph | `new DynamicStructuredTool({ name, schema, … })` |
| LangChain.js             | `new DynamicTool({ name, func })`                |
| LangGraph.js ≥ 0.2       | `tool(fn, { name, schema })`                     |

The scanner understands common Zod patterns statically — `z.string()`,
`z.number().int()`, `z.boolean()`, `z.enum([...])`, `z.literal(x)`,
`z.array(inner)`, `z.object({ ... })`, plus `.optional()` and
`.nullish()` refiners. Unknown Zod calls degrade gracefully to
`{ type: "string" }` — the parameter **name** is what most Sponsio
heuristics key on.

## Output format

Emits OpenAI function-calling JSON — the same shape your agent already
speaks:

```json
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "lookup_customer",
        "description": "Fetch a customer record by their ID",
        "parameters": {
          "type": "object",
          "properties": {
            "user_id": { "type": "string" }
          },
          "required": ["user_id"]
        }
      }
    }
  ]
}
```

This is consumed transparently by
`sponsio/discovery/extractors/tool_inventory.py`, so you don't need
any adapter code — just point `sponsio scan` at the JSON file.

## Supported toolkits

Out of the box the scanner recognises:

| Pattern                                          | Source                |
| ------------------------------------------------ | --------------------- |
| `tool({ description, parameters, execute })`     | Vercel AI SDK         |
| `new DynamicStructuredTool({...})`               | LangChain.js          |
| `new DynamicTool({...})`                         | LangChain.js          |
| `tool(fn, { name, description, schema })`        | LangGraph.js v0.2+    |
| `createTool({...})` / `defineTool({...})` / etc. | Generic factory       |
| `Sponsio.tool({...})` (member access)            | Generic factory       |
| `@tool` / `@createTool` decorators on methods    | Generic decorator     |

The generic fallback is conservative — an object literal must carry at
least two of `{name, description, parameters/schema/inputSchema}`
before it's treated as a tool, so unrelated `tool({...})` calls in
user code don't pollute the inventory.

## Limitations (by design)

- **Static only.** Tools whose `name` / `schema` are computed at
  runtime won't appear. For dynamic tools, serialize your tools array
  to JSON from within your app and feed that to `sponsio scan`
  directly.
- **No type-checking.** We parse the source but don't resolve types,
  so Zod schemas referenced by identifier from another file still
  yield an empty parameter list. Inline the schema, or dump it at
  runtime.

## Releasing

Releases are automated via the `publish-scan-ts` GitHub Action. To cut
a new version:

```bash
# 1. Bump the version
cd ts-scanner
npm version patch    # or: minor / major / 0.2.0-beta.0

# 2. Add an entry to CHANGELOG.md under the new version

# 3. Push the version bump and the matching tag
git push origin main
git push origin scan-ts-v$(node -p "require('./package.json').version")
```

The workflow runs the test suite, verifies the tag matches
`package.json`, packs with `--dry-run` for visibility, then publishes
with `--provenance` so consumers can verify the tarball came from
this repo.

## License

Apache-2.0
