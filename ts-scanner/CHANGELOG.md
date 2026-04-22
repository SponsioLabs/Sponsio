# @sponsio/scan-ts changelog

All notable changes to the TypeScript scanner will be documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this package adheres to [SemVer](https://semver.org/spec/v2.0.0.html).

The release process: bump the version in `package.json`, add an entry
below, push a tag of the form `scan-ts-v<version>`. The
`.github/workflows/publish-scan-ts.yml` workflow handles build, test,
provenance, and publish.

## [Unreleased]

### Added

- `--config sponsio.yaml` — reads `scan:` defaults (patterns, ignore,
  out, provenance) and passes the `extractor:` section through to the
  output JSON as `_extractor` so downstream `sponsio scan` picks it up
  without a second `--config`. `${VAR}` / `${VAR:-default}` env-var
  interpolation with the same semantics as the Python loader; unset
  variables surface as a stderr warning instead of silently expanding
  to empty. CLI flags still win over any YAML value.

- Generic / framework-agnostic extractor that catches the long-tail
  of homegrown agent toolkits:
  - Factory calls: `createTool({...})`, `defineTool({...})`,
    `makeTool({...})`, `registerTool({...})`, `buildTool({...})`,
    `Tool({...})`.
  - Dotted member access: `Sponsio.tool({...})`,
    `MyKit.createTool({...})`.
  - Decorator on methods/functions: `@tool` and `@tool({...})`,
    `@createTool`, `@defineTool`, `@registerTool`.
  - Conservative shape check (object literal must carry at least two
    of `{name, description, parameters/schema/inputSchema}`) so
    unrelated `tool({...})` calls in user code don't slip into the
    inventory.
  - Framework-specific extractors still win over the fallback for
    the same call site (provenance keeps the more useful
    `vercel_ai` / `langchain_js` label).

## [0.1.0] - 2026-04-21

Initial release.

### Added

- Static AST scanner for TypeScript/JavaScript agent codebases that
  emits OpenAI function-calling JSON, directly consumable by
  `sponsio scan` on the Python side.
- Extractors for the three most-common Node.js agent toolkits:
  - **Vercel AI SDK** — `tool({ description, parameters, execute })`
  - **LangChain.js** — `new DynamicStructuredTool({...})` and
    `new DynamicTool({...})`
  - **LangGraph.js v0.2+** — `tool(fn, { name, description, schema })`
- Custom Zod-expression to JSON Schema converter handling
  `z.object`, `z.string`, `z.number`, `z.boolean`, `z.enum`,
  `z.literal`, `z.array`, plus modifiers `.int()`, `.optional()`,
  `.describe()`.
- CLI binary `sponsio-scan-ts` with `--out`, `--pretty`, and
  `--provenance` flags. Defaults to stdout so it pipes cleanly into
  the Python CLI: `npx @sponsio/scan-ts ./src | sponsio scan -`.
- Vitest test suite covering the Vercel AI SDK and LangChain.js
  extractors, Zod conversion edge cases, and end-to-end scan output
  shape.
