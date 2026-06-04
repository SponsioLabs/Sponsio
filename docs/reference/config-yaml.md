---
title: sponsio.yaml reference
description: Full schema for the Sponsio config file. Agents, tools, contracts, modes, thresholds, strategies.
---

# `sponsio.yaml` reference

`sponsio.yaml` is the canonical way to declare contracts. `sponsio scan` writes it; `sponsio init` writes it; `Sponsio(config=…)` reads it.

A minimal valid file:

```yaml
agents:
  bot:
    contracts:
      - name: "policy gate before refund"
        G: "must call `check_policy` before `issue_refund`"
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
        G: "must call `check_policy` before `issue_refund`"
        strategy: block
      - name: "refund rate limit"
        G: "tool `issue_refund` at most 5 times"
        strategy: escalate
      - name: "no destructive deletes"
        G: "bash command must not contain `rm -rf`"
        strategy: block
```

---

## Top-level fields

| Field | Type | Default | Notes |
|---|---|---|---|
| `mode` | `observe` \| `enforce` | `observe` | Global default; per-contract `mode:` overrides. |
| `framework` | string | auto-detect | `langgraph`, `claude_agent`, `openai`, `openai_agents`, `crewai`, `google_adk`, `vercel_ai`, `mcp`, or omitted. |
| `sessions_dir` | path | `~/.sponsio/sessions/` | Set to `null` to disable local session logging. |
| `tools` | map | `{}` | Optional tool metadata; scan populates automatically. |
| `tool_policy` | map | `{}` | Default-deny posture + approved-tool allowlist. See [`tool_policy`](#tool_policy) below. |
| `agents` | map | required | Per-agent contract set. |

---

## `tool_policy`

Declarative default-deny posture. The agent can only call tools in `approved:` when `default: deny` is set. Adding a new tool to the underlying framework does not auto-trust it.

```yaml
tool_policy:
  default: deny          # allow (default, backwards-compat) | deny
  approved: [search, read_file, list_dir]
  enforcement: reactive  # reactive (default) | proactive
```

| Field | Default | Behavior |
|---|---|---|
| `default` | `allow` | `deny` synthesizes a `tool_allowlist` contract that blocks every tool not in `approved`. `allow` is a no-op (backwards-compat). |
| `approved` | `[]` | Explicit allowlist. Empty + deny blocks every tool (useful "lock-down completely" posture). Accepts a flat list or `{tools: [...]}` for future per-host scoping. |
| `enforcement` | `reactive` | `reactive`: the agent still sees the full tool menu; denied calls get blocked at call time via `guard_before`. `proactive`: wrap-time adapters (LangGraph, CrewAI, OpenAI Agents SDK, Google ADK) strip denied tools from the bound toolset before the model ever sees them. |

Inline equivalent on `Sponsio(tool_policy={...})`. The two paths produce the same synthesized contract.

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
| `A` | string \| object | no | Assumption. When the rule fires. Omit for unconditional rules. |
| `E` | string \| object | yes | Enforcement. The rule itself. |
| `strategy` | string | no | `block` (`DetBlock`), `escalate` (`EscalateToHuman`, accepts `notify:` list of dotted callable paths), `redirect_to_safe` (substitute a pre-approved tool), `warn_only` (log without blocking), or a dotted callable path. |
| `mode` | `observe` \| `enforce` | no | Per-contract override. |

### Shorthand form

`A:` and `G:` accept a natural-language string; the parser matches it to a pattern:

```yaml
- G: "tool `check_policy` must precede `issue_refund`"
- G: "bash command must not contain `rm -rf`"
- G: "tool `query_db` at most 5 times"
```

### Structured form

For patterns that need typed arguments (lists, regex tuples, threshold floats). Use the structured form:

```yaml
- G:
    pattern: arg_blacklist
    args: ["bash", "rm -rf"]
```

See the [pattern catalog](patterns.md) for the full list of deterministic patterns.

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

Broader. Also checks framework detection, provider credentials, session-log writability.

---

## Loading from Python

```python
from sponsio.langgraph import Sponsio

guard = Sponsio(config="sponsio.yaml", agent_id="support_bot")
```

`agent_id` picks which entry in `agents:` applies. If omitted, the default is the first agent in the file.

---

## Next

- [Pattern catalog](patterns.md). Every deterministic pattern with NL form.
- [CLI reference](cli.md), `sponsio scan`, `sponsio validate`, `sponsio doctor`.
- [Contract sources](../guides/contract-sources.md). Scan, policy-doc mining, trace mining.
