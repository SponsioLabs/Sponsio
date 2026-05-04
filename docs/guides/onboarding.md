---
title: Onboarding an existing agent
description: Use `sponsio onboard` to scan a codebase, draft contracts, and get a ready-to-paste snippet.
---

# Onboarding an existing agent

`sponsio onboard` is the one-command path for a repo that already has an agent. It scans the codebase, detects your framework, writes a `sponsio.yaml` in observe mode, and prints the three lines to paste into your agent entry file.

```bash
pip install sponsio
sponsio onboard .
```

The `.` is the path to scan. Any path works. It defaults to the current directory, so plain `sponsio onboard` is equivalent.

---

## What it does

1. **Detects your framework.** Grep for imports: `langgraph`, `claude_agent_sdk`, `openai`, `crewai`, `google.adk`, `vercel_ai`, `mcp`. If more than one is found, picks the one with the most imports.
2. **Scans for tools.** AST-walks your Python. Finds `@tool`-decorated functions, `Agent(tools=[...])`, and LangGraph `graph.add_node()` calls.
3. **Picks a starter pack.** Name-heuristic rules propose safety-relevant contracts (e.g. `dangerous_bash_commands` when it sees a `bash` tool, `no_data_leak` when it sees `read_db` alongside `send_email`).
4. **Writes `sponsio.yaml`.** Observe mode. Includes `tools:`, `contracts:`, and the detected framework.
5. **Runs `sponsio doctor`.** Verifies the config is loadable and the framework integration is wired.
6. **Prints a paste snippet.** Three lines (import + factory + wrap) specific to your detected framework.

`onboard` only *reads* your source. It writes a single `sponsio.yaml` into the current directory and nothing else.

---

## Typical output

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
```

Paste the snippet, run your agent, review the observe-mode report, then flip to enforce when the contract set is stable.

---

## When `onboard` is the wrong tool

- **Greenfield project with no agent code yet.** Nothing to scan. Start from [First contract](../getting-started/first-contract.md) instead.
- **You want LLM-inferred contracts from policy docs.** `onboard` is AST-only and free. For policy-document mining, use `sponsio scan --policy security.md --llm`. See [contract sources](contract-sources.md).
- **Multiple agents, each needing its own rules.** `onboard` writes one `sponsio.yaml` with one agent. For multi-agent projects, run `onboard` to get a starting point, then split into per-agent sections by hand.

---

## Next

- [Contract sources](contract-sources.md). Scan, policy-doc mining, trace mining.
- [Observe vs. enforce](observe-vs-enforce.md). Shadow mode to production.
- [CLI reference](../reference/cli.md), `sponsio onboard` flags.
