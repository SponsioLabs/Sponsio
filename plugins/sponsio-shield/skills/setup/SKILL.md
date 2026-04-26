---
name: setup
description: Set up the sponsio-shield runtime — bootstrap the per-plugin contract library tree at ~/.sponsio/plugins/, optionally install starter libraries for popular MCP servers (github, filesystem, playwright), tune the shipped rules to the user's actual environment (workspace path, expected call volume, dev/CI/prod profile), and verify with a smoke test. Use when the user says any of "set up sponsio-shield", "configure sponsio-shield", "install the shield", "first-time setup of sponsio", "wire up sponsio in this Claude Code session", "add Sponsio guardrails for my MCP tools", "tune the shield for my environment", "the shield is too strict / too loose", "calibrate sponsio rules", or asks how to make sponsio-shield actually block things.
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

## Step 4 — tune the shipped rules to the user's actual environment

The shipped libraries are templates with conservative defaults.
Without this step, half the rules are either too strict (blocking
legitimate work) or too loose (giving the user false confidence).
Walk through the four parameter classes below and write any
agreed-upon adjustments as `overrides:` blocks into the relevant
library. **Don't skip this step on the assumption defaults are
fine** — defaults are a starting point, not a fit.

### 4.1 — workspace path

Several rules in `_host` and `filesystem` use `<workspace>/` as
the path-allowlist root. Until that's substituted with the user's
actual project root, those rules either match nothing (silent
no-op) or match too broadly. Ask:

> "Where's your main working directory for this session?
>  (`pwd` or your project root.)"

Then write to the relevant `agents:<id>` block:

```yaml
agents:
  _host:
    workspace: /Users/<them>/projects/<repo>
```

If multiple plugins want different workspaces, add `workspace:`
under each agent block separately.

### 4.2 — expected call volume

The shipped `rate_limit` defaults are tuned for a single
interactive session (50 Bash calls / 200k tokens / 5 PRs).
Operators running CI scripts, batch jobs, or recurring agents
often hit these legitimately. Ask:

> "How chatty is this agent? Is this an interactive session,
>  a CI run, a long-running operator, or a one-shot script?"

Use the answer to override:

| Scenario | Adjustment |
|---|---|
| Interactive (default) | leave as-is |
| Heavy CI / batch | bump exec/Bash rate to 200, token budget to 500k |
| Read-only research | tighten Bash rate to 10, drop exec rate cap entirely |
| Long-running (>1h) | drop session-bounded counts, switch to time-window pacing (note: needs daemon mode for time-window — surface as a future-work caveat) |

Example override:

```yaml
agents:
  _host:
    overrides:
      - match: { desc: "Cap exec calls per session" }
        args: [Bash, 200]
```

### 4.3 — environment profile

Different blast radius means different default tightness. Ask:

> "What kind of environment is this — local dev, staging,
>  production, or a customer-data context?"

Apply this matrix:

| Profile | What changes |
|---|---|
| Local dev | leave defaults |
| Staging | enable `audit_after` on destructive tools (logs every action); keep delete rules permissive |
| Production | move `delete_*` from `rate_limit 0` to **assumption-gated** — require an explicit `confirm_reconfirmed` tool emission (see existing pattern in `capability/shell` §4) |
| Regulated / PII | tighten sto rules — `core/universal`'s β from 0.95 → 0.99; force `semantic_pii_free` even on agents that don't currently include it |

### 4.4 — known-false-positive overrides

Walk the user through each shipped rule that's commonly tripped
by legitimate workflows. For every `Yes, that's a problem for me`
answer, write a targeted `overrides:` entry. Common cases:

| Rule | When it false-positives | Override |
|---|---|---|
| `_host` "Each exec call needs its own confirm_reconfirmed" | Any agent that doesn't emit `confirm_reconfirmed` markers | `disabled: true` (until the integration ships markers) |
| `github` "delete_repository is blocked outright" | Cleanup bots, automated repo lifecycle | `disabled: true` + add a custom rule with a tighter pattern (only allow deletion of repos matching `^test-`) |
| `filesystem` "read_file must not exfiltrate dotenv" | dotenv rotators, secret-rotation agents | `disabled: true` only for `read_file` (keep `write_file` denied) |
| `playwright` "browser_navigate must not target internal hosts" | Anyone testing their own internal app | replace with a narrower allowlist of the user's actual internal hostnames |

### 4.5 — write the overrides + verify

After the walkthrough, write all agreed-upon overrides into the
relevant `~/.sponsio/plugins/<id>/sponsio.yaml` files. **Do not
edit the shipped library inline** — always add overrides under
`overrides:` so future `sponsio shield install --force` doesn't
clobber the user's customisations.

Run `sponsio validate --config ~/.sponsio/plugins/<id>/sponsio.yaml`
on every file you touched. Any error means you wrote a malformed
override; fix before continuing.

### 4.6 — observe-mode dial for tuning runs

If the user has *no idea* what numbers to use, suggest:

```bash
export SPONSIO_GUARD_MODE=observe
```

Run their normal workflow for a day, then come back and:

```bash
sponsio report --since 24h
```

Surface the would-have-blocked rules. For every cluster of
legitimate-looking violations, tighten the matching `overrides:`.
This is the data-driven counterpart to the questionnaire above —
the questionnaire is the cold-start prior, the report is the
posterior.

## Step 5 — verify the deny path actually works

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

## Step 6 — tell the user to reload the plugin

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
