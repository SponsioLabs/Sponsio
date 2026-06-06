---
title: Contract sources
description: Three ways to populate sponsio.yaml.
---

# Contract sources

Sponsio reads contracts from three sources. All produce the same output: enforceable contracts loaded via a framework `Sponsio()` factory.

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

## Source 1: code scan

Extract tools and infer constraints from agent source.

```bash
sponsio scan src/agents/ -o sponsio.yaml
```

Without `--llm`, the scan is rule-based:

1. Finds tools (`@tool` decorators, `Agent(tools=[...])`, `graph.add_node()`).
2. Extracts ordering from `graph.add_edge("A", "B")` and call graphs.
3. Generates `must_precede` constraints for each ordering dependency.
4. Outputs tools and constraints in yaml.

With `--llm`, the LLM sees the full source and discovers constraints static analysis can't:

- `always_followed_by` (liveness obligations)
- `rate_limit` (from constants like `MAX_RETRIES = 3`)
- `no_reversal` (from business logic semantics)

```bash
sponsio scan src/agents/ --llm -o sponsio.yaml
sponsio scan src/agents/ --llm --provider gemini
```

Provider env vars and the full matrix: [reference/cli.md](../reference/cli.md#provider-matrix).

## Source 2: policy documents

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

## Source 3: hand-written

Edit `sponsio.yaml` directly. Two formats, mixable.

### NL strings

```yaml
agents:
  customer_bot:
    contracts:
      - G: "tool `check_policy` must precede `issue_refund`"
      - G: "tool `issue_refund` at most 3 times"
      - G: "response must not contain PII"
```

Each entry is one `(assumption, guarantee)` pair. `A` is optional, `G` is required. Each field can be a scalar or a list (lists are ANDed). The legacy keys `E:` / `enforcement:` are still accepted for backward compatibility.

Deterministic syntax (must use backtick-quoted tool names):

```
tool `A` must precede `B`
tool `X` at most N times
tool `A` requires permission `perm_name`
tools `A` and `B` are mutually exclusive
after `A`, tool `B` is forbidden
tool `A` cooldown of N steps
```

Sponsio parses NL strings through two stages: rule-based first (free), LLM fallback last (requires API key).

### Structured entries

```yaml
agents:
  customer_bot:
    contracts:
      - pattern: must_precede
        args: [check_policy, issue_refund]
        source: scan
      - pattern: rate_limit
        args: [issue_refund, 3]
```

Compiled directly. No NL parsing. Auto-emitted by `sponsio scan`.

### Tool policy (v0.2 shortcut for default-deny)

For the common "the agent can only call these tools" rule, you can skip writing a `tool_allowlist` contract by declaring it at the agent level:

```yaml
agents:
  customer_bot:
    tool_policy:
      default: deny
      approved: [search, read_file, list_dir]
      enforcement: proactive   # optional. strip denied tools at wrap()
```

Sponsio synthesizes the equivalent `tool_allowlist` contract internally. See [config-yaml.md#tool-policy](../reference/config-yaml.md#tool_policy) for the full schema.

## Loading config in Python

```python
from sponsio.langgraph import Sponsio

guard = Sponsio(config="sponsio.yaml", agent_id="customer_bot")
agent = create_react_agent(model, guard.wrap(tools))
```

Inline contracts add on top of yaml:

```python
guard = Sponsio(
    config="sponsio.yaml",
    agent_id="customer_bot",
    contracts=["tool `notify` at most 5 times"],
)
```

## Validation

```bash
sponsio validate --config sponsio.yaml             # parse + structural
sponsio validate --config sponsio.yaml --json      # CI-friendly
```

## End-to-end workflow

```bash
sponsio scan src/agents/ --llm -o sponsio.yaml                     # 1. discover
sponsio scan src/agents/ --policy compliance.md --llm --append     # 2. policy
# 3. edit sponsio.yaml, add hand-written rules
sponsio validate --config sponsio.yaml                             # 4. validate
python my_agent.py                                                 # 5. run
```

## See also

- [Quickstart](../getting-started/quickstart.md)
- [Config reference](../reference/config-yaml.md)
- [CLI reference](../reference/cli.md)
- [Integrations](../integrations/index.md)
