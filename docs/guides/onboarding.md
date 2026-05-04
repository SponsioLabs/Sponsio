---
title: Onboarding an existing agent
description: Use `sponsio init` to wire framework, host hooks, skill, and mode in one wizard.
---

# Onboarding an existing agent

`sponsio init` is the 4-axis setup wizard. One run covers every decision that matters on first install. Three surfaces (interactive TTY, `--plan` dry-run, `--apply` non-interactive) share the same dispatch table, so an IDE-agent's preview is guaranteed to match what `--apply` actually runs.

```bash
pip install sponsio
sponsio init
```

---

## The four axes

| Axis | Picks | What it does |
|---|---|---|
| **1. Framework wrap** (single) | `langgraph` / `crewai` / `openai` / `claude_agent` / `agents` / `vercel_ai` / `google_adk` / `mcp` / `none` | AST-scans your code, writes `sponsio.yaml`, prints a 2-line patch for your agent entry. |
| **2. Protect host agents** (multi) | `claude-code` / `cursor` / `openclaw` | Installs the host's pre-tool hook so the IDE's own tool calls (Bash, Edit, MCP servers) get gated too. |
| **3. Install Sponsio skill** (multi) | `claude-code` / `cursor` / `codex` | Drops `SKILL.md` into the host's skill directory. Auto-triggers on phrases like *"audit my agent"*, *"explain my sponsio.yaml"*. |
| **4. Mode** (single) | `observe` (default) / `enforce` | `observe` evaluates and logs; `enforce` blocks unsafe calls. |

Pick `none` for axis 1 if your code uses a custom tool-call loop and you'd rather call `guard.guard_before()` / `guard.guard_after()` yourself.

---

## Three surfaces

### Interactive TTY (humans)

```bash
sponsio init
```

The wizard prompts each axis in turn. Defaults are highlighted; press Enter to accept.

### Non-interactive (CI, scripts, IDE agents)

```bash
sponsio init --apply 'framework=langgraph;hosts=cursor;mode=observe'
```

Picks format: `framework=<name>;hosts=<a>,<b>;skills=<a>,<b>;mode=<observe|enforce>`. Each axis is optional; omit any axis to take its default.

### Dry-run preview

```bash
sponsio init --plan 'framework=langgraph;hosts=cursor;mode=observe'
```

Prints the would-run commands without executing. The IDE-agent onboarding prompt uses this exact format for its preview step, so what shows in the preview is what `--apply` would do.

---

## What `init` calls under the hood

```
sponsio init
  ├── axis 1 → sponsio onboard <target>     framework wrap, AST scan, write sponsio.yaml
  ├── axis 2 → sponsio host install <host>  one call per picked host
  ├── axis 3 → sponsio skill install        per picked IDE
  └── axis 4 → write `mode: <observe|enforce>` into sponsio.yaml
```

`init` is the orchestrator. The underlying commands stay focused (each one knows about exactly one axis) so you can re-do a single axis later without re-running the whole wizard. `sponsio host install cursor` adds a host gate to an existing project; `sponsio mode enforce` flips mode without touching anything else.

---

## A typical interactive run

```text
━━━ ◒◓ sponsio init ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
▎ detected: langgraph (3 imports), cursor IDE present
▎
▎ axis 1: framework wrap (langgraph) [Y/n]: y
▎ axis 2: protect host agents (cursor)? [Y/n]: y
▎ axis 3: install skill into cursor? [Y/n]: y
▎ axis 4: mode (observe) [Enter to confirm]:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✓ wrote sponsio.yaml (langgraph, observe mode, 17 contracts)
✓ installed cursor host hook
✓ installed cursor skill
✓ doctor: 8/9 ok, 1 warn

Add this to your agent entry point:

  from sponsio.langgraph import Sponsio
  guard = Sponsio(config="sponsio.yaml", agent_id="agent")
  agent = create_react_agent(model, guard.wrap(tools))
```

Paste the snippet. Run your agent. Review the observe-mode report (`sponsio report --since 24h`). Flip to enforce (`sponsio mode enforce`) when the contract set is stable.

---

## Eval scaffold (`--with-example`)

Orthogonal flag. Drops a pre-tuned `sponsio eval` scaffold (one `sponsio.yaml` + six labelled traces) into the target directory.

```bash
sponsio init . --with-example
```

Useful for smoke tests, demos, and evaluating a contract set before integrating into real code. `--with-example` is incompatible with `--plan`, `--apply`, and `--no-demo` (the wizard flow doesn't run on this path).

---

## When `init` is the wrong tool

- **Greenfield project with no agent code yet.** Nothing to scan. Start from [First contract](../getting-started/first-contract.md) instead.
- **Just want to try Sponsio.** `sponsio demo --scenario wire` runs a 30-second packaged unsafe trajectory with no setup.
- **You already have `sponsio.yaml` and just need to flip mode.** `sponsio mode enforce`.
- **You already have `sponsio.yaml` and just need to add a host hook.** `sponsio host install cursor`.
- **Multi-agent project.** `init` writes one `sponsio.yaml` with one agent block. Run it once, then split into per-agent sections by hand.

---

## Next

- [Contract sources](contract-sources.md): scan, policy-doc mining, trace mining.
- [Observe vs. enforce](observe-vs-enforce.md): shadow mode to production.
- [Plugins (Mode A)](../plugins.md): what axis 2's `host install` installs and how it routes tool calls.
- [CLI reference](../reference/cli.md): `sponsio init` flags and the underlying commands it calls.
