# sponsio-openclaw — quickstart

The OpenClaw equivalent of [the
sponsio-claude-code plugin](../sponsio-claude-code/QUICKSTART.md).
Same engine, same library files, different runtime hook protocol.

> Verified against the `sponsio plugin guard --stdin` backend
> (10 Node integration tests, including edge cases and subprocess
> crash recovery). Type definitions track the public OpenClaw docs
> (manifest / hooks / sdk-entrypoints) verbatim as of 2026-04-26.

---

## What this gives you

An OpenClaw plugin that intercepts every `before_tool_call` event
and runs it through Sponsio's deterministic contract engine. Tool
calls that match a deny rule are blocked **before they execute**
via OpenClaw's standard `{block: true}` return.

Concretely, after install:

* `Bash: rm -rf /` — blocked.
* `mcp__github__delete_repository` — blocked.
* `mcp__filesystem__write_file({path: "~/.aws/credentials"})` — blocked.
* Anything else — passes through.

The library files are **the same files** the sponsio-claude-code plugin
reads. If you set both up, you author rules once and both runtimes
enforce them.

---

## Install

### 1. Install the Sponsio CLI (Python — does the actual evaluation)

```bash
pip install sponsio              # PyPI
# or pip install -e .            # from a clone

sponsio --version                # smoke-check
```

### 2. Deploy + register in one command

```bash
sponsio host install openclaw
```

This idempotently does three things:

| Step | What | Where |
|---|---|---|
| 1 | Writes the fallback contract library | `~/.sponsio/plugins/_host_openclaw/sponsio.yaml` |
| 2 | Copies the bundled prebuilt plugin (no `npm install` needed) | `~/.openclaw/extensions/sponsio-openclaw/` |
| 3 | Patches `plugins.entries.sponsio-openclaw = { enabled: true }` | `~/.openclaw/openclaw.json` (backup → `openclaw.json.before-sponsio` on first install) |

Re-running is safe — the library + extension folder + JSON patch are all idempotent.

### 3. Restart OpenClaw to pick up the plugin

```bash
# Standard docker layout (what `openclaw onboard` ships):
docker restart openclaw-openclaw-gateway-1

# Bare-host install: re-launch your OpenClaw gateway.
```

Confirm load with `sponsio host status openclaw` — the report shows the `_host_openclaw` library presence, extension folder integrity, and `openclaw.json` registration. Watch live tool calls flowing through Sponsio with `sponsio host trace openclaw --follow`.

### 4. (Optional) Auto-generate per-plugin libraries

The `_host_openclaw` library catches OpenClaw's first-party tools (`exec`, `read`, `write`, …). For per-plugin libraries — one per OpenClaw plugin / MCP server — use:

```bash
sponsio plugin scan --plugin-id <name> --target-host openclaw \
  --introspect "<spawn-command>"
```

`--introspect` spawns the MCP server, runs the JSON-RPC `initialize` + `tools/list` handshake, and emits a starter library plus a tool-inventory JSON the agent can extend semantically. ALWAYS pass `--target-host openclaw` so the generated contracts use OpenClaw's flat tool names, not the `mcp__<server>__` shape Claude Code expects.

---

## Installing into a running Docker container

If OpenClaw runs inside a container that doesn't have the Sponsio Python CLI on PATH, the host-side `sponsio host install openclaw` plants the *files* but the in-container plugin still can't subprocess-spawn `sponsio`. For that case use the dedicated docker installer:

```bash
plugins/sponsio-openclaw/install_into_running_openclaw.sh \
    --container openclaw-openclaw-gateway-1
```

This builds the plugin in an ephemeral container matching the running gateway's node version, copies it into the bind-mounted `~/.openclaw/extensions/sponsio-openclaw/`, `pip install -e`'s the Sponsio repo into the running container at `/opt/sponsio`, and bootstraps `~/.sponsio/plugins/` inside the container so the subprocess transport finds its libraries. See the script header for assumptions and dry-run mode.

---

## Developing the plugin (contributors only)

End users do not need to clone the repo. If you're modifying the plugin source:

```bash
cd plugins/sponsio-openclaw
npm install
npm run build                    # tsc → dist/index.js
```

The `sponsio host install openclaw` flow uses the prebuilt `dist/index.js` shipped with the wheel under `sponsio/plugin/openclaw_artifact/`. After a local rebuild, copy `dist/` over that bundled artifact (or use `install_into_running_openclaw.sh` which builds + copies in one shot for the docker case).

---

## Verify it works

### Without OpenClaw (Node-native test suite)

```bash
cd plugins/sponsio-openclaw
npm test
```

Spawns the real `sponsio plugin guard` binary and exercises five
end-to-end paths (allow / block / multi-plugin routing / no-library
fallback / mocked API surface). Requires Node 22+ for
`--experimental-strip-types`.

### Inside a live OpenClaw session

Pick a tool the agent might invoke, set up a rule that blocks a
specific arg, and watch the agent fail to execute. The deny reason
travels back via `{block: true, reason: "…"}` — your OpenClaw
runtime decides how to surface it to the user (most do log the
reason at the agent's UI level).

---

## Customising rules

The library format is identical to the sponsio-claude-code plugin's. See
[the Claude Code QUICKSTART § Customising
rules](../sponsio-claude-code/QUICKSTART.md#customising-rules) for:

* direct yaml edits
* `overrides:` blocks
* `sponsio plugin scan` for unbundled plugins
* `SPONSIO_GUARD_MODE=observe` for shadow-mode rollout

Any change you make to `~/.sponsio/plugins/<id>/sponsio.yaml`
applies to **both** runtimes simultaneously — the libraries are
runtime-agnostic.

---

## Where everything lives

```
Sponsio/
├── plugins/
│   ├── sponsio-claude-code/                    ← Claude Code transport (stdin hooks)
│   └── sponsio-openclaw/           ← OpenClaw transport (TS register fn)
│       ├── openclaw.plugin.json           — OpenClaw manifest
│       ├── src/index.ts                   — register(api) entry + subprocess transport
│       ├── test/integration.test.ts       — 5 end-to-end tests
│       ├── package.json
│       ├── tsconfig.json
│       ├── README.md                      — internals
│       └── QUICKSTART.md                  ← you are here
│
└── sponsio/                               ← shared backend (Python)
    ├── plugin/
    │   ├── defaults/                      — same per-plugin libraries
    │   └── ...
    ├── guard_stdin.py                     — `sponsio plugin guard --stdin` core
    └── ...
```

The OpenClaw plugin **doesn't ship its own libraries** — it shares
the entire `~/.sponsio/plugins/` tree with the sponsio-claude-code plugin.
A user running both runtimes installs libraries once.

---

## Configuration knobs

| Env var | Purpose |
|---|---|
| `SPONSIO_GUARD_BIN` | Path to the `sponsio` Python binary (default: looked up on `$PATH`). Set this if OpenClaw is launched from an environment where `sponsio` isn't on PATH (e.g. inside a containerized agent runtime). |
| `SPONSIO_PLUGIN_ROOT` | Override the per-plugin library root (default: `~/.sponsio/plugins`). |
| `SPONSIO_GUARD_MODE` | `enforce` (default) or `observe`. |

---

## Known limitations

| Gap | Workaround / status |
|---|---|
| `{block: true, reason: "…"}` — `reason` may be ignored by some OpenClaw versions | The sponsio-claude-code plugin's deny reason makes it back to the model via `is_error` content. OpenClaw's runtime decides whether to do the same. |
| 80ms per-call subprocess startup | Same daemon-mode mitigation as the sponsio-claude-code plugin. |
| Tool name conventions differ from Claude Code (no `mcp__server__tool` standard for native OpenClaw tools) | Per-plugin routing falls back to `_host` for unrecognised names; author libraries explicitly under `~/.sponsio/plugins/<openclaw-tool-prefix>/sponsio.yaml`. |

---

## Troubleshooting

**"`spawn sponsio ENOENT`"**

The Node process can't find the Python `sponsio` CLI. Either add it
to PATH or set `SPONSIO_GUARD_BIN=/full/path/to/sponsio`.

**"Hook fires but my rule doesn't block"**

Same diagnostics as the sponsio-claude-code plugin (see [its
QUICKSTART](../sponsio-claude-code/QUICKSTART.md#troubleshooting)) —
the underlying `sponsio plugin guard --stdin` is identical.

**"Plugin loaded but `before_tool_call` never fires"**

OpenClaw's hook subscription model may require explicit
registration in the plugin manifest, in addition to the runtime
`api.registerHook` call. Check your OpenClaw version's plugin
loading docs.

---

## Next steps

* When OpenClaw lifecycle gets exercised end to end, fold the
  observed event shape back into [`src/index.ts`](src/index.ts).
* Daemon mode (Stage 3) — same plan as the sponsio-claude-code plugin;
  this plugin's transport switches automatically because the
  subprocess call goes to the same `sponsio plugin guard --stdin`
  endpoint.
