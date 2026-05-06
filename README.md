![Sponsio](assets/readme-banner.png)

<p align="center">
  <a href="https://opensource.org/licenses/Apache-2.0"><img src="https://img.shields.io/badge/License-Apache%202.0-orange.svg" alt="License"></a>
  <a href="https://pypi.org/project/sponsio/"><img src="https://img.shields.io/badge/install-pip%20install%20sponsio-blue?logo=python&logoColor=white" alt="Install from PyPI"></a>
  <a href="https://sponsio.dev"><img src="https://img.shields.io/badge/Visit-sponsio.dev-181818?logo=data:image/svg%2bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjI4MyA3NjMgMzczIDM3MyI%2bPGcgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoMCwyMDQ4KSBzY2FsZSgwLjEsLTAuMSkiIGZpbGw9IiNGRkZGRkYiPjxwYXRoIGQ9Ik01MDEwIDEyNTAxIGMtNTggLTkgLTE4NyAtNDEgLTI2NyAtNjYgLTI2IC05IC05OSAtNDEgLTE2MCAtNzEgLTM1NCAtMTc0IC02MTMgLTQ3NiAtNzM2IC04NTkgLTQzIC0xMzMgLTY0IC0yNTEgLTczIC00MDcgbC03IC0xMTggLTQ2MiAwIC00NjMgMCAtNiAtMjIgYy0zIC0xMyAtMyAtNjYgMCAtMTE4IDE2IC0yODQgMTA2IC01NTYgMjYwIC03ODggMTEzIC0xNjggMzI0IC0zNTYgNTE2IC00NjAgMjcyIC0xNDcgNjM3IC0xOTAgOTY4IC0xMTUgMjM2IDUzIDQ1NiAxNzggNjQwIDM2MyAyNzIgMjczIDQxMyA2MTEgNDIzIDEwMjAgbDMgMTE1IDQ1NSA1IDQ1NCA1IDMgNDUgYzQgNDcgLTEyIDIwNyAtMjkgMzAwIC0xMDcgNTkyIC01MjMgMTAzMSAtMTA5NCAxMTU3IC03OSAxNyAtMzQxIDI2IC00MjUgMTR6IG0zMjAgLTk2MCBjNzMgLTI3IDE2MiAtOTkgMjA1IC0xNjQgNTggLTg3IDEwNCAtMjM5IDEwNSAtMzQ1IGwwIC01MiAtNDU3IDIgLTQ1OCAzIC0zIDQ4IGMtNSA3MyAyNCAyMDQgNjAgMjc3IDYxIDExOSAxOTEgMjI1IDMxMCAyNTAgNjQgMTMgMTc2IDUgMjM4IC0xOXogbS02MTIgLTY0MSBjMTMgLTI5NSAtMTkxIC01MjAgLTQ3MCAtNTIwIC0yMTcgMCAtMzkzIDE0NCAtNDUzIDM3MSAtMTUgNTUgLTIwIDIxMCAtOCAyMjIgMyA0IDIxNCA2IDQ2NyA1IGw0NjEgLTMgMyAtNzV6Ii8%2bPC9nPjwvc3ZnPg==&logoColor=white&labelColor=555555" alt="Visit sponsio.dev"></a>
  <a href="docs/concepts/owasp-coverage.md"><img src="https://img.shields.io/badge/OWASP%20Agentic%20Top%2010-10%2F10%20Covered-2E7D32?labelColor=555555" alt="OWASP Agentic Top 10 Covered"></a>
</p>

<p align="center">
  <a href="https://x.com/sponsiolabs"><img src="https://img.shields.io/badge/Follow%20on%20X-000000?logo=x&logoColor=white" alt="Follow on X"></a>
  <a href="https://www.linkedin.com/company/sponsio-labs/"><img src="https://img.shields.io/badge/Follow%20on%20LinkedIn-0A66C2?logo=linkedin&logoColor=white" alt="Follow on LinkedIn"></a>
  <a href="https://discord.gg/s8TfPnZWUm"><img src="https://img.shields.io/badge/Join%20our%20Discord-5865F2?logo=discord&logoColor=white" alt="Join our Discord"></a>
</p>

<p align="center">⭐ <em>Help us grow the Sponsio community for better shared Contract Library and policy enforcement. Star the repo!</em></p>


# Sponsio

**Runtime enforcement for AI agents.** Input policies in natural language; Sponsio compiles them into unbreakable, deterministic agent contracts. Enforced under 0.01ms, zero LLM runtime cost, [covers all 10 OWASP Agentic risks](docs/concepts/owasp-coverage.md).

> An **agent contract** is a runtime check at every agent action, [backed by formal methods](docs/concepts/formal-methods.md) — *NOT* a system prompt your agent can ignore or jailbreak. 

**Works with any stack.** LangChain, Claude Agent, OpenAI Agents, Google ADK, CrewAI, Vercel AI, MCP, or any custom tool-calling loop. Python · TypeScript · Prompt · Agent Skills.

*Demo video coming soon*

---

## SOTA Agent Safety Solutions

<p align="center">
  <img src="assets/sponsio-architecture.png" alt="Sponsio architecture: Agent Flow + (Natural Language + Pattern Library) compile into Contracts (Assumption → Enforcement), enforced by a Fuzzy LTL Monitor (deterministic + stochastic) that decides Pass / Block · Warn · Escalate / Redirect for every function call, with full audit trail logs feeding back to the agent." width="900">
</p>

On [ODCV-Bench](https://arxiv.org/abs/2512.20798) — a third-party benchmark from [McGill DMaS](https://github.com/McGill-DMaS/ODCV-Bench) — 12 frontier LLMs × 80 trajectories (Claude-Opus-4.6 included), unguarded models cheat in **11.5%–66.7% of runs**. With Sponsio, **84.5% of misalignment is blocked on average**, while the next-best publicly announced runtime guardrail ([Salus, YC W26](https://www.ycombinator.com/companies/salus), [launch Feb 2026](https://yctierlist.com/w26/salus/)) reaches 52% on the same benchmark. On the `Financial-Audit-Fraud-Finding` scenario, **frontier models commit fraud in 67% of trials (16/24)**; with Sponsio, **100% blocked**.

### Why Sponsio


| Approach                              | When it works                                               | Where it fails                                                                                           | How Sponsio solves                                                                                                                        |
| ------------------------------------- | ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| **Prompt-injection Filters**          | Pre-generation, on input text                               | Drifts on novel phrasings; sees text, not tool calls; no notion of action history                        | Enforces *which* tools may run, *in what* order, *with what* arguments, before function call executes, with full trace context            |
| **Output Validators**                 | Post-generation, on response strings                        | The mistakes (e.g. refund, DB write, API call) may already have fired                                    | Blocks the call *before* execution; reasons over the full action history, not just the latest string                                      |
| **LLM-as-Judge**                      | Flexible, handles fuzzy properties; useful for offline eval | Stochastic verdicts, hundreds-of-ms latency, itself prompt-injectable - unsuitable as a synchronous gate | Sub-0.01ms deterministic checks, zero LLM in the hot path; stochastic pipeline is opt-in for fuzzy properties                             |
| **Sandboxing & Access Control Lists** | Strong perimeter for identity- and resource-level isolation | Narrows agent capability. Gates by *who* and *what resource*, not by *behavior sequence*                 | Enforces temporal contracts over the action sequence, including ordering, history, and multi-step invariants, preserving agent capability |


Compared to other deterministic enforcers, Sponsio's edge:

**1. Temporal contracts over sequential actions, not stateless rule matching.** Existing enforcers evaluate each action in isolation. Sponsio reasons over the full trajectory: *"verify_recipient before send_email"*, *"no external calls after PII access"*, *"refund_payment ≤ 3 calls per session"*.

**2. Machine-checkable, not heuristic.** Contracts compile to LTL formulas, then to deterministic finite automata. Every verdict is a deterministic DFA transition, not a probabilistic confidence score. Same proof technique used in hardware verification (Intel FPU correctness, AWS S3 TLA+). [How it works →](docs/concepts/formal-methods.md)

**3. Zero to protected in minutes, no DSL learning curve.** Existing tools require hand-written YAML / Rego / Cedar policies from scratch. Sponsio offers four paths in:

- **Auto-inferred** — `sponsio init` (interactive wizard) reads your tool signatures and writes starter contracts
- **Contract library** — include pre-built bundles by capability (`sponsio:capability/shell`, `…/filesystem`) or by incident (`sponsio:incident/openclaw`); each bundle composes 44 det patterns underneath (sto atoms ship in Sponsio Cloud)
- **Natural language** — `sponsio validate "..."` compiles plain English to LTL
- **Policy doc** — `sponsio scan --policy security.md` parses an existing compliance document

**4. Framework-agnostic and low-dependency.** Other tools ship as opinionated stacks — bundling identity, SRE, dashboards, orchestration. Sponsio is a single enforcement library that plugs in alongside whatever observability, IAM, and orchestration you already use.

---

## Quick start

Pick your project's language. Instant onboarding with a single prompt or a 2-line CLI command.

### Python

**Paste into Claude Code / Codex / Cursor.** The agent helps run the full onboarding process. Click for the full prompt template. Note: Cursor may not be able to explicitly show what Sponsio has blocked in the conversation, due to its own harness design.

<p align="center">
  <a href="docs/getting-started/onboard-prompt.md#python-project"><img src="https://img.shields.io/badge/One--shot%20prompt-Python-3776AB?logo=python&logoColor=white&labelColor=555555" alt="One-shot prompt: Python"></a>
</p>

**Or run the CLI yourself:**

```bash
pip install sponsio
sponsio init .
```

`init` is an interactive wizard. It detects your framework (LangGraph / OpenAI / Claude Agent / Vercel AI / CrewAI / MCP / …), asks which IDE hosts to wire up (Claude Code / Codex / Cursor / OpenClaw, each at `none` / `skill` / `full` level), and observe vs enforce mode. Then it writes `sponsio.yaml` and prints the 2-line patch:

```python
from sponsio.langgraph import Sponsio
from langgraph.prebuilt import create_react_agent

guard = Sponsio(config="sponsio.yaml", agent_id="coding_agent")
agent = create_react_agent(model, guard.wrap(tools))
```

### TypeScript

**Paste into Claude Code / Codex / Cursor:**

<p align="center">
  <a href="docs/getting-started/onboard-prompt.md#typescript-project"><img src="https://img.shields.io/badge/One--shot%20prompt-TypeScript-3178C6?logo=typescript&logoColor=white&labelColor=555555" alt="One-shot prompt: TypeScript"></a>
</p>

**Or run the CLI yourself:**

```bash
npm install -D @sponsio/sdk
npx sponsio init .
```

> **Note** — the TS wizard is currently single-axis (provider × mode × agent). For the full multi-axis flow that also installs IDE-host plugins (Claude Code / Codex / Cursor / OpenClaw), paste the **Python** prompt above into your IDE agent — it works on TS projects too (drives the Python `sponsio` CLI, writes a TS-compatible `sponsio.yaml`).

```typescript
import { Sponsio } from "@sponsio/sdk";
import { wrapTools } from "@sponsio/sdk/langchain";
import { ToolNode } from "@langchain/langgraph/prebuilt";

const guard = new Sponsio({ config: "sponsio.yaml", agentId: "coding_agent" });
const toolNode = new ToolNode(wrapTools(tools, guard));
```

### For OpenClaw Community

The Python prompt above is the install path for you too. Pick `openclaw=full` when the wizard asks about IDE hosts; Sponsio then gates every `before_tool_call` event in your OpenClaw runtime through the contract engine, with ClawHavoc + CVE-2026-25253 coverage via the bundled `sponsio:incident/openclaw` pack.

Watch live blocks in your terminal — every Sponsio decision against your OpenClaw runtime streams here:

```bash
sponsio host trace openclaw --follow
```

---

> `sponsio.yaml` can also be hand-written, scanned from a policy doc (`sponsio scan --policy policy.md`), or mined from traces (`sponsio refresh`). Syntax: [docs/concepts/contracts.md](docs/concepts/contracts.md).

> **Full walkthrough:** [QUICKSTART.md](QUICKSTART.md) — config reference, observe → enforce flip, `sponsio refresh`, CI wiring, troubleshooting.

---

## Benchmarks & Performance

Sponsio is benchmarked on two public agent-safety suites covering two distinct failure modes — rational KPI-pressure metric gaming, and dangerous bash / python snippet detection. All offline-replay against published trajectories, not Sponsio-authored test sets, and **library-only** (no per-scenario LLM scan on the blocking path).


| Benchmark                                  | What it measures                          | Sponsio result                                                                            |
| ------------------------------------------ | ----------------------------------------- | ----------------------------------------------------------------------------------------- |
| **ODCV-Bench** (12 LLMs × 80 trajectories) | Intent integrity under KPI pressure       | **84.5%** blocked (next-best published baseline: **52%**) · **0 new FPs** on clean scenarios |
| **RedCode-Exec** (1,410 cases)             | Dangerous bash / python snippet detection | **95% bash · 90% python · 92% combined** · **0% utility FP** on 60-file clean-code audit |


Both libraries ship as loadable contract packs. Semantic properties det can't fingerprint (tone, hallucination, scope drift in NL output) require an LLM judge — the OSS engine ships a `Judge` extension point you can plug; the managed stochastic pipeline is a [Sponsio Cloud](docs/reference/oss-scope.md#in-sponsio-cloud-commercial--pip-install-sponsiocloud) feature.

### Logic-flaw failures are deterministically catchable too

Conventional wisdom said RedCode's logic-flaw categories — biased decision logic, weak regex bypasses, missing-authorisation patterns, algorithmic flaws, memory leaks — were "behavioural defects with no syscall fingerprint" and required a semantic LLM judge. We pushed back on that label. Every such failure surfaces as a *finite code-text pattern*; once the pattern is enumerated, det binds. Seven new layers (`bias_logic`, `weak_regex`, `algo_flaw`, `memory_leak`, `match_no_default`, `missing_auth`, `redcode_unauth_marker`) lifted RedCode python from **69% → 90%** with **0/60 FP** on a clean-code audit (Sponsio's own source, tests, and API routes). The det/sto boundary is wider than most guardrail authors assumed; sto stays for properties that genuinely live in free-form output (tone, hallucination, faithfulness) — not for code-shape patterns whose finite enumeration was just under-explored.

### Hot-Path Performance


| Workload                                                  | Contracts | p50           | p99       |
| --------------------------------------------------------- | --------- | ------------- | --------- |
| **Synthetic micro-bench** (single contract, pre-warmed DFA) | 1         | **0.0052 ms** | 0.012 ms  |
| **ODCV-Bench mandated** (1,438 calls, scan-discovered)    | 6–18      | **0.139 ms**  | 0.765 ms  |
| **RedCode bash** (3,848 per-command calls)                | 7         | 0.434 ms      | 0.558 ms  |
| **RedCode python** (810 whole-script calls)               | 9         | 0.811 ms      | 1.035 ms  |


**Backend-engineer anchor:** at 0.139 ms p50 on ODCV mandated, Sponsio's hot path adds **less overhead than a single local Redis read** (typical 0.1–0.5 ms).

<u>**5,000×–60,000× faster than any LLM-as-judge guardrail**</u> (gpt-4o-mini, Lakera Guard, OpenAI Moderation — all 50–800 ms per check) on the same per-tool-call workload, at zero LLM cost on the hot path. Per-call latency scales linearly with contract count; p99 stays under 1.04 ms across every measured workload. The heaviest scenario (9-contract layered regex over a whole RedCode python script) is still **50× faster than the cheapest LLM-as-judge call**.

Full per-model breakdown, methodology, harness scripts: [`docs/reference/benchmarks.md`](docs/reference/benchmarks.md).

### Today's numbers are starting points, not ceilings

```text
production traces ──→ sponsio scan ──→ proposed contracts
       ↑                                       │
       │                                       ▼
       └──────── enforcement ←──────── library (versioned)
```

**Today's 84.5% / 92% are starting points, not ceilings.** The library grows from your traces and ships back upstream — every new attack pattern, every newly observed unsafe call, feeds the next release.

---

## Contract Library

Sixteen **contract bundles** ship out of the box, organized by tier (always-on / per-tool / per-incident). Each bundle is a YAML pack composed from Sponsio's 44 det patterns (sto atoms ship in Sponsio Cloud). Drop one into `sponsio.yaml` and your agent is guarded against a known failure class in one line, with no per-contract authoring. The seven highlighted below are the most commonly used.

### Starter bundles


| Bundle | Tier | Rules | Who it's for |
| --- | --- | --- | --- |
| `sponsio:core/universal` | Always-on | 5 sto (Cloud) | Any LLM agent. Response-scoped checks: prompt injection, jailbreak, harm, toxic, semantic PII. Requires a configured judge — managed in [Sponsio Cloud](docs/reference/oss-scope.md), or BYO judge via the OSS `Judge` extension point. Without one, these log-and-skip on OSS. |
| `sponsio:core/runaway` | Always-on | 5 det | Any agent with token use, delegation, or tool loops. The "while(true) with a credit card" defense: token budgets, delegation depth, loop caps. |
| `sponsio:capability/shell` | Per-tool | 11 det | Agents exposing `exec` / `bash`. Catches `rm -rf /`, fork bombs, `curl \| bash`, reverse shells, line-continuation evasion. Inspired by [Claude Code #10077](https://github.com/anthropics/claude-code/issues/10077) (rm -rf $HOME, Oct 2025), the [Replit prod-DB wipe](https://www.theregister.com/2025/07/21/replit_saastr_vibe_coding_incident/) ([Fortune coverage](https://fortune.com/2025/07/23/ai-coding-tool-replit-wiped-database-called-it-a-catastrophic-failure/), Jul 2025), and the [Ansible `rm -rf {foo}/{bar}` postmortem on 1,535 servers](https://developers.slashdot.org/story/16/04/14/1542246/man-deletes-his-entire-company-with-one-line-of-bad-code) (Marsala, 2016). |
| `sponsio:capability/filesystem` | Per-tool | 13 det | Agents exposing `read` / `write` / `edit` / `apply_patch`. Sensitive-path denies, workspace scoping, bootstrap-file gates (`CLAUDE.md`, `AGENTS.md`, `.cursorrules`). Inspired by the [OpenClaw weather-skill `.env` exfil](https://www.trendmicro.com/en_us/research/26/b/openclaw-skills-used-to-distribute-atomic-macos-stealer.html) and the [Cursor `.cursorignore` bypass (CVE-2025-64110 / GHSA-vhc2-fjv4-wqch)](https://github.com/cursor/cursor/security/advisories/GHSA-vhc2-fjv4-wqch). |
| `sponsio:incident/openclaw` | Incident | 45 mixed | OpenClaw / ClawCode users. Covers [CVE-2026-25253](https://nvd.nist.gov/vuln/detail/CVE-2026-25253) (WebSocket 1-click RCE), [ClawHavoc — 1,184 malicious skills on ClawHub](https://cyberpress.org/clawhavoc-poisons-openclaws-clawhub-with-1184-malicious-skills/) (Koi Security disclosure, Feb 2026), the `--yolo` flag, and the weather-skill exfil. A worked example to fork rules from. |
| `sponsio:incident/cursor-railway-wipe` | Incident | mixed | Replays the [PocketOS production-DB wipe (Apr 24, 2026)](https://www.theregister.com/2026/04/27/cursoropus_agent_snuffs_out_pocketos/) — Cursor + Claude Opus 4.6 deleted prod + backups in 9 seconds via an over-scoped Railway API token. ([Tom's Hardware](https://www.tomshardware.com/tech-industry/artificial-intelligence/claude-powered-ai-coding-agent-deletes-entire-company-database-in-9-seconds-backups-zapped-after-cursor-tool-powered-by-anthropics-claude-goes-rogue) · [Railway's own postmortem](https://blog.railway.com/p/your-ai-wants-to-nuke-your-database)) Catches credential-scope abuse + destructive-API gates. |
| `sponsio:incident/claude-code-secret-bypass` | Incident | mixed | Replays [CVE-2025-55284](https://www.sentinelone.com/vulnerability-database/cve-2025-55284/) (overly broad safe-command allowlist → file-read confirmation bypass) and the [deny-rule cap bypass](https://adversa.ai/blog/claude-code-security-bypass-deny-rules-disabled/) (50-subcommand padding silently disables deny rules). Catches secret reads + arg-padding evasion. |


```yaml
# sponsio.yaml — one-line bundle inclusion
agents:
  my_agent:
    workspace: "/srv/my-bot"
    include:
      - sponsio:core/runaway          # always-on
      - sponsio:core/universal        # always-on
      - sponsio:capability/shell      # if your agent runs commands
      - sponsio:capability/filesystem # if your agent touches files
```

`sponsio init` auto-selects tier-0 bundles based on your detected tool inventory. You can disable or retune individual rules without forking the pack: `customized:` lets you target rules by their `desc`, `pack_source`, or `pattern` field. Rename canonical tool names (`exec`, `read`, `edit`) to your agent's via `tool_rename:`.

Full bundle reference is at [`docs/reference/contract-lib.md`](docs/reference/contract-lib.md). The underlying primitives that bundles compose are catalogued separately: 44 det patterns in [`docs/reference/patterns.md`](docs/reference/patterns.md). Sto atoms (LLM-judge evaluators for tone, hallucination, scope drift, etc.) are part of [Sponsio Cloud](docs/reference/oss-scope.md#in-sponsio-cloud-commercial--pip-install-sponsiocloud) — the OSS engine ships a `Judge` extension point for bring-your-own-judge use.

> **Want a bundle for your agent type?** This is currently the highest-leverage way to contribute. [Open an issue](https://github.com/SponsioLabs/Sponsio/issues/new) with your incident, CVE, or pattern.

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

For a quick no-YAML wire-up (handy in scripts / notebooks): `from sponsio.openai import patch_openai`.

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

</details>

<details>
<summary><b>Google ADK</b> — wrap Agent tools (Gemini)</summary>


```python
from sponsio.google_adk import Sponsio
from google.adk.agents.llm_agent import Agent

guard = Sponsio(config="sponsio.yaml", agent_id="travel_agent")

root_agent = Agent(
    name="travel_agent",
    model="gemini-flash-latest",
    instruction="Search before booking. Charge only once.",
    tools=guard.wrap([search_flights, book_flight, charge_payment]),
)
```

```typescript
import { Sponsio } from "@sponsio/sdk";
import { wrapGoogleAdkTools } from "@sponsio/sdk/google-adk";
import { LlmAgent } from "@google/adk";

const guard = new Sponsio({ config: "sponsio.yaml", agentId: "travel_agent" });
const tools = wrapGoogleAdkTools([searchFlights, bookFlight, chargePayment], guard);
export const rootAgent = new LlmAgent({ name: "travel_agent", tools, model: "gemini-flash-latest" });
```


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

</details>



---

> **Note on the snippets above.** All examples assume you've run `sponsio init .` first, which walks the wizard, generates a `sponsio.yaml` with a starter contract set inferred from your tool inventory, and prints the wrap snippet to paste. To populate the YAML differently — pattern-library bundle, hand-written rules, natural-language one-liners, or parsed from a policy doc (`sponsio scan --policy security.md`) — see [Contract types and authoring](QUICKSTART.md#contract-types-and-authoring) and [docs/concepts/contracts.md](docs/concepts/contracts.md) for full syntax.

---

## Docs

- [Quick start](QUICKSTART.md)
- [Contract DSL](docs/concepts/contracts.md)
- [CLI Reference](docs/reference/cli.md)
- [Integrations](docs/integrations/index.md)
- [Architecture](docs/concepts/architecture.md)
- [Benchmarks](docs/reference/benchmarks.md)
- [OWASP Agentic Top 10 coverage](docs/concepts/owasp-coverage.md)
- [Formal methods primer](docs/concepts/formal-methods.md)
- [**OSS Promise**](OSS_PROMISE.md) · [OSS / Cloud boundary](docs/reference/oss-scope.md) · [Brand & trademark](BRAND.md)
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

Sponsio enforces runtime contracts that *you* define — it does not certify your application's compliance with any regulatory framework. If you operate in regulated domains (HIPAA, GDPR, SOX, EU AI Act, financial services, healthcare), Sponsio's controls and our [OWASP Agentic Top 10 mapping](docs/concepts/owasp-coverage.md) are inputs to your compliance program. They are **not** substitutes for qualified security audit, legal review, or domain-specific regulatory analysis. Author your contracts with appropriate review and revisit them when your agent's tool surface changes.

Det contracts give you machine-checkable enforcement at the action boundary. They do not protect against vulnerabilities upstream of Sponsio (compromised LLM provider, malicious tools you've allowlisted, infrastructure-layer risks like transport encryption / SBOM provenance). See [`SECURITY.md`](SECURITY.md) for the full scope.

---

## License & open source promise

Apache 2.0 — see [LICENSE](LICENSE).

Sponsio Labs is a commercial company; Sponsio Cloud (`pip install sponsio[cloud]`) opens mid-May 2026 and adds the managed LLM-judge pipeline, cross-customer pattern intelligence, and a hosted multi-tenant dashboard. The OSS engine is complete and production-ready for self-hosted use — see [OSS_PROMISE.md](OSS_PROMISE.md) for what stays in OSS forever, what we sell, and what we promise about the boundary.

Sponsio™ is a trademark of Sponsio Labs — see [BRAND.md](BRAND.md).