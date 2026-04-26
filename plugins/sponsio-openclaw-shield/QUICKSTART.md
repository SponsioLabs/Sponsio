# sponsio-openclaw-shield — quickstart

The OpenClaw equivalent of [the Claude Code
shield](../sponsio-shield/QUICKSTART.md). Same engine, same
library files, different runtime hook protocol.

> **Status**: working prototype. Verified against the
> `sponsio shield guard --stdin` backend (5 Node integration
> tests). Has **not** been exercised inside a live OpenClaw
> session yet — the manifest field set and plugin lifecycle
> match the public OpenClaw docs but haven't been confirmed end
> to end.

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

The library files are **the same files** the Claude Code shield
reads. If you set both up, you author rules once and both runtimes
enforce them.

---

## Install

### 1. Install the Sponsio CLI (Python — does the actual evaluation)

```bash
pip install -e .                # from a clone
# or pip install sponsio        # when published

sponsio --version               # smoke-check
```

### 2. Bootstrap the per-plugin contract libraries

```bash
sponsio shield init              # writes ~/.sponsio/plugins/_host/sponsio.yaml
sponsio shield install --all     # adds github / filesystem / playwright starters
```

### 3. Build the OpenClaw plugin

```bash
cd plugins/sponsio-openclaw-shield
npm install
npm run build                    # tsc → dist/index.js
```

### 4. Register the plugin with OpenClaw

OpenClaw's plugin loading mechanism varies by version. The two
common forms:

**A. Local plugin directory** (development):

```bash
# Pseudo — replace with your OpenClaw runtime's actual flag.
openclaw --plugin-dir /path/to/Sponsio/plugins/sponsio-openclaw-shield
```

**B. Installed npm package** (once published):

```bash
npm install -g @sponsio/openclaw-shield
# Then add to your OpenClaw plugin config (~/.openclaw/config.json
# or equivalent — exact path depends on the OpenClaw release).
```

Refer to your OpenClaw version's plugin docs for the precise
registration command.

---

## Verify it works

### Without OpenClaw (Node-native test suite)

```bash
cd plugins/sponsio-openclaw-shield
npm test
```

Spawns the real `sponsio shield guard` binary and exercises five
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

The library format is identical to the Claude Code shield's. See
[the Claude Code QUICKSTART § Customising
rules](../sponsio-shield/QUICKSTART.md#customising-rules) for:

* direct yaml edits
* `overrides:` blocks
* `sponsio shield scan` for unbundled plugins
* `SPONSIO_GUARD_MODE=observe` for shadow-mode rollout

Any change you make to `~/.sponsio/plugins/<id>/sponsio.yaml`
applies to **both** runtimes simultaneously — the libraries are
runtime-agnostic.

---

## Where everything lives

```
Sponsio/
├── plugins/
│   ├── sponsio-shield/                    ← Claude Code transport (stdin hooks)
│   └── sponsio-openclaw-shield/           ← OpenClaw transport (TS register fn)
│       ├── openclaw.plugin.json           — OpenClaw manifest
│       ├── src/index.ts                   — register(api) entry + subprocess transport
│       ├── test/integration.test.ts       — 5 end-to-end tests
│       ├── package.json
│       ├── tsconfig.json
│       ├── README.md                      — internals
│       └── QUICKSTART.md                  ← you are here
│
└── sponsio/                               ← shared backend (Python)
    ├── shield/
    │   ├── defaults/                      — same per-plugin libraries
    │   └── ...
    ├── guard_stdin.py                     — `sponsio shield guard --stdin` core
    └── ...
```

The OpenClaw plugin **doesn't ship its own libraries** — it shares
the entire `~/.sponsio/plugins/` tree with the Claude Code shield.
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
| Not yet exercised end-to-end in a live OpenClaw session | The 5 Node tests validate the protocol translation; once the OpenClaw runtime confirms the `before_tool_call` event shape this plugin sees in production, we may need to update field names. |
| `{block: true, reason: "…"}` — `reason` may be ignored by some OpenClaw versions | The Claude Code shield's deny reason makes it back to the model via `is_error` content. OpenClaw's runtime decides whether to do the same. |
| 80ms per-call subprocess startup | Same daemon-mode mitigation as the Claude Code shield. |
| Tool name conventions differ from Claude Code (no `mcp__server__tool` standard for native OpenClaw tools) | Per-plugin routing falls back to `_host` for unrecognised names; author libraries explicitly under `~/.sponsio/plugins/<openclaw-tool-prefix>/sponsio.yaml`. |

---

## Troubleshooting

**"`spawn sponsio ENOENT`"**

The Node process can't find the Python `sponsio` CLI. Either add it
to PATH or set `SPONSIO_GUARD_BIN=/full/path/to/sponsio`.

**"Hook fires but my rule doesn't block"**

Same diagnostics as the Claude Code shield (see [its
QUICKSTART](../sponsio-shield/QUICKSTART.md#troubleshooting)) —
the underlying `sponsio shield guard --stdin` is identical.

**"Plugin loaded but `before_tool_call` never fires"**

OpenClaw's hook subscription model may require explicit
registration in the plugin manifest, in addition to the runtime
`api.registerHook` call. Check your OpenClaw version's plugin
loading docs.

---

## Next steps

* When OpenClaw lifecycle gets exercised end to end, fold the
  observed event shape back into [`src/index.ts`](src/index.ts).
* Daemon mode (Stage 3) — same plan as the Claude Code shield;
  this plugin's transport switches automatically because the
  subprocess call goes to the same `sponsio shield guard --stdin`
  endpoint.
