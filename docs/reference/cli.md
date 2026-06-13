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

## sponsio init

Interactive 4-axis project setup wizard. Walks through framework / hosts / skills / mode, writes `sponsio.yaml` in the chosen mode, runs `sponsio doctor`, and prints the agent-entry patch.

```bash
sponsio init [PATH]
```

| Option | Description |
|---|---|
| `PATH` | Target directory (default: current). Writes `sponsio.yaml` if not present. |
| `--plan PICKS` | Print the would-run commands for these picks. Used by IDE-agent wizards for dry-run previews. |
| `--apply PICKS` | Run non-interactively. Picks format: `framework=<name>;ides=<ide>:<level>,<ide>:<level>;mode=observe\|enforce` where `<level>` is `none`, `skill`, or `full`. Legacy form with separate `hosts=<a>,<b>;skills=<a>,<b>` lists is still accepted (`hosts=` ↔ level `full`, `skills=` ↔ level `skill`). |
| `--no-demo` | Skip the post-install demo offer. |

```bash
sponsio init .                                                              # interactive
sponsio init . --apply "framework=langgraph;mode=observe"                   # non-interactive, no IDE wiring
sponsio init . --apply "framework=langgraph;ides=claude-code:full;mode=observe"   # + Claude Code plugin (full)
sponsio init . --plan "framework=crewai;ides=cursor:skill"                  # dry-run preview
```

See [getting-started/quickstart.md](../getting-started/quickstart.md) for the typical interactive flow.

## sponsio onboard

One-shot project wire-up: composes `init` + `scan` + `doctor` into a single command so first-time users don't have to learn three subcommands. Detects the framework, picks the best available LLM provider (env → `OPENAI_BASE_URL` → local Ollama → none), writes `sponsio.yaml` in observe mode with an inferred contract set, then prints the framework-specific agent-entry patch.

```bash
sponsio onboard [TARGET] [--agent NAME] [--mode observe|enforce] [--force]
```

| Option | Description |
|---|---|
| `TARGET` | File or directory to scan (default: current). |
| `--mode` | Runtime mode written into `sponsio.yaml`. Omit to be prompted; `observe` is the safe default. |
| `--force` | Overwrite an existing `sponsio.yaml` without prompting. |
| `--no-probe-ollama` | Skip the `localhost:11434` liveness probe. |
| `--no-doctor` | Skip the post-onboard `sponsio doctor` run. |
| `--emit-context` | Skip the LLM step; emit the structured inputs as JSON for the `sponsio` skill. Pair with `sponsio prompt onboard`. |
| `--json` | Emit the structured `OnboardReport` as JSON. |

```bash
sponsio onboard
sponsio onboard src/ --agent customer_bot
sponsio onboard --force --no-probe-ollama
```

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

## sponsio refresh

Library-maintenance companion to `sponsio scan`. Re-mines `source: trace` contracts from recent session logs and surgically merges them into an existing `sponsio.yaml`. Where `scan` *creates* contracts, `refresh` keeps them honest as traces accumulate — discovering new patterns and retiring stale ones without clobbering anything you wrote or tuned by hand.

```bash
sponsio refresh                           # dry-run, last 7d of session logs
sponsio refresh --since 24h               # narrower window
sponsio refresh --apply                   # write it (backs up to <config>.sponsio.bak)
sponsio refresh -t 'otel/*.jsonl' --apply # explicit trace source
sponsio refresh --mode add-only --apply   # only add, never remove or drift
```

| Option | Description |
|---|---|
| `-c, --config PATH` | sponsio.yaml to update in place (default: `sponsio.yaml`). |
| `-a, --agent NAME` | Agent to refresh. Omit to refresh every agent in the config. |
| `-t, --trace GLOB` | Trace file glob (repeatable). Default: `~/.sponsio/sessions/<agent>/*.jsonl`. |
| `--since DUR` | Window for default session-log discovery (`24h` / `7d` / `all`). Ignored when `-t` is given. |
| `--mode add-only\|replace-trace` | Merge strategy (default `replace-trace`). |
| `--apply` | Write changes. Without it, refresh is a pure dry-run that prints the diff. |

Only `source: trace` contracts are touched. User-written rules, `source: scan` (from code), `source: policy`, and `customized:` blocks flow through unchanged. Comments are not preserved through `--apply` (PyYAML can't round-trip them) — the `.sponsio.bak` backup exists so you can recover any prose annotations.

> Trace mining is an extension point that may not be bundled in every distribution; where it isn't, `refresh` degrades to "no new contracts mined" rather than failing.

## sponsio packs

List shipped contract packs with rule counts and `include:` syntax.

```bash
sponsio packs
```

Reads from `sponsio/contracts/` and prints one row per pack: spec name, tier, rule count, one-line summary. Useful right after `sponsio scan` / `sponsio init` to see what a generated yaml's `include:` lines pull in.

## sponsio eval

Offline trace replay with FPR / FNR scoring. Runs a contract set against recorded traces and reports false-positive and false-negative rates against the upstream ground-truth labels.

```bash
sponsio eval TRACE_PATH [CONTRACTS...] [--config sponsio.yaml] [--agent NAME]
```

Used internally for the [Benchmarks](benchmarks.md) numbers. Also useful for tuning a contract set against your own labelled trace corpus.

## sponsio export

Convert a Sponsio session dump into OTLP for downstream tools.

```bash
sponsio export SOURCE [--to TARGET_DIR]
```

`SOURCE` can be a single session file or a directory. Output is OTLP/JSON ready for ingestion by `sponsio eval` or any OTLP collector.

## sponsio export-sessions

Push session-log files (the JSONL written by `mode="observe"`) to an OTLP endpoint or write them as OTLP/JSON files.

```bash
sponsio export-sessions [--since 24h] [--to PATH | --otlp ENDPOINT]
```

Use `--to PATH` for local files, `--otlp ENDPOINT` for an HTTPS push to your collector. Time windows: `90s`, `30m`, `24h`, `7d`, or `all`.

## sponsio replay

Re-render a recorded session as a coloured terminal view.

```bash
sponsio replay [SESSION] [--config sponsio.yaml]
```

`SESSION` is a session id under `~/.sponsio/sessions/<agent>/`. Without an arg, lists recent sessions. With `--config`, the contracts-armed table shows what each verdict was; without, falls back to the bare event table.

## sponsio explain

Show source, compiled formula, and the last violation for a contract.

```bash
sponsio explain QUERY [--config sponsio.yaml]
```

`QUERY` matches against contract `desc` substrings. Useful when debugging "why is this rule firing?".

## sponsio skill

Install the `sponsio` Agent Skill into the local Claude Code, Cursor, or Codex skill directory. The skill bundles five lifecycle workflows (initial setup, audit and refine, tune in observe, flip to enforce, troubleshoot).

```bash
sponsio skill install [--force] [--link]
```

`--link` symlinks instead of copying, so future `pip install -U sponsio` upgrades the skill in place.

## sponsio mode

Flip a single agent between observe and enforce mode without editing yaml.

```bash
sponsio mode (observe|enforce) [--config sponsio.yaml] [--agent NAME]
```

Equivalent to setting `runtime.mode:` in yaml. The `SPONSIO_MODE` env var still wins over both.

**Parent-aware patching (v0.2)**. The CLI walks the yaml line by line tracking the current top-level key, then:

1. Prefers updating an existing `mode:` line nested under `runtime:`. This is the only line the TypeScript loader reads, so picking the wrong line would silently leave TS stale.
2. Falls back to `mode:` nested under `defaults:` if no `runtime.mode` exists. Both loaders honor this.
3. On a yaml that has neither, appends a fresh `runtime:` block ONLY when target is `observe`. Refuses to append a fresh `enforce` block when no mode line exists and exits 1 with a clear hint. CI scripts that relied on the old exit-1 behavior for malformed configs keep working. To flip a clean yaml to enforce, run `sponsio mode observe` first (which appends the block), then `sponsio mode enforce`.

The walker ignores `mode:` lines nested under unrelated keys (e.g. `judge.fallback_mode:` is not the runtime mode), and preserves inline comments and line endings on the patched line.

## sponsio prompt

Print the agent-facing prompt template for a Sponsio workflow. Used by the `sponsio` skill (W1 initial setup, W2 audit, W3 tune, W4 enforce, W5 troubleshoot).

```bash
sponsio prompt (onboard|refresh|scan)
```

Output is a copy-pasteable prompt block your AI assistant can run.

## sponsio serve

Placeholder for the web-dashboard server. This distribution ships the contract runtime + CLI only; the long-lived HTTP backend is not bundled, so the command exits non-zero and points you at the local-inspection alternatives.

```bash
sponsio serve   # prints the alternatives below and exits 2
```

For local observability use `sponsio host trace --follow` (live stream), `sponsio report --since 1h` (session summary), `sponsio replay <session>` (re-render a recorded session), or `sponsio export-sessions` (ship to a collector).

## sponsio daemon

Privileged-process side of the IPC split. The daemon owns the host bucket / per-plugin yaml files and is the only entity the host agent can reach to write them, so self-modify protection becomes an OS-level guarantee (ideally a separate UID under launchd/systemd) rather than a regex-on-tool-args one.

```bash
sponsio daemon run [--socket PATH] [--mode 0600]   # foreground; used by launchd/systemd
sponsio daemon ping [--echo VALUE]                 # round-trip health check
sponsio daemon status                              # resolved socket path + reachability
```

Socket path resolves to `$SPONSIO_DAEMON_SOCKET`, then `/var/run/sponsio.sock` if writable, else `~/.sponsio/sponsio.sock`.

## sponsio cursor

Cursor IDE integration. Cursor 1.7+ ships a deny-capable hook system (`hooks.json`); Sponsio plugs in as the command for the relevant pre-* events so every Shell/Read/Write/MCP call is evaluated against the contract library before Cursor executes it.

```bash
sponsio cursor install-hooks            # writes ~/.cursor/hooks.json (or project .cursor/hooks.json)
sponsio cursor guard --event <name>     # runtime hook handler; reads payload on stdin, denies via exit 2
```

---

## TypeScript CLI

The `@sponsio/sdk` package ships a parallel CLI with the same command surface. Same yaml output, same block / allow decisions.

| Python | TypeScript |
|---|---|
| `sponsio init` | `npx @sponsio/sdk init` |
| `sponsio scan` | `npx @sponsio/sdk scan` |
| `sponsio validate` | `npx @sponsio/sdk validate` |
| `sponsio check` | `npx @sponsio/sdk check` |
| `sponsio doctor` | `npx @sponsio/sdk doctor` |
| `sponsio demo` | `npx @sponsio/sdk demo` |
| `sponsio report` | `npx @sponsio/sdk report` |
| `sponsio packs` | `npx @sponsio/sdk packs` |
| `sponsio patterns` | `npx @sponsio/sdk patterns` |
| `sponsio mode` | `npx @sponsio/sdk mode` |
| `sponsio explain` | `npx @sponsio/sdk explain` |
| `sponsio replay` | `npx @sponsio/sdk replay` |
| `sponsio export` | `npx @sponsio/sdk export` |
| `sponsio export-sessions` | `npx @sponsio/sdk export-sessions` |
| `sponsio eval` | `npx @sponsio/sdk eval` |
| `sponsio skill` | `npx @sponsio/sdk skill` |
| `sponsio prompt` | `npx @sponsio/sdk prompt` |

Cross-language scenarios in `tests/cross_language/` validate identical verdicts on both engines. The `@sponsio/sdk` was previously published as `@sponsio/scan-ts`; that package was merged in and the deprecation shim removed.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Parse error, violation, or missing input |
