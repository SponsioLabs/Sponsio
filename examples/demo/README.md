# Sponsio Hackathon Demos

Three demos showing Sponsio enforcing hard + soft contracts on LLM agents in different scenarios.

## Setup

```bash
# From project root
pip install -e .
pip install langgraph langchain-core langchain-google-genai
```

## Demos

### Demo A: Coding Agent Database Safety

A coding agent is asked to clean up test data. The DB connection points at production. Without Sponsio, it runs `DELETE FROM users` and wipes 47K records.

```bash
python examples/demo/demo_coding_agent.py
```

| Constraint | Type | What it does |
|---|---|---|
| `confirm_with_user` must precede `execute_sql` | Hard | Blocks destructive SQL without human approval |
| `execute_sql` rate limited to 2 calls | Hard | Prevents runaway deletion loops |
| Generated SQL must have safety conditions | Soft | Catches bare `DELETE FROM users` (no WHERE), feedback guides agent to add `WHERE type='test'` |

---

### Demo B: MCP Agent Data Leak Prevention

An internal agent connected to Google Drive and Slack via MCP is asked to summarize a board deck and share it with marketing. Without Sponsio, it posts revenue, burn rate, and valuation to a 200-person channel.

```bash
python examples/demo/demo_mcp_leak.py
```

| Constraint | Type | What it does |
|---|---|---|
| Confidential content blocked from unauthorized channels | Hard | `no_data_leak`: board-confidential data only goes to #exec-team, #board |
| `read_document` must precede `slack_post` | Hard | Ensures agent reads before posting |
| Shared summaries must not contain raw financials | Soft | Catches `$2.3M revenue` in summary, feedback guides agent to redact to qualitative language |

---

### Demo C: Customer Service Social Engineering

A customer service agent gets socially engineered into issuing a refund without checking policy. Customer claims "manager already approved it".

```bash
# Mock mode (no API key needed)
USE_MOCK=1 python examples/demo/demo_customer_service.py

# Real LLM mode (Gemini via LangGraph)
GOOGLE_API_KEY=your_key USE_MOCK=0 python examples/demo/demo_customer_service.py
```

| Constraint | Type | What it does |
|---|---|---|
| `check_refund_policy` must precede `issue_refund` | Hard | Blocks refund without policy check — social engineering defeated |
| `issue_refund` rate limited to 1 call | Hard | Prevents duplicate refunds |
| Response must use empathetic tone | Soft | Catches blunt denial, feedback guides agent to offer alternatives |

---

## Demo Structure

All three demos follow the same format:

1. **Contracts** — show what's being enforced (hard + soft)
2. **WITHOUT Sponsio** — agent does the dangerous thing
3. **WITH Sponsio** — hard constraint blocks it, agent self-corrects
4. **Soft constraint** — scored evaluation + feedback + retry loop

## Architecture

```
NL contract string
  -> parse_nl_rule_based()           # keyword matching
  -> must_precede("A", "B")          # pattern function
  -> G(Implies(called(B), ...))      # LTL formula

Agent calls tool
  -> guard.pre_check(tool_name)
    -> monitor.check_action()
      -> append Event to Trace
      -> ground(trace)               # trace -> predicate truth values
      -> evaluate(formula, vals)     # LTL on finite trace
      -> violation? -> BLOCK
      -> pass? -> execute normally
```
