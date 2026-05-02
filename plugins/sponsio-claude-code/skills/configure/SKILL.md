---
name: configure
description: Use after `/plugin install sponsio-claude-code` to wire the runtime end-to-end. The plugin install only registers hooks + skills; the contract library and per-environment overrides are configured here. Bootstraps the per-plugin contract library tree at ~/.sponsio/plugins/, installs bundled starter libraries for popular MCP servers (github, filesystem, playwright), generates fresh starter libraries for plugins / MCP servers that don't ship one (via `sponsio plugin scan`), tunes the shipped rules to the user's actual environment (workspace path, expected call volume, dev/CI/prod profile), and verifies with a smoke test. Use when the user says any of "configure sponsio-claude-code", "set up sponsio-claude-code", "first-time setup of sponsio", "wire up sponsio in this Claude Code session", "add Sponsio guardrails for my MCP tools", "tune the plugin for my environment", "the plugin is too strict / too loose", "calibrate sponsio rules", "scan this plugin", "generate sponsio rules for X", "create a contract library for my plugin", "I just installed Y plugin / MCP server, what should sponsio block", or asks how to make sponsio-claude-code actually block things.
---

# sponsio-claude-code — configure the host shield

You are walking the user through configuring the **sponsio-claude-code**
plugin so it actually wraps tool calls in this Claude Code session.
Without these steps, the plugin loads but every per-plugin library
is empty and every tool call passes through unguarded.

The flow has two halves: **setup** (steps 1–2, 4–6) covers the common
case where the user runs popular MCP servers we ship starters for;
**scan** (step 3) covers plugins / MCP servers without a bundled
starter and lives inline because most sessions touch both halves.

## Prerequisites (check silently first)

Run:

```bash
sponsio --version
```

* Not found → tell the user to install: `pip install sponsio` (or
  `pip install -e ".[all]"` from a local clone). Stop here until they
  confirm.
* Present → continue.

If the user owns the agent code and wants to wire Sponsio into their
own framework integration (LangGraph, OpenAI Agents, Claude Agent
SDK, …) **instead of** running it as a Claude Code host plugin,
delegate to the `sponsio` skill (`sponsio onboard .`). This skill is
only for the host-plugin case where the user is gating tool calls
inside their Claude Code session.

## Routing rules from a policy document

Sponsio has two layers, and rules must land in the right YAML or they
do nothing. When the user hands you a policy document / instruction
file / "list of things the agent must not do" and asks you to
encode it, classify **each rule** before writing anywhere.

| Signal | → Layer 1 (this skill — write to `~/.sponsio/plugins/<id>/sponsio.yaml`) | → Layer 2 (delegate to `sponsio` skill — writes `<project>/sponsio.yaml`) |
|---|---|---|
| Tool names mentioned | `Bash`, `Edit`, `Write`, `Read`, `mcp__*` | tool names from the user's project's tool inventory |
| Path form | absolute or `~/...` paths outside the user's project | paths relative to the project (`src/...`) |
| Subject of the rule | "Cursor must not…", "Claude Code must not…" | "the loan agent must…", "the chatbot should…" |
| Domain language | shell, git, file system, MCP server primitives | AML, KYC, refund, PII, approval, faithfulness, hallucination |
| `./sponsio.yaml` exists in cwd | weaker signal — Layer 2 is in play, but rule may still be Layer 1 | stronger signal — most rules belong here |

Process:
1. For each rule, score by the signals above.
2. Route unambiguously-Layer-1 rules to this skill's flow (write under
   `~/.sponsio/plugins/<id>/sponsio.yaml`).
3. For Layer-2 rules, **stop writing here** and tell the user
   "rules X, Y look like rules for the agent you're building, not the
   IDE agent — switching to the `sponsio` skill (`sponsio onboard`) for
   those". Do not silently dump them into a host-plugin YAML.
4. For genuinely ambiguous rules ("PII must not leak"), ask the user
   which layer they mean before writing.

The default failure mode is over-writing to Layer 1 because that's
this skill's home turf. Cross-layer leakage is a worse user error
than the extra clarification round.

## Step 1 — bootstrap the library root

Run:

```bash
sponsio plugin init
```

This creates `~/.sponsio/plugins/_host/sponsio.yaml` (covers Claude
Code's first-party tools — Bash, Edit, Write, …) and runs a built-in
allow + block smoke test. Show the user the output verbatim; if the
smoke test fails, **stop** and surface the error — the install is
broken and we shouldn't pretend otherwise.

If the file already exists (the user re-ran setup), the command
prints `…already exists. Re-run with --force to overwrite.` That's
informational, not an error.

## Step 2 — enumerate the user's MCP servers

You need to know which MCP servers / plugins the user actually has
installed before deciding which libraries to ship. Run
`claude mcp list`, or have the user open `~/.claude/settings.json`
and read `mcpServers`. Don't guess. If the user doesn't know, that's
the answer — they have none beyond the built-ins, and you can skip
straight to step 4.

For each registered server, decide which branch of step 3 applies:

```bash
sponsio plugin install --list
#   _host (auto-installed by `plugin init`)
#   filesystem
#   github
#   playwright
```

* **Bundled** (`github`, `filesystem`, `playwright`) → step 3a.
* **Not bundled** (anything else) → step 3b.

A single session often hits both branches.

## Step 3a — bundled MCP servers (use `sponsio plugin install`)

Show the user what each bundled starter covers so they can pick:

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

Then install **only what the user has** — don't push the whole
bundle:

```bash
sponsio plugin install github filesystem
# or, if they confirm they want everything:
sponsio plugin install --all
```

`plugin install` prints a per-library digest after each successful
write — categories, rule descriptions, and the YAML path. **Surface
the full digest to the user verbatim**; do not paraphrase or
summarise. The digest is the user's only chance to see what just
got loaded before flipping to enforce mode. If they ask to inspect
later, run:

```bash
sponsio plugin show <name>           # installed library
sponsio plugin show <name> --root … # custom root
```

## Step 3b — unbundled plugins (use `sponsio plugin scan`)

For every MCP server / plugin that isn't in the bundled set, generate
a starter library. Don't try to hand-author one — the scanner has
two engines (heuristic + LLM) that together cover most patterns
better than ad-hoc rules.

### 3b.1 — discover the plugin's tool inventory

Three discovery paths in priority order:

1. **`--introspect "<spawn-cmd>"`** (preferred for MCP servers).
   sponsio spawns the server, does the JSON-RPC handshake, calls
   `tools/list`, and auto-populates the tool inventory along with
   parameter schemas the LLM can use.  Read the plugin's `.mcp.json`
   (or `claude mcp list`) to find the spawn command.

   ```bash
   sponsio plugin scan \
     --plugin-id <name> \
     --target-host claude-code \
     --introspect "python3 /path/to/server.py"
   ```

   For Claude Code MCP integrations, ALWAYS pass
   `--target-host claude-code` so tool names get the
   `mcp__<plugin-id>__` prefix Claude Code surfaces them under.

2. **Slash-command plugins** (no MCP server, just `skills/*/SKILL.md`).
   Tool names are `<plugin-id>:<skill-name>`.  Read `<dir>/skills/`
   to enumerate, then pass via `--tools`:

   ```bash
   sponsio plugin scan <plugin-dir> --tools my-plugin:foo,my-plugin:bar
   ```

3. **Operator-supplied tool list** (when introspection fails or is
   inconvenient).  Ask the user "what tools does this MCP server
   expose?" and pass them via `--tools t1,t2,t3`.  Last resort.

### 3b.2 — dry-run the scan

ALWAYS start with the dry-run.  **Never** `--apply` until the user
has seen the output.

```bash
sponsio plugin scan --plugin-id <id> --target-host claude-code \
  --introspect "<spawn-cmd>"
```

The output has two parts:

1. **Heuristic library yaml** (one per routed group) — name-pattern
   rules: destructive verbs → `irreversible_once`, generic
   rate-limit / loop-detection caps.  This is the deterministic
   floor; ship it as-is or harden further.

2. **Tool inventory JSON** under `# === tool inventory ... ===` —
   every tool's `name`, `description`, `input_schema`, plus a
   `tool_name_in_contracts` field already namespaced for the target
   host.  This is the *input* for the agent's own contract
   extraction pass (Step 3b.3).

### 3b.3 — agent applies the contract-extraction prompt

The heuristic engine catches destructive verbs by name; the agent
fills semantic gaps by reading each tool's description and
input_schema.  No API call needed — you (the host agent) ARE the
LLM the prompt is written for.

1. Get the prompt:
   ```bash
   sponsio plugin prompt claude-code
   ```
   Pipe to a variable or just remember it for the next step.

2. Apply the prompt mentally to the JSON tool inventory from the
   previous step.  Output should be a JSON object matching:
   ```json
   {"contracts": [{"desc": "...", "pattern": "...", "args": [...]}]}
   ```

3. Translate that JSON into the YAML contract block format and
   merge it into the heuristic library (or keep it as a separate
   `sponsio.semantic.yaml` next to the heuristic one).  Each
   semantic contract should carry `source: agent-extracted` so
   future `sponsio refresh` runs can distinguish them from
   heuristic rules.

The whole loop is fast because (a) introspect + heuristic generation
is one CLI call, (b) the prompt is short and on-disk (no network),
(c) you reason over the inventory in your own context.

### 3b.4 — review every contract

For each contract in each group, state:

* **What it blocks** — translate the regex / rate cap into plain
  English. Example: "blocks `delete_repository` outright; for one-off
  deletes the user has to add an override."
* **Why** — point at the `heuristic:` evidence: `starter_irreversible`
  / `starter_bash` / `starter_sql` / `starter_rate_limit` /
  `starter_loop`. Each comes from the rules in
  `sponsio/discovery/starter_pack.py:_per_tool_rules`.
* **What it doesn't catch** — heuristic rules are conservative
  baselines, not airtight. Tell the user explicitly when a rule feels
  generic so they don't assume it's covering everything.

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

### 3b.5 — apply

Once the user is happy with the dry-run:

```bash
sponsio plugin scan <plugin-dir> --tools ... --apply
```

Show every path the apply writes to:

```
✓ wrote ~/.sponsio/plugins/_host/sponsio.yaml
✓ wrote ~/.sponsio/plugins/github/sponsio.yaml
```

If a target file already exists, scan refuses without `--force`.
**Don't pass `--force` for the user automatically** — surface the
warning and let them decide.

## Step 4 — tune the shipped rules to the user's actual environment

The shipped libraries are templates with conservative defaults.
Without this step, half the rules are either too strict (blocking
legitimate work) or too loose (giving the user false confidence).
Walk through the four parameter classes below and write any
agreed-upon adjustments as `overrides:` blocks into the relevant
library. **Don't skip this step on the assumption defaults are
fine** — defaults are a starting point, not a fit.

### 4.1 — workspace path

Several rules in `_host` and `filesystem` use `<workspace>/` as the
path-allowlist root. Until that's substituted with the user's actual
project root, those rules either match nothing (silent no-op) or
match too broadly. Ask:

> "Where's your main working directory for this session?
>  (`pwd` or your project root.)"

Then write to the relevant `agents:<id>` block:

```yaml
agents:
  _host:
    workspace: /Users/<them>/projects/<repo>
```

If multiple plugins want different workspaces, add `workspace:` under
each agent block separately.

### 4.2 — expected call volume

The shipped `rate_limit` defaults are tuned for a single interactive
session (50 Bash calls / 200k tokens / 5 PRs). Operators running CI
scripts, batch jobs, or recurring agents often hit these
legitimately. Ask:

> "How chatty is this agent? Is this an interactive session, a CI
>  run, a long-running operator, or a one-shot script?"

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

> "What kind of environment is this — local dev, staging, production,
>  or a customer-data context?"

Apply this matrix:

| Profile | What changes |
|---|---|
| Local dev | leave defaults |
| Staging | enable `audit_after` on destructive tools (logs every action); keep delete rules permissive |
| Production | move `delete_*` from `rate_limit 0` to **assumption-gated** — require an explicit `confirm_reconfirmed` tool emission (see existing pattern in `capability/shell` §4) |
| Regulated / PII | tighten sto rules — `core/universal`'s β from 0.95 → 0.99; force `semantic_pii_free` even on agents that don't currently include it |

### 4.4 — known-false-positive overrides

Walk the user through each shipped rule that's commonly tripped by
legitimate workflows. For every "Yes, that's a problem for me"
answer, write a targeted `overrides:` entry. Common cases:

| Rule | When it false-positives | Override |
|---|---|---|
| `_host` "Each exec call needs its own confirm_reconfirmed" | Any agent that doesn't emit `confirm_reconfirmed` markers | `disabled: true` (until the integration ships markers) |
| `github` "delete_repository is blocked outright" | Cleanup bots, automated repo lifecycle | `disabled: true` + add a custom rule with a tighter pattern (only allow deletion of repos matching `^test-`) |
| `filesystem` "read_file must not exfiltrate dotenv" | dotenv rotators, secret-rotation agents | `disabled: true` only for `read_file` (keep `write_file` denied) |
| `playwright` "browser_navigate must not target internal hosts" | Anyone testing their own internal app | replace with a narrower allowlist of the user's actual internal hostnames |

### 4.5 — hand off to the user (don't write the file yourself)

The `~/.sponsio/plugins/<id>/sponsio.yaml` bundle libraries are
**user-only files**. You must NOT use `Edit`, `Write`, `MultiEdit`,
or shell redirects (`>`, `>>`, `tee`, `sed -i`, …) to modify them
— the runtime self-modify pack will block those calls anyway, and
relying on the block is a worse experience than just doing the
right thing first time.

The only legitimate ways to update a bundle:

1. **CLI commands** that the user runs (or you run on their behalf
   via `Bash`):
   - `sponsio plugin install <name>` — copy a fresh bundled starter
   - `sponsio plugin scan --apply` — regenerate from a tool inventory
2. **Hand-edit by the user** in their text editor — they own the
   file and can edit it however they want; you're not the gatekeeper
   of *how* they apply changes, only the helper that gives them the
   raw material and validates afterwards.

The tuning loop is a one-shot exchange:

1. Tell the user the file path and *suggest* what they could add as a
   YAML snippet — treat it as guidance, not a template they must
   paste verbatim:

   ```yaml
   # Suggested addition to ~/.sponsio/plugins/github/sponsio.yaml —
   # under the ``github`` agent, in an ``overrides:`` block beside
   # ``contracts:``:
   overrides:
     - match: { desc: "delete_repository is blocked outright (overrides: disabled: true to allow)" }
       disabled: true
   ```

   The user can paste it, rewrite it, restructure the file, add
   their own rules — whatever. Don't dictate the exact mechanics.

2. When the user says they're done editing, run:

   ```bash
   sponsio validate --config ~/.sponsio/plugins/<id>/sponsio.yaml
   ```

   Any error means the result didn't parse; surface the error and
   help them debug. Don't assume their edits matched your snippet.

The reason for the user-only rule: if the IDE agent could rewrite
the file that's about to fire against it, an attacker
prompt-injecting the agent could disable the very rule blocking
exfiltration. Keeping the human as the only writer is the
privilege boundary that makes Sponsio's guarantees real.

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
This is the data-driven counterpart to the questionnaire above — the
questionnaire is the cold-start prior, the report is the posterior.

## Step 5 — verify the deny path actually works

Run a synthetic event through the hook command:

```bash
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"rm -rf /"}}' \
  | sponsio plugin guard --stdin
```

Expect: a JSON deny payload on stdout. If it's empty:

1. The library at `~/.sponsio/plugins/_host/sponsio.yaml` is wrong or
   wasn't written. Re-run `sponsio plugin init --force`.
2. The Sponsio CLI version is older than the libraries' rule shapes.
   Update with `pip install -U sponsio`.

For an unbundled plugin you scanned in step 3b, do the same with one
of its tool names plus a pattern you expect to block.

## Step 6 — tell the user to reload the plugin

If the user is in a live Claude Code session, the plugin needs to
re-pick up the manifest. Tell them to run `/reload-plugins` and
confirm it shows `sponsio-claude-code`.

## Common configuration adjustments

**Operator wants observe mode (log, don't block) — pilot rollout:**

```bash
export SPONSIO_GUARD_MODE=observe
```

This dial only affects this plugin; other Sponsio integrations in the
same shell still respect `SPONSIO_MODE` independently.

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

## Troubleshooting

**"My rule looks right but the deny doesn't fire."**

Most common causes:

1. **Wrong tool name.** The contract uses `mcp__github__delete_repo`
   but the actual tool is `mcp__github__delete_repository`. The
   stateless hook silently no-ops on non-matches (correct behaviour
   — we can't deny on a tool we have no rule for). Confirm the real
   name from `claude mcp list` or by triggering the tool once and
   reading the PreToolUse event from
   `~/.claude/projects/.../*.jsonl`.
2. **Wrong routing.** Check the file actually lives at
   `~/.sponsio/plugins/<routed-id>/sponsio.yaml` where the routed id
   matches what `derive_plugin_id` would return for the tool name.
   `mcp__github__X` → `github`; `acme:fetch` → `acme`; bare `Bash` →
   `_host`.
3. **Rate / count rules don't fire on the first call.** The
   stateless prototype gets a fresh empty trace per hook. Until the
   daemon mode lands, only argument-level rules (`arg_blacklist`,
   `arg_value_range`, `scope_limit`, `arg_length_limit`,
   `dangerous_*`, `tool_allowlist`) reliably fire on a single call.
   `rate_limit`, `loop_detection`, `must_precede`, `cooldown` are all
   daemon-future.

## What you must not do

* **Do not** auto-install everything without asking. The user picks
  which starter libraries they want — don't push the whole bundle.
* **Do not** apply scan output without showing the dry-run first.
* **Do not** invent tool names. If the user can't list them, tell
  them you can't safely scan and suggest they list MCP tools from a
  live Claude session first.
* **Do not** use `--force` to overwrite a user-edited library without
  explicit consent.
* **Do not** edit files under `sponsio/contracts/*.yaml` inside the
  installed Sponsio package. Those are read-only shipped packs.
  User-level adjustments go in
  `~/.sponsio/plugins/<plugin>/sponsio.yaml`.
* **Do not** flip `SPONSIO_GUARD_MODE=observe` "to make the smoke
  test pass". A failing smoke test means something is actually
  broken; silencing it hides the bug.
