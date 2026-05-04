---
title: CLI reference
description: Sponsio's CLI commands, arguments, and options.
---

# CLI reference

Every `sponsio` command exits 0 on success and 1 on failure (parse error, violation, missing input). For LLM-backed commands, install the LLM extra: `pip install "sponsio[llm]"`. API keys come from environment variables only.

## sponsio scan

Scan source code, policy documents, or execution traces to discover contracts.

```bash
sponsio scan PATHS... [--llm] [--policy DOC] [--trace FILE] [-o sponsio.yaml]
```

| Option | Description |
|---|---|
| `--agent`, `-a` | Agent ID (default: `agent`) |
| `--llm` | Enable LLM inference. Auto-detects provider from env. |
| `--model`, `-m` | LLM model name (default: provider default) |
| `--provider` | `openai`, `anthropic`, or `gemini` |
| `--base-url` | OpenAI-compatible HTTP endpoint (Ollama, OpenRouter, DeepSeek, Together, Groq, vLLM, Azure) |
| `--out`, `-o` | Output file (default: `./sponsio.yaml`; `-o -` for stdout) |
| `--append` | Append to existing file instead of overwriting |
| `--policy`, `-p` | Policy document(s), repeatable |
| `--trace`, `-t` | Trace file or glob (OTLP, Phoenix, Langfuse, Sponsio session JSONL). No LLM required. |
| `--trace-min-support` | Minimum traces a pattern must appear in (default `1`) |
| `--trace-confidence-threshold` | Confidence floor for ordering or sequence mining, 0-1 (default `0.95`) |

### Provider matrix

| Provider | Env var | Default model | Notes |
|---|---|---|---|
| Gemini | `GOOGLE_API_KEY` or `GEMINI_API_KEY` | `gemini-2.0-flash` | 1500 requests/day free tier |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-3-5-sonnet-20241022` | `pip install anthropic` |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o-mini` | |
| Ollama (local) | none | (set `--model`) | `--base-url http://localhost:11434/v1` |
| OpenRouter / DeepSeek / Together / Groq / Cerebras / Fireworks / vLLM / Azure | provider's key | (set `--model`) | `--base-url https://...` against any OpenAI-compatible endpoint |

Auto-detection precedence (when `--provider` is unset): explicit `--base-url` resolves to `openai`; else `ANTHROPIC_API_KEY` resolves to `anthropic`; else `GOOGLE_API_KEY` or `GEMINI_API_KEY` resolves to `gemini`; else `OPENAI_API_KEY` resolves to `openai`.

```bash
# Rule-based scan, no LLM
sponsio scan src/agents/

# With LLM and policy
sponsio scan src/agents/ --policy security.md --llm -o sponsio.yaml

# Mine from traces (no LLM)
sponsio scan src/ -t '~/.sponsio/sessions/agent/*.jsonl'

# Local model via Ollama
sponsio scan src/ --llm --base-url http://localhost:11434/v1 --model llama3.1
```

### TypeScript scanner

The Python AST scanner only parses Python. For Node.js agents, use `@sponsio/sdk`:

```bash
npx @sponsio/sdk ./src --out tools.json
sponsio scan tools.json --out sponsio.yaml
```

The TS scanner statically understands Vercel's `tool({...})`, LangChain's `DynamicStructuredTool`, LangGraph.js's `tool(fn, cfg)`, and common Zod patterns. See [`ts/packages/sdk/README.md`](https://github.com/sponsio-labs/sponsio/tree/main/ts/packages/sdk) for the full matrix.

## sponsio onboard

One-shot project setup. Detects framework, writes `sponsio.yaml` in observe mode, prints a 2-line patch.

```bash
sponsio onboard [PATH]
```

`PATH` defaults to current directory. See [getting-started/quickstart.md](../getting-started/quickstart.md) for the typical output and the patch flow.

## sponsio validate

Parse-check contract strings. CI-friendly.

```bash
sponsio validate [CONTRACTS...] [--config sponsio.yaml] [--agent NAME] [--json]
```

```bash
sponsio validate "tool \`check_policy\` must precede \`issue_refund\`"
sponsio validate --config sponsio.yaml --json
```

## sponsio check

Run contracts against a saved trace file.

```bash
sponsio check --trace FILE [CONTRACTS...] [--config sponsio.yaml] [--agent NAME] [--json]
```

## sponsio patterns

List the deterministic pattern catalog.

```bash
sponsio patterns [--search KEYWORD] [--json]
```

## sponsio demo

Replay a packaged unsafe-trajectory scenario.

```bash
sponsio demo [--scenario NAME] [--mode mock|integration] [--no-guard] [--fast]
```

| Scenario | OWASP | Story |
|---|---|---|
| `cleanup` | (any) | Claude Code agent deletes `.env` and `.git/` |
| `backup` | ASI-10 | SRE cost-optimizer deletes prod DR backups |
| `wire` | ASI-09 | AP copilot wires $847k to an unverified vendor |
| `freeze` | ASI-10 | Replit-style agent violates declared code freeze, drops prod tables, fabricates replacement rows |

`--mode mock` is the default. `--mode integration` runs the framework-specific example scripts and needs a source checkout.

## sponsio report

Summarize observe-mode session logs into Markdown, HTML, or JSON.

```bash
sponsio report [--since 7d] [--agent NAME] [--format md|html|json] [-o FILE] [--live]
```

Reads `~/.sponsio/sessions/<agent_id>/*.jsonl` and produces a violations summary, top offending contracts, most-violating sessions. Read-only, no network.

```bash
sponsio report --since 24h
sponsio report --format html -o report.html
sponsio report --live --interval 5
```

`--live` cannot combine with `-o`. Malformed JSONL lines and unreadable files are skipped silently.

## sponsio host

Run inside a Claude Code or OpenClaw host plugin.

```bash
sponsio host install <host>           # claude-code | openclaw
sponsio host status <host>
sponsio host trace <host> [--follow]  # live coloured event stream
```

See [plugins.md](../plugins.md) for the host-plugin walkthrough.

## sponsio plugin

Per-plugin contract library tooling.

```bash
sponsio plugin init                       # bootstraps ~/.sponsio/plugins/_host/sponsio.yaml
sponsio plugin install <name>...          # installs starter packs (github, filesystem, ...)
sponsio plugin install --list             # see what's bundled
sponsio plugin scan <path> --tools t1,t2  # generate library from a plugin's tool set
```

## sponsio doctor

Health checks: install integrity, config syntax, framework wiring.

```bash
sponsio doctor
```

## sponsio refresh (Sponsio Cloud)

Re-mine `source: trace` contracts from recent sessions.

```bash
sponsio refresh --since 7d           # dry-run
sponsio refresh --since 7d --apply   # write back, with .sponsio.bak
```

User-written rules and `customized:` blocks pass through unchanged. Requires `pip install sponsio[cloud]`.

## sponsio serve (Sponsio Cloud)

```bash
sponsio serve
```

The OSS package ships a stub that exits 2 and points at the Cloud install. For OSS-only observability, use `sponsio host trace --follow` (live stream) or `sponsio report --since 1h` (summary).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Parse error, violation, or missing input |
| 2 | Cloud-only command in OSS install |
