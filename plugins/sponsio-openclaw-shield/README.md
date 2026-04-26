# sponsio-openclaw-shield (OpenClaw plugin) — prototype

The OpenClaw counterpart to [`plugins/sponsio-shield`](../sponsio-shield/),
which targets Claude Code. Same architecture, different transport.

> **Just want to install + use it?** See [QUICKSTART.md](QUICKSTART.md).
> **Status:** prototype. Type definitions track the public OpenClaw
> docs ([manifest.md](https://docs.openclaw.ai/plugins/manifest.md),
> [hooks.md](https://docs.openclaw.ai/plugins/hooks.md),
> [sdk-entrypoints.md](https://docs.openclaw.ai/plugins/sdk-entrypoints.md))
> verbatim as of 2026-04-26. Verified end-to-end against the same
> `sponsio shield guard --stdin` backend used by the Claude Code
> shield (10 Node integration tests under [`test/`](test/)). **Has
> not** been exercised inside a live OpenClaw runtime yet.

## Architecture

```
agent calls a tool (acme_fetch, mcp__github__delete_repo, …)
  │
  ▼
OpenClaw runtime fires `before_tool_call` hook
  │
  ▼
@sponsio/openclaw-shield (this plugin):
  - read tool name + args from event
  - spawn `sponsio shield guard --stdin`
  - pipe a Claude-Code-style PreToolUse JSON over stdin
  - read deny JSON / silence over stdout
  - return {block: true, reason} or undefined
  │
  ▼
OpenClaw runtime: terminate this tool call (block) or proceed
```

The shield does **not** evaluate contracts in TypeScript — it
delegates to the same Python `sponsio shield guard` CLI that the
Claude Code plugin uses. Both shields read the same per-plugin
library files under `~/.sponsio/plugins/<id>/sponsio.yaml`, so a
library written for one runtime works for the other unchanged.

Why subprocess instead of pure-TS evaluation: the Sponsio config
loader, contract-pack `include:` resolution, `tool_rename:` /
`workspace:` substitution, and `overrides:` merging are all in
Python today. Spawning the existing CLI gives 100% logic reuse at
the cost of ~80ms per tool call. The
[`ts-sdk/`](../../ts-sdk/) has the deterministic engine; a
pure-TS path is feasible later but requires porting the YAML
config loader.

## Install (development)

```bash
# 1. Sponsio CLI on PATH (Python).
pip install -e .                            # from a clone
sponsio --version

# 2. Bootstrap per-plugin libraries (same as Claude Code).
sponsio shield init
sponsio shield install --all                # github / filesystem / playwright

# 3. Build this plugin.
cd plugins/sponsio-openclaw-shield
npm install
npm run build                               # produces dist/index.js

# 4. Tell OpenClaw where to find it.
#    Method depends on your OpenClaw install — typically a
#    `plugins.json` entry pointing at this directory or a published
#    `@sponsio/openclaw-shield` npm package. Refer to OpenClaw's
#    plugin loading docs.
```

## Verify it works without OpenClaw

The plugin's hook is a plain function. Tests under [`test/`](test/)
exercise it end-to-end against the real `sponsio shield guard
--stdin` backend, with a mock OpenClaw API:

```bash
npm test
# ✔ register: hook is installed for before_tool_call
# ✔ before_tool_call returns undefined when no library exists
# ✔ before_tool_call returns {block: true} when shield denies
# ✔ before_tool_call allows benign commands
# ✔ before_tool_call routes mcp__server__tool to the right library
```

These tests skip automatically if `sponsio` isn't on PATH (so they
don't false-positive in TS-only environments). Requires Node 22+
for `--experimental-strip-types`.

## Layout

```
sponsio-openclaw-shield/
├── openclaw.plugin.json      # OpenClaw manifest (minimal — `contracts.tools` is empty
│                             #   because the shield doesn't own tools, it wraps them)
├── package.json              # @sponsio/openclaw-shield npm package
├── tsconfig.json
├── src/
│   └── index.ts              # `register(api)` entry + subprocess transport
├── test/
│   └── integration.test.ts   # Node-native tests against real sponsio shield guard
├── README.md                 # this file
└── QUICKSTART.md             # user-facing install + usage
```

## Configuration knobs

| Env var | Purpose |
|---|---|
| `SPONSIO_GUARD_BIN` | Path to the `sponsio` binary (default: looked up on `$PATH`). Set if your install keeps it in a venv-local location. |
| `SPONSIO_PLUGIN_ROOT` | Override the per-plugin library root (default: `~/.sponsio/plugins`). Same env var the Claude Code shield reads — set once, both shields agree. |
| `SPONSIO_GUARD_MODE` | `enforce` (default) or `observe`. Same dial as the Claude Code shield. |

## Known gaps

| Gap | Status |
|---|---|
| Has not been tested end-to-end inside a real OpenClaw runtime | The protocol layer + library loading + deny JSON translation are validated by the Node test suite, but the manifest field set + plugin lifecycle are inferred from the public docs. |
| No reason text in OpenClaw's `{block: true}` reply | The OpenClaw SDK example shows `{block: true}` only — no documented `reason` field. We include it anyway in case OpenClaw adds support; if the runtime ignores it the user just sees a generic block. |
| 80ms per-call subprocess startup | Same daemon-mode mitigation applies as for the Claude Code shield (Stage 3). |
| `tool_rename:` for OpenClaw-flavoured tool names | OpenClaw tool names appear flat (`firecrawl_search`) rather than `mcp__<server>__<tool>`. Current routing fallback puts them in `_host` — operators can either author per-plugin libraries explicitly or wait for a future runtime-aware routing mode. |
