---
name: scan
description: Generate a starter Sponsio contract library for a Claude Code plugin or MCP server that doesn't have a pre-built one in `sponsio shield install`. Reads the plugin's `.claude-plugin/plugin.json` + tool list, partitions tools by routing key, runs name-heuristic rule generation, dry-runs the YAML for review, then writes one file per group under `~/.sponsio/plugins/<id>/sponsio.yaml`. Use when the user says any of "scan this plugin", "generate sponsio rules for X", "create a contract library for my plugin", "I just installed Y plugin / MCP server, what should sponsio block".
---

# sponsio-shield — scan a plugin / MCP server

You are walking the user through generating a starter contract
library for a plugin or MCP server. **Use this skill only when the
user has a plugin sponsio-shield doesn't ship a starter for**;
otherwise delegate to the **setup** skill which uses
`sponsio shield install`.

## Prerequisites

* Sponsio CLI is on PATH: `sponsio --version` succeeds.
* The user can point you at the plugin directory (the one
  containing `.claude-plugin/plugin.json`). Either ask them or
  glob `~/.claude/plugins/*` if they don't know.

## Step 1 — discover the plugin's tool inventory

The scanner can't currently introspect MCP servers — you have to
get the tool names from the user another way:

* If the plugin defines tools as **`skills/*/SKILL.md`** (slash
  commands), tool names are `<plugin-id>:<skill-name>`. Read
  `<dir>/skills/` to enumerate.
* If the plugin runs an **MCP server**, real tool names are
  `mcp__<server>__<tool>`. Run
  `claude mcp list` to see registered servers, or read the plugin's
  `.mcp.json` for server names. The actual tool list comes from
  running the MCP server — not parsable statically. Ask the user:
  "What tools does this MCP server expose? You can paste a list, or
  open `claude` and let me see." If they don't know, suggest they
  open a session, list any `mcp__<server>__*` tool names that
  appear in `/help`, and bring those back.
* If the user just says "everything", run scan with no `--tools`
  and explain the result is baseline-only (just `core/runaway`).

## Step 2 — dry-run scan

Always start with the dry-run. **Never** `--apply` until the user
has seen the output.

```bash
sponsio shield scan <plugin-dir> --tools tool_a,tool_b,tool_c
```

The output is one rendered yaml per **routed group** (the same
partitioning that `sponsio.guard_stdin.derive_plugin_id` does at
runtime — `Bash` → `_host`, `mcp__github__X` → `github`, etc.).
Show the user every group.

## Step 3 — review every contract with the user

For each contract in each group, state:

* **What it blocks** — translate the regex / rate cap into plain
  English. Example: "blocks `delete_repository` outright; for
  one-off deletes the user has to add an override."
* **Why** — point at the `heuristic:` evidence: `starter_irreversible`
  / `starter_bash` / `starter_sql` / `starter_rate_limit` /
  `starter_loop`. Each comes from the rules in
  `sponsio/discovery/starter_pack.py:_per_tool_rules`.
* **What it doesn't catch** — heuristic rules are conservative
  baselines, not airtight. Tell the user explicitly when a rule
  feels generic so they don't assume it's covering everything.

If a rule is wrong (e.g. the heuristic flagged `list_users` as
external-send because the name contains `send`), tell the user to
either:

1. Drop that line from the rendered yaml before running with
   `--apply`.
2. Apply, then add an override:
   ```yaml
   overrides:
     - match: { desc: "list_users at most 10 times per session" }
       disabled: true
   ```

## Step 4 — apply

Once the user is happy with the dry-run:

```bash
sponsio shield scan <plugin-dir> --tools ... --apply
```

This writes one file per group. Show the user every path:

```
✓ wrote ~/.sponsio/plugins/_host/sponsio.yaml
✓ wrote ~/.sponsio/plugins/github/sponsio.yaml
```

If a target file already exists, scan refuses without `--force`.
**Don't pass `--force` for the user automatically** — surface the
warning and let them decide.

## Step 5 — tell the user to reload the plugin

`/reload-plugins` in Claude Code, then test:

```bash
echo '{"hook_event_name":"PreToolUse","tool_name":"<tool>","tool_input":{<dangerous args>}}' \
  | sponsio shield guard --stdin
```

If the deny doesn't fire on a case you expected to block, jump to
the troubleshooting section below.

## Troubleshooting

**"My rule looks right but the deny doesn't fire."**

Most common causes:

1. **Wrong tool name.** The contract uses `mcp__github__delete_repo`
   but the actual tool is `mcp__github__delete_repository`. The
   stateless hook silently no-ops on non-matches (which is correct
   behaviour — we can't deny on a tool we have no rule for).
   Confirm the real name from `claude mcp list` or by triggering the
   tool once and reading the PreToolUse event from
   `~/.claude/projects/.../*.jsonl`.
2. **Wrong routing.** Check the file actually lives at
   `~/.sponsio/plugins/<routed-id>/sponsio.yaml` where the routed id
   matches what `derive_plugin_id` would return for the tool name.
   `mcp__github__X` → `github`; `acme:fetch` → `acme`; bare
   `Bash` → `_host`.
3. **Rate / count rules don't fire on the first call.** The
   stateless prototype gets a fresh empty trace per hook. Until the
   daemon mode lands, only argument-level rules
   (`arg_blacklist`, `arg_value_range`, `scope_limit`,
   `arg_length_limit`, `dangerous_*`, `tool_allowlist`) reliably
   fire on a single call. `rate_limit`, `loop_detection`,
   `must_precede`, `cooldown`, `rate_limit` are all daemon-future.

## What you must not do

* **Do not** apply without showing the dry-run first.
* **Do not** invent tool names. If the user can't list them, tell
  them you can't safely scan and suggest they list MCP tools from a
  live Claude session first.
* **Do not** use `--force` to overwrite a user-edited library
  without explicit consent.
