---
title: Quickstart
description: Block an unsafe tool call in 60 seconds.
---

# Quickstart

Get Sponsio blocking an unsafe tool call in under a minute. No API key, no framework SDK, no Docker.

## 1. Install

```bash
pip install sponsio
```

Optional extras (all pure Python):

```bash
pip install "sponsio[all]"     # yaml + llm + otel
```

## 2. See a contract fire

Three recorded unsafe-agent trajectories ship in the wheel. Replay one:

```bash
sponsio demo --scenario wire --fast
```

You'll see an accounts-payable agent try to wire $847k to an unverified vendor, and Sponsio block it on three fronts at once:

```text
  ━━━ ◒◓ sponsio ━━━━━━━━━━━━━━━━━━━━━━━━━━
  ▎ contract · ap_copilot
  ▎ single wire capped at $50k
  ▎ enforce ▸ wire_transfer.amount must be in range [0, 50000]
  ▎ contract · ap_copilot
  ▎ compliance_approve must precede wire_transfer
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  -> wire_transfer(to='Acme Logistics LLC', amount=847000, ...)
  ✗ amount must be in range [0, 50000] — VIOLATED → blocked
  ✗ compliance_approve must precede wire_transfer — VIOLATED → blocked
```

Other scenarios:

```bash
sponsio demo --scenario cleanup    # Claude Code agent deletes .env + .git/
sponsio demo --scenario backup     # SRE cost-optimizer deletes prod DR backups
sponsio demo --scenario wire --no-guard   # same trajectory without contracts
```

## 3. Wire it into your project

One command. Detects framework, writes `sponsio.yaml` in observe mode, runs `sponsio doctor`, prints a 2-line patch:

```bash
sponsio onboard .
```

Typical output:

```text
· framework: langgraph (found 1 langgraph import in agent.py)
· provider: none (no provider credentials detected)
· starter-pack: +5 contracts from name-heuristic safety rules
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
  agent = create_react_agent(model, guard.wrap(tools))
```

Without an LLM key, `onboard` still ships a name-heuristic starter plus `sponsio:core/runaway` (token budgets, delegation depth, loop caps), all deterministic.

For TypeScript, install `@sponsio/sdk` and run `npx sponsio onboard .`. Same yaml output, same `Sponsio({ config: "sponsio.yaml" })` API.

## 4. Run and observe

`sponsio.yaml` starts in observe mode. Every contract evaluates, nothing blocks. Would-have-blocked decisions land in `~/.sponsio/sessions/<agent_id>/*.jsonl`.

After exercising the agent, review what would have blocked:

```bash
sponsio report --agent agent --since 24h
```

Pure-OSS live stream:

```bash
sponsio host trace --follow
```

## 5. Flip to enforce

When the report is clean:

```bash
export SPONSIO_MODE=enforce        # no code change
```

Or bake it into yaml:

```yaml
runtime:
  mode: enforce
```

Precedence: explicit ctor arg > env var > yaml > default.

## Troubleshooting

```bash
sponsio doctor                              # install + config + wiring
sponsio validate --config sponsio.yaml      # parse + structural checks
sponsio check --trace trace.json --config sponsio.yaml --agent agent
```

## Next

- [First contract](first-contract.md): write your own rule.
- [Integrations](../integrations/index.md): plug into LangGraph, CrewAI, OpenAI Agents, and others.
- [Config reference](../reference/config-yaml.md): full `sponsio.yaml` schema.
- [CLI reference](../reference/cli.md): every command and flag.
