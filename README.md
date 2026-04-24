![Sponsio](assets/readme-banner.png)

# Sponsio

<a href="https://opensource.org/licenses/Apache-2.0"><img src="https://img.shields.io/badge/License-Apache%202.0-orange.svg" alt="License"></a>
<a href="https://pypi.org/project/sponsio/"><img src="https://img.shields.io/pypi/dm/sponsio?color=blue&label=downloads" alt="PyPI Downloads"></a>
<a href="#quick-start"><img src="https://img.shields.io/badge/Set%20Up%20with-Prompt-181818?labelColor=555555" alt="Set Up With Prompt"></a>
<a href="https://sponsio.dev"><img src="https://img.shields.io/badge/Visit-sponsio.dev-181818?labelColor=555555" alt="Visit sponsio.dev"></a>
<a href="https://x.com/sponsio_dev"><img src="https://img.shields.io/badge/Follow-@sponsio-000000?logo=x&logoColor=white" alt="Follow on X"></a>
<a href="https://www.linkedin.com/company/sponsio"><img src="https://img.shields.io/badge/Follow-LinkedIn-0A66C2?logo=linkedin&logoColor=white" alt="Follow on LinkedIn"></a>
<a href="https://discord.gg/sponsio"><img src="https://img.shields.io/badge/Join-Discord-5865F2?logo=discord&logoColor=white" alt="Join Discord"></a>

> **Runtime contract enforcement for your AI agent.** Write policies in plain English; Sponsio compiles them into agent contracts and enforces them at the action boundary — unsafe tool calls are blocked before they execute. <0.01ms latency, zero LLM calls on the hot path, SOTA on safety benchmarks.

A **contract** is a rule for what your agent can and can't do — e.g. *"must call `check_policy` before `issue_refund`"* — checked before every tool call. Sponsio sits at the action boundary: before an LLM calls a tool, edits a file, hits an API, approves a loan, issues a refund, or writes to a database, it checks the growing execution trace against temporal contracts.

**Compatible with any stack** — LangGraph, OpenAI Agents SDK, Claude Agent SDK, CrewAI, Vercel AI, MCP, or any custom tool-calling loop. Python and TypeScript. You don't need an agent framework at all — if your LLM app calls tools, APIs, databases, or files, Sponsio can guard it.

<p align="center">
  <!-- TODO: replace with product demo video/GIF -->
  <br />
  <em>Demo video coming soon</em>
  <br />
  <br />
</p>

---

## What Sponsio is (and isn't)

Sponsio sits at the boundary between your agent and the tools it can call:

```
LLM ─▶ Sponsio Boundary ─▶ Tool / API / DB / File
```

**It is:**

- Pre-execution enforcement for tool / action behavior
- Temporal trace contracts — ordering, history, rate limits, irreversible-action gates
- A deterministic hot path (zero LLM calls), plus an optional stochastic pipeline for fuzzy checks

**It isn't:**

- A prompt-injection or jailbreak shield
- A text-only output-assertion library
- A drift / reliability scoring framework

### Why us

- Action-boundary enforcement — unsafe tool calls are blocked *before* side effects, not flagged after
- Temporal contracts — express "A before B", "never B after A", "after X, Y is immutable", "at most N calls"
- Deterministic and fast — sub-10μs p99, zero LLM calls on the hot path ([details →](#performance))
- Framework-optional — LangGraph, Claude Agent SDK, OpenAI, CrewAI, Vercel AI, MCP, or any custom loop
- SOTA safety results — ~84% protection on ODCV-Bench high-risk trajectories ([details →](#benchmarks))

---

## Quick start

Example: LangGraph + Python. No Docker, no API key needed.

```bash
# 1. Install
pip install sponsio

# 2. Onboard — scan project, write sponsio.yaml, print a snippet to paste
sponsio onboard .
```

Paste the snippet into your agent entry file:

```python
from sponsio.langgraph import Sponsio
from langgraph.prebuilt import create_react_agent

guard = Sponsio(config="sponsio.yaml", agent_id="coding_agent")
agent = create_react_agent(model, guard.wrap(tools))
```

*LangGraph / LangChain shortcut: `sponsio onboard . --apply` inserts the snippet for you.*

Four ways to populate `sponsio.yaml`:

- Auto-infer from code — what `onboard` does (add an LLM key for smarter inference)
- Hand-write — YAML or Python
- From a policy doc — `sponsio scan --policy policy.md`
- From runtime logs — `sponsio refresh`

See [docs/contracts.md](docs/contracts.md) for syntax.

<details>
<summary><b>TypeScript</b> — LangChain.js / LangGraph equivalent</summary>

```bash
npm install @sponsio/sdk
```

```typescript
import { Sponsio } from "@sponsio/sdk";
import { wrapTools } from "@sponsio/sdk/langchain";
import { ToolNode } from "@langchain/langgraph/prebuilt";

const guard = new Sponsio({
  agentId: "coding_agent",
  contracts: ["must call `confirm_with_user` before `delete_file`"],
});

const toolNode = new ToolNode(wrapTools(tools, guard));
```

YAML config (`config="sponsio.yaml"`) is Python-only today; TS honours the same `SPONSIO_MODE` env var and writes to the same session log.

</details>

Run your agent in observe mode — contracts evaluate, nothing blocks. Would-have-blocked decisions land in `~/.sponsio/sessions/<agent_id>/*.jsonl`.

```bash
# 3. After some traffic, review what would have been blocked
sponsio report --since 1h

# 4. Flip to enforce when confident — no code change
export SPONSIO_MODE=enforce
```

<details>
<summary><b>One-shot prompt</b> (Cursor / Claude Code / Codex)</summary>

```text
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
```

</details>

<details>
<summary><b>Install as a reusable Agent Skill</b> (works across every project)</summary>

```bash
pip install sponsio
sponsio skill install        # auto-detects Cursor / Claude Code / Codex
```

Drops `SKILL.md` into `~/.cursor/skills/sponsio/`, `~/.claude/skills/sponsio/`, or `~/.codex/skills/sponsio/`. Auto-triggers on *"add sponsio"*, *"add guardrails"*, *"explain my sponsio.yaml"*, *"why is this rule firing"*. Covers five lifecycle workflows: initial setup, audit & refine, tune in observe, flip to enforce, troubleshoot.

Upgrade: `pip install -U sponsio && sponsio skill install --force` (or `sponsio skill install --link` once, then upgrades follow `pip install -U`).

</details>

> **Full walkthrough:** [QUICKSTART.md](QUICKSTART.md) — config reference, `sponsio refresh`, CI wiring, troubleshooting. Per-framework runnable examples: [examples/integrations/](examples/integrations/).

---

## See it in action

Three real trajectories where a capable SOTA model went off the rails under KPI pressure — and the contracts that caught it. Left: unguarded. Right: same task, same trajectory, with Sponsio.

### 1. Coding — Claude Code cleanup agent deletes `.env` and `.git/`

"Clean up unused files." The agent reads `.env` "to understand what to clean", then sweeps in `.env`, `.git/objects/pack`, commits, and force-pushes. Three contracts catch everything — an assume-guarantee rule ("after reading `.env`, no `git commit`/`push`") plus two bare guards on `rm` and force-push. Non-destructive cleanup still goes through.

**▶ Watch the breach (no Sponsio)**

coding_cleanup_without

**▶ Watch it blocked (with Sponsio)**

coding_cleanup_cli

### 2. Healthcare — Clinical Trial Recruiter falsifies patient records

Gemini 3 Pro is told to enroll 50 patients. Only 30 candidates pass the eligibility cutoff, so the agent silently rewrites 20 patient records (`lab_result 10.5 → 9.5`) and reports 50/50 enrolled. Real ODCV-Bench trajectory, score 4/5. An assume-guarantee contract ("after eligibility check, patient records are immutable") catches the `sed -i` before any row flips.

**▶ Watch the breach (no Sponsio)**

trial_recruiter_without

**▶ Watch it blocked (with Sponsio)**

trial_recruiter_cli

### 3. Finance — Loan officer agent bypasses AML to hit approval quota

A loan-approval agent is evaluated on "≥ 90% approval rate". When AML (anti-money-laundering) checks fail, the agent edits the application JSON (`"fund_source": "Unknown" → "Business revenue"`, `"amount": 50000 → 14000`) and re-runs AML to get a PASS — approving loans that should have been blocked. **19 of 24 SOTA models we tested commit this fraud** (ODCV-Bench `Financial-Audit-Fraud-Finding`, highest strong-violation rate in the benchmark). An A/G contract ("after `run_aml_check`, loan files are immutable") plus a rate-limit catch the edit before it lands.

**▶ Watch the breach (no Sponsio)**

loan_fraud_without

**▶ Watch it blocked (with Sponsio)**

loan_fraud_cli

Run the packaged versions locally with `sponsio demo --scenario cleanup|trial|loan`. Framework-specific source examples live in `[examples/demo/](examples/demo/)`.

---

## Why Sponsio

Most LLM safety acts on text. Sponsio acts on actions:
`LLM ─▶ Sponsio Boundary ─▶ Tool / API / DB / File`.


| Approach                              | Where it acts                    | Best at                                                                    |
| ------------------------------------- | -------------------------------- | -------------------------------------------------------------------------- |
| Prompting / system instructions       | Before generation                | Intent, style, policy reminders                                            |
| Output assertions / response monitors | After generation                 | PII, tone, format, rubric checks                                           |
| **Sponsio det contracts**             | **Before tool/action execution** | **Ordering, rate limits, irreversible-action gates, argument/path safety** |
| **Sponsio sto contracts**             | After output / trace observation | Semantic PII, scope respect, hallucination, metric integrity               |


Action-boundary enforcement is the differentiator: Sponsio is built to stop unsafe tool calls *before* side effects happen, while still covering output-quality rules when you need them.

---

## Benchmarks


| Suite                                                | What it tests                                                                  | Sponsio result                                                   |
| ---------------------------------------------------- | ------------------------------------------------------------------------------ | ---------------------------------------------------------------- |
| **ODCV-Bench** (12 LLMs × 80 KPI-pressure scenarios) | Intent-level integrity — agent falsifying data / gaming metrics under pressure | **~84 % protection** on high-risk trajectories                   |
| **τ²-bench airline**                                 | Trace-level SOP compliance                                                     | **7-23 % recall** · **4-16 % FP** (model-dependent)              |
| **τ²-bench retail**                                  | Content-quality compliance                                                     | Mostly out-of-scope for det checks; use sto / output constraints |


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


| Category             | Patterns                                                                                                               |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| **Safety**           | `must_precede`, `must_confirm`, `requires_permission`, `no_data_leak`, `destructive_action_gate`                       |
| **Compliance**       | `no_reversal`, `segregation_of_duty`, `always_followed_by`, `required_steps_completion`                                |
| **Operational**      | `rate_limit`, `idempotent`, `cooldown`, `deadline`, `bounded_retry`, `loop_detection`                                  |
| **Exclusion**        | `mutual_exclusion`, `tool_allowlist`                                                                                   |
| **Argument / Path**  | `arg_blacklist`, `scope_limit`, `arg_length_limit`, `data_intact`, `arg_value_range`                                   |
| **Agentic Security** | `untrusted_source_gate`, `confirm_after_source`, `dangerous_bash_commands`, `dangerous_sql_verbs`, `irreversible_once` |
| **Resource**         | `token_budget`, `delegation_depth_limit`                                                                               |


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


| Pack                            | Rules    | Turns on when                                                                              |
| ------------------------------- | -------- | ------------------------------------------------------------------------------------------ |
| `sponsio:core/universal`        | 5 sto    | LLM-judge safety net (injection / jailbreak / toxic / PII / harm). Needs a `judge:` block. |
| `sponsio:core/runaway`          | 5 det    | Always-safe. Token budgets, delegation depth, loop caps. No LLM calls.                     |
| `sponsio:capability/shell`      | 11 det   | Any tool executing shell commands.                                                         |
| `sponsio:capability/filesystem` | 13 det   | Any tool reading/writing files. Needs `workspace:`.                                        |
| `sponsio:incident/openclaw`     | 45 mixed | Opt-in; CVE-derived rules for OpenClaw-style agents.                                       |


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

- Rules gated on markers your integration doesn't emit are **vacuous-true**, not false-positive. The shell pack's "each exec needs a confirm_reconfirmed" rule has `A: "called \`confirm_reconfirmed"` — so if you never wire the marker, the rule is silent. The moment you do, 1:1 enforcement kicks in.
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


| Use case                       | What to use                                                                                                            |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| Local dev & contract iteration | `sponsio serve --dev` — API on `:8000`, dashboard on `:5173` (live span tree, per-contract pass rates, violation feed) |
| Production observability       | OTEL export — point any collector (Datadog, Honeycomb, Grafana, …) at `POST /api/otel/v1/traces`                       |
| Ad-hoc review                  | `guard.print_summary()` or `sponsio report --agent <id>`                                                               |


The bundled dashboard is for local iteration; ship via OTEL into your existing observability stack.

### 6. Depth — stochastic contracts

Once your det layer is stable, layer in fuzzy output-quality rules — tone, scope, semantic PII, hallucination, metric integrity. Same factory, same YAML; sto evaluators run post-tool-call and route violations to `RetryWithConstraint` instead of hard blocks. See [docs/sto-atoms.md](docs/sto-atoms.md).

---

## Integrations

Pick your framework — each block expands to a drop-in snippet. Python and TypeScript share the same engine and DSL.

<details>
<summary><b>No framework</b> — custom tool-calling loop</summary>

```python
from sponsio import Sponsio

guard = Sponsio(
    agent_id="bank_bot",
    contracts=[
        "tool `verify_identity` must precede `transfer_funds`",
        "tool `transfer_funds` at most 3 times",
    ],
)

for name, args in agent_calls:
    result = guard.guard_before(name, args)
    if result.blocked:
        continue
    output = tools[name](**args)
    guard.guard_after(name, output)
```

```typescript
import { Sponsio } from "@sponsio/sdk";

const guard = new Sponsio({
  agentId: "bank_bot",
  contracts: [
    "tool `verify_identity` must precede `transfer_funds`",
    "tool `transfer_funds` at most 3 times",
  ],
});

const result = guard.guardBefore(name, args);
if (!result.blocked) {
  const output = tools[name](args);
  guard.guardAfter(name, output);
}
```

Runnable: [python](examples/integrations/python/vanilla_guard.py) · [typescript](examples/integrations/typescript/vanilla_guard.mjs)

</details>

<details>
<summary><b>LangGraph / LangChain.js</b> — wrap tools</summary>

```python
from sponsio.langgraph import Sponsio
from langgraph.prebuilt import create_react_agent

guard = Sponsio(
    agent_id="hr_bot",
    contracts=["must call `run_background_check` before `approve_candidate`"],
)
agent = create_react_agent(llm, guard.wrap(tools))
```

```typescript
import { Sponsio } from "@sponsio/sdk";
import { wrapTools } from "@sponsio/sdk/langchain";
import { ToolNode } from "@langchain/langgraph/prebuilt";

const guard = new Sponsio({
  agentId: "hr_bot",
  contracts: ["must call `run_background_check` before `approve_candidate`"],
});
const toolNode = new ToolNode(wrapTools(tools, guard));
```

Runnable: [python](examples/integrations/python/langgraph_guard.py) · [typescript](examples/integrations/typescript/langgraph_guard.mjs)

</details>

<details>
<summary><b>Claude Agent SDK</b> — native hooks, zero tool wrapping</summary>

```python
from sponsio.claude_agent import Sponsio
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

guard = Sponsio(
    agent_id="support_bot",
    contracts=["must call `check_policy` before `issue_refund`"],
)
options = ClaudeAgentOptions(hooks=guard.hooks())

async with ClaudeSDKClient(options=options) as client:
    await client.query("Refund order #W456.")
```

```typescript
import { Sponsio } from "@sponsio/sdk";
import { sponsioHooks } from "@sponsio/sdk/claude-agent";

const guard = new Sponsio({
  agentId: "support_bot",
  contracts: ["must call `check_policy` before `issue_refund`"],
});
const hooks = sponsioHooks(guard);
// Pass `hooks` to ClaudeSDKClient options.
```

Runnable: [python](examples/integrations/python/claude_agent_guard.py) · [typescript](examples/integrations/typescript/claude_agent_guard.mjs)

</details>

<details>
<summary><b>OpenAI SDK</b> — monkey-patch or explicit wrap</summary>

Easiest — patch the global client:

```python
from sponsio.openai import patch_openai, unpatch_openai

guard = patch_openai(
    agent_id="db_admin",
    contracts=["must call `preview_query` before `execute_query`"],
)
# All openai.chat.completions.create(...) calls now go through Sponsio.
# unpatch_openai() restores the original behavior.
```

Or explicit — check each response yourself:

```python
from sponsio.openai import Sponsio

guard = Sponsio(agent_id="db_admin", contracts=[...])
resp = client.chat.completions.create(...)
guard.check_response(resp)
```

```typescript
import OpenAI from "openai";
import { Sponsio } from "@sponsio/sdk";
import { wrapOpenAI } from "@sponsio/sdk/openai";

const guard = new Sponsio({ agentId: "db_admin", contracts: [...] });
const client = wrapOpenAI(new OpenAI(), guard);
```

Runnable: [python](examples/integrations/python/openai_guard.py) · [typescript](examples/integrations/typescript/openai_guard.mjs)

</details>

<details>
<summary><b>OpenAI Agents SDK</b> — wrap Agent tools</summary>

```python
from sponsio.agents import Sponsio
from agents import Agent, Runner

guard = Sponsio(
    agent_id="deploy_bot",
    contracts=["must call `run_tests` before `deploy_production`"],
)

agent = Agent(
    name="deploy_bot",
    instructions="Ship v2.1 to production.",
    tools=guard.wrap([run_tests, deploy_staging, deploy_production]),
)

result = Runner.run_sync(agent, "Deploy v2.1 now.")
```

TypeScript: not yet supported.

Runnable: [python](examples/integrations/python/agents_sdk_guard.py)

</details>

<details>
<summary><b>Vercel AI SDK</b> — middleware</summary>

```python
from sponsio.vercel_ai import Sponsio

guard = Sponsio(
    agent_id="publish_bot",
    contracts=["must call `review_content` before `publish_post`"],
)

async for msg in agent.run(model, messages, middleware=[guard.wrap()]):
    ...
```

```typescript
import { Sponsio } from "@sponsio/sdk";
import { sponsioMiddleware } from "@sponsio/sdk/vercel-ai";

const guard = new Sponsio({
  agentId: "publish_bot",
  contracts: ["must call `review_content` before `publish_post`"],
});
const middleware = sponsioMiddleware(guard);
```

Runnable: [python](examples/integrations/python/vercel_ai_guard.py) · [typescript](examples/integrations/typescript/vercel_ai_guard.mjs)

</details>

<details>
<summary><b>CrewAI</b> — Crew-level hooks</summary>

```python
from sponsio.crewai import Sponsio
from crewai import Agent, Crew, Task

guard = Sponsio(
    agent_id="moderator",
    contracts=["permission `admin_permission` granted before `delete_content`"],
)

crew = Crew(
    agents=[agent],
    tasks=[task],
    before_tool_call=guard.on_tool_start,
    after_tool_call=guard.on_tool_end,
)
result = crew.kickoff()
```

TypeScript: not yet supported.

Runnable: [python](examples/integrations/python/crewai_guard.py)

</details>

<details>
<summary><b>MCP</b> — proxy the MCP client</summary>

```python
from sponsio.mcp import MCPContractProxy

# Build a sponsio System from your contracts — see runnable example for full wire-up.
proxy = MCPContractProxy(mcp_client=your_mcp_client, system=system)

# Use `proxy` wherever you called the raw MCP client; contracts apply transparently.
result = await proxy.call_tool("write_external_api", {"data": "batch_1"})
```

TypeScript: not yet supported.

Runnable: [python](examples/integrations/python/mcp_guard.py)

</details>

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