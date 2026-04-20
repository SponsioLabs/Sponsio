# Getting Started

Get Sponsio enforcing contracts on your agent in 5 minutes.

---

## Install

```bash
pip install -e .

# Optional extras
pip install -e ".[config]"    # YAML config support
pip install -e ".[llm]"      # LLM-powered contract discovery
pip install -e ".[otel]"     # OpenTelemetry span export
pip install -e ".[all]"      # everything
```

---

## Option A: Inline Contracts (quickest)

Add 3 lines to your existing agent:

```python
import sponsio

guard = sponsio.init(
    framework="langgraph",          # or "openai", "crewai", "agents_sdk"
    agent_id="my_bot",
    contracts=[
        "tool `check_policy` must precede `issue_refund`",
        "tool `issue_refund` at most 3 times",
    ],
)

# Wrap your tools — that's it
agent = create_react_agent(model, guard.wrap(tools))
```

When the agent tries to call `issue_refund` without calling `check_policy` first, Sponsio blocks the call and returns an error to the LLM. The LLM then self-corrects.

---

## Option B: Config File (recommended for production)

### Step 1: Scan your code

```bash
sponsio scan src/agents/ -o sponsio.yaml
```

This discovers your tools and infers ordering constraints automatically.

### Step 2: Review and edit

Open `sponsio.yaml` — add, remove, or adjust constraints:

```yaml
version: "1"

tools:
  - name: check_policy
    description: "Verify refund eligibility"
  - name: issue_refund
    description: "Process refund"

agents:
  my_bot:
    contracts:
      - E:
          pattern: must_precede
          args: [check_policy, issue_refund]
          source: scan
      - E: "tool `issue_refund` at most 3 times"
      - E: "response must not contain PII"
```

### Step 3: Validate

```bash
sponsio validate --config sponsio.yaml
```

### Step 4: Use

```python
import sponsio

guard = sponsio.init(
    framework="langgraph",
    config="sponsio.yaml",
    agent_id="my_bot",
)
agent = create_react_agent(model, guard.wrap(tools))
```

---

## What Happens at Runtime

```
  ━━━ Sponsio ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ▎ contract · my_bot
  ▎
  ▎ enforce ▸ check_policy must precede issue_refund
  ▎         ▸ tool `issue_refund` at most 3 times
  ▎         ▸ response must not contain PII
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ✗ enforce check_policy must precede issue_refund — VIOLATED → blocked
  ✓ enforce check_policy must precede issue_refund — pass
  ✓ enforce tool `issue_refund` at most 3 times — pass

  Sponsio Session Summary (my_bot)
  Total checks: 3  |  Det violations: 1  |  Sto violations: 0
```

---

## Next Steps

- [Contract Types & Atoms](contracts.md) — understand det vs sto constraints and the atom vocabulary
- [Input Formats](input-formats.md) — YAML spec, scan workflow, three input sources
- [Integrations](integrations.md) — framework-specific setup (LangGraph, OpenAI, CrewAI, MCP, Agents SDK)
- [CLI Reference](cli.md) — all commands and options
