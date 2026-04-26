---
name: setup
description: Set up the sponsio-shield runtime — bootstrap the per-plugin contract library tree at ~/.sponsio/plugins/, optionally install starter libraries for popular MCP servers (github, filesystem, playwright), and verify with a smoke test. Use when the user says any of "set up sponsio-shield", "configure sponsio-shield", "install the shield", "first-time setup of sponsio", "wire up sponsio in this Claude Code session", "add Sponsio guardrails for my MCP tools", or asks how to make sponsio-shield actually block things.
---

# sponsio-shield — first-time setup

You are walking the user through configuring the **sponsio-shield**
plugin so it actually wraps tool calls in this Claude Code session.
Without these steps, the plugin loads but every per-plugin library
is empty and every tool call passes through unguarded.

## Prerequisites (check silently first)

Run:

```bash
sponsio --version
```

* Not found → tell the user to install: `pip install sponsio` (or
  `pip install -e ".[all]"` from a local clone). Stop here until they
  confirm.
* Present → continue.

## Step 1 — bootstrap the library root

Run:

```bash
sponsio shield init
```

This creates `~/.sponsio/plugins/_host/sponsio.yaml` (covers Claude
Code's first-party tools — Bash, Edit, Write, …) and runs a built-in
allow + block smoke test. Show the user the output verbatim; if the
smoke test fails, **stop** and surface the error — the install is
broken and we shouldn't pretend otherwise.

If the file already exists (the user re-ran setup), the command
prints `…already exists. Re-run with --force to overwrite.` That's
informational, not an error.

## Step 2 — pick starter libraries for the user's MCP servers

Run:

```bash
sponsio shield install --list
```

Show the user the bundled starters. Currently:

* `_host` — already installed by step 1.
* `github` — github-mcp-server. Hard-deny on `delete_repository`,
  blocks deletes of `main` / `master` / production branches, blocks
  writes to `.env`, `.github/workflows/*.yml`, `CODEOWNERS`. Caps
  issue / PR / merge rates.
* `filesystem` — @modelcontextprotocol/server-filesystem. Blocks
  read/write/edit/move on `.env`, `.ssh/`, `.aws/credentials`,
  `/etc/`, browser cookie databases, etc.
* `playwright` — microsoft/playwright-mcp. Blocks navigation to
  internal hosts, `browser_evaluate` exfil patterns
  (`document.cookie`, `sendBeacon`, `fetch` to remote hosts),
  credit-card-shape typing.

Ask the user which of their installed MCP servers map to these.
**Don't guess** — if they're not sure, run
`claude mcp list` (or have them check their `~/.claude/settings.json`
under `mcpServers`) and match names.

Then install only what's needed:

```bash
sponsio shield install github filesystem
# or
sponsio shield install --all       # everything except _host
```

## Step 3 — for plugins not in the bundled set, scan them

If the user has a plugin / MCP server we don't ship a starter for,
delegate to the **scan** skill (sponsio-shield:scan). Don't try to
hand-author a library here.

## Step 4 — verify the deny path actually works

Run a synthetic event through the hook command:

```bash
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"rm -rf /"}}' \
  | sponsio shield guard --stdin
```

Expect: a JSON deny payload on stdout. If it's empty:

1. The library at `~/.sponsio/plugins/_host/sponsio.yaml` is wrong
   or wasn't written. Re-run `sponsio shield init --force`.
2. The Sponsio CLI version is older than the libraries' rule shapes.
   Update with `pip install -U sponsio`.

## Step 5 — tell the user to reload the plugin

If the user is in a live Claude Code session, the plugin needs to
re-pick up the manifest. Tell them to run `/reload-plugins` and
confirm it shows `sponsio-shield`.

## Common configuration adjustments

**Operator wants observe mode (log, don't block) — pilot rollout:**

```bash
export SPONSIO_GUARD_MODE=observe
```

This dial only affects the shield; other Sponsio integrations in
the same shell still respect `SPONSIO_MODE` independently.

**Operator wants per-plugin overrides instead of editing the library:**

Add an `overrides:` block under the agent in
`~/.sponsio/plugins/<plugin>/sponsio.yaml`:

```yaml
agents:
  github:
    contracts: [...shipped...]
    overrides:
      - match: { desc: "delete_repository is blocked outright (overrides: disabled: true to allow)" }
        disabled: true
```

## What you must not do

* **Do not** auto-install everything without asking. The user picks
  which starter libraries they want — don't push the whole bundle.
* **Do not** edit files under `sponsio/contracts/*.yaml` inside the
  installed Sponsio package. Those are read-only shipped packs.
  User-level adjustments go in `~/.sponsio/plugins/<plugin>/sponsio.yaml`.
* **Do not** flip `SPONSIO_GUARD_MODE=observe` "to make the smoke test
  pass". A failing smoke test means something is actually broken;
  silencing it hides the bug.
