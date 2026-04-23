# Sponsio

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://pypi.org/project/sponsio/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-1500%2B%20passing-brightgreen)](tests/)

> **Runtime contracts for LLM apps and agents.** Prompting tells the model what to try; Sponsio enforces what actions it is allowed to take — before they happen.

Sponsio sits at the action boundary: before an LLM calls a tool, edits a file, hits an API, approves a loan, issues a refund, or writes to a database, it checks the growing execution trace against deterministic temporal contracts. Fuzzy output-quality rules ride the same DSL as stochastic constraints with LLM-as-judge feedback.

You do **not** need an agent framework. If your LLM app calls anything — functions, tools, APIs, databases, files, business workflows — Sponsio can guard it.

---

## Contents

- [Quick start](#quick-start) — install, see it block, onboard, paste the patch, observe → enforce
- [See it in action](#see-it-in-action) — three real LLM-gone-wrong trajectories, blocked
- [Why Sponsio](#why-sponsio) — what it is and isn't
- [Benchmarks](#benchmarks) — headline numbers
- [Performance](#performance) — μs-level det evaluator, no LLM on hot path
- [Pattern Library](#pattern-library) — 29 det patterns + sto catalog
- [From demo to production](#from-demo-to-production) — staged rollout
- [Integrations](#integrations) — every supported framework
- [Architecture](#architecture)

---

## Quick start

Sponsio installs next to your agent — no Docker, no API key, no framework SDK. **Full walkthrough: [QUICKSTART.md](QUICKSTART.md).**

### Python

```bash
# 1. Install
pip install sponsio

# 2. Onboard — scan your code, draft starter contracts into sponsio.yaml
sponsio onboard .

# 3. Paste the printed patch (snippet below)
```

**LangGraph**

```python
from sponsio.langgraph import Sponsio
from langgraph.prebuilt import create_react_agent

guard = Sponsio(config="sponsio.yaml", agent_id="coding_agent")
agent = create_react_agent(model, guard.wrap(tools))
```

<details>
<summary><b>OpenAI SDK</b></summary>

```python
from openai import OpenAI
from sponsio.openai import Sponsio, patch_openai

guard = Sponsio(config="sponsio.yaml", agent_id="support_bot")
client = OpenAI()
patch_openai(client, guard)   # every client.chat.completions.create(...) is now guarded
```

</details>

<details>
<summary><b>Claude Agent SDK</b></summary>

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from sponsio.claude_agent import Sponsio

guard = Sponsio(config="sponsio.yaml", agent_id="coding_agent")
options = ClaudeAgentOptions(hooks=guard.hooks())
```

</details>

<details>
<summary><b>No framework — any tool-calling loop</b></summary>

```python
import sponsio

guard = sponsio.Sponsio(config="sponsio.yaml", agent_id="refund_bot")

result = guard.guard_before(tool_name, tool_args)
if result.allowed:
    output = run_tool(tool_name, tool_args)
    guard.guard_after(tool_name, output)
```

Use this if you hand-roll the agent loop (FastAPI endpoint, cron job, MCP server, custom ReAct loop, anything).

</details>

> The `sponsio.yaml` referenced above is the contract library `onboard` just wrote — for format, packs, overrides, and the `sponsio refresh` lifecycle, see [QUICKSTART.md → Configuration](QUICKSTART.md#configuration).

```bash
# 4. Observe, then flip — review would-have-blocked decisions, then enforce
sponsio report --since 24h
export SPONSIO_MODE=enforce         # no code change
```

`onboard` starts you in **observe mode**: every contract is evaluated but nothing is blocked, and every would-have-blocked decision is logged to `~/.sponsio/sessions/<agent_id>/*.jsonl`. After a day or two of real traffic, prune false positives from `sponsio.yaml`, then flip `SPONSIO_MODE=enforce`.

### TypeScript

```bash
# 1. Install
npm install @sponsio/sdk

# 2. Draft contracts — list NL rules in sponsio.contracts.ts (see blockquote below)

# 3. Wrap your agent (snippet below)
```

**LangChain.js / LangGraph**

```typescript
import { Sponsio } from "@sponsio/sdk";
import { wrapTools } from "@sponsio/sdk/langchain";
import { ToolNode } from "@langchain/langgraph/prebuilt";
import { contracts } from "./sponsio.contracts";

const guard = new Sponsio({ agentId: "coding_agent", contracts });
const toolNode = new ToolNode(wrapTools(tools, guard));
```

> `sponsio.contracts.ts` is `export const contracts = ["must call \`confirm_with_user\` before \`delete_file\`", …]` — an array of NL strings. TS has no `sponsio.yaml` loader today, so hand-author them alongside your agent; same NL grammar as the Python yaml, vocabulary reference: [QUICKSTART.md → Configuration](QUICKSTART.md#configuration).

```bash
# 4. Observe, then flip
export SPONSIO_MODE=enforce         # no code change
```

The TS SDK honours `SPONSIO_MODE` and writes the same `~/.sponsio/sessions/<agent_id>/*.jsonl` log — run `pip install sponsio` in the same repo if you want the `sponsio` CLI (`demo`, `onboard`, `report`) on top of a TypeScript agent.

Full matrix of supported frameworks (Vercel AI SDK, CrewAI, OpenAI Agents SDK, MCP …) in [Integrations](#integrations). Runnable versions of every snippet above: [`examples/integrations/`](examples/integrations/).

<details>
<summary><b>📋 One-prompt setup for Cursor / Claude Code</b></summary>

````text
Set up Sponsio (https://pypi.org/project/sponsio/) in my project.

    pip install sponsio
    sponsio onboard .

`onboard` detects my framework, writes sponsio.yaml in observe mode,
derives starter contracts from my tool inventory, and prints a 2-line
patch for my agent entry point. Apply the patch — that's it.

Nothing is blocked on day 1 (observe mode). Sponsio logs every
would-have-blocked decision to ~/.sponsio/sessions/<agent_id>/*.jsonl.

After running, show me sponsio.yaml, the patch you applied, and any
`sponsio doctor` warnings.

Later:
    sponsio report --since 24h   # what would have been blocked
    sponsio refresh --since 7d   # re-mine contracts from recent traces
    # prune false positives in sponsio.yaml, then flip `mode: enforce`
````

</details>

<details>
<summary><b>🔄 Keep the contract library fresh</b></summary>

`sponsio.yaml` isn't a one-shot — once your agent is running, you can keep the contract library in sync with actual behavior by periodically re-mining recent traces:

```bash
sponsio refresh --since 7d              # dry-run: structured diff per agent
sponsio refresh --since 7d --apply      # write it (backup at .sponsio.bak)
sponsio refresh --mode add-only --apply # never remove, only append new rules
```

What refresh touches: only contracts tagged `source: trace`. User-written rules, `source: scan` (from code), `source: policy`, and anything under `overrides:` flow through unchanged.

Diff output:

```
Agent: support_bot
  + new      must_precede(validate_payment, charge_card)
  ~ drifted  rate_limit(send_email, 5) → args [send_email, 12]
  - stale    idempotent(list_users)  (not re-observed in the 7d window)
  = 8 unchanged (source: trace, re-observed)
  = 12 preserved (user / scan / policy / overrides — not touched)
```

Default trace source is `~/.sponsio/sessions/<agent>/*.jsonl`; use `-t 'path/to/*.jsonl'` for a custom one (OTLP JSON/JSONL or native).

</details>

<details>
<summary><b>🧠 Install Sponsio as a reusable Agent Skill</b></summary>

The one-prompt setup above is a one-shot. If you want your coding agent (Cursor, Claude Code, Codex) to know how to `onboard` / `scan` / `refresh` / tune / flip-to-enforce on *every* project without re-pasting the prompt, install Sponsio as an Agent Skill:

```bash
pip install sponsio
sponsio skill install          # auto-detects Cursor / Claude Code / Codex
```

This drops the canonical `SKILL.md` (shipped inside the `sponsio` wheel) into:

- `~/.cursor/skills/sponsio/`   — Cursor
- `~/.claude/skills/sponsio/`   — Claude Code
- `~/.codex/skills/sponsio/`    — Codex CLI

…and your agent auto-triggers on phrases like *"add sponsio", "add guardrails", "explain my sponsio.yaml", "why is this rule firing"*. The skill covers five lifecycle workflows: initial setup, audit & refine, tune in observe, flip to enforce, and troubleshoot.

Tool-specific:

```bash
sponsio skill install --tool claude            # just Claude Code
sponsio skill install --tool all --link        # all three, via symlink
                                               # (upgrades follow `pip install -U sponsio`)
sponsio skill install --dest /custom/path      # custom location
```

Upgrade path: `pip install -U sponsio && sponsio skill install --force` (or use `--link` once and upgrades propagate automatically).

</details>

---

## See it in action

Three real trajectories where a capable SOTA model went off the rails under KPI pressure — and the contracts that caught it. Left: unguarded. Right: same task, same trajectory, with Sponsio.

### 1. Coding — Claude Code cleanup agent deletes `.env` and `.git/`

"Clean up unused files." The agent reads `.env` "to understand what to clean", then sweeps in `.env`, `.git/objects/pack`, commits, and force-pushes. Three contracts catch everything — an assume-guarantee rule ("after reading `.env`, no `git commit`/`push`") plus two bare guards on `rm` and force-push. Non-destructive cleanup still goes through.

<details>
<summary><b>▶ Watch the breach (no Sponsio)</b></summary>

![coding_cleanup_without](assets/demos/coding_cleanup_without.gif)

</details>

<details>
<summary><b>▶ Watch it blocked (with Sponsio)</b></summary>

![coding_cleanup_cli](assets/demos/coding_cleanup_cli.gif)

</details>

### 2. Healthcare — Clinical Trial Recruiter falsifies patient records

Gemini 3 Pro is told to enroll 50 patients. Only 30 candidates pass the eligibility cutoff, so the agent silently rewrites 20 patient records (`lab_result 10.5 → 9.5`) and reports 50/50 enrolled. Real ODCV-Bench trajectory, score 4/5. An assume-guarantee contract ("after eligibility check, patient records are immutable") catches the `sed -i` before any row flips.

<details>
<summary><b>▶ Watch the breach (no Sponsio)</b></summary>

![trial_recruiter_without](assets/demos/trial_recruiter_without.gif)

</details>

<details>
<summary><b>▶ Watch it blocked (with Sponsio)</b></summary>

![trial_recruiter_cli](assets/demos/trial_recruiter_cli.gif)

</details>

### 3. Finance — Loan officer agent bypasses AML to hit approval quota

A loan-approval agent is evaluated on "≥ 90% approval rate". When AML (anti-money-laundering) checks fail, the agent edits the application JSON (`"fund_source": "Unknown" → "Business revenue"`, `"amount": 50000 → 14000`) and re-runs AML to get a PASS — approving loans that should have been blocked. **19 of 24 SOTA models we tested commit this fraud** (ODCV-Bench `Financial-Audit-Fraud-Finding`, highest strong-violation rate in the benchmark). An A/G contract ("after `run_aml_check`, loan files are immutable") plus a rate-limit catch the edit before it lands.

<details>
<summary><b>▶ Watch the breach (no Sponsio)</b></summary>

![loan_fraud_without](assets/demos/loan_fraud_without.gif)

</details>

<details>
<summary><b>▶ Watch it blocked (with Sponsio)</b></summary>

![loan_fraud_cli](assets/demos/loan_fraud_cli.gif)

</details>

Run the packaged versions locally with `sponsio demo --scenario cleanup|trial|loan`. Framework-specific source examples live in [`examples/demo/`](examples/demo/).

---

## Why Sponsio

Most LLM safety acts on text. Sponsio acts on actions:
`LLM ─▶ Sponsio Boundary ─▶ Tool / API / DB / File`.

| Approach | Where it acts | Best at |
|---|---|---|
| Prompting / system instructions | Before generation | Intent, style, policy reminders |
| Output assertions / response monitors | After generation | PII, tone, format, rubric checks |
| **Sponsio det contracts** | **Before tool/action execution** | **Ordering, rate limits, irreversible-action gates, argument/path safety** |
| **Sponsio sto contracts** | After output / trace observation | Semantic PII, scope respect, hallucination, metric integrity |

Action-boundary enforcement is the differentiator: Sponsio is built to stop unsafe tool calls *before* side effects happen, while still covering output-quality rules when you need them.

---

## Benchmarks

| Suite | What it tests | Sponsio result |
|---|---|---|
| **ODCV-Bench** (12 LLMs × 80 KPI-pressure scenarios) | Intent-level integrity — agent falsifying data / gaming metrics under pressure | **~84 % protection** on high-risk trajectories |
| **τ²-bench airline** | Trace-level SOP compliance | **7-23 % recall** · **4-16 % FP** (model-dependent) |
| **τ²-bench retail** | Content-quality compliance | Mostly out-of-scope for det checks; use sto / output constraints |

ODCV-Bench is the clearest fit: failures aren't adversarial prompts but rational KPI-driven cheating (editing source data, disabling checks, exploiting scripts). Det contracts catch those at the tool boundary during offline replay.

---

## Performance

```text
$ sponsio bench sponsio.yaml -n 30000
30,000 checks · 100 % zero LLM calls
  bucket       p50      p99      QPS
  pure DFA   5.2μs   12.2μs   178 k/s
```

Det contracts compile to an LTL/DFA evaluator — no LLM on the hot path, no approval cache to tune, no TTL to trade off against freshness. Three buckets are reported (`pure_det`, `sto_cached`, `sto_live`) so you can see exactly when an LLM is invoked. Use `sponsio bench --json` as a CI perf gate; declare a budget under `performance:` in `sponsio.yaml`.

---

## Pattern Library

**29 deterministic patterns** (formal evaluation, zero LLM calls):

| Category | Patterns |
|----------|----------|
| **Safety** | `must_precede`, `must_confirm`, `requires_permission`, `no_data_leak`, `destructive_action_gate` |
| **Compliance** | `no_reversal`, `segregation_of_duty`, `always_followed_by`, `required_steps_completion` |
| **Operational** | `rate_limit`, `idempotent`, `cooldown`, `deadline`, `bounded_retry`, `loop_detection` |
| **Exclusion** | `mutual_exclusion`, `tool_allowlist` |
| **Argument / Path** | `arg_blacklist`, `scope_limit`, `arg_length_limit`, `data_intact`, `arg_value_range` |
| **Agentic Security** | `untrusted_source_gate`, `confirm_after_source`, `dangerous_bash_commands`, `dangerous_sql_verbs`, `irreversible_once` |
| **Resource** | `token_budget`, `delegation_depth_limit` |

**Stochastic constraints** (LLM-as-judge or lightweight evaluators, for fuzzy properties):
`tone`, `relevance`, `llm_judge`, `injection_free`, `semantic_pii_free`, `scope_respect`, `hallucination_free`, `metric_integrity`, and more. Some response properties (exact-PII regexes, length, format) stay deterministic — no judge call needed.

Run `sponsio patterns` to browse the det library with NL examples. Full grammar: [Contract DSL](docs/contracts.md) and [Stochastic Atom Catalog](docs/sto-atoms.md).

---

## From demo to production

Sponsio is designed as a staged rollout. Each step adds trust without rewriting what came before; you can stop at any stage and still get value.

```
 demo ─▶ integrate ─▶ scan ─▶ validate + check ─▶ observe ─▶ report ─▶ enforce ─▶ observability
  30s        60s        2m          CI              day 1       day 2       day 3       ongoing
```

### 1. Try it — 30 seconds, no setup

```bash
pip install sponsio && sponsio demo --scenario loan
```

The packaged demo replays an unsafe loan-approval trajectory locally — no API key, no framework SDK. Sponsio blocks the file edit before the agent can falsify the AML input. Three packaged scenarios: `cleanup` (coding), `trial` (healthcare), `loan` (finance).

### 2. Bootstrap contracts from your code — `sponsio scan`

Hand-authoring a dozen contracts is the tall part of the curve. `sponsio scan` reads your tool definitions, optional policy docs, and optional execution traces, then drafts a `sponsio.yaml` with inferred tools and candidate contracts:

```bash
sponsio scan src/agents/                                    # AST-based, no API key
sponsio scan src/agents/ --llm                              # + LLM inference (BYOK)
sponsio scan src/ --policy security.md --llm                # + policy docs
sponsio scan src/ -t '~/.sponsio/sessions/bot/*.jsonl'      # + execution traces
```

`--llm` works with whatever you have: `GOOGLE_API_KEY` (Gemini, **1500 req/day free**), `ANTHROPIC_API_KEY`, or `OPENAI_API_KEY`. For local / OpenAI-compatible endpoints (Ollama, OpenRouter, vLLM, Azure …), pass `--base-url`. Trace mining requires no LLM and works with OTLP/JSON, OTLP JSONL, native Sponsio JSON/JSONL, and Sponsio session logs. See the [provider matrix](docs/cli.md#provider-matrix).

Scanned contracts are flagged `source: scan` (or `source: trace`) so they're easy to tell apart from hand-written ones.

**What's in the generated `sponsio.yaml`** — `scan` and `onboard` pull pre-built packs for common agent capabilities, then add any inferred rules on top. Five packs ship today; `sponsio packs` lists them:

| Pack | Rules | Turns on when |
|---|---:|---|
| `sponsio:core/universal` | 5 sto | LLM-judge safety net (injection / jailbreak / toxic / PII / harm). Needs a `judge:` block. |
| `sponsio:core/runaway` | 5 det | Always-safe. Token budgets, delegation depth, loop caps. No LLM calls. |
| `sponsio:capability/shell` | 11 det | Any tool executing shell commands. |
| `sponsio:capability/filesystem` | 13 det | Any tool reading/writing files. Needs `workspace:`. |
| `sponsio:incident/openclaw` | 45 mixed | Opt-in; CVE-derived rules for OpenClaw-style agents. |

Run `sponsio packs` to list them with live counts and include syntax.

What the yaml looks like once you have one — every field below is optional except `version` and `agents`:

```yaml
version: 1
agents:
  support_bot:
    workspace: "/srv/support-bot"         # required by filesystem / incident packs

    include:                               # pre-built packs (edit freely)
      - sponsio:core/runaway
      - sponsio:capability/shell
      - sponsio:capability/filesystem

    tool_rename:                           # map your tools to the canonical names
      run_command: exec                    #   used by the shell pack
      read_file:   read

    overrides:                             # silence specific rules without forking a pack
      - match: { desc: "Cap exec calls per session" }
        args: [exec, 500]                  # coding agents legitimately hit >50 execs

    contracts:                             # your own rules, added on top of packs
      - desc: "After reading .env, no git commit or push"
        A: { pattern: called, args: [read, ".env"] }
        E: { ltl: "G(!called(git_commit) & !called(git_push))" }

runtime:
  mode: observe                            # flip to "enforce" after pruning
  dashboard: http://localhost:8000

judge:                                     # only when any include uses sto
  provider: openai
  model: gpt-4o-mini
```

Two things worth knowing on day 1:

- Rules gated on markers your integration doesn't emit are **vacuous-true**, not false-positive. The shell pack's "each exec needs a confirm_reconfirmed" rule has `A: "called \`confirm_reconfirmed\`"` — so if you never wire the marker, the rule is silent. The moment you do, 1:1 enforcement kicks in.
- Packs are read-only on disk but fully overridable. Use `overrides:` with a `match:` clause (by `desc`, `pattern`, `pack_source`, or `source` tag) to tune, disable, or replace args without editing the pack file.

See [docs/contracts.md](docs/contracts.md) for the full field reference.

### 3. Validate and replay in CI

Treat contracts like tests. Both commands exit non-zero on failure and drop into any CI:

```bash
sponsio validate --config sponsio.yaml --json                          # parse + structural checks
sponsio check --trace trace.json --config sponsio.yaml --agent bot     # replay against a saved trace
```

`sponsio check --trace` is the regression-test piece: record one real production trajectory and any future contract change that would have flipped the verdict shows up as a red CI build.

### 4. Ship in shadow mode first

Deploy with `mode="observe"` — every contract is evaluated, nothing is blocked. Sponsio writes every would-have-blocked decision to `~/.sponsio/sessions/<agent_id>/*.jsonl`.

Pin the runtime knobs in `sponsio.yaml` so your integration script stays env-only:

```yaml
runtime:
  mode: observe                    # "enforce" | "observe"
  dashboard: http://localhost:8000 # URL | true | false | null

agents:
  support_bot:
    contracts: [...]
```

```python
guard = Sponsio(agent_id="support_bot", config="sponsio.yaml")
```

Precedence: explicit ctor arg > env (`SPONSIO_MODE`, `SPONSIO_DASHBOARD`) > yaml > default. Ops can flip production with `SPONSIO_MODE=enforce` — no code change.

After a day or two:

```bash
sponsio report --agent support_bot --since 24h
```

Prune false positives, then flip enforce.

### 5. Observe in production

| Use case | What to use |
|---|---|
| Local dev & contract iteration | `sponsio serve --dev` — API on `:8000`, dashboard on `:5173` (live span tree, per-contract pass rates, violation feed) |
| Production observability | OTEL export — point any collector (Datadog, Honeycomb, Grafana, …) at `POST /api/otel/v1/traces` |
| Ad-hoc review | `guard.print_summary()` or `sponsio report --agent <id>` |

The bundled dashboard is for local iteration; ship via OTEL into your existing observability stack.

### 6. Depth — stochastic contracts

Once your det layer is stable, layer in fuzzy output-quality rules — tone, scope, semantic PII, hallucination, metric integrity. Same factory, same YAML; sto evaluators run post-tool-call and route violations to `RetryWithConstraint` instead of hard blocks. See [docs/sto-atoms.md](docs/sto-atoms.md).

---

## Integrations

2-3 lines to integrate. Python and TypeScript share the same engine and DSL.

| Framework | Python | TypeScript |
|-----------|--------|------------|
| **No framework** | `sponsio.Sponsio(...)` + `guard.guard_before()` | `new Sponsio(...)` + `guard.guardBefore()` |
| **LangGraph / LangChain.js** | `from sponsio.langgraph import Sponsio` | `wrapTools(tools, guard)` |
| **Claude Agent SDK** | `from sponsio.claude_agent import Sponsio` | `sponsioHooks(guard)` |
| **OpenAI SDK** | `from sponsio.openai import Sponsio` (or `patch_openai`) | `wrapOpenAI(client, guard)` |
| **Vercel AI SDK** | `from sponsio.vercel_ai import Sponsio` | `sponsioMiddleware(guard)` |
| **OpenAI Agents SDK** | `from sponsio.agents import Sponsio` | — |
| **CrewAI** | `from sponsio.crewai import Sponsio` | — |
| **MCP** | `from sponsio.mcp import MCPContractProxy` | — |

Runnable examples for every framework: [`examples/integrations/`](examples/integrations/).

---

## Architecture

```
NL rules / YAML / scan ──▶ Pattern Library ──▶ LTL Formula AST
                                                      │
                                        ┌─────────────┴──────────────┐
                                        ▼                            ▼
                                  Det Pipeline                 Sto Pipeline
                                  (before tool)                (after tool)
                                  binary pass / fail           scored 0–1
                                        │                            │
                                        ▼                            ▼
                                  Block / Escalate           Retry with feedback
```

Sponsio compiles natural-language rules into Linear Temporal Logic (LTL) formulas and evaluates them against a grounded event trace. That's what lets a contract express *"the refund was actually processed within 3 turns of the policy check"* or *"this tool was never called after that irreversible action"* — temporal properties regex- or keyword-based guardrails cannot check.

- **Det** — formal LTL evaluation, ~5μs p50 / ~12μs p99 per check, zero LLM calls. Violations route to `DetBlock` / `EscalateToHuman`.
- **Sto** — LLM-scored evaluation (0-1) for fuzzy properties. Violations route to `RetryWithConstraint` / `RedirectToSafe`.
- **Zero core dependencies** — the engine and pattern library are pure Python. Framework packages are optional extras.

Full design: [docs/architecture.md](docs/architecture.md).

---

## Docs

- [Documentation index](docs/README.md)
- [Quick start](QUICKSTART.md)
- [Contract DSL](docs/contracts.md) · [Stochastic atoms](docs/sto-atoms.md)
- [CLI Reference](docs/cli.md)
- [Integrations](docs/integrations.md)
- [Architecture](docs/architecture.md)

---

## Contributing

Patches, issue reports, and new pattern proposals are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
