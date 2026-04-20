---
name: sponsio
description: Audit an LLM agent's tools and code for runtime safety risks, then generate formal behavioral contracts that enforce those safety properties at runtime. Use this when the user asks to audit, scan, analyze, or harden an agent; check tool configurations for risks like data leaks, unguarded writes, or missing confirmations; generate or refine a sponsio.yaml contract file; or translate natural-language safety policies into enforceable contracts. Triggers on phrases like "audit my agent", "scan for safety", "what could go wrong with these tools", "generate contracts", or "make my agent safer".
---

# Sponsio — Agent Safety Audit & Contract Generation

This skill runs Sponsio's scan/validate pipeline on an agent's source code and turns the raw YAML output into a plain-language safety report plus actionable contracts.

Sponsio is an existing Python CLI tool. This skill does NOT reimplement its logic — it orchestrates `sponsio scan` / `sponsio validate`, parses the YAML output, and uses the LLM to explain findings.

## When to use this skill

Trigger when the user:
- Asks to audit, scan, or harden an LLM agent / agentic system
- Asks "what could go wrong" with a set of tools or an agent configuration
- Wants to generate or improve a `sponsio.yaml` contract file
- Has a natural-language policy (PDF/markdown) and wants to turn it into enforceable contracts
- Mentions safety, guardrails, compliance, or behavioral constraints for an LLM agent

Do NOT trigger for:
- General LLM-safety conversations that aren't about a specific codebase
- Requests to review non-agent code (normal linting, correctness, etc.)

## Prerequisites — check before running

Run this check silently before the main workflow:

```bash
sponsio --version
```

- If `sponsio` is not found → check whether the user is working from a local clone:
  - **Published install** (when available on PyPI): `pip install sponsio[all]`
  - **Local development**: `pip install -e ".[all]"` from the repo root
- If version is missing LLM deps, the `--llm` flag will still work but emit a warning; proceed anyway.

If the user wants LLM-enhanced extraction, check for an API key:
```bash
[ -n "$OPENAI_API_KEY" ] || [ -n "$GEMINI_API_KEY" ] || [ -n "$GOOGLE_API_KEY" ] && echo OK
```
If no key is set, fall back to rule-based scanning (still useful — still covers the core safety checks).

## Where contracts come from (3 sources → 1 YAML)

Every contract in a Sponsio project lives in a single `sponsio.yaml`. That YAML is filled from exactly three sources, possibly mixed:

| # | Source | What it is | How to produce it |
|---|---|---|---|
| **1** | **Extraction** | Sponsio auto-derives candidate contracts from artifacts you already have (source code, policy docs, MCP tool lists). | `sponsio scan <paths> [--llm] [--policy <doc>]` |
| **2** | **User input** | You write a contract yourself — either directly as a YAML entry, or as an NL sentence that Sponsio parses into a pattern. | Hand-edit `sponsio.yaml`, or `sponsio validate "tool \`X\` must precede \`Y\`"` to check before pasting. |
| **3** | **Contract library** | Pre-built, parameterized templates shipped with Sponsio. You pick a template from the catalog and fill in the arguments. | `sponsio patterns` lists 29 det + 7 sto templates across 5 categories. A per-user accumulation also exists in `~/.sponsio/patterns.json` (`PatternStore`). |

**The three sources converge on the same file.** A `sponsio.yaml` can (and often should) contain contracts from all three — e.g. extracted safety baselines from `scan`, a library-picked `rate_limit` with a custom N, and a hand-written domain-specific rule.

The skill's job is to (a) figure out which sources the user wants to pull from, (b) route to the right CLI command for each, and (c) merge results into the same YAML (using `--append` when adding to existing files).

## Core workflow

### Step 0 — Quick path: existing `sponsio.yaml`

**Before doing anything else**, check if the user already has a `sponsio.yaml` in the current directory or mentioned one explicitly. If they just want it explained, validated, or reviewed — skip all extraction and jump straight to Step 3 + Step 4:

```bash
# Quick path — no scan needed
sponsio validate --config sponsio.yaml --json
```

Read the JSON output, then produce the report (Step 4). This is the fastest path and should be the default when the user says things like "explain my contracts", "check my sponsio.yaml", or "what does this config do".

### Step 1 — Decide which of the 3 sources to use

If the quick path doesn't apply, figure out which source(s) the user needs. The decision rule is simple: **match the user's input to the source**.

| User has… | Use source(s) | Commands |
|---|---|---|
| An existing `sponsio.yaml` and wants it checked/explained | None — skip extraction | Quick path above |
| Python agent code | Extraction (code) | `sponsio scan <paths>` (+ `--llm` if key available) |
| A compliance / policy document | Extraction (policy) | `sponsio scan <paths> --policy <doc> --llm` |
| An NL constraint they wrote themselves | User input | `sponsio validate "<NL>"` to check, then append to YAML |
| Knows what pattern they want but not the syntax | Contract library | `sponsio patterns` to list, then hand-craft the YAML entry |
| Nothing — just exploring | Contract library | `sponsio patterns` to show the catalog; offer to seed a starter YAML interactively |

Users with **multiple sources** are common (and encouraged). Process them in order: library/user-input → extraction (so extraction can `--append` to the file instead of overwriting).

If the route is ambiguous, ask ONE question: "Do you want me to (a) scan your code, (b) extract from a policy document, or (c) start from the pattern library?"

### Step 2 — Gather concrete inputs

Once sources are chosen, pin down the arguments:
- **For extraction**: source paths (`@tool` / `Agent(tools=...)` / `graph.add_node(...)` locations), optional policy doc(s).
- **For user input**: the NL sentences themselves, one per line.
- **For library**: the pattern name(s) from `sponsio patterns` plus the concrete args (e.g. `must_precede(check_policy, issue_refund)`).
- **Target file**: either a new `sponsio.yaml` or an existing one to extend (use `-o <path> --append`).

If the user says "scan my project" without specifics, prefer the narrower path. Ask: "Which file or directory contains the agent? Scanning the whole repo will include test fixtures and config."

### Step 3 — Run the scan

Run `sponsio scan` with sensible defaults. Write output to the **current working directory** (not `/tmp`) so it persists and is visible to the user:

```bash
# Rule-based only (fast, no API key)
sponsio scan <PATHS> --agent <AGENT_NAME> -o ./sponsio_scan.yaml --no-push

# With LLM inference (richer, if API key available)
sponsio scan <PATHS> --agent <AGENT_NAME> --llm -o ./sponsio_scan.yaml --no-push

# With policy document
sponsio scan <PATHS> --policy <POLICY.md> --llm -o ./sponsio_scan.yaml --no-push
```

Key flags:
- `--no-push` — suppresses the dashboard push (skill runs should be side-effect-free)
- `-o <path>` — writes YAML to an explicit path; use `./sponsio_scan.yaml` by default, or `./sponsio.yaml` if the user wants the final config directly
- `--agent <name>` — sets the agent ID; use the filename stem by default

### Step 4 — Validate the generated YAML

Run validate to confirm every contract in the YAML parses cleanly into a formal pattern:

```bash
sponsio validate --config ./sponsio_scan.yaml --json
```

Read the JSON output. The shape is:
```json
{
  "contracts": [
    {
      "nl": "tool `A` must precede `B`",
      "ok": true,
      "type": "det",
      "pattern": "must_precede",
      "formula": "Or(U(Not(Atom('called', 'B')), Atom('called', 'A')), G(Not(Atom('called', 'B'))))",
      "agent": "customer_bot",
      "section": "guarantees"
    }
  ],
  "ok": true
}
```

Key fields per entry:
- `ok` — `true` if the NL compiled into a formal pattern, `false` otherwise
- `type` — `"det"` (hard, LTL) or `"sto"` (soft, scorer) or `"unknown"` / `"error"` on failure
- `pattern` — matched pattern name (e.g. `"must_precede"`)
- `formula` — LTL formula repr (det only)

Failures usually mean the NL phrasing didn't match a known pattern — surface these to the user as "ambiguous constraints" rather than swallowing them.

### Step 5 — Translate findings into a human-readable report

Read `./sponsio_scan.yaml` and the validate JSON. Produce a report in this exact structure:

```markdown
## Agent Safety Report — <agent_name>

### Summary
<1-2 sentences: how many tools were found, how many contracts were generated, overall risk posture>

### Key Risks Identified
<For each risk flagged by scan, 1 bullet. Translate the technical pattern name into plain language.>

- **Unguarded writes**: `update_user` can run without calling `get_user` first. Risk: updating the wrong record.
- **External communication without confirmation**: `send_email` has no `confirm_send` gate. Risk: spam or accidental disclosure.
- **No rate limiting on mutations**: Financial tool `issue_refund` has no rate cap. Risk: repeat-call abuse.

### Contract Sources
<One bullet per source that fed the YAML, so the user can weigh confidence.>

- **Extraction**: N contracts derived from code/policy. Note if any came from LLM vs heuristic — LLM-derived ones deserve extra review.
- **User input**: N contracts the user wrote or validated manually.
- **Contract library**: N contracts picked from the pattern catalog and parameterized.

Use the `source:` field inside each `E:` block (`scan` / `policy` when present) to attribute. If no source field is present, the contract is either user-written or library-picked — say "source: user/library" rather than guessing.

### Generated Contracts (sponsio.yaml)
<Show the YAML block verbatim — users will copy it.>

### Contracts Explained
Each Sponsio contract is an **assumption-enforcement pair**: under the stated assumption(s), the enforcement must hold. Report every contract using this shape, even when the assumption is absent (state it as "unconditional"):

- **When**: `<assumption in plain language, or "unconditional">`
  **Then**: `<enforcement in plain language>`
  **Runtime effect**: `<what Sponsio does if violated — block / warn / retry / escalate>`

Examples:

- **When**: unconditional
  **Then**: the agent must call `get_user` before `update_user`.
  **Runtime effect**: any `update_user` call without a prior `get_user` in the trace is blocked.

- **When**: the agent has called `modify_order`
  **Then**: `get_order_details` must have happened at some point before.
  **Runtime effect**: if the assumption holds and the enforcement fails, the call is blocked and the agent is told why.

- **When**: unconditional
  **Then**: `send_email` is rate-limited to 5 per session.
  **Runtime effect**: the 6th call is blocked.

The assumption is not decoration — it defines **when the enforcement applies**. Don't collapse it into the enforcement sentence.

### Parsing Issues
<If validate JSON lists any entries with `"ok": false`, show them here so the user knows which lines need manual refinement. Otherwise omit this section.>

### Next Steps
1. Review the generated `sponsio.yaml`, remove any contracts you don't want.
2. Integrate into your agent:
   ```python
   import sponsio
   guard = sponsio.init(config="sponsio.yaml", framework="langgraph")
   agent = create_react_agent(model, guard.wrap(tools))
   ```
   All frameworks use `guard.wrap(tools)`:
   - **LangGraph**: `create_react_agent(model, guard.wrap(tools))`
   - **Agents SDK**: `Agent(tools=guard.wrap(tools))`
   - **CrewAI**: `Crew(tools=guard.wrap(tools))`
   - **Vercel AI**: `agent.run(model, messages, middleware=[guard.wrap()])`
   - **OpenAI SDK**: `patch_openai(contracts=[...])`
3. Run `sponsio serve` to see runtime enforcement in the dashboard.
```

Keep the report dense but scannable. Do NOT pad with disclaimers or encourage the user to "consult an expert" — they already are one (Sponsio is the expert).

### Step 6 — Offer to refine

End the report with a concrete follow-up question:
- If parsing issues exist: "Want me to rewrite those ambiguous contracts?"
- If policy document wasn't provided: "Do you have a compliance policy (PDF/markdown) I should fold in?"
- If scan was rule-only: "Want me to re-run with `--llm` for richer inference?"

Pick the ONE most useful question given what you saw. Don't list all three.

## Patterns the LLM should recognize

Sponsio ships 29 det (hard, LTL) patterns + 7 sto (soft, scorer) evaluators across 5 categories. When explaining to the user, translate pattern names into plain language.

### Core Temporal Patterns (14 det)

| Pattern | Plain-language meaning |
|---|---|
| `must_precede(A, B)` | A must be called before B can be called |
| `always_followed_by(A, B)` | Every call to A must eventually be followed by B |
| `no_reversal(A, B)` | Once A fires, B is permanently forbidden |
| `requires_permission(tool, perm)` | Agent must hold permission `perm` before calling tool |
| `no_data_leak(source, sink)` | Data read from source must not flow to sink |
| `mutual_exclusion(A, B)` | At most one of A, B may appear in the entire trace |
| `rate_limit(tool, N)` | Tool can be called at most N times per session |
| `idempotent(tool)` | Tool must not be called more than once |
| `deadline(trigger, action, N)` | Action must happen within N steps of trigger |
| `must_confirm(action)` | A confirmation tool must precede action |
| `cooldown(action, N)` | Minimum N steps between consecutive calls |
| `segregation_of_duty(A, B)` | Same agent can't perform both A and B |
| `bounded_retry(action, N)` | At most N retries allowed |
| `loop_detection(action, N)` | Max N consecutive calls to the same tool |

### Argument & Path Constraints (4 det)

| Pattern | Plain-language meaning |
|---|---|
| `arg_blacklist(tool, param, patterns)` | Tool's argument field must not match these regex patterns |
| `scope_limit(tool, paths)` | File-touching tool is restricted to these path prefixes |
| `arg_length_limit(tool, param, max)` | Argument field must not exceed max characters (blocks injection) |
| `data_intact(bound_tool, paths)` | Tool must only operate on original, unmodified data |

### OWASP Agentic Security Patterns (8 det)

| Pattern | Plain-language meaning |
|---|---|
| `destructive_action_gate(tool, role)` | Destructive tool needs human approval + role permission |
| `untrusted_source_gate(sources, sinks)` | After reading untrusted input, sinks need re-confirmation *(returns A,E pair)* |
| `required_steps_completion(trigger, steps)` | All required steps must follow trigger (liveness checklist) |
| `tool_allowlist(tools)` | Only tools in the allowlist may be called |
| `dangerous_bash_commands(forbidden)` | Preset: ban dangerous shell commands (rm -rf, sudo, etc.) |
| `dangerous_sql_verbs(tool, forbidden)` | Preset: ban dangerous SQL verbs (DROP, TRUNCATE, etc.) |
| `irreversible_once(action)` | Irreversible action may be called at most once |
| `confirm_after_source(source, action)` | After untrusted source, action needs confirmation *(returns A,E pair)* |

### Resource & Delegation Constraints (3 det)

| Pattern | Plain-language meaning |
|---|---|
| `token_budget(max_tokens, scope)` | Session token consumption must not exceed max |
| `arg_value_range(tool, field, min, max)` | Numeric argument must be within range |
| `delegation_depth_limit(max_depth)` | Agent-to-agent delegation chain limited to max depth |

### Soft Evaluators (7 sto)

| Evaluator | Plain-language meaning | Needs LLM? |
|---|---|---|
| `pii` | Response must not contain PII (SSN, email, etc.) | No (regex) |
| `length` | Response must be under N words/characters | No (count) |
| `format` | Output must be valid JSON/markdown/etc. | No (parse) |
| `content_prohibition` | Response must not contain forbidden terms | No (substring/regex) |
| `tone` | Response must match required tone/style | Yes |
| `relevance` | Response must be relevant to the topic | Yes |
| `llm_judge` | Generic policy compliance check | Yes |

(Full list: run `sponsio patterns` — shows all 29 det + 7 sto with examples.)

## YAML schema (what the skill reads back)

Sponsio's YAML accepts both **short keys** (`A` / `E` — recommended for terse hand-edited YAML) and **long keys** (`assumption` / `enforcement` — self-describing, matches the Python API). Mixing is fine across entries; using both forms of the same field in a single entry (e.g. both `A` and `assumption`) raises `ConfigError`. Every contract entry is an `(assumption, enforcement)` pair; if `assumption`/`A` is omitted the contract is unconditional.

```yaml
version: "1"

tools:                              # optional — tool inventory for grounding
  - name: <tool_name>
    description: "..."
    params: "..."

agents:                             # DICT (mapping), not a list
  <agent_id>:                       # e.g. customer_bot
    contracts:                      # LIST of contract entries
      # Form 1 — NL strings with backticks around tool names
      - A: "called `modify_order`"
        E: "must call `get_order_details` before `modify_order`"

      # Form 2 — assumption omitted (unconditional)
      - E: "tool `send_email` is rate-limited to 5 per session"

      # Form 3 — structured dict (what `sponsio scan` emits for det patterns)
      - E:
          pattern: must_precede
          args: [check_policy, issue_refund]
          source: scan              # "scan" or "policy"

      # Form 4 — list on either side (interpreted as AND)
      - A:
          - "called `modify_order`"
          - "verified_identity"
        E:
          - "U(Not(called(modify_order)), called(get_order_details))"
          - "tool `modify_order` at most 3 times"
```

Both `A` and `E` can each be:
- a scalar (single NL string)
- a list (logical AND of elements)
- a structured dict `{pattern, args, source?}`

**The skill MUST read both `A` and `E`** and pair them together in the report. Treating `A` as optional-informational is wrong — the assumption defines the contract's scope.

When `A` is absent, report the contract as "unconditional" — don't silently drop the dimension.

## Edge cases

- **No tools found**: The scan output will have an empty `contracts` list. Tell the user "I didn't find any tool decorators (`@tool`, `@function_tool`, or `Agent(tools=[...])`) in the scanned paths. Point me at the right file or widen the scope."
- **Scan hits a syntax error**: Surface the file + line from stderr. Don't try to fix the syntax — ask the user if they want to fix it first.
- **LLM flag requested but no key**: Run without `--llm` and prepend the report with: "Note: ran in rule-based mode because no `OPENAI_API_KEY` / `GEMINI_API_KEY` was found. Set one and re-run for richer inference."
- **Dashboard push fails**: Expected when dashboard isn't running. `--no-push` avoids this entirely; use it by default.

## What this skill does NOT do

Be honest about scope. This skill:
- Does NOT run the agent. It only analyzes static code + docs.
- Does NOT guarantee the generated contracts are complete. They are *proposals* derived from heuristics + LLM inference. The user should review.
- Does NOT handle runtime enforcement — that's `sponsio.init()` + the BaseGuard integrations, which the user wires into their agent code.

If the user asks for something out of scope (e.g., "also check my database schema"), say so and offer the closest thing this skill *can* do.

## Public API surface this skill depends on

For maintainers of Sponsio: this skill only uses these CLI interfaces. Internal refactors are safe as long as these stay stable:

1. **CLI commands + flags**:
   - `sponsio scan PATHS [--agent N] [--llm] [--policy P] [-o FILE] [--append] [--no-push]`
   - `sponsio validate --config FILE --json`
   - `sponsio validate "NL string" [--json]`
   - `sponsio patterns`
   - Exit code 0 on success, non-zero on fatal error.

2. **YAML schema (read side)** — all of the following must remain parseable:
   - Top-level `agents:` as a **dict** mapping agent_id → `{contracts: [...]}`.
   - Contract entries use either short keys (`A` / `E`) or long keys (`assumption` / `enforcement`); both are accepted. Mixing both forms of the same field in one entry raises `ConfigError`.
   - `A` / `assumption` is optional; `E` / `enforcement` is required.
   - Each field accepts three forms: scalar NL string, list of NL strings (AND), or structured dict `{pattern, args, source?}`.
   - NL strings use backtick-wrapped tool names: `` tool `name` ``.

3. **Pattern registry**: the pattern names referenced in the tables above must keep their semantics. Renaming a pattern is a breaking change for this skill.

4. **`validate --json` output shape**: the skill reads per-contract `ok`, `type`, `pattern`, and `formula` fields. Schema additions are fine; renaming these is breaking.

Any change to items 1–4 should bump Sponsio's minor version and update this SKILL.md. Internal changes to `score_tools`, `UnifiedExtractor`, `CodeAnalyzer`, or the pattern library's Python signatures are NOT part of this skill's contract and can evolve freely.
