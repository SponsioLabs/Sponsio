# sponsio-claude-code — quickstart

Use this doc when you want to **actually run sponsio-claude-code against
your Claude Code session** end-to-end. It's the user-facing
counterpart to [README.md](README.md), which is more architecture +
internals.

> **Status**: working prototype. Argument-level rules
> (`arg_blacklist`, `scope_limit`, `arg_value_range`,
> `dangerous_bash_commands`, …) really do block. Trace-aware rules
> (`must_precede`, `rate_limit`, `cooldown`) are silent until the
> daemon mode lands — see [Known limitations](#known-limitations).

---

## What this gives you

A Claude Code plugin that intercepts every `PreToolUse` event in
your session and runs it through Sponsio's deterministic contract
engine. Tool calls that match a deny rule are blocked **before they
execute**, and the model receives a structured deny reason it can
explain to the user.

Concretely, after install:

* `Bash: rm -rf /` — blocked at the hook layer (never runs).
* `mcp__github__delete_repository` — blocked outright.
* `mcp__filesystem__write_file({path: "~/.aws/credentials"})` — blocked.
* `mcp__playwright__browser_evaluate("() => document.cookie")` — blocked.
* Everything else — passes through, no change in agent behaviour.

---

## Three-step install

### 1. Install the Sponsio CLI

```bash
# from a clone (current path)
pip install -e .

# OR (when published)
pip install sponsio
```

Verify:

```bash
sponsio --version
# sponsio, version 0.1.0a0

sponsio plugin --help
# Host-plugin runtime (Claude Code, …).
#
# Commands:
#   guard    Plugin-system hook entry point — evaluates one tool call.
#   init     Bootstrap ~/.sponsio/plugins/ with the default _host library.
#   install  Copy bundled starter libraries into ~/.sponsio/plugins/<name>/.
#   scan     Generate a starter contract library from a Claude Code plugin.
```

### 2. Bootstrap the per-plugin contract libraries

```bash
# Writes ~/.sponsio/plugins/_host/sponsio.yaml + runs an allow + block smoke test
sponsio plugin init
```

Then drop in starter libraries for any popular MCP servers you use:

```bash
sponsio plugin install --list
#   _host (auto-installed by `plugin init`)
#   filesystem
#   github
#   playwright

# Install the ones that match your setup
sponsio plugin install github filesystem playwright
# or just:
sponsio plugin install --all
```

After this you'll have:

```
~/.sponsio/plugins/
├── _host/sponsio.yaml         # Bash / Edit / Write / Read / etc.
├── github/sponsio.yaml        # mcp__github__*
├── filesystem/sponsio.yaml    # mcp__filesystem__*
└── playwright/sponsio.yaml    # mcp__playwright__*
```

### 3. Load the plugin in Claude Code

```bash
# Path is the directory that contains .claude-plugin/plugin.json
claude --plugin-dir /path/to/Sponsio/plugins/sponsio-claude-code
```

When the session starts you should see (in `--verbose` / `stream-json`
output) something like:

```json
"plugins": [
  {"name": "sponsio-claude-code", "path": "...", "source": "sponsio-claude-code@inline"}
]
```

That's it. From this point every tool call passes through the
plugin.

---

## Verify it's actually working

### Without Claude Code (shell-only)

The hook is just a JSON-on-stdin → JSON-on-stdout protocol. You can
exercise it directly:

```bash
# Allowed — exit 0, no stdout
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"ls"}}' \
  | sponsio plugin guard --stdin

# Blocked — JSON deny on stdout
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"rm -rf /"}}' \
  | sponsio plugin guard --stdin
# {"hookSpecificOutput": {"hookEventName": "PreToolUse",
#                         "permissionDecision": "deny",
#                         "permissionDecisionReason": "_host.Bash — det constraint violated: …"}}
```

### Inside a Claude Code session

Pick a command you know matches a default rule (`rm -rf /`, fork
bomb, `curl … | bash`, …) and ask the agent to run it:

```
> please use Bash to run rm -rf / on this machine
```

You should see:

1. The agent decides to call `Bash`.
2. The hook fires, sees the regex match, returns the deny JSON.
3. Claude Code shows the tool result as `is_error: true` with the
   Sponsio reason as content.
4. The model reads the reason and explains to you that it was
   blocked by Sponsio.

If `--include-hook-events` is on, you also get explicit
`hook_started` / `hook_response` events in the stream so you can
confirm the protocol is healthy.

---

## Interacting in Claude Code

The plugin ships **one skill** that covers the whole configure path
— bundled starters, scanning unknown plugins, environment tuning,
verification. It's auto-invoked by the agent and also available as a
slash command:

| Slash command / skill | What it does |
|---|---|
| `/sponsio-claude-code:setup` | Bootstrap `~/.sponsio/plugins/`, pick + install bundled starters, generate starters for unbundled plugins via `sponsio plugin scan`, tune the rules to the user's environment, smoke-test the deny path. |

Or just say it in plain English ("set up sponsio-claude-code",
"generate sponsio rules for this MCP server I just installed",
"the plugin is too strict") — the skill description is keyword-dense
enough that Claude Code auto-invokes it.

---

## Customising rules

### Edit a library directly

```yaml
# ~/.sponsio/plugins/github/sponsio.yaml
agents:
  github:
    contracts:
      - desc: "block force-push to release branches"
        E:
          pattern: arg_blacklist
          args:
            - mcp__github__push_files
            - branch
            - - "^release/"
```

No reload needed — the next hook fire picks it up.

### Override a shipped rule without forking

```yaml
agents:
  github:
    contracts: [...as shipped...]
    overrides:
      # Allow `delete_repository` in this environment
      - match:
          desc: "delete_repository is blocked outright (overrides: disabled: true to allow)"
        disabled: true
```

### Generate rules for a plugin we don't ship a starter for

```bash
sponsio plugin scan ./path/to/your-plugin \
  --tools tool_a,tool_b,tool_c
```

Dry-run by default. Add `--apply` once you've reviewed the output.
The scan output is partitioned by routing key — one yaml per
plugin id — so a single Claude Code plugin that bundles multiple
MCP servers produces multiple files.

### Run in observe mode (log, don't block)

For pilot rollouts:

```bash
export SPONSIO_GUARD_MODE=observe
```

Only affects this plugin. Other Sponsio integrations in the same
shell still respect `SPONSIO_MODE` independently.

---

## Where everything lives in the repo

```
Sponsio/
├── plugins/sponsio-claude-code/                    ← the plugin itself
│   ├── .claude-plugin/plugin.json             — required Claude Code manifest
│   ├── hooks/hooks.json                       — PreToolUse → `sponsio plugin guard --stdin`
│   ├── skills/
│   │   └── setup/SKILL.md                     — auto-invokable + /sponsio-claude-code:setup
│   │                                            (covers bundled install + scan + tuning)
│   ├── libraries/
│   │   ├── _host/sponsio.yaml                 — mirror copy for --plugin-dir users
│   │   ├── github/sponsio.yaml
│   │   ├── filesystem/sponsio.yaml
│   │   └── playwright/sponsio.yaml
│   ├── README.md                              — internals / architecture
│   └── QUICKSTART.md                          ← you are here
│
├── sponsio/plugin/                            ← Python module
│   ├── defaults/                              — package-data source-of-truth for the libraries
│   │   ├── _host.yaml                         (`plugin install` reads from here)
│   │   ├── github.yaml
│   │   ├── filesystem.yaml
│   │   └── playwright.yaml
│   ├── registry.py                            — list/read bundled starters
│   └── scan.py                                — `sponsio plugin scan` engine
│
├── sponsio/guard_stdin.py                     ← `sponsio plugin guard --stdin` core
├── sponsio/cli.py                             ← all CLI commands (`plugin init/install/scan/guard`)
│
└── tests/
    ├── test_guard_stdin.py                    — hook adapter (23 tests)
    ├── test_namespaced_tool_names.py          — colon-disambiguation heuristic
    ├── test_plugin_init.py                    — init command + _host sync
    ├── test_plugin_scan.py                    — scan command + manifest parsing (16 tests)
    ├── test_plugin_install.py                 — starter libraries + install command (33 tests)
    └── test_plugin_layout.py           — plugin manifest / hooks / skills validity
```

Two source-of-truth files for each shipped library:

* `sponsio/plugin/defaults/<name>.yaml` — what `pip install` ships.
  `sponsio plugin install` and `sponsio plugin init` copy from here.
* `plugins/sponsio-claude-code/libraries/<name>/sponsio.yaml` — verbatim
  copy for users running `claude --plugin-dir /path/to/this/repo/plugins/sponsio-claude-code`.

A test (`test_plugin_install.py::test_starter_library_matches_plugin_checkout`)
keeps the two byte-identical, so neither install path goes stale.

---

## Runtime data — where library lookups go

```
~/.sponsio/                                    (XDG-style user data root)
├── plugins/                                   — per-plugin contract libraries
│   ├── _host/sponsio.yaml                     (built-in tools)
│   ├── github/sponsio.yaml                    (mcp__github__*)
│   ├── filesystem/sponsio.yaml                (mcp__filesystem__*)
│   ├── playwright/sponsio.yaml                (mcp__playwright__*)
│   └── <your-plugin>/sponsio.yaml             (one per routing key)
└── sessions/<agent_id>/*.jsonl                — observe-mode logs (pre-existing)
```

Override the root with `$SPONSIO_PLUGIN_ROOT` — useful for tests
and dev environments. The runtime `derive_plugin_id()` function
maps every incoming `tool_name` to the directory it loads:

| Tool name shape | Plugin id |
|---|---|
| `Bash`, `Edit`, `Write`, `Read`, … (Claude Code built-ins) | `_host` |
| `mcp__<server>__<tool>` | `<server>` |
| `<plugin>:<skill>` (Claude Code namespaced skill) | `<plugin>` |
| anything else | `_host` (fallback) |

---

## Known limitations

| Gap | Workaround / status |
|---|---|
| `must_precede`, `rate_limit`, `cooldown`, `loop_detection` don't fire on the first call | Stateless prototype gets a fresh trace per hook. Daemon mode (Stage 3) fixes this — gated on user signal. |
| MCP server tool inventory not auto-introspected | Pass tool names via `sponsio plugin scan --tools t1,t2,…`. MCP `tools/list` introspection is Stage 2.5. |
| Marketplace install (`/plugin install sponsio-claude-code`) not yet available | Use `--plugin-dir` from a clone; marketplace upload is Stage 4. |
| Hot-reload on `~/.sponsio/plugins/*` changes | Already free — every hook fire re-reads the yaml. No `/reload-plugins` needed for rule edits, only for plugin-itself changes. |
| Rule count → per-call latency | Negligible up to ~1000 rules in a single library; 80ms Python startup dominates. Daemon mode brings this to ~5ms. |

---

## Performance

* **Per-call cost**: ~90ms (80ms Python startup + 10ms Sponsio work).
* **50-step session overhead**: ~4.5s cumulative — usually
  imperceptible, slightly noticeable.
* **Daemon mode (Stage 3)** would drop per-call to ~5ms,
  cumulative to ~250ms.
* The Sponsio det engine itself is sub-millisecond regardless of
  rule count; the bottleneck is process startup, not evaluation.

---

## Troubleshooting

**"Hook fires but my rule doesn't block"**

1. Check the file lives at `~/.sponsio/plugins/<routed-id>/sponsio.yaml`
   where `<routed-id>` matches `derive_plugin_id(tool_name)` —
   `mcp__github__X` → `github`, not `mcp__github` or `github_mcp`.
2. Run `echo '<event-json>' | sponsio plugin guard --stdin` to see
   the verdict locally before debugging in Claude Code.
3. Check `sponsio plugin guard --stdin` exits 0 (it always does;
   non-2 exit codes are non-blocking by Claude Code design).
4. If it's a `rate_limit` / `must_precede` / `cooldown` rule —
   that's the daemon-mode gap above, not a bug.

**"Plugin loads but no hooks fire"**

Confirm `--include-hook-events` shows
`hook_started: PreToolUse:<tool>` events. If absent, the plugin
manifest didn't load — inspect with `claude --plugin-dir … -p "x"
--output-format stream-json` and grep for the `system.init` line:
the `plugins` array should contain `sponsio-claude-code`. If it doesn't,
your `--plugin-dir` path is wrong.

**"Smoke test in `plugin init` fails"**

`✗ smoke test failed (allow_ok=…, block_ok=…)` means either:

1. The CLI version is older than the library's rule shapes (run
   `pip install -U sponsio` from your dev environment).
2. The default `_host` library was hand-edited to a broken shape.
   Re-run with `--force` to overwrite.

Don't paper over the failure with `--no-smoke-test` — the failure
is a real signal something's wrong.

---

## Next steps

* Add per-plugin overrides to taste (see "Customising rules" above).
* For non-bundled plugins, run `sponsio plugin scan` and review.
* When you hit the daemon-needed limitations, file an issue —
  Stage 3 work is gated on the signal.
