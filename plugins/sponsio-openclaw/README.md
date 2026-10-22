# sponsio-openclaw (OpenClaw plugin)

The OpenClaw counterpart to [`plugins/sponsio-claude-code`](../sponsio-claude-code/),
which targets Claude Code. Same architecture, different transport.

> **Just want to install + use it?** See [QUICKSTART.md](QUICKSTART.md).
> Type definitions track the public OpenClaw docs
> ([manifest.md](https://docs.openclaw.ai/plugins/manifest.md),
> [hooks.md](https://docs.openclaw.ai/plugins/hooks.md),
> [sdk-entrypoints.md](https://docs.openclaw.ai/plugins/sdk-entrypoints.md))
> verbatim as of 2026-04-26. Verified end-to-end against a real
> Sponsio daemon (Node integration tests under [`test/`](test/)).

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
  - send a Claude-Code-style PreToolUse event to the Sponsio daemon
    (guard.check over its Unix socket)
  - start the daemon if none answers; block if it still cannot be reached
  - return {block: true, blockReason} or undefined
  │
  ▼
OpenClaw runtime: terminate this tool call (block) or proceed
```

The plugin does **not** evaluate contracts in TypeScript. The Sponsio
daemon (Python) decides, the same daemon the Claude Code and Cursor
hooks talk to, so all of them read the same per-plugin library files
under `~/.sponsio/plugins/<id>/sponsio.yaml` and a library written for
one runtime works for the others unchanged. The daemon keeps each
session's history in memory, and a tool call costs one socket round
trip.

`sponsio host install openclaw` writes `sponsio-config.json` next to the
deployed plugin: the daemon socket, the plugin root and state dir a
daemon started by the plugin uses, the command that starts it, and the
mode. A daemon that cannot be reached within the deadline (2 s) is a
block, with a recovery hint in the reason.

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
drive it with a mock OpenClaw API against a real daemon, which the
plugin starts on a socket under a temp dir (HOME is pointed there too,
so the tests never touch your real `~/.sponsio`):

```bash
SPONSIO_TEST_PYTHON=$(which python) npm test
```

The end-to-end cases skip when `$SPONSIO_TEST_PYTHON` (default
`python3`) cannot import sponsio. Requires Node 22.6+ for
`--experimental-strip-types`.

## Layout

```
sponsio-openclaw/
├── openclaw.plugin.json      # OpenClaw manifest (minimal — `contracts.tools` is empty
│                             #   because the plugin doesn't own tools, it wraps them)
├── package.json              # @sponsio/openclaw npm package
├── tsconfig.json
├── src/
│   └── index.ts              # `register(api)` entry + daemon socket client
├── test/
│   └── integration.test.ts   # Node-native tests against a real daemon
├── README.md                 # this file
└── QUICKSTART.md             # user-facing install + usage
```

## Configuration knobs

`sponsio-config.json` beside the deployed plugin, written by
`sponsio host install openclaw`. The plugin reads no environment
variable; edit the file (or reinstall) and restart OpenClaw.

| Key | Purpose |
|---|---|
| `socketPath` | The daemon socket (default `~/.sponsio/sponsio.sock`). |
| `pluginRoot` | Library root a daemon started by the plugin serves. |
| `stateDir` | State dir a daemon started by the plugin uses. |
| `sponsio` | Command that runs the Sponsio CLI, as a list (default `["sponsio"]`). |
| `mode` | `enforce` (default) or `observe`. |
| `deadlineMs` | Budget for one decision (default 2000). |

## Known gaps

| Gap | Status |
|---|---|
| No reason text in OpenClaw's `{block: true}` reply | The OpenClaw SDK example shows `{block: true}` only — no documented `reason` field. We include it anyway in case OpenClaw adds support; if the runtime ignores it the user just sees a generic block. |
| `tool_rename:` for OpenClaw-flavoured tool names | OpenClaw tool names appear flat (`firecrawl_search`) rather than `mcp__<server>__<tool>`. Current routing fallback puts them in `_host` — operators can either author per-plugin libraries explicitly or wait for a future runtime-aware routing mode. |
