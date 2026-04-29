# sponsio-openclaw (OpenClaw plugin) — prototype

The OpenClaw counterpart to [`plugins/sponsio-claude-code`](../sponsio-claude-code/),
which targets Claude Code. Same architecture, different transport.

> **Just want to install + use it?** See [QUICKSTART.md](QUICKSTART.md).
> **Status:** prototype. Type definitions track the public OpenClaw
> docs ([manifest.md](https://docs.openclaw.ai/plugins/manifest.md),
> [hooks.md](https://docs.openclaw.ai/plugins/hooks.md),
> [sdk-entrypoints.md](https://docs.openclaw.ai/plugins/sdk-entrypoints.md))
> verbatim as of 2026-04-26. Verified end-to-end against the same
> `sponsio plugin guard --stdin` backend used by the
> sponsio-claude-code plugin (10 Node integration tests under
> [`test/`](test/)). **Has
> not** been exercised inside a live OpenClaw runtime yet.

## Architecture

```
agent calls a tool (acme_fetch, mcp__github__delete_repo, …)
  │
  ▼
OpenClaw runtime fires `before_tool_call` hook
  │
  ▼
@sponsio/openclaw (this plugin):
  - read tool name + args from event
  - spawn `sponsio plugin guard --stdin`
  - pipe a Claude-Code-style PreToolUse JSON over stdin
  - read deny JSON / silence over stdout
  - return {block: true, reason} or undefined
  │
  ▼
OpenClaw runtime: terminate this tool call (block) or proceed
```

The plugin does **not** evaluate contracts in TypeScript — it
delegates to the same Python `sponsio plugin guard` CLI that the
Claude Code plugin uses. Both plugins read the same per-plugin
library files under `~/.sponsio/plugins/<id>/sponsio.yaml`, so a
library written for one runtime works for the other unchanged.

Why subprocess instead of pure-TS evaluation: the Sponsio config
loader, contract-pack `include:` resolution, `tool_rename:` /
`workspace:` substitution, and `overrides:` merging are all in
Python today. Spawning the existing CLI gives 100% logic reuse at
the cost of ~80ms per tool call. The
[`ts/packages/sdk/`](../../ts/packages/sdk/) has the deterministic engine; a
pure-TS path is feasible later but requires porting the YAML
config loader.

## Install — end users

```bash
pip install sponsio
sponsio host install openclaw     # deploys prebuilt extension + library + json patch
# restart your OpenClaw gateway (e.g. `docker restart openclaw-openclaw-gateway-1`)
```

`sponsio host install openclaw` performs three idempotent writes:

1. `~/.sponsio/plugins/_host_openclaw/sponsio.yaml` — fallback contract library (OpenClaw-shape: `exec`, `read`, `write`, `apply_patch`, …).
2. `~/.openclaw/extensions/sponsio-openclaw/` — prebuilt plugin folder copied from the wheel's bundled `sponsio/plugin/openclaw_artifact/`. **No `npm install` needed** for end users.
3. `~/.openclaw/openclaw.json` — patches `plugins.entries.sponsio-openclaw = { enabled: true }` (with backup at `openclaw.json.before-sponsio` on first install).

Verify with `sponsio host status openclaw`. See [`QUICKSTART.md`](QUICKSTART.md) for tuning, per-plugin scan, and the docker-in-container path.

## Install — plugin developers

Only required if you're modifying the plugin source itself (end users use the bundled artifact above):

```bash
# 1. Sponsio CLI from the clone.
pip install -e .
sponsio --version

# 2. Bootstrap libraries.
sponsio plugin init                         # writes _host / _host_subagent / _host_openclaw

# 3. Build the plugin.
cd plugins/sponsio-openclaw
npm install
npm run build                               # produces dist/index.js
```

After a local rebuild, copy `dist/` over the bundled artifact at `sponsio/plugin/openclaw_artifact/dist/` (or use [`install_into_running_openclaw.sh`](install_into_running_openclaw.sh) which builds + syncs into a running container in one shot).

## Verify it works without OpenClaw

The plugin's hook is a plain function. Tests under [`test/`](test/)
exercise it end-to-end against the real `sponsio plugin guard
--stdin` backend, with a mock OpenClaw API:

```bash
npm test
# ✔ register: hook is installed for before_tool_call
# ✔ before_tool_call returns undefined when no library exists
# ✔ before_tool_call returns {block: true} when guard denies
# ✔ before_tool_call allows benign commands
# ✔ before_tool_call routes mcp__server__tool to the right library
```

These tests skip automatically if `sponsio` isn't on PATH (so they
don't false-positive in TS-only environments). Requires Node 22+
for `--experimental-strip-types`.

## Layout

```
sponsio-openclaw/
├── openclaw.plugin.json      # OpenClaw manifest (minimal — `contracts.tools` is empty
│                             #   because the plugin doesn't own tools, it wraps them)
├── package.json              # @sponsio/openclaw npm package
├── tsconfig.json
├── src/
│   └── index.ts              # `register(api)` entry + subprocess transport
├── test/
│   └── integration.test.ts   # Node-native tests against real sponsio plugin guard
├── README.md                 # this file
└── QUICKSTART.md             # user-facing install + usage
```

## Configuration knobs

| Env var | Purpose |
|---|---|
| `SPONSIO_GUARD_BIN` | Path to the `sponsio` binary (default: looked up on `$PATH`). Set if your install keeps it in a venv-local location. |
| `SPONSIO_PLUGIN_ROOT` | Override the per-plugin library root (default: `~/.sponsio/plugins`). Same env var the sponsio-claude-code plugin reads — set once, both plugins agree. |
| `SPONSIO_GUARD_MODE` | `enforce` (default) or `observe`. Same dial as the sponsio-claude-code plugin. |

## Known gaps

| Gap | Status |
|---|---|
| Has not been tested end-to-end inside a real OpenClaw runtime | The protocol layer + library loading + deny JSON translation are validated by the Node test suite, but the manifest field set + plugin lifecycle are inferred from the public docs. |
| No reason text in OpenClaw's `{block: true}` reply | The OpenClaw SDK example shows `{block: true}` only — no documented `reason` field. We include it anyway in case OpenClaw adds support; if the runtime ignores it the user just sees a generic block. |
| 80ms per-call subprocess startup | Same daemon-mode mitigation applies as for the sponsio-claude-code plugin (Stage 3). |
| `tool_rename:` for OpenClaw-flavoured tool names | OpenClaw tool names appear flat (`firecrawl_search`) rather than `mcp__<server>__<tool>`. Current routing fallback puts them in `_host` — operators can either author per-plugin libraries explicitly or wait for a future runtime-aware routing mode. |
