# sponsio-claude-code (Claude Code plugin)

A Claude Code plugin that guards every `PreToolUse` event in your
session against per-plugin Sponsio contract libraries.

> **Just want to install + use it?** See [QUICKSTART.md](QUICKSTART.md).
> The README below is the architecture / internals reference.

> **Note on trace-aware contracts.** Argument-level contracts
> (`scope_limit`, `arg_blacklist`, `arg_value_range`,
> `dangerous_bash_commands`, …) fire on every hook today.
> Trace-aware contracts (`must_precede`, `rate_limit`, `cooldown`)
> require cross-call session state and land with daemon mode; the
> stateless hook can't see them on its own.

## Architecture (Mode A — host-installed plugin)

```
PreToolUse fires → Claude Code spawns `sponsio plugin guard --stdin`
                 → stdin: {"tool_name": "...", "tool_input": {...}, ...}
                 → derive plugin id from tool_name
                       Bash, Edit, Write, …      → "_host"
                       acme:fetch_data           → "acme"
                       mcp__acme__fetch          → "acme"
                 → load ~/.sponsio/plugins/<plugin>/sponsio.yaml
                 → guard.guard_before(tool_name, tool_input)
                 → emit deny JSON or exit 0 silently
```

Each plugin gets its own contract library so:

* the user can ship official + community + private libraries side by side
* installing/uninstalling a plugin doesn't churn an unrelated library
* evaluation is fast — only one plugin's rules run per tool call

## Install (development)

```bash
# 1. Install Sponsio so `sponsio plugin guard --stdin` is on PATH.
pip install -e .

# 2. Bootstrap the per-plugin library tree (creates ~/.sponsio/plugins/_host/
#    and runs an allow + block smoke test).
sponsio plugin init

# 3. (Optional) Drop in starter libraries for popular MCP servers.
sponsio plugin install --list                  # see what's bundled
sponsio plugin install github filesystem playwright

# 4. Load the plugin into Claude Code.
claude --plugin-dir ./plugins/sponsio-claude-code
```

`plugin init` ships the same default `_host` library as the
`libraries/_host/sponsio.yaml` in this directory — pick whichever
install path is more convenient.

### Starter libraries shipped today

| Routing key | Source MCP server | Highlights |
|---|---|---|
| `_host` | Claude Code built-ins (Bash, Edit, …) | `rm -rf /`, fork bombs, `curl \| bash`, line-continuation evasion, reverse shells |
| `github` | `github/github-mcp-server` | hard-deny on `delete_repository`, protected-branch deletes blocked, dotenv / workflow-yaml writes blocked, issue / PR / merge rate caps |
| `filesystem` | `@modelcontextprotocol/server-filesystem` | dotenv / SSH / AWS / `/etc` / launchd path blacklist on read + write + edit + move |
| `playwright` | `microsoft/playwright-mcp` | navigation blacklist (localhost / RFC1918 / `file://` / `javascript:`), `browser_evaluate` cookie / fetch / sendBeacon exfil block, credit-card-shape typing block |

To generate a starter library for an unbundled plugin from its
manifest + tool list:

```bash
sponsio plugin scan ./path/to/some-plugin --tools tool_a,tool_b --apply
```

## Verifying it works without Claude Code

The hook contract is just JSON-on-stdin → JSON-on-stdout, so you can
exercise it with `echo`:

```bash
# Allowed — exit 0, no stdout
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"ls"}}' \
  | sponsio plugin guard --stdin

# Blocked — JSON deny on stdout
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"rm -rf /"}}' \
  | sponsio plugin guard --stdin
# {"hookSpecificOutput": {"hookEventName": "PreToolUse",
#                         "permissionDecision": "deny",
#                         "permissionDecisionReason": "..."}}
```

## Layout

```
sponsio-claude-code/
├── .claude-plugin/
│   └── plugin.json              # required Claude Code manifest
├── hooks/
│   └── hooks.json               # PreToolUse → `sponsio plugin guard --stdin`
├── libraries/
│   ├── _host/sponsio.yaml       # Claude Code built-in tools (Bash, Edit, …)
│   ├── github/sponsio.yaml      # mcp__github__* — github-mcp-server
│   ├── filesystem/sponsio.yaml  # mcp__filesystem__* — server-filesystem
│   └── playwright/sponsio.yaml  # mcp__playwright__* — playwright-mcp
└── README.md
```

These are mirror copies of the bundled package data under
`sponsio/plugin/defaults/*.yaml` — one is for `--plugin-dir` users
(cp from this directory), the other for pip-install users (via
`sponsio plugin install`). A test enforces byte-equality.

## Adding rules for a specific plugin

```bash
mkdir -p ~/.sponsio/plugins/acme
cat > ~/.sponsio/plugins/acme/sponsio.yaml <<'YAML'
version: "1"
agents:
  acme:
    contracts:
      - desc: "mcp__acme__fetch may not call internal hosts"
        G:
          pattern: arg_blacklist
          args: [mcp__acme__fetch, url, ["^https?://(localhost|10\\.|192\\.168\\.)"]]
YAML
```

## Known gaps (planned)

| Gap | Status |
|---|---|
| Trace-aware contracts (`must_precede`, `rate_limit`) | Need daemon mode for per-session state |
| `sponsio plugin scan` over a Claude Code plugin manifest | Stage 2 |
| Hot-load library updates without `/reload-plugins` | TBD |
| Claude Code namespaced-skill names (`my-plugin:hello`) | Grounding bug — Stage 2 |
