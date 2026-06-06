---
title: Sponsio documentation
description: Runtime contracts for LLM agents. Install, integrate, reference.
---

# Sponsio documentation

Sponsio is a runtime contract layer for LLM agents. It sits at the action boundary, blocks unsafe tool calls before they fire, and ships every verdict to your observability stack.

If you have never run Sponsio before, start here:

```bash
pip install sponsio
sponsio init .
```

Then go to the [Quickstart](getting-started/quickstart.md).

## Sections

- **[Getting started](getting-started/install.md)**: install, run your first guarded agent, write your first contract. Includes paste-ready [IDE-agent prompts](getting-started/onboard-prompt.md) for Claude Code / Cursor / Codex driven setup.
- **[Concepts](concepts/overview.md)**: what contracts are, how the runtime evaluates them, the LTL (linear temporal logic) backbone, OWASP coverage.
- **[Integrations](integrations/index.md)**: drop-in adapters for LangGraph, Claude Agent, OpenAI Agents, CrewAI, Vercel AI, MCP, and others.
- **[Guides](guides/onboarding.md)**: task-oriented walkthroughs. Tuning, observe-vs-enforce, contract sources, reporting, FAQ.
- **[Plugins](plugins.md)**: gate an entire Claude Code or OpenClaw session without code changes.
- **[Reference](reference/cli.md)**: CLI, `sponsio.yaml` schema, pattern catalog, observability schema, benchmarks, OSS / Cloud boundary.

For LLM assistants, a flat link map is at [`llms.txt`](../llms.txt).
