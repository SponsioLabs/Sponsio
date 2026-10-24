---
title: CLI reference
description: Sponsio's CLI commands, arguments, and options.
---

# CLI reference

Every `sponsio` command exits 0 on success and 1 on failure (parse error, violation, missing input). For LLM-backed commands, install the LLM extra: `pip install --pre "sponsio[llm]"`. API keys come from environment variables only.

## sponsio scan

Scan source code or policy documents to discover contracts.

```bash
sponsio scan PATHS... [--llm] [--policy DOC] [-o sponsio.yaml]
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

See [getting-started/first-contract.md](../getting-started/first-contract.md) for the typical interactive flow.

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
sponsio host install <host>           # claude-code | cursor | openclaw
sponsio host install <host> --strict  # refuse tools that have no contract library
sponsio host install <host> --mode observe  # shadow run: log violations, let the tool run
sponsio host status <host>
sponsio host trace <host> [--follow]  # live coloured event stream
```

A tool whose namespace has no contract library (any MCP server outside the
shipped examples) runs with a one-time `running UNCHECKED` warning by
default. `--strict` writes `defaults.unconfigured: deny` into the host
library so those calls are refused instead; the setting survives
reinstalls, and the host library is covered by `capability/self-modify`
so the agent cannot change it. `unconfigured: deny|allow` in the daemon's
`daemon.yaml` overrides it for every library.

The installed hook runs a small shim, `sponsio_shim.py`, under the
interpreter that ran the install (`python -I -S`). The shim forwards each
event to the daemon (see `sponsio daemon` below), starts one when none
answers, and denies the call, with exit code 2 and the host's deny body,
when no daemon can be reached within its deadline (2 s). Every value the
shim needs (socket, plugin root, state dir, deadline) is written on the
hook command, and the shim reads no environment variable, so an `env`
block in the host's settings cannot change it. On Claude Code the
`PreToolUse` entry also carries `"onFailure": "block"` and a 10 s
`timeout`, so a hook that cannot start blocks the call (Claude Code
v2.1.295 or later).

The hook enforces by default. `--mode observe` sets the host's mode in
the daemon's config (`daemon.yaml`, see `sponsio daemon` below). The mode
is not written on the hook command, and the daemon ignores a mode sent
with a call, so editing the hook or the host's `env` does not switch the
guard to observe. Re-run `host install` to switch modes, or to move hooks
installed by an earlier release onto the shim.

While the daemon cannot be reached, every tool call is denied, including
the ones the agent would use to fix it. The deny reason names the
recovery, to run from your own terminal: `sponsio daemon run --detach`,
or `sponsio host uninstall <host>` to remove the hook.

Before `host install` or `host uninstall` changes a host's config file
(Claude Code `settings.json`, Cursor `hooks.json`, OpenClaw
`openclaw.json`), it copies the file to
`<file>.sponsio-backup-YYYYmmddHHMMSS` beside it, then writes the new
version atomically. A config file that is a symlink is written through
to its target. `host uninstall` removes only Sponsio's entries and then
offers to restore the most recent backup, which is the file as it was
before Sponsio last changed it; it asks on a terminal and does nothing
otherwise. A file that holds nothing but Sponsio's entries (one that did
not exist before Sponsio wrote it) is removed outright, with its backups,
since they hold nothing of yours; and a skills directory left empty by
removing the Sponsio skill is removed too.

```bash
sponsio host uninstall <host> --restore-backup     # remove the entries, then put the latest backup back
sponsio host uninstall <host> --no-restore-backup  # remove the entries, don't ask
```

```bash
sponsio host guard <host> [--event E]   # legacy hook entry: same as the shim, through the daemon
sponsio host guard <host> --direct      # debugging: evaluate in-process from the files on disk
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

Two checks cover the host hooks. **Hook wiring** reads every Sponsio
hook in the Claude Code and Cursor settings (user and project) and the
OpenClaw plugin config, and checks that each can run: the interpreter
and shim it names still exist, its daemon socket path is short enough to
bind, and a daemon answers or can be started. A hook that cannot run
denies every tool call, so a failure names the fix to run from your
terminal (`sponsio host install <host>` or `sponsio host uninstall
<host>`). **Claude Code** reports the installed version: before v2.1.295
Claude Code ignores the hooks' `onFailure: "block"`, so a hook that
cannot start lets the tool run and only the shim's own deadline applies.

**Enforcement tier** reports which tier the machine is in (see [the threat model](../concepts/threat-model.md)) and what the next tier needs. `sponsio doctor --tier` lists every guarantee of Tier 1, 2 and 3 with pass or fail and the fix (`--json` for a machine-readable form). The checks read the filesystem and the daemon's socket. Tier 1: host hooks go through the daemon. Tier 2 adds: the daemon runs as its own user, this user cannot reach the admin socket, the shim and engine are root-owned and read-only, the rule store belongs to the daemon's user, Claude Code's hooks come from managed settings only, and the host sandbox is on. Tier 3 adds Sponsio's tool gateway and egress proxy, which this release does not include. A default install is Tier 1.

`sponsio host install` refuses to write a hook whose daemon socket path
is too long to bind (more than 103 bytes on macOS, 107 on Linux); set
`SPONSIO_DAEMON_SOCKET` to a shorter path.

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

`--link` symlinks instead of copying, so future `pip install -U --pre sponsio` upgrades the skill in place.

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
sponsio prompt (onboard|scan)
```

Output is a copy-pasteable prompt block your AI assistant can run.

## sponsio serve

Placeholder for the web-dashboard server. This distribution ships the contract runtime + CLI only; the long-lived HTTP backend is not bundled, so the command exits non-zero and points you at the local-inspection alternatives.

```bash
sponsio serve   # prints the alternatives below and exits 2
```

For local observability use `sponsio host trace --follow` (live stream), `sponsio report --since 1h` (session summary), `sponsio replay <session>` (re-render a recorded session), or `sponsio export-sessions` (ship to a collector).

## sponsio daemon

The process that decides host hook calls (Claude Code, Cursor, OpenClaw). It holds the compiled rules, each session's history (in memory, with a write-ahead log, so a restart keeps every live session and deleting trace files does not reset a running one) and its own config. A hook that finds no daemon starts one.

```bash
sponsio daemon run [--socket PATH] [--mode 0600] [--state-dir DIR] [--plugin-root DIR] [--detach] [--watch]
                                                   # foreground; used by launchd/systemd
sponsio daemon ping [--echo VALUE]                 # round-trip health check
sponsio daemon status                              # resolved socket path + reachability
```

Socket path resolves to `$SPONSIO_DAEMON_SOCKET`, then `/var/run/sponsio.sock` if writable, else `~/.sponsio/sponsio.sock`. The state dir defaults to `~/.sponsio/state`. The plugin root is `--plugin-root`, else `~/.sponsio/plugins`; the daemon does not read `$SPONSIO_PLUGIN_ROOT`.

- **Config.** Every setting a hook decision depends on is in `<state-dir>/daemon.yaml`. No environment variable affects a hook decision.
- **Two sockets.** The agent socket above, and an admin socket beside it (`sponsio.sock` becomes `sponsio.admin.sock`, mode 0600) for everything that changes state. The caller's uid, gid and pid come from the kernel, and every admin call is audited.
- **One daemon per state dir.** A second `daemon run` finds the lock taken and exits 0. `--detach` starts it in the background and logs to `<state-dir>/daemon.log`.
- **`--watch`** applies every rule change at once instead of holding weakening ones, for working on Sponsio's own rules. Each change is still recorded.

The `daemon.yaml` schema, both sockets' methods, proposals and every audit event are in the [daemon reference](daemon.md).

## sponsio rules

What the daemon enforces, and rule changes waiting for review. These commands talk to the admin socket, so run them from your own terminal. The agent's hooks deny `sponsio rules reload`.

```bash
sponsio rules status [--json]          # loaded libraries; HELD marks a change waiting for review
sponsio rules diff                     # what a reload would apply: - removed or changed, + added
sponsio rules reload [--bucket B] [--yes]
sponsio rules verify                   # compare with the book the cloud publishes, now
```

The daemon gives every compiled contract a stable id. The id is a hash of the contract's agent, the pack it came from, its formula, strategy and parameters, but not its description. Settings that change decisions (`defaults`, `tools`, `tool_policy`, `runtime`) get ids too. When a library or one of its packs changes on disk:

- If every loaded id is still present, the new rules apply at once, and `ruleset_strengthened` is recorded with what was added.
- If any id was removed or changed, the daemon keeps enforcing the rules it has loaded. It records `config_drift` with the removed and changed contracts and adds a one-line warning to the next hook reply (stderr, and the reason of a deny). The new rules apply only after `sponsio rules reload`, which records `ruleset_reloaded` with the caller's uid and pid. A changed parameter counts as one removal plus one addition. A library that no longer loads counts as removing everything.

Each applied ruleset is kept in `<state-dir>/rules/`. A daemon that starts onto a library weaker than the last applied ruleset, with nothing older in memory, denies that bucket's calls until a reload. `sponsio daemon run --watch` applies every change at once, for working on Sponsio's own rules, and still records each one.

The daemon also polls the library files, their packs and the engine modules on the hook path every 2 s, and records `config_drift` with the old and new SHA-256 of each file that changed. A deleted or truncated trace log is restored from memory and recorded as `trace_deleted`. A call denied by `capability/self-modify` or `capability/host-config-integrity` is recorded as `tamper_attempt`. At Tier 1 these records make tampering visible. They do not prevent it.

With a `cloud:` section in `daemon.yaml`, the daemon also checks every `verify_interval_s` (and on `sponsio rules verify`) that each linked bucket enforces the book the project publishes for its agent. It sends the bucket's contract ids to `POST /v1/rulebook/verify`, and the service computes the published book's ids with the same algorithm. The ids ignore descriptions and whether a contract came from a pack, and keep its mode. The answer is `match`, `stronger` (local additions), `weaker` (published contracts missing locally, recorded as `cloud_drift` and announced on the next hook reply), `incomparable` (different SDK versions disagree) or `unsupported`. This check never runs on the decision path. A failed check is reported as a status and never causes a deny.

## sponsio install --system

Tier 2: Sponsio for the whole machine, out of the agent's reach. Run with `sudo`.

```bash
sudo sponsio install --system [--host claude-code] [--host cursor]
                              [--python PATH] [--package SPEC] [--allow-user-hooks]
                              [--no-migrate] [--dry-run]
sudo sponsio uninstall --system [--keep-state] [--dry-run]
```

`install --system` creates the `_sponsio` user, a root-owned engine in `/opt/sponsio` on a root-owned Python 3.10+ (`--python`), root-owned hook shims, the daemon's private state and its launchd or systemd service. It imports `~/.sponsio/plugins` (unless `--no-migrate`) and writes the host's managed settings. `--package` is what pip installs into the engine: a version spec, a wheel or a source directory. `--dry-run` prints every step and changes nothing. What it sets up and what to know about it: [Running Sponsio on a host](../guides/host-operations.md#tier-2-the-system-install).

## sponsio run --sandbox

Tier 3: start an agent whose only way out is Sponsio.

```bash
sponsio run --sandbox [--workspace DIR] [--writable PATH]... [--keep-env NAME]...
                      [--hide-home] [--socket PATH] -- <agent command>
sponsio run --container -- <agent command>    # inside deploy/tier3's agent container
```

The daemon opens an egress proxy for the run; the agent is not started without one. Inside the sandbox the network is that proxy and nothing else, files are read-only outside the workspace and the `--writable` paths, Sponsio's own state cannot be read, and credentials are removed from the environment (names ending in `_TOKEN`, `_SECRET`, `_PASSWORD`, `_API_KEY`, `_KEY`, and known cloud credentials; the agent's own model key and `--keep-env` names stay). Linux uses bubblewrap, macOS `sandbox-exec`. `--container` is for a container that is already the sandbox: it only routes the network through the proxy. Exits with the agent's status.

## sponsio admin

Operator commands. They talk to the daemon's admin socket: at Tier 1 that is your own daemon, at Tier 2 run them with `sudo`. The agent's hooks deny them.

```bash
sponsio admin proposals list [--status pending|approved|rejected] [--json]
sponsio admin proposals show ID
sponsio admin proposals approve ID [--yes]     # applies it; recorded with your uid and the change
sponsio admin proposals reject ID [--yes]
sponsio admin audit verify [--state-dir DIR]   # exit 1 when the chain is broken
sponsio admin cloud-key set [--from-file F]    # the daemon's own cloud key; only a fingerprint is recorded
sponsio admin cloud-key status
```

`audit verify` asks the running daemon, which also checks that the last record it wrote is still in the log. With `--state-dir`, or with no daemon, it checks the file's chain alone. The [daemon reference](daemon.md#audit-events) describes the chain.

## sponsio gateway

The MCP gateway. The daemon runs the agent's MCP servers with the credentials in `<state-dir>/gateway.yaml`, and the host is given one MCP server that lists only the tools the session's state allows.

```bash
sponsio gateway command [--socket PATH] [--state-dir DIR] [--plugin-root DIR]
                                       # print the MCP server entry for the host's config
sponsio gateway tools [--socket PATH]  # the tools a new session is offered, and servers that failed
```

Put the entry `gateway command` prints under `mcpServers` in `.mcp.json` (Claude Code) or `~/.cursor/mcp.json` (Cursor), and remove the servers it replaces: their credentials move to `gateway.yaml`. The file format and how listing and calls are decided are in the [daemon reference](daemon.md#mcp-gateway).

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
