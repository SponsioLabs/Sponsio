# Input Formats & Contract Sources

Sponsio accepts contracts from three sources. All sources produce the same result — enforceable contracts loaded via a framework namespace `Sponsio()` factory.

---

## Overview

```
Source 1: Code scan          sponsio scan src/ -o sponsio.yaml
Source 2: Policy documents   sponsio scan src/ --policy security.md --llm -o sponsio.yaml
Source 3: Engineer hand-write  (edit sponsio.yaml directly)
                                        │
                                        ▼
                              ┌─────────────────┐
                              │  sponsio.yaml    │  single file, three formats
                              └────────┬────────┘
                                       │
                                       ▼
                              guard = Sponsio(
                                  config="sponsio.yaml",
                                  agent_id="bot",
                              )
```

---

## Source 1: Code Scan (`sponsio scan`)

Automatically extract tools and infer constraints from your agent source code.

### Basic usage (rule-based, no LLM needed)

```bash
sponsio scan src/agents/ -o sponsio.yaml
```

What it does:
1. **Finds tools** — decorated functions (`@tool`), `Agent(tools=[...])`, LangGraph `graph.add_node()`
2. **Extracts ordering** — from `graph.add_edge("A", "B")` and function call graphs
3. **Generates `must_precede` constraints** for each ordering dependency
4. **Outputs tools + constraints** in YAML format

### With LLM inference (discovers more constraint types)

```bash
sponsio scan src/agents/ --llm -o sponsio.yaml
```

The LLM sees the full source code + tool inventory and can discover constraints that static analysis can't:
- `always_followed_by` (liveness obligations)
- `rate_limit` (from constants like `MAX_RETRIES = 3`)
- `no_reversal` (from business logic semantics)
- Sto constraints (output quality requirements)

### Provider selection

```bash
# Auto-detect from environment (GOOGLE_API_KEY → Gemini, OPENAI_API_KEY → OpenAI)
sponsio scan src/ --llm

# Explicit
sponsio scan src/ --llm --provider gemini
sponsio scan src/ --llm --provider openai --model gpt-4o
```

---

## Source 2: Policy Documents

Extract contracts from policy/compliance documents using the tool inventory as context.

```bash
# Scan code first (gets tool names), then extract from policy
sponsio scan src/agents/ --policy security_policy.md --llm -o sponsio.yaml
```

The tool inventory is critical — without it, the LLM can only produce generic sto constraints. With tool names as context, it maps policy rules to specific tools:

```
Policy: "All refunds require supervisor approval"
+ Tool inventory: [check_policy, issue_refund, notify_customer]
= Constraint: must_precede(check_policy, issue_refund)
```

### Supported document formats

- `.md` (Markdown)
- `.txt` (plain text)
- `.pdf` (requires `pip install sponsio[pdf]`)

### Appending to existing config

```bash
# First scan generates base config
sponsio scan src/ -o sponsio.yaml

# Policy adds more constraints without overwriting
sponsio scan src/ --policy compliance.md --llm -o sponsio.yaml --append
```

---

## Source 3: Engineer Hand-Written

Engineers edit `sponsio.yaml` directly to add constraints that can't be auto-discovered.

### NL String Format

The simplest format — write constraints as natural language strings:

```yaml
agents:
  customer_bot:
    contracts:
      - E: "tool `check_policy` must precede `issue_refund`"
      - E: "tool `issue_refund` at most 3 times"
      - E: "response must not contain PII"
```

Each entry under `contracts:` is one independent `(assumption, enforcement)` pair. `A` (or `assumption`) is optional; `E` (or `enforcement`) is required. Each field may be a scalar or a list — a list is ANDed.

Sponsio parses these through a three-stage cascade:
1. **Rule-based** — keyword matching against the deterministic pattern library (free, milliseconds)
2. **Sto keyword** — keyword matching against sto categories (free, milliseconds)
3. **LLM fallback** — if configured, catches everything else (requires API key)

### NL Syntax Reference

Det constraints must use backtick-quoted tool names:

```
tool `A` must precede `B`
tool `X` at most N times
tool `A` requires permission `perm_name`
tools `A` and `B` are mutually exclusive
after `A`, tool `B` is forbidden
tool `A` cooldown of N steps
tool `A` at most N retries
```

Sto constraints use descriptive language:

```
response must not contain PII
response must be empathetic
output must be in JSON format
response under 200 words
must not mention competitors
```

---

## YAML File Format Specification

### Full schema

```yaml
# sponsio.yaml
version: "1"

# Optional: global defaults
defaults:
  verbose: true
  verbosity: 1      # 0=violations only, 1=all checks, 2=with spans

# Optional: tool inventory (auto-populated by sponsio scan)
tools:
  - name: check_policy
    description: "Verify refund eligibility"
    params: "order_id: str"
  - name: issue_refund
    description: "Process customer refund"
    params: "order_id: str, amount: float"

# Required: agent contracts
agents:
  # Per-contract form: each entry is one (A, E) pair, evaluated independently.
  customer_bot:
    contracts:
      - A: "tool `validate_order` must precede `check_policy`"
        E: "tool `check_policy` must precede `issue_refund`"

      - E: "response must not contain PII"   # unconditional

      - E:
          - "tool `issue_refund` at most 3 times"
          - pattern: must_precede             # structured entries work too
            args: [check_policy, issue_refund]
            source: scan

  # Enforcement-only agent (all unconditional)
  coding_agent:
    contracts:
      - E: "tool `execute_code` requires permission `sandbox`"

  # Bare list shorthand: each item becomes one unconditional contract.
  simple_agent:
    - "tool `A` must precede `B`"
    - "tool `X` at most 5 times"
```

### Constraint entry formats

Three formats can be mixed in the same list:

| Format | Example | When to use |
|--------|---------|------------|
| **NL string** | `- "tool `A` must precede `B`"` | Engineer hand-written. Parsed at load time. |
| **Structured** | `- pattern: must_precede`<br>`  args: [A, B]` | Scan-generated. Compiled directly, no parsing needed. |
| **Structured + metadata** | `- pattern: must_precede`<br>`  args: [A, B]`<br>`  source: scan` | With provenance tag for auditability. |

### Structured entry fields

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `pattern` | yes | string | Pattern function name (e.g., `must_precede`, `rate_limit`) |
| `args` | yes | list | Arguments to the pattern function |
| `source` | no | string | Provenance: `"scan"`, `"policy"`, or omitted for hand-written |

### Tools section

The `tools` section is informational — it records the tool inventory for context but doesn't affect enforcement. It's auto-populated by `sponsio scan` and used as LLM context when parsing NL strings.

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | yes | string | Tool function name |
| `description` | no | string | One-line description |
| `params` | no | string | Parameter signature |

---

## Loading Config in Python

### With a framework namespace `Sponsio()` (recommended)

```python
from sponsio.langgraph import Sponsio

guard = Sponsio(
    config="sponsio.yaml",
    agent_id="customer_bot",
)

# guard has all contracts loaded — use normally
agent = create_react_agent(model, guard.wrap(tools))
```

### With `load_config()` (advanced)

```python
from sponsio.config import load_config, config_to_guard_kwargs
from sponsio.langgraph import LangGraphGuard

config = load_config("sponsio.yaml")
kwargs = config_to_guard_kwargs(config, "customer_bot")
guard = LangGraphGuard(**kwargs)
```

### Inline + config combined

```python
from sponsio.langgraph import Sponsio

# Config provides base contracts, inline adds more
guard = Sponsio(
    config="sponsio.yaml",
    agent_id="customer_bot",
    # These are added ON TOP of config contracts:
    contracts=["tool `notify` at most 5 times"],
)
```

---

## Validation

Always validate your config before deploying:

```bash
# Validate all agents
sponsio validate --config sponsio.yaml

# Validate a specific agent
sponsio validate --config sponsio.yaml --agent customer_bot

# JSON output for CI
sponsio validate --config sponsio.yaml --json
```

Example output:

```
Agent: customer_bot
  Guarantees:
    ✓ DET: must_precede(check_policy, issue_refund)
      Pattern : must_precede
      Formula : ((!(called('issue_refund')) U called('check_policy')) | G(!(called('issue_refund'))))
    ✓ DET: rate_limit(issue_refund, 3)
      Pattern : rate_limit
      Formula : G((Var('count', 'issue_refund') <= 3))
    ✓ STO: response must not contain PII
      Pattern : response must not contain PII

  ✓ All 3 contract(s) validated
```

---

## Complete Workflow Example

```bash
# 1. Discover contracts from code
sponsio scan src/agents/ --llm -o sponsio.yaml

# 2. Add policy constraints
sponsio scan src/agents/ --policy compliance.md --llm -o sponsio.yaml --append

# 3. Engineer reviews and adds hand-written constraints
#    (edit sponsio.yaml, add NL strings)

# 4. Validate everything
sponsio validate --config sponsio.yaml

# 5. Use in agent
python my_agent.py  # calls Sponsio(config="sponsio.yaml")
```
