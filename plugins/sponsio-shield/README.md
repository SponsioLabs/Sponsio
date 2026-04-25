# sponsio-shield (Claude Code plugin) — prototype

A Claude Code plugin that guards every `PreToolUse` event in your
session against per-plugin Sponsio contract libraries.

> **Status:** prototype. Argument-level contracts (`scope_limit`,
> `arg_blacklist`, `arg_value_range`, `dangerous_bash_commands`, …)
> work today. Trace-aware contracts (`must_precede`, `rate_limit`,
> `cooldown`) are silent until the planned daemon mode lands —
> they need cross-call session state that the stateless hook can't
> see on its own.

## Architecture (Mode A — host-installed shield)

```
PreToolUse fires → Claude Code spawns `sponsio shield guard --stdin`
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
# 1. Install Sponsio so `sponsio shield guard --stdin` is on PATH.
pip install -e .

# 2. Bootstrap the per-plugin library tree.
mkdir -p ~/.sponsio/plugins/_host
cp plugins/sponsio-shield/libraries/_host/sponsio.yaml \
   ~/.sponsio/plugins/_host/sponsio.yaml

# 3. Load the plugin into Claude Code.
claude --plugin-dir ./plugins/sponsio-shield
```

The sample `_host` library blocks `rm -rf /`, fork bombs, `curl | bash`,
reverse-shell primitives, line-continuation evasion, and other
shell-incident patterns sourced from `sponsio:capability/shell`.

## Verifying it works without Claude Code

The hook contract is just JSON-on-stdin → JSON-on-stdout, so you can
exercise it with `echo`:

```bash
# Allowed — exit 0, no stdout
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"ls"}}' \
  | sponsio shield guard --stdin

# Blocked — JSON deny on stdout
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"rm -rf /"}}' \
  | sponsio shield guard --stdin
# {"hookSpecificOutput": {"hookEventName": "PreToolUse",
#                         "permissionDecision": "deny",
#                         "permissionDecisionReason": "..."}}
```

## Layout

```
sponsio-shield/
├── .claude-plugin/
│   └── plugin.json              # required Claude Code manifest
├── hooks/
│   └── hooks.json               # PreToolUse → `sponsio shield guard --stdin`
├── libraries/
│   └── _host/
│       └── sponsio.yaml         # default rules for built-in tools
└── README.md
```

## Adding rules for a specific plugin

```bash
mkdir -p ~/.sponsio/plugins/acme
cat > ~/.sponsio/plugins/acme/sponsio.yaml <<'YAML'
version: "1"
agents:
  acme:
    contracts:
      - desc: "mcp__acme__fetch may not call internal hosts"
        E:
          pattern: arg_blacklist
          args: [mcp__acme__fetch, url, ["^https?://(localhost|10\\.|192\\.168\\.)"]]
YAML
```

## Known gaps (planned)

| Gap | Status |
|---|---|
| Trace-aware contracts (`must_precede`, `rate_limit`) | Need daemon mode for per-session state |
| `sponsio shield scan` over a Claude Code plugin manifest | Stage 2 |
| Hot-load library updates without `/reload-plugins` | TBD |
| Claude Code namespaced-skill names (`my-plugin:hello`) | Grounding bug — Stage 2 |
