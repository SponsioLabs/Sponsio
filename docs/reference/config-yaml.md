---
title: sponsio.yaml reference
description: Full schema for the Sponsio config file plus the three ways to populate it. Agents, tools, contracts, modes, thresholds, strategies.
---

# `sponsio.yaml` reference

`sponsio.yaml` is the canonical way to declare contracts. `sponsio scan` writes it, `sponsio init` writes it, and `Sponsio(config=...)` reads it.

This page covers two things: the three ways to populate the file, and the full schema of what can live inside it.

A minimal valid file:

```yaml
agents:
  bot:
    contracts:
      - name: "policy gate before refund"
        G: "must call `check_policy` before `issue_refund`"
```

---

## How to populate sponsio.yaml

Three sources produce the same output: enforceable contracts loaded via `Sponsio()`.

```
Source 1: Code scan          sponsio scan src/ -o sponsio.yaml
Source 2: Policy documents   sponsio scan src/ --policy security.md --llm
Source 3: Hand-written       (edit sponsio.yaml directly)
                                       │
                                       ▼
                                 sponsio.yaml
                                       │
                                       ▼
                                 guard = Sponsio(config="sponsio.yaml")
```

The three sources mix freely in one yaml. Each contract entry can carry a `source:` tag for provenance.

### Source 1: code scan

Extract tools and infer constraints from agent source.

```bash
sponsio scan src/agents/ -o sponsio.yaml
```

Without `--llm`, the scan is rule-based:

1. Finds tools (`@tool` decorators, `Agent(tools=[...])`, `graph.add_node()`).
2. Extracts ordering from `graph.add_edge("A", "B")` and call graphs.
3. Generates `must_precede` constraints for each ordering dependency.
4. Outputs tools and constraints in yaml.

With `--llm`, the LLM sees the full source and discovers constraints a static scan cannot find:

- `always_followed_by` (liveness obligations)
- `rate_limit` (from constants like `MAX_RETRIES = 3`)
- `no_reversal` (from business logic semantics)

```bash
sponsio scan src/agents/ --llm -o sponsio.yaml
sponsio scan src/agents/ --llm --provider gemini
```

Provider env vars and the full matrix: [reference/cli.md](cli.md#provider-matrix).

### Source 2: policy documents

Extract contracts from a policy or compliance document, using the tool inventory as context.

```bash
# Scan code first to populate the tool inventory, then add policy:
sponsio scan src/agents/ -o sponsio.yaml
sponsio scan src/agents/ --policy security_policy.md --llm -o sponsio.yaml --append
```

The tool inventory is critical. Without it the LLM produces generic constraints. With it, policy maps to specific tools:

```
Policy:           "All refunds require supervisor approval"
Tool inventory:   [check_policy, issue_refund, notify_customer]
Constraint:       must_precede(check_policy, issue_refund)
```

Supported document formats: `.md`, `.txt`, `.pdf` (`pip install sponsio[pdf]`).

### Source 3: hand-written

Edit `sponsio.yaml` directly. The two forms (NL strings and structured entries) are described in [Contracts](#contracts) below.

### End-to-end workflow

```bash
sponsio scan src/agents/ --llm -o sponsio.yaml                     # 1. discover
sponsio scan src/agents/ --policy compliance.md --llm --append     # 2. policy
# 3. edit sponsio.yaml, add hand-written rules
sponsio validate --config sponsio.yaml                             # 4. validate
python my_agent.py                                                 # 5. run
```

---

## Full schema

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

### Top-level fields

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
| `approved` | `[]` | Explicit allowlist. Empty plus deny blocks every tool (useful for a complete lockdown). Accepts a flat list or `{tools: [...]}` for future per-host scoping. |
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
| `G` | string \| object | yes | Guarantee. The rule itself. |
| `strategy` | string | no | `block` (`DetBlock`), `escalate` (`EscalateToHuman`, accepts `notify:` list of dotted callable paths), `redirect_to_safe` (substitute a pre-approved tool), `warn_only` (log without blocking), or a dotted callable path. |
| `mode` | `observe` \| `enforce` | no | Per-contract override. |

### Shorthand form (natural-language strings)

`A:` and `G:` accept a natural-language string; the parser matches it to a pattern:

```yaml
agents:
  customer_bot:
    contracts:
      - G: "tool `check_policy` must precede `issue_refund`"
      - G: "tool `issue_refund` at most 3 times"
      - G: "response must not contain PII"
```

Each entry is one `(assumption, guarantee)` pair. `A` is optional, `G` is required. Each field can be a scalar or a list (lists are ANDed). The legacy keys `E:` and `enforcement:` are still accepted for backward compatibility.

Sponsio parses NL strings through two stages: rule-based first (free), LLM fallback last (requires API key).

Common NL forms:

```
tool `A` must precede `B`
tool `X` at most N times
tool `A` requires permission `perm_name`
tools `A` and `B` are mutually exclusive
after `A`, tool `B` is forbidden
tool `A` cooldown of N steps
```

### Structured form

For patterns that need typed arguments (lists, regex tuples, threshold floats), use the structured form:

```yaml
agents:
  customer_bot:
    contracts:
      - pattern: must_precede
        args: [check_policy, issue_refund]
        source: scan
      - pattern: rate_limit
        args: [issue_refund, 3]
      - G:
          pattern: arg_blacklist
          args: ["bash", "rm -rf"]
```

Compiled directly. No NL parsing. Auto-emitted by `sponsio scan`.

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

Tags are arbitrary strings and can be referenced in patterns (for example, `destructive_action_gate(tag="destructive")`). `sponsio scan` populates these from your tool definitions automatically.

---

## Validating a config

```bash
sponsio validate --config sponsio.yaml             # parse + structural
sponsio validate --config sponsio.yaml --json      # CI-friendly
```

Parses, type-checks, resolves every pattern reference, and reports unresolved names, mis-typed args, or atoms referenced but not registered.

```bash
sponsio doctor
```

Broader. Also checks framework detection, provider credentials, and session-log writability.

---

## Loading from Python

```python
from sponsio.langgraph import Sponsio

guard = Sponsio(config="sponsio.yaml", agent_id="support_bot")
agent = create_react_agent(model, guard.wrap(tools))
```

`agent_id` picks which entry in `agents:` applies. If omitted, the default is the first agent in the file.

Inline contracts add on top of yaml:

```python
guard = Sponsio(
    config="sponsio.yaml",
    agent_id="support_bot",
    contracts=["tool `notify` at most 5 times"],
)
```

---

## Next

- [Pattern catalog](patterns.md). Every deterministic pattern with NL form.
- [CLI reference](cli.md): `sponsio scan`, `sponsio validate`, `sponsio doctor`.
