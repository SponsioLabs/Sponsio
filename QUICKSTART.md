# Quick Start

Get Sponsio blocking an unsafe tool call in under 60 seconds — no API key, no framework SDK, no Docker.

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
sponsio demo --scenario loan --fast
```

You'll see a loan-approval agent try to falsify an AML check, and Sponsio block it:

```text
  ━━━ ◒◓ sponsio ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ▎ contract · loan_agent
  ▎
  ▎ assume  ▸ AML check has been run
  ▎ enforce ▸ loan applications must not be edited after AML
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  -> run_aml_check(application_file='application_002.json')
  -> falsify_application(file='application_002.json', ...)
  ✗ enforce loan applications must not be edited after AML — VIOLATED → blocked

  ✓ Outcome: AML audit trail intact; unsafe approval is blocked.
```

Other scenarios:

```bash
sponsio demo --scenario cleanup    # Claude Code agent deletes .env + .git/
sponsio demo --scenario trial      # Clinical trial recruiter forges patient records
sponsio demo --scenario loan --no-guard   # same trajectory without contracts
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

Pass `--apply` to additionally patch your agent entry file in-place (with a `.sponsio.bak` backup). Currently supported for LangGraph / LangChain; other frameworks print the snippet and you paste it yourself. Other framework adapters are a one-line import swap — see [`docs/integrations.md`](docs/integrations.md).

### TypeScript (Node.js)

If your agent is TypeScript, use the static scanner and the same `sponsio scan` / `sponsio.yaml` pipeline. Install the SDK, the `yaml` package (loaded when you use `Sponsio({ config: "sponsio.yaml" })`), and the scanner, then run `onboard` as a *subcommand* of the `sponsio-scan-ts` binary:

```bash
npm install @sponsio/sdk yaml
npm install -D @sponsio/scan-ts
npx sponsio-scan-ts onboard .
```

When the Python [`sponsio` CLI](https://pypi.org/project/sponsio/) is on `PATH`, that command pipes the extracted tool JSON into `sponsio scan` and writes a full `sponsio.yaml` (same as the manual pipe in [`ts-scanner`’s README](ts-scanner/README.md)). If `sponsio` is not installed, it still writes a small observe-mode file with a few det-only `E: …` natural-language rules so the TypeScript `Sponsio` class can start without Python. `sponsio-scan-ts onboard . --llm` passes `--llm` through to `sponsio scan` (set `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` as in [`docs/cli.md` → Provider matrix](docs/cli.md#provider-matrix)).

## 4. Run your agent and observe

`sponsio.yaml` starts in **observe mode** — every contract is evaluated, nothing is blocked. Every would-have-blocked decision lands in `~/.sponsio/sessions/<agent_id>/*.jsonl`.

After exercising the agent, review what would have been blocked:

```bash
sponsio report --agent agent --since 24h
```

Or the live dashboard:

```bash
sponsio serve --dev
# API → http://localhost:8000
# UI  → http://localhost:5173
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

Single-file config in `sponsio.yaml` — full field reference in [`docs/contracts.md`](docs/contracts.md):

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

**API keys, full provider list, default models, `base_url` for OpenRouter / DeepSeek / Ollama / Azure:** see [`docs/cli.md` → Provider matrix](docs/cli.md#provider-matrix). The same env-var auto-detection applies to both `judge` (runtime) and `sponsio scan --llm` (onboarding).

Run `sponsio packs` to list shipped packs with rule counts and include syntax.

## Re-mine contracts from recent traces

`sponsio.yaml` is not a one-shot. Periodically refresh the `source: trace` rules:

```bash
sponsio refresh --since 7d             # dry-run: structured diff per agent
sponsio refresh --since 7d --apply     # write it (backup at .sponsio.bak)
```

User-written rules, `source: scan`, `source: policy`, and anything under `overrides:` flow through unchanged.

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

More: [`docs/README.md`](docs/README.md) (full doc index) · [`docs/integrations.md`](docs/integrations.md) · [`docs/cli.md`](docs/cli.md) · [`docs/architecture.md`](docs/architecture.md).
