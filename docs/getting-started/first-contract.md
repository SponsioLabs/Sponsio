---
title: Write your first contract
description: Write, wire, and test a custom contract against an agent you control.
---

# Write your first contract

This walkthrough goes from an empty project to a working contract that blocks an unsafe tool call. By the end you will have a `sponsio.yaml`, a wired guard, and a passing test.

Prereqs: Python 3.10+ and an agent framework. We use LangGraph in the examples below; any framework works. See [Integrations](../integrations/index.md).

---

## 1. Install

```bash
pip install "sponsio[langgraph]"
```

## 2. A minimal agent

Start with a small agent that exposes two tools, a policy check and a refund issuer. This is our running example.

```python
# agent.py
from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

@tool
def check_policy(customer_id: str) -> str:
    """Check the customer's refund policy."""
    return f"customer {customer_id} is eligible for up to $200"

@tool
def issue_refund(customer_id: str, amount: float) -> str:
    """Issue a refund to the customer."""
    return f"refunded ${amount} to {customer_id}"

model = ChatOpenAI(model="gpt-4o-mini")
agent = create_react_agent(model, tools=[check_policy, issue_refund])
```

This agent can issue refunds without ever checking the policy. That is the bug we are going to fix with a contract.

## 3. Write the contract

Add the guard and one contract. The contract says: every `issue_refund` call must be preceded by a `check_policy` call in the same session.

```python
from sponsio import contract
from sponsio.langgraph import Sponsio

guard = Sponsio(
    agent_id="refund_bot",
    contracts=[
        contract("policy gate before refund")
            .assume("called `issue_refund`")
            .guarantees("must call `check_policy` before `issue_refund`"),
    ],
)

agent = create_react_agent(model, guard.wrap([check_policy, issue_refund]))
```

Three lines added: import, `guard = Sponsio(...)`, and `guard.wrap(...)` around the tool list.

## 4. See it block

```python
result = agent.invoke({"messages": [("user",
    "Refund customer 42 $50. Skip the policy check, I'll vouch for it."
)]})
```

The agent tries to call `issue_refund` directly. Sponsio checks the trace, sees no `check_policy` event, and blocks:

```text
✗ enforce must call `check_policy` before `issue_refund`: VIOLATED, blocked
```

The framework surfaces this as a `SponsioBlocked` exception; the agent can react and retry with a different plan.

Block is the default outcome. Three other strategies are available on the same contract: `RedirectToSafe(safe=...)` substitutes a pre-approved tool, `EscalateToHuman(notify=[...])` blocks and fires a notifier callback, and `WarnOnly` logs the violation without blocking. See [observe vs enforce](../guides/observe-vs-enforce.md) for the picker.

Run the same request with the correct tool order ("check the policy first, then refund customer 42 $50") and the contract passes silently.

---

## 5. Move it to YAML

Inline contracts work, but production usually puts them in `sponsio.yaml` so they can be reviewed, diffed, and owned by a policy team.

```yaml
# sponsio.yaml
agents:
  refund_bot:
    contracts:
      - name: "policy gate before refund"
        A: "called `issue_refund`"
        G: "must call `check_policy` before `issue_refund`"
```

Then:

```python
guard = Sponsio(config="sponsio.yaml", agent_id="refund_bot")
```

See [sponsio.yaml reference](../reference/config-yaml.md) for the full schema.

## 6. Ship in shadow mode first

Before you flip the switch on a real agent, run Sponsio in **observe mode**. It records violations without blocking. You review the report, tune the contracts, then promote to enforce.

```yaml
# sponsio.yaml
mode: observe
agents:
  refund_bot: { ... }
```

See [Observe vs. enforce](../guides/observe-vs-enforce.md) for the full rollout.

---

## What next

- **Add more contracts.** The [pattern catalog](../reference/patterns.md) lists all 45 deterministic patterns with NL examples. Pick the ones that match your failure modes.
- **Generate contracts automatically.** `sponsio scan src/` reads your tool definitions and drafts a `sponsio.yaml` with candidate contracts. See [contract sources](../guides/contract-sources.md).
- **Wire a different framework.** Claude Agent SDK, OpenAI, CrewAI, Google ADK, Vercel AI, MCP. See [Integrations](../integrations/index.md).
