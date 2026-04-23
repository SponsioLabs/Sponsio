# CLI Reference

```bash
pip install sponsio
sponsio --help
```

For `sponsio scan --llm`, install the LLM extra so all provider SDKs (including **Gemini**’s `google-genai`) are available: `pip install "sponsio[llm]"`. API keys are **environment variables** only—see [Getting started: API keys](getting-started.md#api-keys-for-llm-contract-discovery).

---

## `sponsio scan`

Scan source code and policy documents to discover contracts.

```bash
sponsio scan PATHS... [OPTIONS]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `PATHS` | Python source files or directories to scan (required, multiple allowed) |

### Options

| Option | Description |
|--------|-------------|
| `--agent`, `-a` | Agent ID in generated config (default: `agent`) |
| `--llm` | Enable LLM inference (auto-detects provider from env) |
| `--model`, `-m` | LLM model name (default: auto-detect) |
| `--provider` | LLM provider: `openai`, `anthropic`, or `gemini` (default: auto-detect from env) |
| `--base-url` | OpenAI-compatible HTTP endpoint (Ollama, OpenRouter, DeepSeek, Together, Groq, vLLM, Azure). Reads `OPENAI_BASE_URL` if not given. |
| `--out`, `-o` | Write output to file (default: `./sponsio.yaml`; use `-o -` for stdout) |
| `--append` | Append to existing file instead of overwriting |
| `--policy`, `-p` | Policy document(s) to extract from (repeatable) |
| `--trace`, `-t` | Execution trace file or glob to mine contracts from (repeatable). Accepts OTLP/JSON, OTLP JSONL, native Sponsio JSON/JSONL, and session JSONL. No LLM required. |
| `--trace-min-support` | Minimum traces a pattern must appear in before it's proposed (default `1`). Bump up for noisy production logs. |
| `--trace-confidence-threshold` | Confidence floor for ordering / sequence mining, 0–1 (default `0.95`). |

### Provider matrix

`sponsio scan --llm` works with any of the following — pick whichever you already have an account for:

| Provider | Env var | Default model | Notes |
|----------|---------|---------------|-------|
| **Gemini** | `GOOGLE_API_KEY` or `GEMINI_API_KEY` | `gemini-2.0-flash` | 1500 requests/day **free tier** — easiest to try |
| **Anthropic** | `ANTHROPIC_API_KEY` | `claude-3-5-sonnet-20241022` | `pip install anthropic` |
| **OpenAI** | `OPENAI_API_KEY` | `gpt-4o-mini` | |
| **Ollama** (local) | — | (set `--model llama3.1`) | `--base-url http://localhost:11434/v1` |
| **OpenRouter / DeepSeek / Together / Groq / Cerebras / Fireworks / vLLM / Azure** | provider's key | (set with `--model`) | `--base-url https://...` against any OpenAI-compatible endpoint |

Auto-detection precedence (when `--provider` is not given): explicit `--base-url` → `openai`; else `ANTHROPIC_API_KEY` → `anthropic`; else `GOOGLE_API_KEY`/`GEMINI_API_KEY` → `gemini`; else `OPENAI_API_KEY` → `openai`.

### Examples

```bash
# Rule-based scan (no LLM, no API key needed)
sponsio scan src/agents/

# With LLM (auto-detects from env vars)
sponsio scan src/agents/ --llm

# Write to file
sponsio scan src/agents/ --llm -o sponsio.yaml

# Add policy constraints to existing config
sponsio scan src/agents/ --policy security.md --llm -o sponsio.yaml --append

# Force provider/model
sponsio scan src/ --llm --provider gemini
sponsio scan src/ --llm --provider anthropic --model claude-3-5-sonnet-20241022
sponsio scan src/ --llm --provider openai --model gpt-4o

# Local model via Ollama (free, offline, ~8GB RAM)
sponsio scan src/ --llm --base-url http://localhost:11434/v1 --model llama3.1

# OpenRouter (any frontier model behind one key)
OPENAI_API_KEY=sk-or-... \
  sponsio scan src/ --llm \
    --base-url https://openrouter.ai/api/v1 \
    --model anthropic/claude-3.5-sonnet

# DeepSeek (cheap + strong on JSON tasks)
OPENAI_API_KEY=sk-... \
  sponsio scan src/ --llm \
    --base-url https://api.deepseek.com \
    --model deepseek-chat
```

### Mining contracts from traces

When you have execution traces, `sponsio scan --trace` learns
ordering / exclusion / rate-limit / sequence patterns statistically
— no LLM required. Format is sniffed from content, so OTel Collector
exports, Phoenix/Langfuse JSONL, and `~/.sponsio/sessions/*.jsonl`
all work without conversion.

```bash
# Phoenix / Langfuse: OpenInference attrs are understood natively
sponsio scan src/ -t 'phoenix-export/*.jsonl'

# OTel Collector ndjson (one resourceSpans batch per line)
sponsio scan src/ -t spans.jsonl

# A Sponsio session log (what `SPONSIO_MODE=observe` writes)
sponsio scan src/ -t ~/.sponsio/sessions/agent/*.jsonl

# Tighten the threshold when feeding a noisy audit log
sponsio scan src/ -t traces/ --trace-min-support 5 \
  --trace-confidence-threshold 0.98
```

Each trace-sourced contract gets a `source: trace` comment in the
emitted YAML, so you can grep / review them as a group. Trace
mining merges with `--policy` and `--llm` inference via a
`(pattern, args)` dedupe.

### Scanning TypeScript / JavaScript agents

The Python AST scanner only parses Python. For Node.js agents
(LangChain.js, Vercel AI SDK, LangGraph.js), use the companion
package `@sponsio/scan-ts`:

```bash
# Emit an OpenAI-function-calling JSON inventory
npx @sponsio/scan-ts ./src --out tools.json

# Feed it to `sponsio scan` — same heuristics, same YAML output
sponsio scan tools.json --out sponsio.yaml
```

The TS scanner statically understands Vercel's `tool({...})`,
LangChain's `new DynamicStructuredTool({...})`, and LangGraph.js's
`tool(fn, cfg)` — plus common Zod patterns
(`z.object / string / number / enum / literal / array / optional`).
See [`ts-scanner/README.md`](https://github.com/sponsio-labs/sponsio/tree/main/ts-scanner)
for the full matrix.

---

## `sponsio validate`

Validate that contract strings parse correctly.

```bash
sponsio validate [CONTRACTS...] [OPTIONS]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `CONTRACTS` | NL contract strings (optional if using `--config`) |

### Options

| Option | Description |
|--------|-------------|
| `--config`, `-c` | YAML config file |
| `--agent`, `-a` | Agent ID to validate (with `--config`) |
| `--json` | JSON output for machine consumption |

### Examples

```bash
# Single contract
sponsio validate "tool \`check_policy\` must precede \`issue_refund\`"

# From config file
sponsio validate --config sponsio.yaml

# Specific agent
sponsio validate --config sponsio.yaml --agent customer_bot

# JSON output (for CI)
sponsio validate --config sponsio.yaml --json
```

---

## `sponsio check`

Check contracts against a saved trace file.

```bash
sponsio check --trace FILE [CONTRACTS...] [OPTIONS]
```

### Options

| Option | Description |
|--------|-------------|
| `--trace`, `-t` | Trace JSON file (required) |
| `--config`, `-c` | YAML config file |
| `--agent`, `-a` | Agent ID (required with multi-agent config) |
| `--json` | JSON output |

### Examples

```bash
# Inline contracts
sponsio check --trace trace.json "tool \`A\` must precede \`B\`"

# From config
sponsio check --trace trace.json --config sponsio.yaml --agent bot

# JSON output
sponsio check --trace trace.json --config sponsio.yaml --json
```

---

## `sponsio patterns`

List all available constraint patterns.

```bash
sponsio patterns [OPTIONS]
```

### Options

| Option | Description |
|--------|-------------|
| `--json` | JSON output |
| `--search` | Filter patterns by keyword |

### Examples

```bash
sponsio patterns
sponsio patterns --json
sponsio patterns --search "precede"
```

---

## `sponsio demo`

Run interactive demo scenarios.

```bash
sponsio demo [OPTIONS]
```

### Options

| Option | Description |
|--------|-------------|
| `--scenario` | Demo scenario: `cleanup` (default), `trial`, `loan` |
| `--mode` | `mock` (default, no optional SDKs) or `integration` (source checkout examples) |
| `--no-guard` | Replay the unsafe trajectory without Sponsio |
| `--fast` | Skip typing delays |

By default, `sponsio demo` runs a packaged mock replay that works from a
plain PyPI install: no API key, no LangGraph/CrewAI/Claude SDK dependency,
and no source checkout required. Use `--mode integration` from a cloned repo
when you want to run the framework-specific example scripts.

| Scenario | Story | Integration example |
|---|---|---|
| `cleanup` | Claude Code cleanup agent deletes `.env` & `.git/` | `from sponsio.claude_agent import Sponsio` · `ClaudeAgentOptions(hooks=guard.hooks())` |
| `trial` | Clinical Trial Recruiter falsifies 20 patient records (15/24 SOTA models) | `from sponsio.langgraph import Sponsio` · `guard.wrap(tools)` |
| `loan` | Loan officer edits applications to bypass AML (19/24 SOTA models) | `from sponsio.crewai import Sponsio` · `guard.wrap(tools)` |

### Examples

```bash
sponsio demo
sponsio demo --scenario trial --fast
sponsio demo --scenario loan --no-guard
sponsio demo --mode integration --scenario trial
```

---

## `sponsio report`

Summarize shadow-mode session logs into a shareable report.

Reads the JSONL files written by `mode="observe"` from
`~/.sponsio/sessions/<agent_id>/*.jsonl` and produces a Markdown / HTML / JSON
summary of violations, would-have-blocked decisions, sto retries, top offending
contracts, and most-violating sessions. The command is read-only — no files are
modified, nothing is sent over the network.

```bash
sponsio report [OPTIONS]
```

### Options

| Option | Description |
|--------|-------------|
| `--since` | Time window: `all`, `30s`, `45m`, `24h`, `7d` (default: `7d`) |
| `--agent` | Filter to one `agent_id`. Default: every agent under `~/.sponsio/sessions` |
| `--format` | Output format: `markdown` / `md` / `html` / `json` (default: `markdown`) |
| `--out`, `-o` | Write report to file. Default: stdout |
| `--live` | Watch mode — re-render every `--interval` seconds until Ctrl+C |
| `--interval` | Seconds between refreshes in `--live` mode (default: `2.0`) |
| `--base-dir` | Override the session log directory (default: `~/.sponsio/sessions`) |

### Examples

```bash
# Markdown summary, last 7 days, all agents
sponsio report

# One agent, last 24 hours
sponsio report --agent support_bot --since 24h

# HTML report to file (for dashboards / email)
sponsio report --format html -o report.html

# Machine-readable dump for CI / downstream tools
sponsio report --format json --since all

# Watch mode — live-refreshing terminal summary
sponsio report --live --interval 5

# Point at a non-default log directory
sponsio report --base-dir ./test-sessions --since all
```

### Notes

- `--live` cannot be combined with `--out`.
- Malformed JSONL lines and unreadable files are skipped silently — one corrupt
  record never poisons the whole report.
- Violations include `blocked`, `observed` (would-have-blocked), and `retrying`
  outcomes. `escalated` events count toward `is_violation` but are surfaced in
  the per-contract table rather than a top-line counter.

---

## `sponsio serve`

Start the Sponsio dashboard server.

```bash
sponsio serve [OPTIONS]
```

### Options

| Option | Description |
|--------|-------------|
| `--host` | Bind address (default: `127.0.0.1`) |
| `--port`, `-p` | Port (default: `8000`) |
| `--dev` | Also start frontend dev server (requires npm) |

### Examples

```bash
sponsio serve                  # API on http://127.0.0.1:8000
sponsio serve -p 9000          # custom port
sponsio serve --dev            # API + frontend dev server
sponsio serve --host 0.0.0.0   # expose to network
```

---

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success (all contracts valid / all checks passed) |
| `1` | Failure (parse error, violation detected, or missing input) |
