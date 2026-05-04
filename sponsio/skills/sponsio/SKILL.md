---
name: sponsio
description: Install, observe, tune, enforce, and periodically refresh Sponsio — a runtime contract layer for LLM agents that blocks unsafe tool calls and scores output quality against declared rules. Use when the user wants to set up / add / install Sponsio, add guardrails or runtime safety to an LLM agent, generate or refine a sponsio.yaml, audit tool configurations for risks (data leaks, unguarded writes, missing confirmations), explain or review existing contracts, check what Sponsio would have blocked (`sponsio report`), refresh the contract library from recent traces (`sponsio refresh`), move from observe to enforce mode, or debug why a contract is (or isn't) firing. Triggers on phrases like "set up sponsio", "add sponsio", "install sponsio", "add guardrails", "monitor my agent", "harden my agent", "audit my agent", "generate contracts", "explain my sponsio.yaml", "sponsio report", "refresh contracts", "update my sponsio.yaml from traces", "flip to enforce", "false positive", "why is this rule firing".
---

# Sponsio — Agent Safety Lifecycle Companion

Sponsio is a Python/TypeScript runtime safety layer for LLM agents: it evaluates deterministic contracts against each tool call and can block (enforce) or just log (observe) violations. (LLM-judged stochastic contracts — tone, prompt-injection, semantic PII, scope respect — are a Sponsio Cloud feature available via `pip install sponsio[cloud]`; the OSS engine is det-only.) This skill covers the full lifecycle — first-time setup, contract authoring/review, observe-mode tuning, and flipping to enforce — by orchestrating Sponsio's CLI and explaining its output in plain language.

This skill does NOT reimplement Sponsio's logic; it calls the CLI and interprets results.

## When to use this skill

Dispatch by what the user is trying to do. Pick ONE workflow and follow it; do not run multiple workflows in one turn.

| User is… | → Workflow |
|---|---|
| Setting up Sponsio for the first time in a project ("add sponsio", "install sponsio", "add guardrails") | **W1 — Initial setup** (just dispatch ``sponsio init``) |
| Handing you a codebase and asking "what could go wrong?" / wants a fresh contract file from scratch / has a policy doc to encode | **W2 — Audit & refine** |
| Authoring contracts for a Claude Code / OpenClaw plugin or a bare MCP server (input is a plugin manifest, not source code) | **W2b — Plugin / MCP contracts** |
| Tightening rules that apply to Task-spawned subagents (Cursor / Claude Code) — they lack user context and need stricter privileges than the main agent | **W2c — Subagent privilege boundary** |
| Tuning the IDE's OWN host-plugin library (Claude Code's Bash / Read / Write / MCP gating; Cursor likewise) — different from the user's project sponsio.yaml | hand off to the ``sponsio-claude-code:configure`` skill (or the cursor analog).  Don't reimplement here. |
| Has Sponsio running in observe mode and wants to review violations, tune thresholds, silence false positives | **W3 — Tune in observe** |
| Wants to re-mine contracts from accumulated production traces / periodically maintain the library | **W3b — Refresh from traces** |
| Ready to ship — wants to move from observe to enforce, needs regression confidence | **W4 — Flip to enforce** |
| Sponsio errored, a rule isn't firing when it should, a rule is firing when it shouldn't | **W5 — Troubleshoot** |

Do NOT trigger for: general LLM-safety discussions not tied to a specific codebase; non-agent code review (linting, correctness).

## Prerequisites (run silently before any workflow)

```bash
sponsio --version
```

- Not found → install: `pip install sponsio` (or `pip install -e ".[all]"` from a local clone).
- For `--llm` inference, check: `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / `GOOGLE_API_KEY`. Absent → still proceed; AST-based extraction and all of W3/W4/W5 work with zero keys.

---

## Editing contract YAML — write rules by file

Sponsio contract YAMLs split into **two trust zones** with different
write rules. Pick the right zone before any edit; the runtime's
self-modify pack enforces the host-zone rules, but cross-zone slips
are still a config-correctness bug we'd rather avoid up-front.

### Zone A — project YAML (you may add additively)

Path: `<project>/sponsio.yaml` — the file `sponsio onboard` writes
into the user's repo. This evolves through every onboard / scan /
refresh cycle. **Adding** new contracts via `Edit` (extending
`old_string`) is the supported workflow.

Three legal write modes:

1. **Add a new contract** — `Edit` with `new_string` extending
   `old_string` (the invariant: `old_string ⊆ new_string`):

   ```text
   old_string  = the verbatim tail of the existing file ending at
                 the last contract entry (or the last `contracts:`
                 line if the list is empty)
   new_string  = old_string + "\n      - <new contract YAML block>"
   ```

2. **Tune an existing pack-shipped rule** — never edit the rule
   directly; append a `customized:` entry:

   ```yaml
   customized:
     - match: { desc: "<the shipped rule's desc, exact>" }
       A: "<extra assumption that narrows when it fires>"   # to relax
       # or args: [...]                                     # to retune thresholds
       # or disabled: true                                  # to silence (last resort)
   ```

3. **Run `sponsio scan`** for bulk additions from code / policy /
   traces — merges additively and writes atomically:

   ```bash
   sponsio scan <paths> -o ./sponsio.yaml --append
   sponsio refresh --since 7d --apply --mode add-only
   ```

### Zone B — host bucket + plugin bundle YAMLs (user-only — never write directly)

Paths:
- `~/.sponsio/plugins/{{HOST_BUCKET}}/sponsio.yaml`             (this host's runtime library)
- `~/.sponsio/plugins/{{HOST_BUCKET_SUBAGENT}}/sponsio.yaml`    (this host's subagent library)
- `~/.sponsio/plugins/<plugin-id>/sponsio.yaml`                 (per-plugin / per-MCP-server bundle — github, filesystem, my-plugin, …)

These files govern *your own future tool calls*. The runtime
self-modify pack blocks every Edit / Write / MultiEdit you'd attempt
against them — for the host bucket because rewriting your own rules
is privilege escalation, and for the per-plugin bundles for the same
reason (they constrain the plugin tools you'll call later). The
`{{HOST_BUCKET}}` placeholders above are baked in at skill install
time (`_host_cursor`, `_host_claude_code`, `_host_openclaw`, …) so
each host only loses write access to its own bucket.

**You must NOT** use `Edit`, `Write`, `MultiEdit`, or `Bash` with
shell redirects (`>`, `>>`, `tee`, `sed -i`, `cp`, `mv`, `rm`, `dd`)
on any path under `~/.sponsio/plugins/`. The legitimate update
paths are:

- **CLI** (you can run via `Bash`):
  ```bash
  sponsio plugin install <name>            # copy a fresh bundled starter
  sponsio plugin scan --apply              # regenerate per-plugin bundle
  sponsio plugin show <name>               # surface what's loaded
  ```
- **Hand-edit by the user** in their text editor.

For customizations the user agreed to during a tuning conversation, do
NOT ghostwrite the YAML they should paste. Contract content in
Zone B has exactly four legitimate sources: bundle libraries
(`sponsio plugin install`), CLI extraction (`sponsio plugin scan`,
`sponsio scan`, `sponsio onboard`), the user's own keystrokes, and
`customized:` blocks the user authors themselves. An LLM-composed
snippet has none of those provenances — it's configuration with no
audit trail.

The flow:

1. Restate the user's intent in plain English and identify the
   shipped rule it affects. `sponsio plugin show <id>` prints the
   `desc:` of every loaded rule; quote a desc verbatim from that
   output if you need to refer to one — do NOT compose YAML around
   it.
2. Tell the user the file path and describe the change in words
   ("add a `customized:` entry beside `contracts:` whose
   `match.desc` matches the rule you want to silence, with
   `disabled: true`"). Point them at the existing pack's syntax for
   reference. Let them write the YAML themselves.
3. When they say they're done, run
   `sponsio validate --config ~/.sponsio/plugins/<id>/sponsio.yaml`
   and help debug if it doesn't parse.

### Forbidden write modes (universal)

- **`Write` on any contract YAML above** — overwrites the whole
  file in one go; bypasses the additive evidence even when the
  result happens to be a superset.
- **`Edit` on a Zone-B path** — denied by the self-modify pack
  regardless of additive intent.
- **`Edit` on a Zone-A path where `new_string` does NOT contain
  `old_string`** — a modification or deletion masquerading as an
  edit; treat as forbidden.
- **`MultiEdit` on any contract YAML** — same shape as `Write`.
- **`Bash` with shell write operators** (`>`, `>>`, `tee`,
  `sed -i`, `cp`, `mv`, `rm`, `dd`) targeting any of these paths —
  the runtime blocks Zone B; treat Zone A the same way.

### Why this matters

The user's invariant: *adding* contracts is always allowed; *modifying* or *deleting* existing ones is not.  Following this protocol makes that invariant easy to see at the diff level (`old_string ⊆ new_string`).  When the user audits your edits later, "was anything removed or changed?" reduces to a string-containment check.

### If you genuinely need to remove a rule

You don't.  Use `customized: ... disabled: true` (an additive edit that silences the rule) or ask the user to delete it by hand.  The agent never has authority to remove its own contracts.

### Pattern generality — match operator intent, not demo data

When the operator's NL says "block public gists" or "cap files at 3", write the rule against the operator's **literal intent**.  Do not infer file extensions, content types, naming conventions, or path structures from sample data, demo fixtures, or examples you've seen — unless the operator explicitly named them.

Concrete: a regex like `(\.md"\s*:.*?){4,}` matches only keys ending in `.md`, which means a 4-file gist of `.json` / `.txt` / `.csv` / extension-less keys passes freely.  If the operator said "cap files at 3", the right form is `(\".+?\"\s*:\s*\{){4,}` — any 4+ keys.  Same principle for path globs (`/work/notes/.*\.md` vs `/work/notes/.+`), `arg_field_has` value patterns, and `match:` selectors.

If the operator's intent **IS** demo-specific ("block any `.md` dump from this notes plugin"), keep the narrow pattern but record the assumption in the contract `desc:` so a later reviewer sees the constraint instead of inferring a bug.

#### The flip side — don't broaden past what the operator named

The operator's NL also has a *verb scope*.  When a policy says "no destructive verbs against AWS" the agent must not emit `\baws\s+` (matches every `aws` invocation including `aws s3 ls` / `aws sts get-caller-identity`).  The regex must require **both** the provider **and** a destructive-verb token from the policy.

Concrete:

| Policy phrase | Wrong (overbroad) | Right (verb-anchored) |
|---|---|---|
| "no destructive AWS calls" | `\baws\s+` | `\baws\s+(rds\|s3api\|ec2)\s+(delete-\|terminate-)\w+\b` |
| "no destructive gcloud calls" | `\bgcloud\s+` | `\bgcloud\s+\w+\s+(delete\|destroy\|drain)\b` |
| "no destructive Railway control plane" | `api\.railway\.app` | `(curl\|http\|wget)[^\|;&]*-X\s+DELETE[^\|;&]*\bapi\.railway\.app\b` |
| "no DROP TABLE" | `DROP` | `\bDROP\s+TABLE\b` |

The shipped packs already follow this convention (see `sponsio:incident/cursor-railway-wipe`).  When you extract from a policy doc, mirror the pack style: every command-shape regex is `(host or tool prefix) AND (destructive verb the operator named)`.  Bare provider prefixes are false-positive factories — every routine `aws s3 ls` in a CI script becomes a violation.

If the operator named providers exhaustively in prose ("Railway, Fly, Render, Supabase, Vercel, Cloudflare, Heroku, AWS, GCP"), keep all named providers, each anchored to its own verb set.  Do not collapse them into a single broad alternation that drops the verb anchor.

---

## W1 — Initial setup

Goal: from "project has no Sponsio" to "agent runs under observe
mode with a sane contract file".

The CLI now has a one-shot wizard that covers the common path —
detect framework + IDEs, ask which to install, dispatch
``sponsio onboard`` / ``sponsio host install`` / ``sponsio skill
install`` accordingly, then verify.  W1 is just orchestrating
that wizard, not reimplementing it.

### Steps

1. Run the wizard.

   ```bash
   sponsio init                                                    # interactive TTY
   sponsio init --apply 'framework=<name>;ides=<ide>:<level>,...;mode=observe'
                                                                   # non-interactive
   ```

   Picks string format:
     - ``framework=<one of langgraph / langchain / crewai / openai /
       anthropic / claude_agent / openai_agents / google_adk /
       vercel_ai / mcp / none>``.  ``none`` is "bare loop, generic
       ``guard.guard_before/after`` wiring"; an empty
       ``framework=`` skips the onboard step entirely.
     - ``ides=<ide>:<level>,...`` per-IDE pick.  ``<level>`` is
       ``none`` / ``skill`` (drop SKILL.md only) / ``full`` (host
       hooks + SKILL.md, the canonical "protect this IDE" pick).
     - ``mode=observe|enforce``.  Default observe; enforce flips
       are W4's job.

   Use ``sponsio init --plan '<picks>'`` first to surface the
   exact commands the wizard will run, especially when you're
   non-interactive.  Surface ``sponsio init``'s output to the user
   verbatim — the panel + preview + recap blocks are designed to
   be readable as-is.

2. Patch the agent entry file (only when ``framework`` ≠ ``""``).

   ```bash
   sponsio onboard . --emit-context > /tmp/sponsio-onboard-context.json
   ```

   Read the JSON.  Pick the entry file in this priority:
     1. ``entry_file_candidates`` has exactly one strong match.
     2. Else dedupe ``tool_inventory[*].filepath`` — one file, use it.
     3. Else stop and ask the user.

   If the file already imports ``from sponsio.<adapter>`` (Python)
   or ``from "@sponsio/sdk"`` (TS), it's already wrapped — skip.
   Otherwise splice ``wrap_snippet`` from the JSON: imports +
   guard construction at the top, wrap site adapted to the file's
   actual idiom (canonical
   ``create_react_agent(model, guard.wrap(tools))`` if present,
   else adapt — e.g. ``tools_by_name = guard.wrap(TOOLS).tools_by_name``
   for a name-keyed dispatch loop).  Show the diff before writing.

3. Verify.

   ```bash
   sponsio doctor
   sponsio validate --config sponsio.yaml
   ```

   Surface every warning / fail line verbatim.  If ``sponsio doctor``
   FAILED (not warned, failed), stop here — don't let the user run
   their agent thinking the install is healthy when it isn't.

4. Explain observe mode explicitly: "Nothing is blocked on day 1.
   Every contract is still evaluated; violations are logged to
   ``~/.sponsio/sessions/<agent_id>/*.jsonl``.  Use ``sponsio report
   --since 24h`` after a day of real traffic to see what would have
   been blocked.  When you're ready to flip, that's W4."

### Beyond W1 — pointers

Onboarding is just install + wire + verify.  These are SEPARATE
workflows, not extensions of W1:

  - **Author tighter contracts** from a policy doc / threat model /
    actually-scanned codebase → **W2** (audit & refine).
  - **Tune host-plugin libraries** (Bash / Read / Write / MCP gating
    in this Claude Code or Cursor session) → invoke the
    ``sponsio-claude-code:configure`` skill (or the cursor analog).
    That skill owns ``sponsio plugin scan`` / ``sponsio plugin
    append`` / per-MCP-server library generation.  W1 doesn't
    duplicate it.
  - **Refresh contracts from accumulated traces** → **W3b**.
  - **Move from observe to enforce** → **W4**.
  - **Something doesn't fire / fires wrong** → **W5**.

### Choosing the write target — Zone A vs Zone B

``sponsio init``'s axes already encode the destination decision:

  - Axis 1 (framework wrap, picked at non-empty / non-``none``)
    → writes ``<project>/sponsio.yaml``.  This is **Zone A** —
    governs the LLM agent the user is wiring Sponsio INTO.  You may
    Edit/Write this file; ``sponsio validate --config <path>``
    after.
  - Axis 2 (per-IDE level=full) → writes
    ``~/.sponsio/plugins/_host_<ide>/sponsio.yaml`` via
    ``sponsio host install``.  This is **Zone B** — governs the
    HOST agent (i.e. you, Claude Code / Cursor).  Edit/Write/
    MultiEdit are denied by the self-modify pack; the only way to
    extend it from inside the host is ``sponsio plugin append``.

If a user later asks you to add rules and the destination is
ambiguous (project rules vs host-IDE rules), ask:

> "These rules — should they govern (a) the LLM agent your project
> deploys (Zone A: ``<project>/sponsio.yaml``), or (b) my own tool
> calls inside this IDE (Zone B: ``~/.sponsio/plugins/_host_<ide>/
> sponsio.yaml``)?"

Two destinations need two files; conflating them silently breaks
one or both.

### Adding rules to Zone B (host bucket) — `sponsio plugin append`

When a Zone-B addition is the right call, you can't Edit/Write the
host yaml directly.  Use the append CLI:

```bash
# 1. Write the new rules to a staging file OUTSIDE Zone B (project
#    root is fine).  Never name it sponsio.yaml — collides with
#    project-config.
sponsio plugin append --from <staging-path> --target <bucket> --dry-run
sponsio plugin append --from <staging-path> --target <bucket>
rm <staging-path>     # delete after success
```

``plugin append`` is structurally additive: it rejects anything
beyond ``contracts:`` entries — no ``customized:``, no
``disabled:``, no desc collisions, no top-level keys.  Tell the
user up front you'll be running this on their behalf so they're
not surprised when it appears.

### Auto-selected packs

`sponsio onboard` uses simple, conservative heuristics:

| Pack | Auto-included when… | Notes |
|---|---|---|
| `sponsio:core/universal` | Always | Empty stub on OSS — kept so existing `include:` lines don't error. The output-quality contracts (injection / jailbreak / toxic / PII / harm) live in `sponsio:core/llm_safety` and require Sponsio Cloud (`pip install sponsio[cloud]`) |
| `sponsio:core/runaway` | Framework runs a multi-step loop (langgraph/crewai/…) | token budget, delegation depth, loop detection; no LLM calls |
| `sponsio:capability/shell` | A tool name matches `{bash, shell, exec, execute, execute_command, run_command, run_shell, run_bash, terminal, subprocess}` | Auto-fills `tool_rename:` if the user's tool name isn't the canonical `exec` |
| `sponsio:capability/filesystem` | A tool name matches `{read, read_file, open_file, write, write_file, edit, edit_file, apply_patch, patch_file, ...}` | Auto-fills `tool_rename:` and `workspace:` |
| `sponsio:incident/openclaw` | Never auto-included — opt-in only | CVE-derived rules for a specific vendor incident |

`sponsio packs` lists all shipped packs with live rule counts and include specs.

### Do NOT

- Do NOT edit `sponsio/contracts/*.yaml` inside the installed package — those are the shipped packs; they're read-only. Adjustments go in the user's `sponsio.yaml` via `customized:` or `contracts:`.
- Do NOT flip `mode: enforce` during W1. The whole point of observe mode is to find false positives before they break production.

---

## W2 — Audit & refine (from scratch, or deepen an existing yaml)

Goal: produce or improve a `sponsio.yaml` from code / policy docs / traces, and explain every contract in plain language.

### Routing rules between layers

A policy document can mix **two kinds of rules** that land in different
YAMLs. Misrouting them (writing host rules to the project file, or
agent rules to the host bucket) means the rule loads but never fires.

Classify **each rule** before writing:

| Signal | → Project YAML (`./sponsio.yaml`) | → Host YAML (`~/.sponsio/plugins/{{HOST_BUCKET}}/sponsio.yaml`) | → Per-plugin YAML (`~/.sponsio/plugins/<plugin-id>/sponsio.yaml`) |
|---|---|---|---|
| Subject of the rule | "the loan agent must…", "the chatbot should…" | "Cursor / Claude Code / OpenClaw must not…" (host's own tools) | "the GitHub MCP server must not…" (a specific plugin/MCP) |
| Tool names mentioned | tool names from the user's project tool inventory | host primitives — `Bash`, `Edit`, `Write`, `Read`, `exec` | namespaced — `mcp__github__*`, `mcp__filesystem__*`, … |
| Path form | paths relative to the project (`src/...`) | absolute / `~/...` paths outside the user's project | paths the named MCP server operates on |
| Domain language | AML, KYC, refund, PII, approval, faithfulness, hallucination | shell, git, file system, FS primitives | the plugin's domain (issues, PRs, repos for github; pages, paths for filesystem) |
| Trigger | the agent under development is the subject | the IDE / coding agent is the subject | a specific plugin or MCP server is the subject |

Process:
1. Score each rule by the signals above.
2. Write each rule to its target YAML using the additive-only protocol
   in the previous section.
3. For genuinely ambiguous rules ("PII must not leak"), ask the user
   *which agent* the rule is about before picking a target.
4. After writing per-plugin rules, run `sponsio plugin show <plugin-id>`
   and surface the digest verbatim so the user sees what's now active.

The default failure mode is to dump everything into the file you
were already editing. Cross-layer leakage is a worse user error than
the extra clarification round.

### Decide which sources to use

Sponsio contracts come from four sources, mixable in one yaml:

| # | Source | What it is | Command |
|---|---|---|---|
| 1 | **Shipped packs** | Pre-built, parameterized rule sets (`sponsio:core/universal`, `sponsio:capability/shell`, …) | Hand-add `include: [sponsio:<spec>]` — or W1's `onboard` does it automatically |
| 2 | **Extraction** | AST + optional LLM inference from your code / policy docs / execution traces | `sponsio scan <paths> [--llm] [--policy <doc>] [-t <trace-glob>]` |
| 3 | **User input** | An NL sentence or a structured dict the user writes | Hand-edit `sponsio.yaml`; validate a single NL string with `sponsio validate "<NL>"` |
| 4 | **Pattern library** | Deterministic parameterized templates (`rate_limit`, `must_precede`, `arg_blacklist`, …) — full list via `sponsio patterns`. Stochastic templates (`injection_free`, `tone`, `llm_judge`, …) live in Sponsio Cloud | `sponsio patterns` to browse; hand-write the YAML entry |

Match the user's input to the source(s):

- "Explain / review my `sponsio.yaml`" → source 1 and/or others already applied; jump to "Explain contracts" below.
- "Scan my agent code" → source 2, code-only.
- "We have a security policy document" → source 2, add `--policy <doc> --llm`.
- "We already have session logs / OTLP traces" → source 2, add `-t '<glob>'` (trace mining; no LLM needed).
- "I know the pattern I want but not the syntax" → source 4, then show them the yaml entry.

If ambiguous: ask ONE question — "(a) scan your code, (b) extract from a policy document, or (c) mine from traces?"

### Run scan (when extraction is needed)

You ARE the LLM.  Use the agent-mediated path — Sponsio collects deterministic inputs, you do the cognition, Sponsio validates.  No `--llm` / API key needed.

```bash
# 1. Sponsio dumps the deterministic inputs (AST tool inventory, policy
#    docs, trace summary, existing yaml) as JSON:
sponsio scan <PATHS> --agent <AGENT> [--policy <doc>] [-t '<traces>'] --emit-context

# 2. Sponsio prints the contract-authoring prompt template:
sponsio prompt scan
```

Read both, apply the prompt to the JSON in your own context, and produce contract YAML entries.  Source-tag every entry you author with `source: agent-extracted` so future `sponsio refresh` can re-consider them.  Trace-mined entries (when `-t` was passed) carry `source: trace` and are the subset W3b maintains over time.

**Decide the write target by intent BEFORE writing**, using the W1 step-4 (A) vs (B) test.  Write path differs by destination:

#### If destination is project YAML — `<project>/sponsio.yaml`

Write via Edit/Write, then validate:

```bash
sponsio validate --config ./sponsio.yaml
```

#### If destination is host bucket — `~/.sponsio/plugins/{{HOST_BUCKET}}/sponsio.yaml`

**Edit / Write / MultiEdit on this path are denied by Zone B's self-modify pack.**  Use ``sponsio plugin append`` — a structurally-additive CLI that writes through validated Python:

1. Write the YAML you produced (above) to a transient staging file outside Zone B (e.g. `<project>/.sponsio.staging.yaml` or `/tmp/sponsio-host-<timestamp>.yaml`).  Never name it `sponsio.yaml` — that collides with project-config and triggers the wrong-destination anti-pattern.

2. Dry-run the merge to confirm what would land:

   ```bash
   sponsio plugin append --from <staging-path> \
     --target {{HOST_BUCKET}} --dry-run
   ```

3. Apply.  The CLI rejects anything beyond additive `contracts:` entries — no `customized:`, no `disabled:`, no desc collisions with existing rules, no top-level `runtime:` / `judge:` / `include:` smuggling.  It validates the merged file via the loader before declaring success.

   ```bash
   sponsio plugin append --from <staging-path> --target {{HOST_BUCKET}}
   ```

4. Delete the staging file once `plugin append` reports success — keeping it around invites accidental re-apply.

This is the only way an in-host agent can extend its own governance file from inside the host.  Direct shell redirects (`cat >> ...`) are blocked by the self-modify pack; the append CLI is the explicit, audited bypass.

**Bare-CLI fallback** (only when no host agent is driving — you're being run from a CI shell or one-shot script):

```bash
sponsio scan <PATHS> --policy <doc> --llm \
  -o ~/.sponsio/plugins/{{HOST_BUCKET}}/sponsio.yaml --append
```

`--llm` uses the configured API key.  Skip this when you (the host agent) are already in the loop — `plugin append` is faster and free.

**Anti-pattern (do NOT do this).**  Writing the contracts to `<project>/sponsio.yaml` and adding a comment block that says "Cursor's hooks read `~/.sponsio/plugins/_host_cursor/sponsio.yaml`; copy this file there".  That comment is an admission you wrote to the wrong place.  Use the staging file + `plugin append`, not a fake project yaml.

### Validate existing yaml (explain-only path)

```bash
sponsio validate --config ./sponsio.yaml --json
```

JSON shape per entry: `{nl, ok, type: "det"|"sto"|"unknown", pattern, formula, agent, section}`. `ok: false` means the NL didn't match any pattern — surface those as "ambiguous — needs refinement".

### Explain contracts (use this as the report template)

```markdown
## Sponsio contract summary — <agent>

**Setup**: <N> contracts total, <sources breakdown>. Mode: <observe|enforce>.

### Active packs
- `sponsio:core/universal` (empty stub on OSS — judge-based output safety lives in `sponsio:core/llm_safety` and requires Sponsio Cloud)
- `sponsio:capability/shell` (11 det) — guards on <tool name after rename>
<...>

### Contracts explained
Each contract is an assumption → enforcement pair. State the assumption explicitly even when absent ("unconditional"), because the assumption defines **when** the enforcement applies.

- **When**: the agent has called `modify_order`
  **Then**: `get_order_details` must have happened earlier.
  **Runtime effect**: if unmet, the call is blocked (or logged, in observe) and the agent is told why.

- **When**: unconditional
  **Then**: `send_email` is called at most 5 times per session.
  **Runtime effect**: the 6th call is blocked.

### Ambiguous contracts (optional — only if validate flagged any)
- `"..."` — didn't match any pattern; rephrase or rewrite as a structured dict.

### Next steps
1. Review; prune anything that doesn't fit the agent's real job.
2. Run in observe mode for 1–2 days.
3. `sponsio report --since 24h` to see what would have been blocked (W3).
4. Prune false positives, then flip `mode: enforce` (W4).
```

Keep it dense. Don't pad with "consult an expert" disclaimers — the user is already using the expert.

---

## W2b — Author plugin / MCP-server contracts

Goal: write a Sponsio contract library for the tools a Claude Code plugin or OpenClaw plugin (or a bare MCP server) exposes.  This is the analogue of W2 but the input is a plugin manifest + MCP `tools/list` introspection rather than your own agent's source code.

You ARE the LLM — same agent-mediated pattern as W1 / W2.

### Steps

1. Get the tool inventory from the plugin / MCP server.

   ```bash
   # Claude Code plugin directory (reads .claude-plugin/plugin.json):
   sponsio plugin scan <plugin-dir>

   # OpenClaw plugin directory (reads openclaw.plugin.json):
   sponsio plugin scan <plugin-dir>

   # Bare MCP server — introspect it directly via tools/list:
   sponsio plugin scan --plugin-id <id> --introspect 'python3 server.py'
   sponsio plugin scan --plugin-id <id> --introspect 'npx -y @org/mcp-server'

   # Or list tools by hand if you already know them:
   sponsio plugin scan --plugin-id <id> --tools 'mcp__foo__list,mcp__foo__delete'
   ```

   Output is a starter YAML built from name-heuristics — useful as a baseline, but the **semantic** rules need you.

2. Get the host-specific contract-authoring prompt:

   ```bash
   sponsio plugin prompt claude-code       # mcp__<plugin>__<tool> naming
   sponsio plugin prompt openclaw          # flat tool names
   sponsio plugin prompt mcp-bare          # generic, no host-specific assumptions
   ```

   The prompt pins the tool naming convention for the target host, the destructive-verb / outbound-side-effect / path-arg playbook, and the same pattern vocabulary the rest of Sponsio uses.

3. **You** apply the prompt to the tool inventory in your own LLM context.  Tool descriptions + `inputSchema` are the load-bearing inputs — read them carefully, especially for destructive verbs (`delete_*`, `drop_*`, `revoke_*`, `transfer_*`) and outbound side-effects (`send_*`, `post_*`, `publish_*`, `create_issue_comment`).

4. Write the YAML to the per-plugin library so the host runtime hook will pick it up:

   ```text
   ~/.sponsio/plugins/<plugin-id>/sponsio.yaml
   ```

   (`<plugin-id>` matches what `sponsio.guard_stdin.derive_plugin_id` would compute from the tool name — `mcp__github__*` → `github`, `<server>__<tool>` on OpenClaw → `<server>`. First-party host tools route to the per-host bucket: `{{HOST_BUCKET}}` for main-agent calls, `{{HOST_BUCKET_SUBAGENT}}` for Task-spawned subagent calls.)

5. Validate:

   ```bash
   sponsio validate --config ~/.sponsio/plugins/<plugin-id>/sponsio.yaml
   ```

### When to use each prompt template

| Context | Prompt |
|---|---|
| Plugin will be loaded by Claude Code (tool calls show up as `mcp__<plugin>__<tool>`) | `sponsio plugin prompt claude-code` |
| Plugin will be loaded by OpenClaw (flat names like `read`, `write`, `exec`) | `sponsio plugin prompt openclaw` |
| Cursor MCP, custom host, or unknown — tool names show up as the MCP server reports them | `sponsio plugin prompt mcp-bare` |

Cursor's MCP tools surface to its agent loop with the same MCP-bare names; use the `mcp-bare` template when authoring contracts for them, then `sponsio host install cursor` wires the runtime hook so violations are enforced before Cursor executes the call.

---

## W2c — Subagent privilege boundary

Goal: ensure tool calls from Task-spawned subagents are subject to a stricter rule library than the main agent.  This applies to both Cursor (`Task` tool) and Claude Code (also `Task` tool, with hook payloads carrying an `agent_id`).

### Why a separate library

A subagent is dispatched with a *task description*, not the full user conversation.  It can't perform the alignment-style refusal "wait, the user didn't ask for this" — so the same Bash/Write call that the main agent might pause on tends to fire silently from a subagent.  Sponsio routes subagent-context tool calls to `{{HOST_BUCKET_SUBAGENT}}` instead of `{{HOST_BUCKET}}`, applying the same shell / database / credentials baseline plus the `capability/subagent` pack which blocks side-effects whose authorisation should travel back through the main conversation (git mutations, package installs, outbound network, system-path writes).

### How routing happens

| Host | Signal | Library |
|---|---|---|
| Claude Code | hook payload has non-empty `agent_id` field | `~/.sponsio/plugins/{{HOST_BUCKET_SUBAGENT}}/sponsio.yaml` |
| Cursor | `subagentStart` event recorded the conversation_id; later `preToolUse` calls match against the registry at `~/.sponsio/cursor-subagents.jsonl` | same library |

Each host has its own per-host subagent bucket — this skill copy was materialised under the host's skill dir with `{{HOST_BUCKET_SUBAGENT}}` baked in. `sponsio host install <host>` bootstraps both the main and subagent buckets on first run.

### Steps

1. Verify the subagent library exists:

   ```bash
   ls ~/.sponsio/plugins/{{HOST_BUCKET_SUBAGENT}}/sponsio.yaml
   ```

   If missing, re-run `sponsio host install <host>` — the installer auto-bootstraps it.

2. Read the shipped baseline + decide what to add:

   ```bash
   cat ~/.sponsio/plugins/{{HOST_BUCKET_SUBAGENT}}/sponsio.yaml
   sponsio packs | grep capability/subagent
   ```

   The shipped pack already covers most cross-cutting subagent risks.  Project-specific tightening goes in this file's `contracts:` block.

3. Common subagent-specific contracts worth adding:

   - **`Bash` allowlist** — subagents typically only need read/search; deny everything not on a small list (`grep`, `find`, `ls`, `cat`, `git log`, …).
   - **`Write` / `Edit` to anything outside `<workspace>/.sponsio-subagent/`** blocked.  Subagents should return findings, not write files.
   - **No outbound network from subagents** — `curl`, `wget`, `httpie` denied unconditionally.
   - **No git mutations** — `git commit`, `git push`, `git reset --hard` denied (the main agent re-runs them on the user's blessing).
   - **No package installs** — `pip install`, `npm install`, `apt install` denied.

4. Validate after editing:

   ```bash
   sponsio validate --config ~/.sponsio/plugins/{{HOST_BUCKET_SUBAGENT}}/sponsio.yaml
   ```

### Cursor caveat

Cursor's `subagentStart` event fires once per Task spawn; Sponsio records the subagent's `conversation_id` to `~/.sponsio/cursor-subagents.jsonl`.  If you wipe or rotate that file mid-session, in-flight subagent calls fall back to `{{HOST_BUCKET}}` (the main bucket).  Override the registry path via `SPONSIO_CURSOR_SUBAGENT_REGISTRY` if you need test isolation.

---

## W3 — Tune in observe

Goal: look at what Sponsio logged during observe mode, decide which violations are real vs false positives, and adjust `sponsio.yaml` accordingly. **Never flip mode during this workflow.**

### Steps

1. Pull the violation report:

   ```bash
   sponsio report --agent <AGENT> --since 24h
   ```

2. For each violation cluster:
   - **Real violation** → leave the contract as-is. Note it for the user.
   - **False positive** → adjust `sponsio.yaml`. Use this decision tree, **routing by the contract's source first** — `customized:` is a tool for shipped-pack rules you can't reach, not a default response to noise:

     | Source of the noisy rule | Situation | Edit |
     |---|---|---|
     | `source: policy` (extracted from the user's policy doc) | The regex is too broad / wrong threshold | **Fix it at the source**: edit the `contracts:` entry directly, **or** edit the policy doc and rerun `sponsio scan --policy <doc> --append` |
     | `source: agent-extracted` / `source: scan` / hand-authored under `contracts:` | The rule is wrong | Edit `contracts:` directly |
     | `pack_source: sponsio:...` (shipped pack pulled in via `include:`) | Too strict globally | Add `customized: - match: {desc: "..."}` with tuned `args:` |
     | `pack_source: sponsio:...` | Doesn't apply to this agent at all | `customized: - match: {...}, disabled: true` |
     | `pack_source: sponsio:...` | Threshold is wrong (e.g. `rate_limit` N) | `customized: ..., args: [<tool>, <new_N>]` |
     | Any source | Conflicts with a legitimate workflow Sponsio couldn't infer | Weaken via an `A:` (assumption) so it only fires in the specific unsafe context |

   The default failure mode is reaching for `customized:` on a rule the user authored themselves (directly, or through their own policy doc).  Don't do that — `customized:` for self-authored content is a paper-over that detaches the runtime behavior from the source-of-truth document.  Fix the source.

   Use `desc`, `pattern`, `pack_source`, or `source` as the `match:` key (see the YAML schema below).

3. Do NOT edit a contract you haven't seen a violation for. "It looks strict" isn't a reason to remove it in observe mode — it's the reason to leave it.

4. After each edit, verify the yaml still parses: `sponsio validate --config sponsio.yaml`.

### Using assumptions to relax (the right way)

When a rule is correct in principle but too broad in practice, prefer adding an `A:` over disabling:

```yaml
customized:
  - match: { desc: "Each exec call needs its own confirm_reconfirmed" }
    A: "called `confirm_reconfirmed`"
```

With the assumption, the rule stays silent until the integration actually emits the marker — it's vacuous-true without the precondition, enforced once the precondition holds. This is the preferred pattern for "I want this rule eventually but not today".

---

## W3b — Refresh from traces

Goal: treat `sponsio.yaml` as a **living library**, not a one-shot output. As real session logs accumulate, re-mine them to discover new patterns and retire stale ones, without clobbering anything the user wrote or tuned.

### When to run

- Weekly / sprint boundary — recommended cadence for an agent seeing live traffic.
- After a material behavior change (new tools, new workflow, new integration).
- Before flipping to enforce (W4) — catches late-arriving trace-sourced rules that weren't present in the original scan.

### Steps

You ARE the LLM here too.  Use the agent-mediated path: Sponsio mines deterministic candidates from the trace, you decide which to keep / drop / adjust in your own context.

1. Dry-run first. Always.

   ```bash
   sponsio refresh --since 7d --emit-traces
   sponsio prompt refresh
   ```

   The first dumps recent trace events + the current `source: trace` rules as JSON; the second prints the merge / dedup / drift prompt.  Apply the prompt to the JSON in your context — you produce a structured diff per agent.  Show the user the diff in this shape:

   ```
   Agent: support_bot
     + new      must_precede(validate_payment, charge_card)
     ~ drifted  rate_limit(send_email, 5) → args [send_email, 12]
     - stale    idempotent(list_users)  (not re-observed)
     = 8 unchanged (source: trace, re-observed)
     = 12 preserved (user / scan / policy / customized — not touched)
   ```

2. Review with the user. For each bucket:
   - `+ new` — "the agent started doing X that your current yaml doesn't cover." User usually wants this.
   - `~ drifted` — threshold moved. If new value is bigger, the rule was too tight; if smaller, production tightened up. User decides.
   - `- stale` — rule hasn't fired in this window. **Not necessarily dead** — could just be rare. Conservative default is `--mode add-only` (never remove), which we recommend for the first few refreshes until the user trusts the window size.
   - `= preserved` — these include every user rule, `source: scan`, `source: policy`, and anything under `customized:`. The count being non-zero is the load-bearing invariant: refresh **only** ever changes `source: trace` entries.

3. Apply via Edit/Write.  You produced the YAML changes in step 1 in your own context — write them to `sponsio.yaml`, back the old file up to `sponsio.yaml.sponsio.bak`, then validate:

   ```bash
   sponsio validate --config sponsio.yaml
   ```

   **Fallback** (bare CLI, no host agent): `sponsio refresh --since 7d --apply [--mode add-only|replace-trace]` — uses Sponsio's own LLM via API key.  Only that path needs a key.

   **Comments in the YAML are not preserved** — the backup is how users recover any prose annotations they'd inlined. Warn them of this before running.

### Mode selection

| Situation | Mode |
|---|---|
| First time running refresh, or small trace window | `add-only` (never removes) |
| Healthy trace volume, stable agent behavior | `replace-trace` (default — recent traces are authoritative) |
| Window just covers a launch / migration / incident (abnormal traffic) | Skip — artifact traces mislead the miner |

### Do NOT

- Do NOT run refresh on traces from an agent still in early iteration. Wait until the workflow is stable; otherwise every sprint invalidates the library.
- Do NOT remove `source: scan` / `source: policy` entries in the yaml by hand just because refresh doesn't touch them — they represent knowledge refresh can't reconstruct (code AST, policy docs). If you think one is wrong, edit `customized:`, don't delete.

---

## W4 — Flip to enforce

Goal: move from observe to enforce with regression confidence — no more logging, actual blocking. **This is a production change**; don't skip the checks.

### Steps

1. **Regression test on real traces.** Pick a representative trace from `~/.sponsio/sessions/<agent>/` and replay:

   ```bash
   sponsio check --trace <trace.jsonl> --config sponsio.yaml --agent <agent>
   ```

   Exit 0 = every contract passes on this trace. Non-zero = something would have been blocked. If non-zero, go back to W3.

2. **Flip the mode.** Two options:

   a. Pin in yaml (permanent):
      ```yaml
      runtime:
        mode: enforce
      ```

   b. Flip via env var (reversible, no code change):
      ```bash
      SPONSIO_MODE=enforce python -m your_agent
      ```

   Precedence: explicit ctor arg > env > yaml > default.

3. **Keep the dashboard.** In production, point OTEL export at Sponsio's collector (`POST /api/otel/v1/traces`) or keep using `~/.sponsio/sessions/*.jsonl` + `sponsio report`. Don't run `sponsio serve --dev` in prod — that's a dev tool.

4. Tell the user how to roll back: `SPONSIO_MODE=observe` without redeploying.

---

## W5 — Troubleshoot

Common symptoms and their fixes.

### "LLM extraction call failed: cannot import name 'genai' from 'google'"

User has `google-generativeai` not `google-genai`. Fix:
```bash
pip install -U 'sponsio[llm]'
```

### "ConfigError: include 'sponsio:...': pack must define exactly one agent named '*' (the template)"

The pack file's `agents:` uses a literal agent name instead of `"*"`. If it's a user pack, rename. If it's a shipped pack, that's a Sponsio bug — file an issue.

### "ConfigError: Agent '<x>': pattern '<y>' uses the '<workspace>/' placeholder but the agent has no 'workspace:' set."

Add `workspace: /path/to/project/root` under the agent in `sponsio.yaml`. The `filesystem` and `openclaw` packs need this.

### "A rule I expected to fire isn't firing."

Check in this order:

1. `sponsio validate --config sponsio.yaml --json` — does the contract parse? `ok: false` means it was silently dropped.
2. The tool name in the contract matches the actual tool name the agent calls (LangGraph `node_id` vs the `@tool` Python name is a common trap; see `tool_rename:`).
3. The rule has an `A:` (assumption) that isn't holding. Look at the trace — is the precondition ever true?
4. The rule is a `sto` pattern (`injection_free`, `tone_*`, `semantic_pii_free`, …). The OSS engine ships no evaluator for those — `pip install sponsio[cloud]` to activate the judge pipeline. Without Cloud, OSS rejects the contract loudly at config load.

### "A rule is firing that shouldn't."

Jump to W3. Don't disable in a panic — add a targeted `customized:` entry with a `match:` clause, so the adjustment is traceable.

### "`sponsio onboard` detected the wrong framework."

`onboard` doesn't currently take a `--framework` override flag. Fall back to the manual two-step:

```bash
sponsio init --agent <id>
sponsio scan <paths> --agent <id> -o sponsio.yaml
```

Then hand-apply the integration snippet for your framework from the "Integration snippets" section below.

---

## YAML schema (what contracts look like)

Every contract is an `(assumption, enforcement)` pair. Both short keys (`A` / `E`) and long keys (`assumption` / `enforcement`) are accepted; mixing both forms of the same field in one entry raises `ConfigError`.

```yaml
version: "1"

agents:
  <agent_id>:                        # dict keyed by agent_id, NOT a list
    workspace: /path/to/project      # required by packs that use <workspace>/

    include:                         # pre-built packs (see W1 "Auto-selected packs")
      - sponsio:core/universal
      - sponsio:capability/shell

    tool_rename:                     # map the pack's canonical names to the
      exec: run_bash                 # host's actual tool names
      read: read_file

    customized:                      # tune shipped packs without forking them
      - match: { desc: "Each exec call needs its own confirm_reconfirmed" }
        A: "called `confirm_reconfirmed`"
      - match: { pattern: rate_limit, args: [send_email, 5] }
        args: [send_email, 20]
      - match: { pack_source: sponsio:incident/openclaw, desc: "..." }
        disabled: true

    contracts:                       # hand-written + scan-inferred contracts
      # NL form (short keys)
      - A: "called `modify_order`"
        G: "must call `get_order_details` before `modify_order`"
      # Omit A for unconditional
      - E: "tool `send_email` is rate-limited to 5 per session"
      # Structured dict (what scan emits for det patterns)
      - E:
          pattern: must_precede
          args: [check_policy, issue_refund]
          source: scan

runtime:
  mode: observe                      # "observe" | "enforce"
  dashboard: http://localhost:8000   # URL | true | false | null

judge:                               # Sponsio Cloud only — required when any include uses sto patterns
  provider: openai                   # openai | anthropic | gemini
  model: gpt-4o-mini
```

Both `A` and `E` accept: scalar NL string, list (AND), or structured dict `{pattern, args, source?}`.

Placeholders rewritten at include-time: `<workspace>/` (from agent's `workspace:`), `<agent>` (from agent id).

---

## Pattern reference

Full list: `sponsio patterns` (deterministic templates only). LLM-judged stochastic templates are a Sponsio Cloud feature.

### Core Temporal (14 det)
| Pattern | Meaning |
|---|---|
| `must_precede(A, B)` | A must be called before B |
| `always_followed_by(A, B)` | Every A must eventually be followed by B |
| `no_reversal(A, B)` | Once A fires, B is permanently forbidden |
| `requires_permission(tool, perm)` | Agent must hold `perm` before `tool` |
| `no_data_leak(source, sink)` | Data from source must not reach sink |
| `mutual_exclusion(A, B)` | At most one of A, B per trace |
| `rate_limit(tool, N)` | Tool at most N calls per session |
| `idempotent(tool)` | Tool at most once |
| `deadline(trigger, action, N)` | Action within N steps of trigger |
| `must_confirm(action)` | A confirmation tool must precede action |
| `cooldown(action, N)` | Min N steps between consecutive calls |
| `segregation_of_duty(A, B)` | Same agent can't perform both |
| `bounded_retry(action, N)` | At most N retries |
| `loop_detection(action, N)` | Max N consecutive calls |

### Argument & Path (4 det)
`arg_blacklist(tool, param, patterns)` · `scope_limit(tool, paths)` · `arg_length_limit(tool, param, max)` · `data_intact(tool, paths)`

### OWASP Agentic (8 det)
`destructive_action_gate` · `untrusted_source_gate` (returns A,E pair) · `required_steps_completion` · `tool_allowlist` · `dangerous_bash_commands` · `dangerous_sql_verbs` · `irreversible_once` · `confirm_after_source` (returns A,E pair)

### Resource & Delegation (3 det)
`token_budget(max_tokens)` · `arg_value_range(tool, field, min, max)` · `delegation_depth_limit(max_depth)`

### Soft Evaluators
LLM-judged stochastic atoms (`tone_*`, `relevance`, `llm_judge`, `injection_free`, `semantic_pii_free`, `scope_respect`, …) ship in Sponsio Cloud (`pip install sponsio[cloud]`). The OSS engine refuses to load them at config time with a clear pointer. Det response-quality patterns (`no_pii` regex, `max_length`, `no_keywords`) remain available in OSS.

---

## Integration snippets (paste into your agent entry file after onboard)

All frameworks share the same `sponsio.yaml`; only the wiring at the agent entry point differs.

**LangGraph** (Python):
```python
from sponsio.langgraph import Sponsio
guard = Sponsio(agent_id="support_bot", config="sponsio.yaml")
graph = guard.wrap_graph(graph)
```

**OpenAI Agents SDK** (Python):
```python
from sponsio.openai_agents import Sponsio
guard = Sponsio(agent_id="support_bot", config="sponsio.yaml")
agent = Agent(tools=guard.wrap(tools))
```

**Claude Agent SDK / CrewAI**: analogous — `from sponsio.claude_agent import Sponsio` / `from sponsio.crewai import Sponsio`, then `guard.wrap(tools)`.

**No framework** (bare LLM calls):
```python
from sponsio import Sponsio
guard = Sponsio(agent_id="support_bot", config="sponsio.yaml")
guard.guard_before(tool_name="...", tool_args={...})   # before the call
guard.guard_after(output="...")                         # after
```

**TypeScript** (`@sponsio/core`):
```ts
import { Sponsio } from "@sponsio/core";
const guard = new Sponsio({ agentId: "support_bot", config: "sponsio.yaml" });
await guard.guardBefore({ toolName: "...", toolArgs: {...} });
```

---

## What this skill does NOT do

- Does NOT run the agent. It orchestrates the CLI and explains results.
- Does NOT guarantee contracts are complete — they're proposals from heuristics + LLM; the user reviews.
- Does NOT modify the user's code at all — the wrap snippet is printed for the user (or their coding assistant) to paste into the agent entry file.

If asked for something out of scope (e.g., "also check my DB schema"), say so and offer the closest thing Sponsio can do.

---

## Public API surface (for Sponsio maintainers — do not break)

This skill only uses these. Internal refactors are safe as long as these stay stable.

1. **CLI**: `sponsio onboard`, `sponsio scan PATHS [--agent N] [--llm] [--policy P] [-t GLOB] [-o FILE] [--append]`, `sponsio refresh [-c FILE] [-a AGENT] [-t GLOB] [--since DUR] [--mode add-only|replace-trace] [--apply]`, `sponsio validate [--config FILE | "NL string"] [--json]`, `sponsio check --trace FILE --config FILE --agent ID`, `sponsio report --agent ID --since DUR`, `sponsio doctor`, `sponsio patterns`, `sponsio packs`, `sponsio skill install [--tool cursor|claude|codex|both|auto]`. Exit 0 on success.
2. **YAML**: top-level `agents:` as dict; each agent has optional `include:` / `tool_rename:` / `customized:` / `workspace:` and required `contracts:`; top-level `runtime:` and `judge:`.
3. **Patterns**: names in the table above keep their semantics. Renaming is a breaking change for this skill.
4. **`validate --json` shape**: per-contract `ok` / `type` / `pattern` / `formula` / `agent`.
5. **`onboard` JSON report shape**: `out_path` / `tools_count` / `contracts_count` / `mode` / `framework` / `provider` / `doctor_results` / `apply_result.diff`.
6. **Session log path**: `~/.sponsio/sessions/<agent_id>/*.jsonl` — `sponsio report` / `sponsio check --trace` both read this.

Changes to 1–6 should bump Sponsio's minor version and update this SKILL.md. Changes to `UnifiedExtractor`, `CodeAnalyzer`, pattern Python signatures, or other internals are NOT part of this skill's contract.
