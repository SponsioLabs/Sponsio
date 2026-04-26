---
title: Sponsio documentation
description: Reference manual for Sponsio — concepts, integrations, CLI, and API.
---

# Sponsio documentation

This is the reference manual for Sponsio. For the pitch, the benchmarks, and a 30-second install, see the [README](../README.md).

If you are just getting started, the fastest path is:

```bash
pip install sponsio
sponsio demo --scenario freeze --fast
sponsio onboard .
```

Then pick a track below.

---

## Three tracks

| | |
|---|---|
| **[Concepts](concepts/overview.md)** — how Sponsio models contracts, traces, atoms, and the two pipelines. Read this before writing your first custom contract. |
| **[Integrations](integrations/index.md)** — wire Sponsio into LangGraph, Claude Agent SDK, OpenAI, OpenAI Agents, CrewAI, Google ADK, Vercel AI, MCP, or a custom tool-calling loop. |
| **[Reference](reference/cli.md)** — CLI, pattern catalog, stochastic atom catalog, `sponsio.yaml` schema, Python and TypeScript API. |

---

## By task

- **"Block an unsafe tool call in under a minute"** → [Quickstart](getting-started/quickstart.md)
- **"Write my first contract"** → [First contract](getting-started/first-contract.md)
- **"Plug Sponsio into my framework"** → [Integrations](integrations/index.md)
- **"Generate contracts from my code / policy docs / traces"** → [Contract sources](guides/contract-sources.md)
- **"Ship from shadow mode into production"** → [Observe vs. enforce](guides/observe-vs-enforce.md)
- **"Pick the right pattern for a rule I have in mind"** → [Pattern catalog](reference/patterns.md)
- **"Drop in a ready-made contract pack (shell, filesystem, runaway defense)"** → [Contract library](reference/contract-lib.md)
- **"Wrap my Claude Code session with Sponsio guardrails"** → [sponsio-shield quickstart](../plugins/sponsio-shield/QUICKSTART.md)
- **"Add a stochastic atom for tone, PII, or scope"** → [Stochastic atoms](reference/sto-atoms.md)
- **"Understand why Sponsio uses LTL"** → [Architecture](concepts/architecture.md)
- **"Map a control to OWASP Agentic Top 10 (2026)"** → [OWASP coverage](owasp-agentic-top-10.md)
- **"Look up a CLI flag"** → [CLI reference](reference/cli.md)
- **"Reproduce the ODCV-Bench numbers"** → [Benchmarks](BENCHMARKS.md)

---

## LLM-readable index

A flat, link-only index of this documentation is available at [`llms.txt`](../llms.txt) (short) and [`llms-full.txt`](../llms-full.txt) (full). Point an LLM assistant at either to give it a complete map of the project.
