# Host plugins (Mode A)

Sponsio's **host plugin** is a small adapter that hooks into a coding
host (Claude Code, OpenClaw, …) and runs every tool call through the
shared `sponsio plugin guard` backend before the host executes it.
Where Mode B (`sponsio onboard` + skill) targets developers who own
their agent code, Mode A targets users who want to gate a host's
**entire session** — the host's own Bash / Edit / Write, every
sub-agent it spawns, and every MCP server tool call.

Two host adapters ship today:

| Plugin | Repo path | Host | Status |
|---|---|---|---|
| [`sponsio-claude-code`](../plugins/sponsio-claude-code/) | Claude Code | Working prototype |
| [`sponsio-openclaw`](../plugins/sponsio-openclaw/) | OpenClaw | Protocol verified, not yet exercised in a live OpenClaw session |

Both share the same Python backend and read the same per-plugin
contract libraries under `~/.sponsio/plugins/<routed-id>/sponsio.yaml`,
so any rule you write for one host is portable to the other.

## Architecture (shared)

```
agent calls a tool (Bash, Edit, mcp__github__*, …)
  │
  ▼
host fires its pre-execution hook
  │
  ▼
adapter forwards a normalised PreToolUse JSON over stdin to
`sponsio plugin guard --stdin`
  │
  ▼
guard derives plugin_id from tool_name:
  Bash, Edit, Write, …       → "_host"
  mcp__<server>__<tool>      → "<server>"
  <plugin>:<skill>           → "<plugin>"
  anything else              → "_host" (fallback)
  │
  ▼
guard loads ~/.sponsio/plugins/<plugin_id>/sponsio.yaml,
runs the deterministic engine, writes the deny / allow reply
  │
  ▼
host blocks (exit non-zero / `{block: true}`) or proceeds
```

The guard exits 0 in every code path — a Sponsio bug must never wedge
a tool call. Diagnostics go to stderr; deny verdicts go to stdout in
the documented hook reply schema.

## Prerequisites (both hosts)

```bash
# 1. Sponsio CLI on PATH
pip install sponsio
sponsio --version

# 2. Bootstrap the per-plugin library tree
sponsio plugin init                       # writes ~/.sponsio/plugins/_host/sponsio.yaml + smoke-test

# 3. (Optional) install starter libraries for popular MCP servers
sponsio plugin install --list             # see what's bundled
sponsio plugin install github filesystem playwright
# or
sponsio plugin install --all
```

After this you'll have:

```
~/.sponsio/plugins/
├── _host/sponsio.yaml         # Bash / Edit / Write / Read / etc.
├── github/sponsio.yaml        # mcp__github__*
├── filesystem/sponsio.yaml    # mcp__filesystem__*
└── playwright/sponsio.yaml    # mcp__playwright__*
```

These libraries are shared by every host adapter — install once, both
plugins agree.

## Install — Claude Code (`sponsio-claude-code`)

```bash
# From a clone:
claude --plugin-dir /path/to/Sponsio/plugins/sponsio-claude-code
```

Claude Code reads `.claude-plugin/plugin.json` + `hooks/hooks.json`
from the plugin dir, registers the `PreToolUse` hook, and routes
every tool call through `sponsio plugin guard --stdin`.

For new sessions you'll see (in `--verbose` / `stream-json` output):

```json
"plugins": [{"name": "sponsio-claude-code", ...}]
```

A marketplace install (`/plugin install sponsio-claude-code`) is on
the roadmap — until then, `--plugin-dir` from a clone is the supported
path.

### Verifying without Claude Code

The hook protocol is JSON-on-stdin → JSON-on-stdout, so you can
exercise it directly:

```bash
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"rm -rf /"}}' \
  | sponsio plugin guard --stdin
# {"hookSpecificOutput": {"hookEventName": "PreToolUse",
#                         "permissionDecision": "deny",
#                         "permissionDecisionReason": "..."}}
```

For an interactive walkthrough see
[plugins/sponsio-claude-code/QUICKSTART.md](../plugins/sponsio-claude-code/QUICKSTART.md).

## Install — OpenClaw (`sponsio-openclaw`)

```bash
# From a clone — build the TS plugin:
cd plugins/sponsio-openclaw
npm install
npm run build                             # produces dist/index.js

# Then point OpenClaw at this directory.  Method depends on your
# install — typically a `plugins.json` entry or a published
# `@sponsio/openclaw` npm package.  Refer to OpenClaw's plugin
# loading docs.
```

OpenClaw runtime reads `openclaw.plugin.json` + `dist/index.js`,
calls `register(api)`, and routes every `before_tool_call` event
through the same `sponsio plugin guard --stdin` backend.

### Configuration knobs (env / configSchema)

| Env var | configSchema field | Purpose |
|---|---|---|
| `SPONSIO_GUARD_BIN` | `guardBin` | Path to the `sponsio` Python binary (default: `$PATH` lookup) |
| `SPONSIO_PLUGIN_ROOT` | `pluginRoot` | Override the per-plugin library root (default: `~/.sponsio/plugins`) |
| `SPONSIO_GUARD_MODE` | `guardMode` | `enforce` (default) or `observe` |

Env vars win over configSchema fields if both are set.

For the user-facing walkthrough see
[plugins/sponsio-openclaw/QUICKSTART.md](../plugins/sponsio-openclaw/QUICKSTART.md).

## Authoring rules for an unbundled host plugin

For Claude Code plugins / MCP servers we don't ship a starter for:

```bash
sponsio plugin scan ./path/to/some-plugin --tools tool_a,tool_b
# dry-run by default; review the printed yaml

sponsio plugin scan ./path/to/some-plugin --tools tool_a,tool_b --apply
# writes one yaml per routed group under ~/.sponsio/plugins/<id>/
```

Each rule is heuristic-derived (`source: plugin-scan`); review every
contract before flipping enforce, then add `overrides:` for
known-false-positive cases.

## Per-plugin overrides

Rules ship with conservative defaults. Tune without forking by
adding an `overrides:` block under the relevant agent in
`~/.sponsio/plugins/<plugin>/sponsio.yaml`:

```yaml
agents:
  github:
    contracts: [...as shipped...]
    overrides:
      - match: { desc: "delete_repository is blocked outright" }
        disabled: true
```

Override targets: `desc`, `pack_source`, or `pattern`.

## Mode A vs Mode B at a glance

|  | Mode A — host plugin (this doc) | Mode B — agent integration ([sponsio onboard](guides/onboarding.md)) |
|---|---|---|
| Who runs the agent | Someone else (the host) | You |
| What's gated | **Every** tool call in the host session — host's own + sub-agents + MCP | Tool calls inside your framework integration |
| What you write | YAML libraries under `~/.sponsio/plugins/` | `sponsio.yaml` in your project + a 2-line agent-entry patch |
| Install command | `pip install sponsio` + `sponsio plugin init` | `pip install sponsio` + `sponsio onboard .` |
| Skill assistant | `/sponsio-claude-code:configure` (host-shipped) | [`sponsio` skill](../sponsio/skills/sponsio/SKILL.md) (`sponsio skill install`) |

Both modes share the same engine, contract library format, and
`SPONSIO_MODE` enforce / observe dial. They're complementary — a
project that owns its agent code and runs it inside Claude Code can
use both.

## Performance

* **Per-call cost**: ~90ms (~80ms Python startup + ~10ms Sponsio
  evaluation).
* **50-step session overhead**: ~4.5s cumulative — usually
  imperceptible in interactive use.
* **Daemon mode** (Stage 3, gated on user signal) drops per-call to
  ~5ms by keeping a long-lived sponsio process and using a Unix
  socket for the hook event protocol.
* The deterministic engine itself is sub-millisecond regardless of
  rule count — the bottleneck is process startup, not evaluation.

## Known limitations

| Gap | Status |
|---|---|
| Trace-aware contracts (`must_precede`, `rate_limit`, `cooldown`, `loop_detection`) silent on the first call | Stateless prototype gets a fresh trace per hook fire. Daemon mode (Stage 3) fixes this. |
| MCP server tool inventory not auto-introspected | Pass tool names via `sponsio plugin scan --tools t1,t2,…`. MCP `tools/list` introspection planned. |
| Marketplace install (`/plugin install …`) | Not yet available — use `--plugin-dir` (Claude Code) / clone+build (OpenClaw). |
| OpenClaw runtime end-to-end | Protocol layer + library loading + deny JSON translation are validated by the Node test suite, but the manifest field set + plugin lifecycle are inferred from public docs. Not yet run inside a live OpenClaw session. |
| `tool_rename:` for OpenClaw-flavoured tool names | OpenClaw tool names appear flat (`firecrawl_search`) rather than `mcp__<server>__<tool>`. Current routing fallback puts them in `_host` — author per-plugin libraries explicitly or wait for runtime-aware routing. |

## See also

* [plugins/sponsio-claude-code/QUICKSTART.md](../plugins/sponsio-claude-code/QUICKSTART.md) — Claude Code walkthrough
* [plugins/sponsio-openclaw/QUICKSTART.md](../plugins/sponsio-openclaw/QUICKSTART.md) — OpenClaw walkthrough
* [docs/contracts.md](contracts.md) — contract YAML reference
* [docs/integrations.md](integrations.md) — Mode B framework adapters
