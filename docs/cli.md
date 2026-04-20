# CLI Reference

```bash
pip install -e .
sponsio --help
```

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
| `--provider` | LLM provider: `gemini` or `openai` (default: auto-detect from env) |
| `--out`, `-o` | Write output to file (default: stdout) |
| `--append` | Append to existing file instead of overwriting |
| `--policy`, `-p` | Policy document(s) to extract from (repeatable) |

### Examples

```bash
# Rule-based scan (no LLM, no API key needed)
sponsio scan src/agents/

# With LLM (auto-detects GOOGLE_API_KEY or OPENAI_API_KEY)
sponsio scan src/agents/ --llm

# Write to file
sponsio scan src/agents/ --llm -o sponsio.yaml

# Add policy constraints to existing config
sponsio scan src/agents/ --policy security.md --llm -o sponsio.yaml --append

# Force provider/model
sponsio scan src/ --llm --provider gemini
sponsio scan src/ --llm --provider openai --model gpt-4o
```

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
| `--scenario` | Demo scenario: `customer` (default), `coding`, `mcp` |
| `--real` | Use real LLM (requires `GOOGLE_API_KEY`) |

### Examples

```bash
sponsio demo
sponsio demo --scenario coding
sponsio demo --scenario mcp --real
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
