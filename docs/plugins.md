---
title: Host plugins (Mode A)
description: Gate an entire Claude Code or OpenClaw session, not just your agent.
---

# Host plugins (Mode A)

Sponsio's host plugin hooks into a coding host (Claude Code, OpenClaw) and runs every tool call through the shared `sponsio plugin guard` backend before the host executes it. Mode B (`sponsio init` plus skill) targets developers who own their agent code. Mode A targets users who want to gate a host's entire session: the host's own Bash, Edit, Write, every sub-agent it spawns, and every MCP server tool call.

Two host adapters ship today.

| Plugin | Path | Host |
|---|---|---|
| `sponsio-claude-code` | [`plugins/sponsio-claude-code/`](../plugins/sponsio-claude-code/) | Claude Code |
| `sponsio-openclaw` | [`plugins/sponsio-openclaw/`](../plugins/sponsio-openclaw/) | OpenClaw |

Both share the same Python backend and read the same per-plugin contract libraries under `~/.sponsio/plugins/<routed-id>/sponsio.yaml`. A rule written for one host runs on the other.

## Architecture

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
host blocks (exit non-zero or {block: true}) or proceeds
```

The guard exits 0 in every code path. If Sponsio crashes, the tool call still goes through (fail-open). Diagnostics go to stderr; deny verdicts go to stdout in the documented hook reply schema.

## Setup (both hosts)

```bash
pip install sponsio
sponsio plugin init                    # writes ~/.sponsio/plugins/_host/sponsio.yaml
sponsio plugin install --list          # see bundled libraries
sponsio plugin install github filesystem playwright
```

After this:

```
~/.sponsio/plugins/
├── _host/sponsio.yaml         # Bash / Edit / Write / Read
├── github/sponsio.yaml        # mcp__github__*
├── filesystem/sponsio.yaml
└── playwright/sponsio.yaml
```

These libraries are shared by both host adapters. Install once, both plugins agree.

## Claude Code

```bash
claude --plugin-dir /path/to/Sponsio/plugins/sponsio-claude-code
```

Claude Code reads `.claude-plugin/plugin.json` and `hooks/hooks.json`, registers the `PreToolUse` hook, and routes every tool call through `sponsio plugin guard --stdin`. New sessions show:

```json
"plugins": [{"name": "sponsio-claude-code", ...}]
```

A marketplace install is on the roadmap. Until then, `--plugin-dir` from a clone is the supported path. Walkthrough: [plugins/sponsio-claude-code/QUICKSTART.md](../plugins/sponsio-claude-code/QUICKSTART.md).

## OpenClaw

```bash
sponsio host install openclaw
# restart OpenClaw
```

`sponsio host install` deploys the bundled prebuilt extension into `~/.openclaw/extensions/sponsio-openclaw/`, bootstraps the fallback contract library, and registers the plugin in `~/.openclaw/openclaw.json` with a backup. Verify with `sponsio host status openclaw`. Live blocks: `sponsio host trace openclaw --follow`.

Configuration knobs:

| Env var | configSchema field | Purpose |
|---|---|---|
| `SPONSIO_GUARD_BIN` | `guardBin` | Path to the `sponsio` binary |
| `SPONSIO_PLUGIN_ROOT` | `pluginRoot` | Override `~/.sponsio/plugins` |
| `SPONSIO_GUARD_MODE` | `guardMode` | `enforce` (default) or `observe` |

Walkthrough: [plugins/sponsio-openclaw/QUICKSTART.md](../plugins/sponsio-openclaw/QUICKSTART.md).

## Authoring rules

For plugins or MCP servers without a starter library:

```bash
sponsio plugin scan ./path/to/some-plugin --tools tool_a,tool_b           # dry-run
sponsio plugin scan ./path/to/some-plugin --tools tool_a,tool_b --apply   # writes yaml
```

Each rule is heuristic-derived (`source: plugin-scan`). Review every contract before flipping enforce, then add `customized:` for known-false-positive cases.

## Per-plugin overrides

Tune without forking. Add a `customized:` block under the relevant agent in `~/.sponsio/plugins/<plugin>/sponsio.yaml`:

```yaml
agents:
  github:
    contracts: [...as shipped...]
    overrides:
      - match: { desc: "delete_repository is blocked outright" }
        disabled: true
```

Override targets: `desc`, `pack_source`, `pattern`.

## Mode A vs Mode B

| | Mode A (host plugin) | Mode B ([sponsio init](guides/onboarding.md)) |
|---|---|---|
| Who runs the agent | Someone else (the host) | You |
| What's gated | Every tool call in the host session | Tool calls inside your framework integration |
| What you write | YAML libraries under `~/.sponsio/plugins/` | `sponsio.yaml` in your project plus a 2-line agent-entry patch |
| Install command | `pip install sponsio` plus `sponsio plugin init` | `pip install sponsio` plus `sponsio init .` |

Both modes share the same engine, contract library format, and `SPONSIO_MODE` enforce / observe dial. A project that owns its agent code and runs it inside Claude Code can use both.

## Limits

| Gap | Status |
|---|---|
| Trace-aware contracts (`must_precede`, `rate_limit`, `cooldown`, `loop_detection`) silent on the first call | The stateless hook gets a fresh trace per fire. Daemon mode (Stage 3) fixes this. |
| MCP server tool inventory not auto-introspected | Pass tool names via `sponsio plugin scan --tools t1,t2,...`. MCP `tools/list` introspection planned. |
| Marketplace install | Not yet available. Use `--plugin-dir` (Claude Code) or `sponsio host install openclaw`. |
| `tool_rename:` for OpenClaw flat tool names | OpenClaw tool names like `firecrawl_search` route to `_host` by fallback. Author per-plugin libraries explicitly or wait for runtime-aware routing. |

## Performance

Per-call cost: about 90 ms (80 ms Python startup, 10 ms Sponsio evaluation). 50-step session overhead: about 4.5 s cumulative, usually imperceptible interactively. The deterministic engine itself stays under a millisecond for typical rule sets (the latency scales with the number of contracts but stays well below one millisecond at hundreds of contracts). The bottleneck is process startup, not evaluation. Daemon mode (Stage 3, available on request) drops per-call to about 5 ms by keeping a long-lived sponsio process and using a Unix socket for the hook event protocol.

## See also

- [Claude Code walkthrough](../plugins/sponsio-claude-code/QUICKSTART.md)
- [OpenClaw walkthrough](../plugins/sponsio-openclaw/QUICKSTART.md)
- [Concepts: contracts](concepts/contracts.md)
- [Integrations (Mode B)](integrations/index.md)
