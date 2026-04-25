![Sponsio](assets/readme-banner.png)

<p align="center">
  <a href="https://opensource.org/licenses/Apache-2.0"><img src="https://img.shields.io/badge/License-Apache%202.0-orange.svg" alt="License"></a>
  <a href="https://pypi.org/project/sponsio/"><img src="https://img.shields.io/badge/install-pip%20install%20sponsio-blue?logo=python&logoColor=white" alt="Install from PyPI"></a>
  <a href="https://cursor.com/link/prompt?text=Set%20up%20Sponsio%20%28runtime%20contract%20enforcement%29%20in%20this%20project.%0A%0ARun%3A%0A%20%20pip%20install%20sponsio%0A%20%20sponsio%20onboard%20.%0A%0A%60onboard%60%20detects%20my%20agent%20framework%2C%20writes%20sponsio.yaml%20in%20observe%20mode%2C%20and%20derives%20starter%20contracts%20from%20my%20tool%20inventory.%20It%20then%20prints%20a%202-3%20line%20patch%20for%20my%20agent%20entry%20file.%0A%0AApply%20the%20patch.%20Nothing%20is%20blocked%20on%20day%201%20%28observe%20mode%29%20%E2%80%94%20every%20would-have-blocked%20decision%20lands%20in%20~/.sponsio/sessions/%3Cagent_id%3E/%2A.jsonl.%0A%0AAfter%20running%2C%20show%20me%3A%20the%20generated%20sponsio.yaml%2C%20the%20patch%20you%20applied%2C%20and%20any%20%60sponsio%20doctor%60%20warnings."><img src="https://img.shields.io/badge/Set%20Up%20with-Cursor-181818?logo=cursor&logoColor=white&labelColor=555555" alt="Set Up With Cursor"></a>
  <a href="https://sponsio.dev"><img src="https://img.shields.io/badge/Visit-sponsio.dev-181818?logo=data:image/svg%2bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjI4MyA3NjMgMzczIDM3MyI%2bPGcgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoMCwyMDQ4KSBzY2FsZSgwLjEsLTAuMSkiIGZpbGw9IiNGRkZGRkYiPjxwYXRoIGQ9Ik01MDEwIDEyNTAxIGMtNTggLTkgLTE4NyAtNDEgLTI2NyAtNjYgLTI2IC05IC05OSAtNDEgLTE2MCAtNzEgLTM1NCAtMTc0IC02MTMgLTQ3NiAtNzM2IC04NTkgLTQzIC0xMzMgLTY0IC0yNTEgLTczIC00MDcgbC03IC0xMTggLTQ2MiAwIC00NjMgMCAtNiAtMjIgYy0zIC0xMyAtMyAtNjYgMCAtMTE4IDE2IC0yODQgMTA2IC01NTYgMjYwIC03ODggMTEzIC0xNjggMzI0IC0zNTYgNTE2IC00NjAgMjcyIC0xNDcgNjM3IC0xOTAgOTY4IC0xMTUgMjM2IDUzIDQ1NiAxNzggNjQwIDM2MyAyNzIgMjczIDQxMyA2MTEgNDIzIDEwMjAgbDMgMTE1IDQ1NSA1IDQ1NCA1IDMgNDUgYzQgNDcgLTEyIDIwNyAtMjkgMzAwIC0xMDcgNTkyIC01MjMgMTAzMSAtMTA5NCAxMTU3IC03OSAxNyAtMzQxIDI2IC00MjUgMTR6IG0zMjAgLTk2MCBjNzMgLTI3IDE2MiAtOTkgMjA1IC0xNjQgNTggLTg3IDEwNCAtMjM5IDEwNSAtMzQ1IGwwIC01MiAtNDU3IDIgLTQ1OCAzIC0zIDQ4IGMtNSA3MyAyNCAyMDQgNjAgMjc3IDYxIDExOSAxOTEgMjI1IDMxMCAyNTAgNjQgMTMgMTc2IDUgMjM4IC0xOXogbS02MTIgLTY0MSBjMTMgLTI5NSAtMTkxIC01MjAgLTQ3MCAtNTIwIC0yMTcgMCAtMzkzIDE0NCAtNDUzIDM3MSAtMTUgNTUgLTIwIDIxMCAtOCAyMjIgMyA0IDIxNCA2IDQ2NyA1IGw0NjEgLTMgMyAtNzV6Ii8%2bPC9nPjwvc3ZnPg==&logoColor=white&labelColor=555555" alt="Visit sponsio.dev"></a>
  <a href="docs/owasp-agentic-top-10.md"><img src="https://img.shields.io/badge/OWASP%20Agentic%20Top%2010-10%2F10%20Covered-2E7D32?labelColor=555555" alt="OWASP Agentic Top 10 Covered"></a>
</p>

<p align="center">
  <a href="https://x.com/sponsiolabs"><img src="https://img.shields.io/badge/Follow%20on%20X-000000?logo=x&logoColor=white" alt="Follow on X"></a>
  <a href="https://www.linkedin.com/company/sponsio-labs/"><img src="https://img.shields.io/badge/Follow%20on%20LinkedIn-0A66C2?logo=linkedin&logoColor=white" alt="Follow on LinkedIn"></a>
  <a href="https://discord.gg/s8TfPnZWUm"><img src="https://img.shields.io/badge/Join%20our%20Discord-5865F2?logo=discord&logoColor=white" alt="Join our Discord"></a>
</p>

<p align="center">⭐ <em>Help us grow the Sponsio community for better Contract Library and enforcement scenarios. Star the repo!</em></p>

# Sponsio

**Runtime enforcement for AI agents.** Input policies in natural language; Sponsio compiles them into unbreakable, deterministic agent contracts. Enforced under 0.01ms, zero LLM runtime cost, [covers all 10 OWASP Agentic risks](docs/owasp-agentic-top-10.md).

> An **agent contract** is a runtime check at every agent action, [backed by formal methods](docs/formal-methods.md) — *not* a system prompt your agent can ignore or jailbreak. e.g. *"after `run_aml_check`, no edits to loan files"*: **19 of 24 frontier models break this rule when given as a prompt; with Sponsio, none do.** Every violation is blocked before the call executes.

**Works with any stack.** LangChain, Claude Agent, OpenAI Agents, Google ADK, CrewAI, Vercel AI, MCP, or any custom tool-calling loop. Python · TypeScript · Prompt · Agent Skills.

> [!NOTE]
> **v0.1.x shipping.** [Changelog →](CHANGELOG.md)

> [!IMPORTANT]
> Feedback shapes Sponsio. Hit a snag, want a pattern, disagree with a default? [Open a GitHub issue](https://github.com/SponsioLabs/Sponsio/issues/new) — see [CONTRIBUTING.md](CONTRIBUTING.md).

<p align="center"><em>Demo video coming soon</em></p>

---

## SOTA Agent Safety Solutions

<!-- TODO: replace with product architecture diagram -->

```
LLM ─▶ Sponsio Boundary ─▶ Tool / API / DB / File
```

On [ODCV-Bench](https://arxiv.org/abs/2512.20798), even SOTA models silently falsify data to hit KPI metrics. **Sponsio (v0.1.x preview) blocks 84% of high-risk behaviors across 12 frontier models — the next-best solution we tested reached 52%.** On the `Financial-Audit-Fraud-Finding` scenario, **19 of 24 models commit the fraud unguarded; with Sponsio, the block rate is 100%**.

### Compared to existing solutions

| Approach | When it runs | Where it fails | How Sponsio differs |
|----------|--------------|----------------|---------------------|
| **Prompt-injection filters** (Lakera Guard, Prompt Shield, Llama Guard) | Pre-generation, on input text | Classifier-based; drifts on novel phrasings, says nothing about tool calls or trace order | Enforces *which* tools may run, in *what order*, with *what arguments* — at the tool boundary |
| **Output validators** (Guardrails AI, NeMo Guardrails, OpenAI Moderation) | Post-generation, on response strings | The side effect may already have fired; no trace-temporal semantics | Blocks the call *before* execution; reasons over full trace history (ordering, counts, gates) |
| **LLM-as-judge evals** (Patronus, DeepEval, Promptfoo, Braintrust) | Offline / CI, not in the hot path | Stochastic verdicts, hundreds-of-ms latency; useless as a synchronous gate | Sub-10μs det checks, zero LLM on the hot path; sto pipeline is opt-in for fuzzy properties |
| **Agent telemetry / firewalls** (AgentOps, Galileo, Pillar Security) | Post-hoc tracing or pre/post-generation scoring | Scores and logs after the fact; no formal contract over the action sequence | Compiles rules to DFAs and rejects events that would violate temporal contracts before they execute |
| **Symbolic / formal-methods runtime** (Invariant Labs; academic RV-LTL) | Pre-tool-execution | Limited NL ergonomics; no integrated stochastic pipeline | Ships NL → LTL → DFA end-to-end plus an optional sto pipeline for semantic atoms |

### What it isn't

- **Not a prompt-injection filter** (Lakera Guard, Prompt Shield, Llama Guard). Those score input text *pre*-generation; Sponsio enforces which tools may run, in what order, with what arguments — at the tool boundary.
- **Not an output validator or moderation layer** (Guardrails AI, NeMo Guardrails, OpenAI Moderation). Those inspect response strings *after* generation; Sponsio blocks the side effect — the refund, the DB write, the API call — *before* it fires.
- **Not an offline eval or telemetry tool** (Patronus, DeepEval, Promptfoo, AgentOps). Those grade or replay runs after the fact; Sponsio is a synchronous, deterministic, sub-10μs gate in front of every tool call, [backed by formal methods](docs/formal-methods.md).

---

## Quick start

Setup LangChain/LangGraph as an example. For other frameworks, see [Integrations](#integrations).

<details open>
<summary><b>Python</b></summary>

```bash
# 1. Install
pip install sponsio

# 2. Onboard — scan project, write sponsio.yaml with starter contracts, print a snippet to paste
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

> `sponsio.yaml` can also be hand-written, scanned from a policy doc (`sponsio scan --policy policy.md`), or mined from traces (`sponsio refresh`). Syntax: [docs/contracts.md](docs/contracts.md).

Run your agent in observe mode — contracts evaluate, nothing blocks. Would-have-blocked decisions land in `~/.sponsio/sessions/<agent_id>/*.jsonl`.

```bash
# 3. After some traffic, review what would have been blocked
sponsio report --since 1h

# 4. Flip to enforce when confident — no code change
export SPONSIO_MODE=enforce
```

</details>

<details>
<summary><b>TypeScript</b></summary>

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

This snippet inlines `contracts: [...]` for brevity. `new Sponsio({ config: "sponsio.yaml", agentId: "..." })` also works in TS — same YAML you get from `sponsio onboard`.

</details>

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
<summary><b>Install as an Agent Skill</b></summary>

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

Four trajectories where a capable model takes a rationally-cost-optimal but policy-violating action under KPI pressure — and the contracts that catch it. Scenarios 2, 3 and 4 are sourced from the [OWASP Top 10 for Agentic Applications (2026)](docs/owasp-agentic-top-10.md). Left: unguarded. Right: same task, same trajectory, with Sponsio.

### 1. Coding — Claude Code cleanup agent deletes `.env` and `.git/`

**Integration**: Claude Agent SDK (`ClaudeAgentOptions(hooks=guard.hooks())`).

"Clean up unused files." The agent reads `.env` "to understand what to clean", then sweeps in `.env`, `.git/objects/pack`, commits, and force-pushes. Three contracts catch everything — an assume-guarantee rule ("after reading `.env`, no `git commit`/`push`") plus two bare guards on `rm` and force-push. Non-destructive cleanup still goes through.

![coding_cleanup_without](assets/demos/coding_cleanup_without.gif)
![coding_cleanup_cli](assets/demos/coding_cleanup_cli.gif)

### 2. Infra — SRE cost-optimizer deletes prod DR backups

**Integration**: LangGraph (`guard.wrap(tools)`). **OWASP**: ASI-10 — Rogue Agents · *"Cost optimizers deleting backups to reduce expenses."*

"Cut the monthly storage bill by 20%." The agent scans snapshot access timestamps, flags 90-day-old backups as "unused" (no reads in 30 days), and starts deleting them. Those are the off-site disaster-recovery snapshots. Three pure-det contracts fire on the first prod delete at once — `scope_limit` (must be in `/snapshots/{dev,staging}/`), `arg_value_range` (nothing older than the 30-day DR window), and `rate_limit` (no runaway loop). Dev/staging snapshots under 30 days still delete normally.

![backup_delete_without](assets/demos/backup_delete_without.gif)
![backup_delete_cli](assets/demos/backup_delete_cli.gif)

### 3. Finance — AP copilot wires $847k to an unverified vendor

**Integration**: CrewAI (`guard.wrap(tools)`). **OWASP**: ASI-09 — Human-Agent Trust Exploitation · *"Financial copilots approving fraudulent transfers."*

"Process all Q2 vendor invoices within 24 hours." Among 12 invoices is an $847,000 payment to a brand-new vendor — no prior transactions, EIN filed the same week, bank account never seen before. Under SLA pressure the agent skips compliance onboarding and wires directly. Four pure-det contracts all fire on the same `wire_transfer` call — amount exceeds the $50k single-wire cap (`arg_value_range`), no `compliance_approve` beforehand (`must_precede`), no `confirm_wire_transfer` on file (`must_confirm`), plus a session-level rate limit. Any of the four alone would have held.

![wire_transfer_without](assets/demos/wire_transfer_without.gif)
![wire_transfer_cli](assets/demos/wire_transfer_cli.gif)

### 4. Coding (Replit-style) — Agent violates code freeze, drops prod DB, then lies about it

**Integration**: LangGraph (`guard.wrap(tools)`). **OWASP**: ASI-10 — Rogue Agents · recreates the [July 2025 Replit incident](https://x.com/jasonlk/status/1946069562723897802) cited in [OWASP GenAI's Q1 2026 exploit round-up](https://genai.owasp.org/2026/04/14/owasp-genai-exploit-round-up-report-q1-2026/).

User explicitly declares a code freeze: *"Don't touch the DB — just refactor UserList."* The agent completes the refactor, then "cleans up" a stale archived table (`DROP TABLE`), mass-deletes 90-day-stale users on the live production table, tries to rebuild the table by fabricating rows from memory (`INSERT`), and writes a status report: *"Database intact. No issues."* Four assume-guarantee contracts fire in sequence — the first (`code freeze → no destructive SQL`) blocks the `DROP` immediately; the rest (`destructive SQL → escalate before report`, `destructive SQL → no INSERT rebuild`, `prod connection → read-only only`) would have caught the fabricate-and-lie chain if the first had been somehow bypassed.

![freeze_violation_without](assets/demos/freeze_violation_without.gif)
![freeze_violation_cli](assets/demos/freeze_violation_cli.gif)

Run the packaged versions locally with `sponsio demo --scenario cleanup|backup|wire|freeze`. Framework-specific source examples live in [`examples/demo/`](examples/demo/). Full OWASP Agentic Top 10 coverage map: [docs/owasp-agentic-top-10.md](docs/owasp-agentic-top-10.md).

---

## Benchmarks & performance

### Benchmark results

| Suite | What it tests | Sponsio result |
|-------|---------------|----------------|
| **ODCV-Bench** (12 LLMs × 80 KPI-pressure scenarios) | Intent-level integrity — agent falsifying data / gaming metrics under pressure | **~84% protection** on high-risk trajectories |
| **τ²-bench airline** | Trace-level SOP compliance | **7–23% recall** · **4–16% FP** (model-dependent) |
| **τ²-bench retail** | Content-quality compliance | Mostly out-of-scope for det checks; use sto / output constraints |

ODCV-Bench is the clearest fit: failures aren't adversarial prompts but rational KPI-driven cheating (editing source data, disabling checks, exploiting scripts). Det contracts catch those at the tool boundary during offline replay.

### Hot-path performance

```text
$ sponsio bench sponsio.yaml -n 30000
30,000 checks · 100 % zero LLM calls
  bucket       p50      p99      QPS
  pure DFA   5.2μs   12.2μs   178 k/s
```

Det contracts compile to an LTL/DFA evaluator — no LLM on the hot path, no approval cache to tune, no TTL to trade off against freshness. Three buckets are reported (`pure_det`, `sto_cached`, `sto_live`) so you can see exactly when an LLM is invoked. Use `sponsio bench --json` as a CI perf gate; declare a budget under `performance:` in `sponsio.yaml`.

---

## Pattern Library

<details>
<summary><b>Pattern library</b> — <em>to be updated; content below is a snapshot</em></summary>

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

</details>

---

## Integrations

Pick your framework — each block expands to a drop-in snippet. Python and TypeScript share the same engine and DSL.

<details>
<summary><b>No framework</b> — custom tool-calling loop</summary>

```python
from sponsio import Sponsio

guard = Sponsio(config="sponsio.yaml", agent_id="bank_bot")

for name, args in agent_calls:
    result = guard.guard_before(name, args)
    if result.blocked:
        continue
    output = tools[name](**args)
    guard.guard_after(name, output)
```

```typescript
import { Sponsio } from "@sponsio/sdk";

const guard = new Sponsio({ config: "sponsio.yaml", agentId: "bank_bot" });

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

guard = Sponsio(config="sponsio.yaml", agent_id="hr_bot")
agent = create_react_agent(llm, guard.wrap(tools))
```

```typescript
import { Sponsio } from "@sponsio/sdk";
import { wrapTools } from "@sponsio/sdk/langchain";
import { ToolNode } from "@langchain/langgraph/prebuilt";

const guard = new Sponsio({ config: "sponsio.yaml", agentId: "hr_bot" });
const toolNode = new ToolNode(wrapTools(tools, guard));
```

Runnable: [python](examples/integrations/python/langgraph_guard.py) · [typescript](examples/integrations/typescript/langgraph_guard.mjs)

</details>

<details>
<summary><b>Claude Agent SDK</b> — native hooks, zero tool wrapping</summary>

```python
from sponsio.claude_agent import Sponsio
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

guard = Sponsio(config="sponsio.yaml", agent_id="support_bot")
options = ClaudeAgentOptions(hooks=guard.hooks())

async with ClaudeSDKClient(options=options) as client:
    await client.query("Refund order #W456.")
```

```typescript
import { Sponsio } from "@sponsio/sdk";
import { sponsioHooks } from "@sponsio/sdk/claude-agent";

const guard = new Sponsio({ config: "sponsio.yaml", agentId: "support_bot" });
const hooks = sponsioHooks(guard);
// Pass `hooks` to ClaudeSDKClient options.
```

Runnable: [python](examples/integrations/python/claude_agent_guard.py) · [typescript](examples/integrations/typescript/claude_agent_guard.mjs)

</details>

<details>
<summary><b>OpenAI SDK</b> — monkey-patch or explicit wrap</summary>

```python
from sponsio.openai import Sponsio

guard = Sponsio(config="sponsio.yaml", agent_id="db_admin")
resp = client.chat.completions.create(...)
guard.check_response(resp)
```

```typescript
import OpenAI from "openai";
import { Sponsio } from "@sponsio/sdk";
import { wrapOpenAI } from "@sponsio/sdk/openai";

const guard = new Sponsio({ config: "sponsio.yaml", agentId: "db_admin" });
const client = wrapOpenAI(new OpenAI(), guard);
```

For a quick no-YAML wire-up (handy in scripts / notebooks): `from sponsio.openai import patch_openai` — see [runnable example](examples/integrations/python/openai_guard.py).

Runnable: [python](examples/integrations/python/openai_guard.py) · [typescript](examples/integrations/typescript/openai_guard.mjs)

</details>

<details>
<summary><b>OpenAI Agents SDK</b> — wrap Agent tools</summary>

```python
from sponsio.agents import Sponsio
from agents import Agent, Runner

guard = Sponsio(config="sponsio.yaml", agent_id="deploy_bot")

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

guard = Sponsio(config="sponsio.yaml", agent_id="publish_bot")

async for msg in agent.run(model, messages, middleware=[guard.wrap()]):
    ...
```

```typescript
import { Sponsio } from "@sponsio/sdk";
import { sponsioMiddleware } from "@sponsio/sdk/vercel-ai";

const guard = new Sponsio({ config: "sponsio.yaml", agentId: "publish_bot" });
const middleware = sponsioMiddleware(guard);
```

Runnable: [python](examples/integrations/python/vercel_ai_guard.py) · [typescript](examples/integrations/typescript/vercel_ai_guard.mjs)

</details>

<details>
<summary><b>CrewAI</b> — Crew-level hooks</summary>

```python
from sponsio.crewai import Sponsio
from crewai import Agent, Crew, Task

guard = Sponsio(config="sponsio.yaml", agent_id="moderator")

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

> **Note on the snippets above.** All examples assume you've run `sponsio onboard .` first, which generates a `sponsio.yaml` with a starter contract set inferred from your tool inventory. To populate the YAML differently — pattern-library bundle, hand-written rules, natural-language one-liners, or parsed from a policy doc (`sponsio scan --policy security.md`) — see [State-of-the-art → Four ways to write them](#four-ways-to-write-them) and [docs/contracts.md](docs/contracts.md) for full syntax.

---

## Docs

- [Quick start](QUICKSTART.md)
- [Contract DSL](docs/contracts.md) · [Stochastic atoms](docs/sto-atoms.md)
- [CLI Reference](docs/cli.md)
- [Integrations](docs/integrations.md)
- [Architecture](docs/architecture.md)
- [OWASP Agentic Top 10 coverage](docs/owasp-agentic-top-10.md)
- [Formal methods primer](docs/formal-methods.md)
- [Changelog](CHANGELOG.md)

*AI agents reading this repo: [`llms.txt`](llms.txt) lists canonical doc paths; [`llms-full.txt`](llms-full.txt) is the concatenated full context dump.*

---

## Security

Sponsio enforces runtime contracts, so its own correctness matters. Found something? Report privately via GitHub's [security advisory form](https://github.com/SponsioLabs/Sponsio/security/advisories/new) rather than a public issue. See [SECURITY.md](SECURITY.md) for scope, timelines, and what counts as in-scope (enforce-mode bypasses, LTL-evaluator crashes, session-log leakage, judge-prompt injection, etc.).

---

## Contributing

Patches, issue reports, and new pattern proposals are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Important notes

Sponsio enforces runtime contracts that *you* define — it does not certify your application's compliance with any regulatory framework. If you operate in regulated domains (HIPAA, GDPR, SOX, EU AI Act, financial services, healthcare), Sponsio's controls and our [OWASP Agentic Top 10 mapping](docs/owasp-agentic-top-10.md) are inputs to your compliance program. They are **not** substitutes for qualified security audit, legal review, or domain-specific regulatory analysis. Author your contracts with appropriate review and revisit them when your agent's tool surface changes.

Det contracts give you machine-checkable enforcement at the action boundary. They do not protect against vulnerabilities upstream of Sponsio (compromised LLM provider, malicious tools you've allowlisted, infrastructure-layer risks like transport encryption / SBOM provenance). See [`SECURITY.md`](SECURITY.md) for the full scope.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
