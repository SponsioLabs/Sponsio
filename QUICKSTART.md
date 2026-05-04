# Quick Start

Get Sponsio blocking an unsafe tool call in under 60 seconds — no API key, no framework SDK, no Docker.

> [!NOTE]
> **Stability (v0.1.x).** Det engine + LangGraph / Claude Agent SDK / OpenAI / Vercel AI integrations are production-ready. OTEL export (`sponsio.tracer.exporters.OtlpHttpExporter` + `sponsio export-sessions`) is **beta**. CrewAI and OpenAI Agents SDK integrations are **alpha** — surface may shift before 0.2.
>
> **Sponsio Cloud features** (`pip install sponsio[cloud]`): the managed LLM-judge sto pipeline, the multi-tenant `sponsio serve --dev` dashboard (FastAPI + React), and *cross-customer* pattern intelligence layered on top of `sponsio refresh`. The OSS engine ships the full deterministic engine, every framework adapter, the local-mining version of `sponsio refresh`, and an HTML report renderer; OSS installs log-and-skip stochastic contracts with a one-time warning. See [docs/reference/oss-scope.md](docs/reference/oss-scope.md) and [OSS_PROMISE.md](OSS_PROMISE.md) for the boundary.

## Architecture overview

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

Full design: [docs/concepts/architecture.md](docs/concepts/architecture.md).

---

## 1. Install

```bash
pip install sponsio
```

Optional extras (all pure-Python, no build step):

```bash
pip install "sponsio[all]"        # yaml config + llm discovery + OTEL export
```

## 2. See a contract fire

Three recorded unsafe-agent trajectories ship in the wheel. Replay one:

```bash
sponsio demo --scenario wire --fast
```

You'll see an accounts-payable agent try to wire $847k to an unverified vendor, and Sponsio block it on three fronts at once:

```text
  ━━━ ◒◓ sponsio ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ▎ contract · ap_copilot
  ▎ single wire capped at $50k
  ▎ enforce ▸ wire_transfer.amount must be in range [0, 50000]
  ▎
  ▎ contract · ap_copilot
  ▎ compliance_approve must precede wire_transfer
  ▎
  ▎ contract · ap_copilot
  ▎ wire_transfer needs an explicit confirm_wire_transfer
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  -> wire_transfer(to='Acme Logistics LLC', amount=847000, invoice_id='inv_044')
  ✗ enforce wire_transfer.amount must be in range [0, 50000] — VIOLATED → blocked
  ✗ enforce compliance_approve must precede wire_transfer — VIOLATED → blocked
  ✗ enforce wire_transfer requires confirmation (confirm_wire_transfer) — VIOLATED → blocked

  ✓ Outcome: wire blocked — exceeds cap, no compliance_approve, no confirm.
```

Other scenarios:

```bash
sponsio demo --scenario cleanup    # Claude Code agent deletes .env + .git/
sponsio demo --scenario backup     # SRE cost-optimizer deletes prod DR backups (OWASP ASI-10)
sponsio demo --scenario freeze     # Replit-style code-freeze violation + coverup (OWASP ASI-10)
sponsio demo --scenario wire --no-guard   # same trajectory without contracts
```

## 3. Wire it into your own project

One command — detects your agent framework, writes `sponsio.yaml` in observe mode, runs `sponsio doctor`, and prints the three lines to paste into your agent entry file:

```bash
sponsio onboard .
```

The `.` is the codebase to scan — any path works (`sponsio onboard src/`, `sponsio onboard /srv/agent`); it defaults to the current directory, so plain `sponsio onboard` is equivalent. `onboard` only reads; it writes a single `sponsio.yaml` into CWD.

Typical output:

```text
· framework: langgraph (found 1 `langgraph` import(s) (first: agent.py))
· provider: none (no provider credentials detected)
· starter-pack: +5 contract(s) from name-heuristic safety rules
· packs: +2 auto-selected (core/universal, core/runaway)
· wrote sponsio.yaml
· running doctor checks…

✓ sponsio.yaml
  tools:      2
  contracts:  17
  mode:       observe
  framework:  langgraph
  doctor:     8/9 ok, 1 warn

Add this to your agent entry point:

  from sponsio.langgraph import Sponsio
  guard = Sponsio(config="sponsio.yaml", agent_id="agent")
  agent = create_react_agent(model, guard.wrap(tools))
```

What it does:

- Detects framework (LangGraph · OpenAI · CrewAI · Claude Agent · Vercel AI · Agents SDK · MCP)
- Picks the best LLM provider for contract inference (Gemini free tier → Anthropic → OpenAI → local Ollama → none)
- Writes `sponsio.yaml` with inferred contracts plus pre-built packs (`sponsio:core/runaway`, `sponsio:core/universal`, etc.)
- Runs `sponsio doctor` and warns about anything unhealthy

No LLM key? `onboard` still ships a name-heuristic starter plus `sponsio:core/runaway` (token budgets, delegation depth, loop caps) — all deterministic, zero LLM calls.

After `onboard` finishes it prints a framework-specific 2-3 line patch — paste it into your agent entry file at the marked spot (the snippet's inline comment shows where the wrap must run *before* the agent is built). All framework adapters are a one-line import swap — see [`docs/integrations/index.md`](docs/integrations/index.md).

### TypeScript (Node.js)

If your agent is TypeScript, use the static scanner and the same `sponsio scan` / `sponsio.yaml` pipeline. Install the SDK, the `yaml` package (loaded when you use `Sponsio({ config: "sponsio.yaml" })`), and the scanner, then run `onboard` as a *subcommand* of the `sponsio-scan-ts` binary:

```bash
npm install @sponsio/sdk yaml
npm install -D @sponsio/sdk
npx sponsio onboard .
```

When the Python [`sponsio` CLI](https://pypi.org/project/sponsio/) is on `PATH`, that command pipes the extracted tool JSON into `sponsio scan` and writes a full `sponsio.yaml` (same as the manual pipe in [`/scan-ts`’s README](ts/packages/sdk/README.md)). If `sponsio` is not installed, it still writes a small observe-mode file with a few det-only `E: …` natural-language rules so the TypeScript `Sponsio` class can start without Python. `sponsio onboard . --llm` passes `--llm` through to `sponsio scan` (set `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` as in [`docs/reference/cli.md` → Provider matrix](docs/reference/cli.md#provider-matrix)).

## 4. Run your agent and observe

`sponsio.yaml` starts in **observe mode** — every contract is evaluated, nothing is blocked. Every would-have-blocked decision lands in `~/.sponsio/sessions/<agent_id>/*.jsonl`.

After exercising the agent, review what would have been blocked:

```bash
sponsio report --agent agent --since 24h
```

Or the live dashboard (Sponsio Cloud — `pip install sponsio[cloud]`):

```bash
sponsio serve --dev
# API → http://localhost:8000
# UI  → http://localhost:3000
```

## 5. Flip to enforce

Once the report is clean (false positives pruned from `sponsio.yaml`):

```bash
export SPONSIO_MODE=enforce       # no code change — env overrides yaml
```

Or bake it in:

```yaml
# sponsio.yaml
runtime:
  mode: enforce
```

Precedence: explicit ctor arg > env var (`SPONSIO_MODE`, `SPONSIO_DASHBOARD`) > yaml > default.

## Configuration

Single-file config in `sponsio.yaml` — full field reference in [`docs/concepts/contracts.md`](docs/concepts/contracts.md):

```yaml
version: 1
runtime:
  mode: observe                        # "enforce" | "observe"
  dashboard: http://localhost:8000     # URL | true | false | null

agents:
  my_bot:
    workspace: "/srv/my-bot"           # required by filesystem / incident packs
    include:                           # pre-built packs
      - sponsio:core/runaway           # token budgets, delegation depth, loop caps
      - sponsio:capability/filesystem
    contracts:                         # your own rules, added on top
      - desc: "no commits after reading .env"
        A: { pattern: called, args: [read, ".env"] }
        E: { ltl: "G(!called(git_commit) & !called(git_push))" }

judge:                                 # only when any include uses sto (LLM-judged contracts)
  provider: openai                     # openai | anthropic | gemini | ollama | (any OpenAI-compatible)
  model: gpt-4o-mini
  # api_key is read from env (OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY / …)
  # fallback_mode: allow               # allow | deny | skip — what to do if LLM times out
```

**API keys, full provider list, default models, `base_url` for OpenRouter / DeepSeek / Ollama / Azure:** see [`docs/reference/cli.md` → Provider matrix](docs/reference/cli.md#provider-matrix). The same env-var auto-detection applies to both `judge` (runtime) and `sponsio scan --llm` (onboarding).

Run `sponsio packs` to list shipped packs with rule counts and include syntax.

## Contract types and authoring

Three contract categories Sponsio enforces, all deterministic, all checked before the side effect:

| Type | What it catches | Natural-language rule (compiles to LTL) |
|------|-----------------|----------------------------------------|
| **Per-action** | Prohibited or required tool calls | *"no `rm -rf`"* · *"`confirm_with_user` before `delete_file`"* |
| **Sequential** | Out-of-order calls, post-gate tampering | *"`run_tests` before `deploy_production`"* · *"after `run_aml_check`, loan files immutable"* |
| **Bounded** | Retry loops, delegation fan-out, token runaway | *"`check_balance` at most 5 times"* · *"delegation depth ≤ 3"* |

Phrases above are how you write rules in `sponsio.yaml` — Sponsio compiles each into a Linear Temporal Logic formula for [machine-checkable enforcement](docs/concepts/formal-methods.md).

Four ways to author them, all feeding the same `sponsio.yaml`:

- **Auto-inferred** — `sponsio onboard` reads your tool signatures
- **Pattern library** — 29 patterns + starter bundles for Claude Code, OpenAI Agents SDK, CrewAI, MCP
- **Natural language** — `sponsio validate "..."` compiles plain English to LTL
- **Policy doc** — `sponsio scan --policy security.md` parses existing compliance docs

See [`docs/concepts/contracts.md`](docs/concepts/contracts.md) for the full DSL and atom vocabulary.

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

`--llm` works with whatever you have: `GOOGLE_API_KEY` (Gemini, **1500 req/day free**), `ANTHROPIC_API_KEY`, or `OPENAI_API_KEY`. For local / OpenAI-compatible endpoints (Ollama, OpenRouter, vLLM, Azure …), pass `--base-url`. Trace mining requires no LLM and works with OTLP/JSON, OTLP JSONL, native Sponsio JSON/JSONL, and Sponsio session logs. See the [provider matrix](docs/reference/cli.md#provider-matrix).

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

    customized:                             # silence specific rules without forking a pack
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
- Packs are read-only on disk but fully overridable. Use `customized:` with a `match:` clause (by `desc`, `pattern`, `pack_source`, or `source` tag) to tune, disable, or replace args without editing the pack file.

See [docs/concepts/contracts.md](docs/concepts/contracts.md) for the full field reference.

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
| Live coloured event stream     | `sponsio host trace --follow` (pure OSS, terminal)                                                                     |
| Local dev & contract iteration | `sponsio serve --dev` — API on `:8000`, dashboard on `:5173` (Sponsio Cloud — `pip install sponsio[cloud]`)            |
| Production observability       | OTEL export — `sponsio.tracer.exporters.OtlpHttpExporter` (in-process) or `sponsio export-sessions --to <url>` (batch) |
| Ad-hoc review                  | `guard.print_summary()` or `sponsio report --agent <id>`                                                               |


Push contract verdicts into your existing observability stack via OTEL — the
schema is documented in [docs/reference/observability.md](docs/reference/observability.md), and the
[`sponsio.tracer.exporters`](sponsio/tracer/exporters.py) module ships a
batching OTLP/HTTP exporter ready to wire into `Sponsio(otel_exporter=...)`.

### 6. Depth — stochastic contracts (Sponsio Cloud)

Once your det layer is stable, layer in fuzzy output-quality rules — tone,
scope, semantic PII, hallucination, metric integrity. The sto pipeline (LLM-
judge atoms + `RetryWithConstraint` strategy) ships in **Sponsio Cloud**
(`pip install sponsio[cloud]`). The OSS engine logs-and-skips sto contracts
with a one-time warning per contract; Cloud installs replace the stub with the
full sto path. See [docs/reference/oss-scope.md](docs/reference/oss-scope.md) for the boundary.

## Re-mine contracts from recent traces (Sponsio Cloud)

`sponsio.yaml` is not a one-shot — periodic re-mining of `source: trace` rules
ships in **Sponsio Cloud** (`pip install sponsio[cloud]`):

```bash
sponsio refresh --since 7d             # dry-run: structured diff per agent
sponsio refresh --since 7d --apply     # write it (backup at .sponsio.bak)
```

User-written rules, `source: scan`, `source: policy`, and anything under
`customized:` flow through unchanged. The OSS pure-static path (`sponsio scan`)
covers single-project re-scans without trace mining.

## Development Setup

To hack on Sponsio itself:

```bash
git clone https://github.com/SponsioLabs/Sponsio.git
cd Sponsio
pip install -e ".[all]"
pytest -xvs
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full development workflow.

## Troubleshooting

```bash
sponsio doctor                         # checks install, config, framework wiring
sponsio validate --config sponsio.yaml # parse + structural checks (CI-friendly)
sponsio check --trace trace.json --config sponsio.yaml --agent agent
```

More: [`docs/integrations/index.md`](docs/integrations/index.md) · [`docs/reference/cli.md`](docs/reference/cli.md) · [`docs/concepts/contracts.md`](docs/concepts/contracts.md) · [`docs/concepts/architecture.md`](docs/concepts/architecture.md).
