---
name: sponsio
description: Install, observe, tune, enforce, and periodically refresh Sponsio — a runtime contract layer for LLM agents that blocks unsafe tool calls and scores output quality against declared rules. Use when the user wants to set up / add / install Sponsio, add guardrails or runtime safety to an LLM agent, generate or refine a sponsio.yaml, audit tool configurations for risks (data leaks, unguarded writes, missing confirmations), explain or review existing contracts, check what Sponsio would have blocked (`sponsio report`), refresh the contract library from recent traces (`sponsio refresh`), move from observe to enforce mode, or debug why a contract is (or isn't) firing. Triggers on phrases like "set up sponsio", "add sponsio", "install sponsio", "add guardrails", "monitor my agent", "harden my agent", "audit my agent", "generate contracts", "explain my sponsio.yaml", "sponsio report", "refresh contracts", "update my sponsio.yaml from traces", "flip to enforce", "false positive", "why is this rule firing".
---

# Sponsio — Agent Safety Lifecycle Companion

Sponsio is a Python/TypeScript runtime safety layer for LLM agents: it evaluates deterministic + stochastic contracts against each tool call and can block (enforce) or just log (observe) violations. This skill covers the full lifecycle — first-time setup, contract authoring/review, observe-mode tuning, and flipping to enforce — by orchestrating Sponsio's CLI and explaining its output in plain language.

This skill does NOT reimplement Sponsio's logic; it calls the CLI and interprets results.

## When to use this skill

Dispatch by what the user is trying to do. Pick ONE workflow and follow it; do not run multiple workflows in one turn.

| User is… | → Workflow |
|---|---|
| Setting up Sponsio for the first time in a project ("add sponsio", "install sponsio", "add guardrails") | **W1 — Initial setup** |
| Handing you a codebase and asking "what could go wrong?" / wants a fresh contract file from scratch / has a policy doc to encode | **W2 — Audit & refine** |
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

## W1 — Initial setup

Goal: from "project has no Sponsio" to "agent runs under observe mode with a sane contract file", in one command.

### Steps

1. Run the one-shot entry point:

   ```bash
   sponsio onboard . --apply
   ```

   `onboard` detects framework (langgraph / langchain / crewai / openai_agents / claude_agent / vercel-ai / no-framework), picks an LLM provider if available, auto-selects contract packs (see "Auto-selected packs" below), writes `sponsio.yaml` in **observe** mode, and with `--apply` patches the agent entry file with a two-line wrap (backup at `.sponsio.bak`). Falls back to printing the patch snippet if the framework isn't auto-patchable.

2. After `onboard` finishes, show the user three things — do not skip any:
   - The generated `sponsio.yaml` (read it back and summarize: packs included, tools renamed, mode).
   - The applied patch (from `report.apply_result.diff`) or the printed snippet.
   - Any `sponsio doctor` warn/fail lines.

3. Explain observe mode explicitly: "Nothing is blocked on day 1. Every contract is still evaluated; violations are logged to `~/.sponsio/sessions/<agent_id>/*.jsonl` and (if a dashboard is configured) pushed there. Use `sponsio report --since 24h` after a day of real traffic to see what would have been blocked."

4. If `sponsio doctor` failed (not warned, failed), stop and surface it — don't let the user run their agent thinking the install is healthy when it isn't.

### Auto-selected packs

`sponsio onboard` uses simple, conservative heuristics:

| Pack | Auto-included when… | Notes |
|---|---|---|
| `sponsio:core/universal` | Always | sto output-quality (injection / jailbreak / toxic / PII / harm); needs `judge:` configured |
| `sponsio:core/runaway` | Framework runs a multi-step loop (langgraph/crewai/…) | token budget, delegation depth, loop detection; no LLM calls |
| `sponsio:capability/shell` | A tool name matches `{bash, shell, exec, execute, execute_command, run_command, run_shell, run_bash, terminal, subprocess}` | Auto-fills `tool_rename:` if the user's tool name isn't the canonical `exec` |
| `sponsio:capability/filesystem` | A tool name matches `{read, read_file, open_file, write, write_file, edit, edit_file, apply_patch, patch_file, ...}` | Auto-fills `tool_rename:` and `workspace:` |
| `sponsio:incident/openclaw` | Never auto-included — opt-in only | CVE-derived rules for a specific vendor incident |

`sponsio packs` lists all shipped packs with live rule counts and include specs.

### Do NOT

- Do NOT edit `sponsio/contracts/*.yaml` inside the installed package — those are the shipped packs; they're read-only. Adjustments go in the user's `sponsio.yaml` via `overrides:` or `contracts:`.
- Do NOT flip `mode: enforce` during W1. The whole point of observe mode is to find false positives before they break production.

---

## W2 — Audit & refine (from scratch, or deepen an existing yaml)

Goal: produce or improve a `sponsio.yaml` from code / policy docs / traces, and explain every contract in plain language.

### Decide which sources to use

Sponsio contracts come from four sources, mixable in one yaml:

| # | Source | What it is | Command |
|---|---|---|---|
| 1 | **Shipped packs** | Pre-built, parameterized rule sets (`sponsio:core/universal`, `sponsio:capability/shell`, …) | Hand-add `include: [sponsio:<spec>]` — or W1's `onboard` does it automatically |
| 2 | **Extraction** | AST + optional LLM inference from your code / policy docs / execution traces | `sponsio scan <paths> [--llm] [--policy <doc>] [-t <trace-glob>]` |
| 3 | **User input** | An NL sentence or a structured dict the user writes | Hand-edit `sponsio.yaml`; validate a single NL string with `sponsio validate "<NL>"` |
| 4 | **Pattern library** | 29 det + 7 sto parameterized templates | `sponsio patterns` to browse; hand-write the YAML entry |

Match the user's input to the source(s):

- "Explain / review my `sponsio.yaml`" → source 1 and/or others already applied; jump to "Explain contracts" below.
- "Scan my agent code" → source 2, code-only.
- "We have a security policy document" → source 2, add `--policy <doc> --llm`.
- "We already have session logs / OTLP traces" → source 2, add `-t '<glob>'` (trace mining; no LLM needed).
- "I know the pattern I want but not the syntax" → source 4, then show them the yaml entry.

If ambiguous: ask ONE question — "(a) scan your code, (b) extract from a policy document, or (c) mine from traces?"

### Run scan (when extraction is needed)

```bash
# AST-only (fast, no keys):
sponsio scan <PATHS> --agent <AGENT> -o ./sponsio.yaml

# + LLM inference:
sponsio scan <PATHS> --agent <AGENT> --llm -o ./sponsio.yaml

# + policy doc:
sponsio scan <PATHS> --policy <POLICY.md> --llm -o ./sponsio.yaml

# + trace mining (works on OTLP/JSON, OTLP JSONL, native, Sponsio session logs):
sponsio scan <PATHS> -t '~/.sponsio/sessions/<agent>/*.jsonl' -o ./sponsio.yaml
```

Scan auto-validates before writing; only contracts that parse cleanly are saved. Source-tagged with `source: scan` / `source: policy` / `source: trace`. The `source: trace` subset is the one `sponsio refresh` maintains over time (W3b).

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
- `sponsio:core/universal` (5 sto) — judge-based output safety
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

## W3 — Tune in observe

Goal: look at what Sponsio logged during observe mode, decide which violations are real vs false positives, and adjust `sponsio.yaml` accordingly. **Never flip mode during this workflow.**

### Steps

1. Pull the violation report:

   ```bash
   sponsio report --agent <AGENT> --since 24h
   ```

2. For each violation cluster:
   - **Real violation** → leave the contract as-is. Note it for the user.
   - **False positive** → adjust `sponsio.yaml`. Use this decision tree:

     | Situation | Edit |
     |---|---|
     | Pack-shipped rule is too strict globally | Add an `overrides: - match: {desc: "..."}` entry with tuned args |
     | Pack-shipped rule doesn't apply to this agent at all | `overrides: - match: {...}, disabled: true` |
     | Rule's threshold is wrong (e.g. rate_limit N) | `overrides: ..., args: [<tool>, <new_N>]` |
     | Rule conflicts with a legitimate workflow that Sponsio couldn't infer | Weaken via an `A:` (assumption) so it only fires in the specific unsafe context |
     | User's own hand-written contract is wrong | Edit `contracts:` directly |

   Use `desc`, `pattern`, `pack_source`, or `source` as the `match:` key (see the YAML schema below).

3. Do NOT edit a contract you haven't seen a violation for. "It looks strict" isn't a reason to remove it in observe mode — it's the reason to leave it.

4. After each edit, verify the yaml still parses: `sponsio validate --config sponsio.yaml`.

### Using assumptions to relax (the right way)

When a rule is correct in principle but too broad in practice, prefer adding an `A:` over disabling:

```yaml
overrides:
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

1. Dry-run first. Always.

   ```bash
   sponsio refresh --since 7d
   ```

   Prints a structured diff per agent:

   ```
   Agent: support_bot
     + new      must_precede(validate_payment, charge_card)
     ~ drifted  rate_limit(send_email, 5) → args [send_email, 12]
     - stale    idempotent(list_users)  (not re-observed)
     = 8 unchanged (source: trace, re-observed)
     = 12 preserved (user / scan / policy / overrides — not touched)
   ```

2. Review with the user. For each bucket:
   - `+ new` — "the agent started doing X that your current yaml doesn't cover." User usually wants this.
   - `~ drifted` — threshold moved. If new value is bigger, the rule was too tight; if smaller, production tightened up. User decides.
   - `- stale` — rule hasn't fired in this window. **Not necessarily dead** — could just be rare. Conservative default is `--mode add-only` (never remove), which we recommend for the first few refreshes until the user trusts the window size.
   - `= preserved` — these include every user rule, `source: scan`, `source: policy`, and anything under `overrides:`. The count being non-zero is the load-bearing invariant: refresh **only** ever changes `source: trace` entries.

3. Apply.

   ```bash
   sponsio refresh --since 7d --apply                 # default mode: replace-trace
   sponsio refresh --since 7d --apply --mode add-only # conservative: never remove
   ```

   Writes `sponsio.yaml` and backs the old file up to `sponsio.yaml.sponsio.bak`. **Comments in the YAML are not preserved** — the backup is how users recover any prose annotations they'd inlined. Warn them of this before running.

### Mode selection

| Situation | Mode |
|---|---|
| First time running refresh, or small trace window | `add-only` (never removes) |
| Healthy trace volume, stable agent behavior | `replace-trace` (default — recent traces are authoritative) |
| Window just covers a launch / migration / incident (abnormal traffic) | Skip — artifact traces mislead the miner |

### Do NOT

- Do NOT run refresh on traces from an agent still in early iteration. Wait until the workflow is stable; otherwise every sprint invalidates the library.
- Do NOT use `--apply` without first showing the dry-run diff.
- Do NOT remove `source: scan` / `source: policy` entries in the yaml by hand just because refresh doesn't touch them — they represent knowledge refresh can't reconstruct (code AST, policy docs). If you think one is wrong, edit `overrides:`, don't delete.

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
4. The rule is a `sto` pattern but `judge:` isn't configured, so it silently falls back to "pass".

### "A rule is firing that shouldn't."

Jump to W3. Don't disable in a panic — add a targeted `overrides:` entry with a `match:` clause, so the adjustment is traceable.

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

    overrides:                       # tune shipped packs without forking them
      - match: { desc: "Each exec call needs its own confirm_reconfirmed" }
        A: "called `confirm_reconfirmed`"
      - match: { pattern: rate_limit, args: [send_email, 5] }
        args: [send_email, 20]
      - match: { pack_source: sponsio:incident/openclaw, desc: "..." }
        disabled: true

    contracts:                       # hand-written + scan-inferred contracts
      # NL form (short keys)
      - A: "called `modify_order`"
        E: "must call `get_order_details` before `modify_order`"
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

judge:                               # required when any include uses sto
  provider: openai                   # openai | anthropic | gemini
  model: gpt-4o-mini
```

Both `A` and `E` accept: scalar NL string, list (AND), or structured dict `{pattern, args, source?}`.

Placeholders rewritten at include-time: `<workspace>/` (from agent's `workspace:`), `<agent>` (from agent id).

---

## Pattern reference

Full list: `sponsio patterns`. 29 det + 7 sto.

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

### Soft Evaluators (7 sto)
`pii` · `length` · `format` · `content_prohibition` (no LLM) · `tone` · `relevance` · `llm_judge` (LLM judge)

---

## Integration snippets (W1 fallback when `--apply` can't patch)

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
- Does NOT modify code outside the agent entry file (and only in W1 with `--apply`, with a backup).

If asked for something out of scope (e.g., "also check my DB schema"), say so and offer the closest thing Sponsio can do.

---

## Public API surface (for Sponsio maintainers — do not break)

This skill only uses these. Internal refactors are safe as long as these stay stable.

1. **CLI**: `sponsio onboard [--apply]`, `sponsio scan PATHS [--agent N] [--llm] [--policy P] [-t GLOB] [-o FILE] [--append]`, `sponsio refresh [-c FILE] [-a AGENT] [-t GLOB] [--since DUR] [--mode add-only|replace-trace] [--apply]`, `sponsio validate [--config FILE | "NL string"] [--json]`, `sponsio check --trace FILE --config FILE --agent ID`, `sponsio report --agent ID --since DUR`, `sponsio doctor`, `sponsio patterns`, `sponsio packs`, `sponsio skill install [--tool cursor|claude|codex|both|auto]`. Exit 0 on success.
2. **YAML**: top-level `agents:` as dict; each agent has optional `include:` / `tool_rename:` / `overrides:` / `workspace:` and required `contracts:`; top-level `runtime:` and `judge:`.
3. **Patterns**: names in the table above keep their semantics. Renaming is a breaking change for this skill.
4. **`validate --json` shape**: per-contract `ok` / `type` / `pattern` / `formula` / `agent`.
5. **`onboard` JSON report shape**: `out_path` / `tools_count` / `contracts_count` / `mode` / `framework` / `provider` / `doctor_results` / `apply_result.diff`.
6. **Session log path**: `~/.sponsio/sessions/<agent_id>/*.jsonl` — `sponsio report` / `sponsio check --trace` both read this.

Changes to 1–6 should bump Sponsio's minor version and update this SKILL.md. Changes to `UnifiedExtractor`, `CodeAnalyzer`, pattern Python signatures, or other internals are NOT part of this skill's contract.
