# Sponsio TypeScript packages

Workspace root for Sponsio's TypeScript packages. The two published packages live under `packages/`:

| Package | npm name | Purpose |
|---|---|---|
| [`packages/sdk/`](packages/sdk/) | `@sponsio/sdk` | Runtime contract enforcement (DFA, formula evaluator, framework integrations). Hot path. |
| [`packages/sdk/`](packages/sdk/) | `@sponsio/sdk` | Static AST scanner for TypeScript/JavaScript agent tool definitions. CI / dev tool. |

The packages stay separate on npm because:
- `@sponsio/sdk` ships into production agent code and must stay light (only `yaml` as a dep).
- `@sponsio/sdk` pulls in `ts-morph` (~30 MB) for AST parsing and only runs at dev/CI time.
- They use different module systems (ESM vs CommonJS) for compatibility with their respective ecosystems.

Sharing a workspace gives them shared tooling, hoisted `node_modules`, and a single `npm install` command.

## Quick start

```bash
cd ts/
npm install                     # installs both packages with hoisting
npm run build --workspaces      # builds both packages
npm test --workspaces           # runs all tests
```

To work on a single package:

```bash
cd ts/packages/sdk/
npm test
```

## Repo layout

```
ts/
├── package.json              # workspace root (private, not published)
├── pnpm-workspace.yaml       # not used; npm workspaces is the default
├── tsconfig.base.json        # shared compiler options
├── README.md                 # this file
└── packages/
    ├── sdk/
    │   ├── package.json      # @sponsio/sdk
    │   ├── tsconfig.json     # extends ../../tsconfig.base.json
    │   └── src/
    └── scanner/
        ├── package.json      # @sponsio/sdk
        ├── tsconfig.json     # extends ../../tsconfig.base.json
        └── src/
```

## Releasing

Each package is published independently to npm. From inside the package directory:

```bash
cd packages/sdk/
npm version patch
npm publish --access public
```

The workspace root's `package.json` is `"private": true` and is never published.
