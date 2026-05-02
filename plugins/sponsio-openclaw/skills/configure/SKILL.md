---
name: configure
description: Use after installing the sponsio-openclaw plugin to wire the runtime end-to-end. The plugin install only registers hooks + skills; the contract library and per-environment overrides are configured here. Bootstraps the per-plugin contract library tree at ~/.sponsio/plugins/, generates fresh starter libraries for OpenClaw plugins / MCP servers via `sponsio plugin scan` (with `--introspect` to auto-discover tool inventory; the agent then applies the prompt from `sponsio plugin prompt openclaw` to extract semantic contracts using its own LLM context — no separate API call), tunes the shipped rules to the user's actual environment, and verifies with a smoke test. Use when the user says any of "configure sponsio-openclaw", "set up sponsio-openclaw", "first-time setup of sponsio for OpenClaw", "wire up sponsio in this OpenClaw session", "add Sponsio guardrails for my OpenClaw plugins", "tune the plugin for my environment", "the plugin is too strict / too loose", "calibrate sponsio rules", "scan this OpenClaw plugin", "generate sponsio rules for my OpenClaw plugin", "I just installed Y plugin / MCP server in OpenClaw, what should sponsio block", or asks how to make sponsio-openclaw actually block things.
---

# sponsio-openclaw — configure the host plugin

You are walking the user through configuring the **sponsio-openclaw**
plugin so it actually wraps tool calls in this OpenClaw session.
Without these steps, the plugin loads but every per-plugin library
is empty and every tool call passes through unguarded.

The plugin sends `host: "openclaw"` in its hook payload so the
runtime fallback library is `_host_openclaw` (OpenClaw canonical
tool names — `exec` / `read` / `write` / `edit` / `apply_patch` /
`web_fetch` / `send_message` — with `path` / `command` / `url`
params), not the Claude-Code-shaped `_host`.  Both libraries live
under the same `~/.sponsio/plugins/` tree.

## Prerequisites (check silently first)

Run:

```bash
sponsio --version
```

* Not found → tell the user to install: `pip install sponsio` (or
  `pip install -e ".[all]"` from a local clone).  Stop here until
  they confirm.
* Present → continue.

If the user owns the agent code and wants to wire Sponsio into their
own framework integration **instead of** running it as an OpenClaw
host plugin, delegate to the `sponsio` skill (`sponsio onboard .`).
This skill is only for the host-plugin case where the user is gating
tool calls inside an OpenClaw session.

## Routing rules from a policy document

Sponsio has two layers, and rules must land in the right YAML or they
do nothing. When the user hands you a policy document / instruction
file / "list of things the agent must not do" and asks you to encode
it, classify **each rule** before writing anywhere.

| Signal | → Layer 1 (this skill — write to `~/.sponsio/plugins/<id>/sponsio.yaml`) | → Layer 2 (delegate to `sponsio` skill — writes `<project>/sponsio.yaml`) |
|---|---|---|
| Tool names mentioned | `exec`, `read`, `write`, `edit`, `apply_patch`, `web_fetch`, `send_message` (OpenClaw primitives), `mcp__*` | tool names from the user's project's tool inventory |
| Path form | absolute or `~/...` paths outside the user's project | paths relative to the project (`src/...`) |
| Subject of the rule | "OpenClaw must not…", "the coding agent must not…" | "the loan agent must…", "the chatbot should…" |
| Domain language | shell, git, file system, MCP server primitives | AML, KYC, refund, PII, approval, faithfulness, hallucination |
| `./sponsio.yaml` exists in cwd | weaker signal — Layer 2 is in play, but rule may still be Layer 1 | stronger signal — most rules belong here |

Process:
1. For each rule, score by the signals above.
2. Route unambiguously-Layer-1 rules to this skill's flow.
3. For Layer-2 rules, **stop writing here** and tell the user
   "rules X, Y look like rules for the agent you're building, not
   the OpenClaw host — switching to the `sponsio` skill (`sponsio
   onboard`) for those". Do not silently dump them into a host-plugin
   YAML.
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

This creates `~/.sponsio/plugins/_host/sponsio.yaml` (Claude-Code-
shape, mostly inert in an OpenClaw session) and
`~/.sponsio/plugins/_host_openclaw/sponsio.yaml` (OpenClaw-shape,
the one that actually fires for OpenClaw fallback tools — `exec`
fork-bombs, dotenv reads, SSH-key writes, `~/.clawdbot/.env`
exfiltration patterns, etc.).  Show the user the output verbatim;
if the smoke test fails, **stop** and surface the error — the
install is broken.

If the file already exists (re-running setup), the command prints
`…already exists. Re-run with --force to overwrite.`  Informational,
not an error.

## Step 2 — enumerate the user's plugins / MCP servers

You need to know which plugins / MCP servers the user actually has
installed before deciding which contract libraries to author.  Two
discovery paths:

* **OpenClaw plugins** — read `~/.openclaw/plugins.json` or the
  equivalent the user's install uses.  If they don't know, ask them
  to list registered plugins from their OpenClaw config.
* **MCP servers** — same conventions as Claude Code; look for `.mcp.json`
  in any registered plugin's directory.

There are no bundled OpenClaw starter libraries today (Claude Code
ships `github` / `filesystem` / `playwright` because those MCP
servers were prioritised first).  Every OpenClaw plugin goes through
**Step 3** below.

## Step 3 — generate per-plugin libraries via `sponsio plugin scan`

Three discovery paths in priority order:

### 3.1 — `--introspect` (preferred for MCP servers)

sponsio spawns the server, does the JSON-RPC `initialize` +
`tools/list` handshake, and auto-populates the tool inventory along
with parameter schemas.  Read the plugin's manifest to find the
spawn command, then:

```bash
sponsio plugin scan \
  --plugin-id <name> \
  --target-host openclaw \
  --introspect "<spawn-cmd>"
```

For OpenClaw, ALWAYS pass `--target-host openclaw` so the generated
contracts use **flat** tool names (matching how OpenClaw surfaces
them) rather than Claude Code's `mcp__<plugin>__` prefixed shape.

### 3.2 — Static-tool list

When the plugin doesn't run an MCP server (e.g. an OpenClaw skill
that exposes tools through the SDK directly), pass tool names via
`--tools`:

```bash
sponsio plugin scan <plugin-dir> --tools tool_a,tool_b,tool_c \
  --target-host openclaw
```

### 3.3 — Operator-supplied tool list

Last resort — ask the user "what tools does this plugin expose?"
and pass them via `--tools`.  Use only when introspection fails or
the plugin isn't structured as an MCP server.

### 3.4 — Dry-run

```bash
sponsio plugin scan --plugin-id <id> --target-host openclaw \
  --introspect "<spawn-cmd>"
```

Output has two parts:

1. **Heuristic library yaml** (one per routed group) — name-pattern
   rules covering destructive verbs, rate-limit / loop-detection
   caps.  Deterministic floor.

2. **Tool inventory JSON** under `# === tool inventory ... ===` —
   every tool's `name`, `description`, `input_schema`, plus
   `tool_name_in_contracts` (flat for OpenClaw — no `mcp__` prefix).
   This is the *input* for the agent's own contract extraction.

### 3.5 — Apply the contract-extraction prompt

The heuristic engine catches obvious cases by name; the agent
(you) fills semantic gaps by reading each tool's description +
input_schema.  No API call needed — you ARE the LLM the prompt is
written for.

1. Get the prompt:
   ```bash
   sponsio plugin prompt openclaw
   ```

2. Apply it to the JSON tool inventory from Step 3.4.  Output a
   JSON object:
   ```json
   {"contracts": [{"desc": "...", "pattern": "...", "args": [...]}]}
   ```

3. Translate to YAML and merge into the heuristic library.  Mark
   each semantic contract with `source: agent-extracted` for
   later traceability.

### 3.6 — Review every contract

ALWAYS start with the dry-run.  **Never** `--apply` until the user
has seen the output.

For each contract proposed, state:

* **What it blocks** — translate the regex / cap into plain English.
* **Why** — point at the heuristic (`starter_irreversible`,
  `starter_bash`, `starter_rate_limit`, `starter_loop`) or the LLM's
  `desc`.  LLM proposals carry `source: plugin-scan-llm` for
  traceability.
* **What it doesn't catch** — be explicit about generic rules so
  the user doesn't assume coverage they don't have.

If a rule is wrong, drop it from the rendered yaml or add an
`overrides:` block:

```yaml
overrides:
  - match: { desc: "<offending desc>" }
    disabled: true
```

### 3.7 — Apply

Once the user is happy with the dry-run:

```bash
sponsio plugin scan <plugin-dir> --target-host openclaw \
  --introspect "..." --apply
```

This writes one yaml per routed group under
`~/.sponsio/plugins/<plugin-id>/`.

### 3.8 — show the user what got loaded

After every successful apply, render the contract digest so the
user sees what's now enforced before any later tuning:

```bash
sponsio plugin show <plugin-id>
```

The digest groups rules by category (hard denies, rate limits, arg
blocks, …). **Surface it verbatim** — paraphrasing strips the
detail the operator needs to spot misroutes. `sponsio plugin
install <bundled-name>` already calls this digest internally; for
`scan --apply`, you call it manually.

## Step 4 — tune the rules

Walk through three tuning axes the user should answer before
flipping to enforce:

### 4.1 — workspace path

OpenClaw's `capability/filesystem` and `incident/openclaw` packs
use `<workspace>/` as path-allowlist root.  If the user wants those
included (they're NOT in the default `_host_openclaw` to avoid the
no-workspace-set crash), add:

```yaml
agents:
  _host_openclaw:
    workspace: /Users/<them>/projects/<repo>
    include:
      - sponsio:capability/shell
      - sponsio:capability/filesystem
      - sponsio:incident/openclaw
```

### 4.2 — environment profile

| Profile | Adjustment |
|---|---|
| Local dev | leave defaults |
| Staging | enable `audit_after` on destructive tools |
| Production | move `delete_*` from `rate_limit 0` to assumption-gated (require explicit `confirm_reconfirmed`) |
| Regulated / PII | tighten sto rules — `core/universal`'s β from 0.95 → 0.99 |

### 4.3 — known-false-positive overrides

Common cases:

| Rule | When false-positives | Override |
|---|---|---|
| `_host_openclaw` "Block reads of dotenv secrets" | dotenv rotators, secret-rotation agents | `disabled: true` for `read` only (keep `write`) |
| `incident/openclaw` "navigation must not target internal hosts" | testing one's own internal app | replace with allowlist of actual internal hostnames |

## Step 5 — verify the deny path

Run a synthetic event through the hook:

```bash
echo '{"hook_event_name":"PreToolUse","tool_name":"exec","tool_input":{"command":"rm -rf /"},"host":"openclaw"}' \
  | sponsio plugin guard --stdin
```

Expect: a JSON deny payload on stdout.  If empty:

1. Library at `~/.sponsio/plugins/_host_openclaw/sponsio.yaml` is
   missing or wrong — re-run `sponsio plugin init --force`.
2. Sponsio CLI version is older than the libraries' rule shapes —
   `pip install -U sponsio`.

For a per-plugin library you scanned in step 3, do the same with one
of its tool names + a pattern you expect to block.

## Step 6 — tell the user to reload the plugin

OpenClaw runtime needs to re-pick up the manifest.  The exact reload
mechanism depends on the OpenClaw version — typically restarting
the OpenClaw runtime or running its plugin-reload command.  Confirm
the plugin is loaded before testing further.

## Common configuration adjustments

**Operator wants observe mode (log, don't block):**

```bash
export SPONSIO_GUARD_MODE=observe
```

This dial only affects this plugin; other Sponsio integrations in
the same shell still respect `SPONSIO_MODE` independently.

**Operator wants per-plugin overrides instead of editing the library:**

```yaml
agents:
  <plugin-id>:
    contracts: [...shipped...]
    overrides:
      - match: { desc: "<rule desc>" }
        disabled: true
```

## Troubleshooting

**"My rule looks right but the deny doesn't fire."**

Most common causes:

1. **Wrong tool name shape.**  Did you use `--target-host openclaw`?
   With `claude-code` the contract gets `mcp__<plugin>__<tool>` —
   that won't match OpenClaw's flat names.  Re-scan with the right
   target.
2. **Wrong routing.**  Check the file lives at
   `~/.sponsio/plugins/<routed-id>/sponsio.yaml` where the routed id
   matches what `derive_plugin_id` would return.  For OpenClaw flat
   names that don't match a namespace, fallback is `_host_openclaw`,
   not `_host`.
3. **Rate / count rules don't fire on the first call.**  The
   stateless prototype gets a fresh empty trace per hook.  Until
   daemon mode lands, only argument-level rules (`arg_blacklist`,
   `arg_value_range`, `scope_limit`, `arg_length_limit`,
   `dangerous_*`) reliably fire on a single call.

## What you must not do

* **Do not** auto-apply scan results without showing the dry-run.
* **Do not** invent tool names.  If introspection fails and the user
  can't list them, say so — don't fabricate.
* **Do not** use `--force` on a user-edited library without explicit
  consent.
* **Do not** set `--target-host claude-code` for an OpenClaw user —
  the rules won't match.
* **Do not** edit files under `sponsio/contracts/*.yaml` inside the
  installed Sponsio package.  User-level adjustments go in
  `~/.sponsio/plugins/<plugin>/sponsio.yaml`.
* **Do not** flip `SPONSIO_GUARD_MODE=observe` "to make the smoke
  test pass".  A failing smoke test means something's broken;
  silencing it hides the bug.
