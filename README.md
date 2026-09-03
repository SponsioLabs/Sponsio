<p align="right">
  <b>English</b> ·
  <a href="./README.zh-CN.md">简体中文</a> ·
  <a href="./README.ja.md">日本語</a>
</p>

![Sponsio](https://raw.githubusercontent.com/SponsioLabs/Sponsio/main/assets/readme-banner.png)

<p align="center">
  <a href="https://opensource.org/licenses/Apache-2.0"><img src="https://img.shields.io/badge/License-Apache%202.0-orange.svg" alt="License"></a>
  <a href="https://pypi.org/project/sponsio/"><img src="https://img.shields.io/badge/install-pip%20install%20--pre%20sponsio-blue?logo=python&logoColor=white" alt="Install from PyPI"></a>
  <a href="https://sponsio.dev"><img src="https://img.shields.io/badge/Visit-sponsio.dev-181818?logo=data:image/svg%2bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjI4MyA3NjMgMzczIDM3MyI%2bPGcgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoMCwyMDQ4KSBzY2FsZSgwLjEsLTAuMSkiIGZpbGw9IiNGRkZGRkYiPjxwYXRoIGQ9Ik01MDEwIDEyNTAxIGMtNTggLTkgLTE4NyAtNDEgLTI2NyAtNjYgLTI2IC05IC05OSAtNDEgLTE2MCAtNzEgLTM1NCAtMTc0IC02MTMgLTQ3NiAtNzM2IC04NTkgLTQzIC0xMzMgLTY0IC0yNTEgLTczIC00MDcgbC03IC0xMTggLTQ2MiAwIC00NjMgMCAtNiAtMjIgYy0zIC0xMyAtMyAtNjYgMCAtMTE4IDE2IC0yODQgMTA2IC01NTYgMjYwIC03ODggMTEzIC0xNjggMzI0IC0zNTYgNTE2IC00NjAgMjcyIC0xNDcgNjM3IC0xOTAgOTY4IC0xMTUgMjM2IDUzIDQ1NiAxNzggNjQwIDM2MyAyNzIgMjczIDQxMyA2MTEgNDIzIDEwMjAgbDMgMTE1IDQ1NSA1IDQ1NCA1IDMgNDUgYzQgNDcgLTEyIDIwNyAtMjkgMzAwIC0xMDcgNTkyIC01MjMgMTAzMSAtMTA5NCAxMTU3IC03OSAxNyAtMzQxIDI2IC00MjUgMTR6IG0zMjAgLTk2MCBjNzMgLTI3IDE2MiAtOTkgMjA1IC0xNjQgNTggLTg3IDEwNCAtMjM5IDEwNSAtMzQ1IGwwIC01MiAtNDU3IDIgLTQ1OCAzIC0zIDQ4IGMtNSA3MyAyNCAyMDQgNjAgMjc3IDYxIDExOSAxOTEgMjI1IDMxMCAyNTAgNjQgMTMgMTc2IDUgMjM4IC0xOXogbS02MTIgLTY0MSBjMTMgLTI5NSAtMTkxIC01MjAgLTQ3MCAtNTIwIC0yMTcgMCAtMzkzIDE0NCAtNDUzIDM3MSAtMTUgNTUgLTIwIDIxMCAtOCAyMjIgMyA0IDIxNCA2IDQ2NyA1IGw0NjEgLTMgMyAtNzV6Ii8%2bPC9nPjwvc3ZnPg==&logoColor=white&labelColor=555555" alt="Visit sponsio.dev"></a>
</p>

<p align="center">
  <a href="https://x.com/sponsiolabs"><img src="https://img.shields.io/badge/Follow%20on%20X-000000?logo=x&logoColor=white" alt="Follow on X"></a>
  <a href="https://www.linkedin.com/company/sponsio-labs/"><img src="https://img.shields.io/badge/Follow%20on%20LinkedIn-0A66C2?logo=linkedin&logoColor=white" alt="Follow on LinkedIn"></a>
  <a href="https://discord.gg/s8TfPnZWUm"><img src="https://img.shields.io/badge/Join%20our%20Discord-5865F2?logo=discord&logoColor=white" alt="Join our Discord"></a>
</p>


# Sponsio

<p align="center">
  <img src="https://raw.githubusercontent.com/SponsioLabs/Sponsio/main/assets/sponsio-comparison-freeze.png" alt="Same coding agent under a declared code freeze. Without Sponsio it drops the prod users table, back-fills fabricated rows, and files a status report that hides the damage. With Sponsio the first destructive SQL is blocked pre-execution: 35 checks, 100% deterministic, 0 LLM calls, p50 13µs." width="900">
</p>
Sponsio checks an agent's tool calls before they run. A rule can look at what already happened, so "check the policy before issuing a refund" is one rule instead of a paragraph of prompt. Each check takes under 0.01 ms and calls no model. Works with LangChain, Claude Agent, OpenAI Agents, Google ADK, CrewAI, Vercel AI, MCP, or any custom tool-calling loop, in Python or TypeScript.

> An **agent contract** is a runtime rule that is checked at every agent action, [backed by formal methods](docs/concepts/formal-methods.md).

> **v0.2.0a13 alpha is out.** `pip install --pre sponsio`. Bundles that could not load now load: `include: sponsio:incident/openclaw` raised instead of arming its 37 rules, and five patterns the catalog documents were missing from the registry the config loader reads, so `pattern: no_pii` failed while the same rule in English compiled. The install badge left out `--pre`, so it pointed at the last stable release, 0.1.1, which is much older. See the [release notes](https://github.com/SponsioLabs/Sponsio/releases/tag/v0.2.0a13).

---

## How Sponsio works

<p align="center">
  <img src="https://raw.githubusercontent.com/SponsioLabs/Sponsio/main/assets/sponsio-architecture.png" alt="Sponsio architecture: Agent Flow + (Natural Language + Pattern Library) compile into Contracts (Assumption → Enforcement), enforced by a Fuzzy LTL Monitor (deterministic + stochastic) that decides Pass / Block · Warn · Escalate / Redirect for every function call, with full audit trail logs feeding back to the agent." width="900">
</p>

On [ODCV-Bench](https://github.com/McGill-DMaS/ODCV-Bench) (12 frontier LLMs × 80 trajectories), unguarded models cheat in 11.5%–66.7% of runs. **With Sponsio, 95.6% of misalignment is avoided on average; 24/36 high-risk scenarios at 100%.** On the `Financial-Audit-Fraud-Finding` scenario, frontier models commit fraud in 16/24 trials; **Sponsio blocks 18/19**. On RedCode-Exec (1,410 cases), Sponsio reaches **98.9% combined** (bash 98.3% · python 99.4%, lifted from 92.4% by a 4-iteration self-improvement loop), with 0 false positives on a 60-file clean-code audit. These are the open-core numbers; the Cloud version's LLM-judge layer takes ODCV-Bench to **~99%** and RedCode-Exec to **99.4%**. [Book a demo](https://calendar.app.google/ZZmthdxsDXCsHbJk9) for the Cloud and Enterprise versions.

One contract takes p50 **0.0052 ms** to check. The heaviest ODCV workload, 19 contracts on every call, takes **0.139 ms**. An LLM-as-judge guardrail takes 50 to 800 ms, so this is **5,000× to 60,000× faster**, and it calls no model. p99 stays near 1 ms on every workload measured.

See the [full benchmark methodology and per-model breakdown](docs/reference/benchmarks.md), [how Sponsio compares against prompt filters, output validators, LLM-as-judge, and sandboxing](docs/why.md), or dive into the [architecture](docs/concepts/architecture.md) and [formal methods primer](docs/concepts/formal-methods.md).

---

## Quick start

Two ways in: paste a prompt into your coding agent, or run the CLI yourself.

**Paste into Claude Code / Codex / Cursor.** The agent walks the full onboarding flow:

<p align="center">
  <a href="docs/getting-started/onboard-prompt.md#python-project"><img src="https://img.shields.io/badge/One--shot%20prompt-Python-3776AB?logo=python&logoColor=white&labelColor=555555" alt="One-shot prompt: Python"></a>
  &nbsp;
  <a href="docs/getting-started/onboard-prompt.md#typescript-project"><img src="https://img.shields.io/badge/One--shot%20prompt-TypeScript-3178C6?logo=typescript&logoColor=white&labelColor=555555" alt="One-shot prompt: TypeScript"></a>
</p>

**Or run the CLI yourself**:

```bash
pip install --pre sponsio   # or: npm install -D @sponsio/sdk@alpha
sponsio init .              # asks what you use, then writes sponsio.yaml
```

The wizard auto-detects your framework and prints the right wrap snippet. For manual wiring, see [all supported integrations](docs/integrations/index.md). [OpenClaw users](docs/integrations/openclaw.md) get bundled ClawHavoc and CVE-2026-25253 coverage out of the box. For config reference, observe → enforce flip, and CI wiring, see the [full walkthrough](QUICKSTART.md).

**Watching runs, and sharing a rulebook.** Enforcement is local and needs no account. If you want to see what your agent did, or keep the rulebook somewhere a person reviews it before it arms, [app.sponsio.dev](https://app.sponsio.dev) is the hosted side. One line puts a run on screen:

```python
import sponsio
import sponsio.bridge

guard = sponsio.Sponsio(config="sponsio.yaml", agent_id="mailer", mode="enforce")
run = sponsio.bridge.attach(guard)
```

`sponsio push sponsio.yaml` uploads a rulebook as a draft. It does not arm. A person publishes it in the console, and `sponsio pull` or `config="sponsio://default"` brings the reviewed version back. Sending is best effort, so a console that is down never blocks the agent. See [the hosted console](docs/getting-started/hosted-console.md).

**Drafting contracts from natural language.** `sponsio validate "<rule in plain English>"` turns a plain-English rule into a contract you can read back. Treat the output as a starting draft to review and adjust before you enforce. The determinism is in how contracts are *enforced* at runtime, not in how they're drafted.

---

## Contract Library

**22 contract bundles** ship out of the box, organized by tier (always-on / per-tool / per-incident). Each bundle is a YAML pack composed from Sponsio's deterministic patterns. Drop one into `sponsio.yaml` and your agent is guarded against a known failure class in one line, with no per-contract authoring.

```yaml
# sponsio.yaml: one-line bundle inclusion
agents:
  my_agent:
    workspace: "/srv/my-bot"
    include:
      - sponsio:capability/destructive  # gate irreversible actions
      - sponsio:capability/shell        # if your agent runs commands
      - sponsio:capability/filesystem   # if your agent touches files
```

See the [full bundle reference](docs/reference/contract-lib.md) for all 22 bundles, or the [48 underlying patterns](docs/reference/patterns.md) for the primitives they compose. Want a bundle for your agent type? That is the most useful thing to contribute right now. [Open an issue](https://github.com/SponsioLabs/Sponsio/issues/new) with your incident, CVE, or pattern.

---

## Contributing

Patches, issue reports, and new pattern proposals are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md). Sponsio's threat model draws on public security research; e.g. Simon Willison's ["Lethal Trifecta"](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/) shaped our [multi-tool composition contracts](sponsio/contracts/incident/mcp-composition.yaml). Have a threat model we should defend against? [Open an issue](https://github.com/SponsioLabs/Sponsio/issues/new).

---

## License

Apache 2.0 ([LICENSE](LICENSE)).

*AI agents reading this repo: [`llms.txt`](llms.txt) lists canonical doc paths; [`llms-full.txt`](llms-full.txt) is the concatenated full context dump.*
