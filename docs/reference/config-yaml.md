---
title: sponsio.yaml reference
description: Full schema for the Sponsio config file — agents, tools, contracts, modes, thresholds, strategies.
---

# `sponsio.yaml` reference

`sponsio.yaml` is the canonical way to declare contracts. `sponsio scan` writes it; `sponsio onboard` writes it; `Sponsio(config=…)` reads it.

A minimal valid file:

```yaml
agents:
  bot:
    contracts:
      - name: "policy gate before refund"
        E: "must call `check_policy` before `issue_refund`"
```

A complete file, with every top-level field:

```yaml
mode: observe                          # observe | enforce
framework: langgraph                   # optional; auto-detected otherwise
sessions_dir: ~/.sponsio/sessions/     # where session logs are written

tools:                                 # optional; auto-discovered from scan
  check_policy:
    description: "Look up a customer's refund policy"
  issue_refund:
    description: "Issue a refund"

agents:
  support_bot:
    contracts:
      - name: "policy gate before refund"
        A: "called `issue_refund`"
        E: "must call `check_policy` before `issue_refund`"
        strategy: block
      - name: "refund rate limit"
        E: "tool `issue_refund` at most 5 times"
        strategy: escalate
      - name: "response scope"
        E:
          pattern: scope_respect
          args: ["customer support about orders, refunds, accounts"]
        beta: 0.85
        risk_profile: cautious
```

---

## Top-level fields

| Field | Type | Default | Notes |
|---|---|---|---|
| `mode` | `observe` \| `enforce` | `observe` | Global default; per-contract `mode:` overrides. |
| `framework` | string | auto-detect | `langgraph`, `claude_agent`, `openai`, `openai_agents`, `crewai`, `google_adk`, `vercel_ai`, `mcp`, or omitted. |
| `sessions_dir` | path | `~/.sponsio/sessions/` | Set to `null` to disable local session logging. |
| `tools` | map | `{}` | Optional tool metadata; scan populates automatically. |
| `agents` | map | required | Per-agent contract set. |

---

## `agents.<id>`

Each agent has a dedicated contract list. Contracts do not leak across agents.

```yaml
agents:
  support_bot:
    contracts: [...]
  orchestrator:
    contracts: [...]
```

---

## Contracts

Each entry in `contracts:` has these fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | no | Human-readable label for logs and reports. |
| `A` | string \| object | no | Assumption — when the rule fires. Omit for unconditional rules. |
| `E` | string \| object | yes | Enforcement — the rule itself. |
| `strategy` | string | no | `block`, `escalate`, `retry_with_constraint`, `redirect_to_safe`, or a dotted callable path. |
| `mode` | `observe` \| `enforce` | no | Per-contract override. |
| `alpha` | float 0–1 | no | Sto only — assumption confidence threshold. |
| `beta` | float 0–1 | no | Sto only — guarantee confidence threshold. |
| `risk_profile` | string | no | Sto only — `cautious`, `balanced`, `permissive`; sets α/β for you. |

### Shorthand form

`A:` and `E:` accept a natural-language string; the parser matches it to a pattern:

```yaml
- E: "tool `check_policy` must precede `issue_refund`"
- E: "bash command must not contain `rm -rf`"
- E: "tool `query_db` at most 5 times"
```

### Structured form

For patterns with arguments — notably sto atoms — use the structured form:

```yaml
- E:
    pattern: scope_respect
    args: ["customer support about orders, refunds, accounts"]
    context_scope: event         # event | last_k | full_trace
  beta: 0.85
```

See the [pattern catalog](patterns.md) for det patterns and the [sto atom catalog](sto-atoms.md) for sto atoms.

---

## Tool declarations (optional)

```yaml
tools:
  check_policy:
    description: "Look up a customer's refund policy"
    tags: [read_only, customer_data]
  issue_refund:
    description: "Issue a refund"
    tags: [destructive, financial]
```

Tags are free-form and can be referenced in patterns (e.g. `destructive_action_gate(tag="destructive")`). `sponsio scan` populates these from your tool definitions automatically.

---

## Validating a config

```bash
sponsio validate sponsio.yaml
```

Parses, type-checks, resolves every pattern reference, and reports unresolved names, mis-typed args, or atoms referenced but not registered.

```bash
sponsio doctor
```

Broader — also checks framework detection, provider credentials, session-log writability.

---

## Loading from Python

```python
from sponsio.langgraph import Sponsio

guard = Sponsio(config="sponsio.yaml", agent_id="support_bot")
```

`agent_id` picks which entry in `agents:` applies. If omitted, the default is the first agent in the file.

---

## Next

- [Pattern catalog](patterns.md) — every det pattern with NL form.
- [Sto atom catalog](sto-atoms.md) — every sto atom.
- [CLI reference](cli.md) — `sponsio scan`, `sponsio validate`, `sponsio doctor`.
- [Contract sources](../guides/contract-sources.md) — scan, policy-doc mining, trace mining.
