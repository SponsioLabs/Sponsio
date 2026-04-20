# Sponsio

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://pypi.org/project/sponsio/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-789%20passing-brightgreen)](tests/)

> **Runtime contracts for LLM agents.** Stop your agent from doing dumb, dangerous, or unauthorized things — with one line of code, no LLM calls required.

<!-- HERO_GIF -->
<!--
  8-second before/after GIF goes here.
  Left half:  unguarded agent calls `issue_refund` without checking policy — refund issued.
  Right half: same agent wrapped with sponsio.init(...) — `issue_refund` blocked, agent self-corrects.
  Source: assets/hero.gif (to be recorded pre-launch).
-->

---

## 60-second start

```bash
pip install sponsio
```

```python
import sponsio

guard = sponsio.init(
    framework="langgraph",
    agent_id="support_bot",
    contracts=[
        "must call `check_policy` before `issue_refund`",
        "tool `issue_refund` at most 3 times",
        "response must not contain PII",
    ],
)

agent = create_react_agent(model, guard.wrap(tools))
```

That's it. Your agent now has runtime safety.

Contracts can be unconditional (bare strings) or conditional (assumption / enforcement pairs):

```python
contracts=[
    "tool `sed` arg contains `-i` is banned",
    {
        "assumption":  "called `cancel_order`",
        "enforcement": "must call `get_order_details` before `cancel_order`",
    },
]
```

See [Getting Started](docs/getting-started.md) for YAML config, non-framework use, and the full contract DSL.

---

## What you get

- **29 built-in contract patterns** — rate limits, tool allowlists, PII blocking, dangerous-bash detection, ordering, permissions, and more.
- **Natural language → formal contracts** — write rules in English; Sponsio compiles them. No formal-methods knowledge needed.
- **<1 ms latency** for deterministic rules — regex / AST / counter-based, zero LLM calls at runtime.
- **Works with every major agent framework** — LangGraph, OpenAI SDK, OpenAI Agents SDK, CrewAI, Vercel AI SDK, Claude Agent SDK, MCP. Python and TypeScript share the same engine.
- **Shadow mode** — flip a single flag to observe contracts on production traffic without blocking anything. Ship the rule, gather evidence, then turn on enforcement.

---

## Shadow mode — see what your agent is doing first

Run Sponsio in observe-only mode. Every contract is still evaluated; nothing is blocked. Every would-have-blocked decision is logged to `~/.sponsio/sessions/<agent_id>/*.jsonl`.

```python
guard = sponsio.init(
    framework="langgraph",
    agent_id="support_bot",
    mode="observe",                       # or set SPONSIO_MODE=observe
    contracts=[...],
)
```

```bash
# After running your agent for a while:
sponsio report --agent support_bot --since 24h
```

Staged rollout: deploy with `mode="observe"`, watch the report for a day or two, then flip `SPONSIO_MODE=enforce` — no code change required.

---

## Integrations

2-3 lines to integrate. Python and TypeScript share the same engine.

| Framework | Python | TypeScript |
|-----------|--------|------------|
| **No framework** | `guard.guard_before()` | `guard.guardBefore()` |
| **LangGraph / LangChain.js** | `guard.wrap(tools)` | `wrapTools(tools, guard)` |
| **Claude Agent SDK** | `guard.hooks()` | `sponsioHooks(guard)` |
| **OpenAI SDK** | `patch_openai()` | `wrapOpenAI(client, guard)` |
| **Vercel AI SDK** | `guard.wrap()` | `sponsioMiddleware(guard)` |
| **OpenAI Agents SDK** | `guard.wrap(tools)` | — |
| **CrewAI** | `guard.wrap(tools)` | — |
| **MCP** | `MCPContractProxy()` | — |

Runnable examples for every framework live under [`examples/integrations/`](examples/integrations/).

---

## Pattern Library

**29 deterministic patterns** (formal evaluation, zero LLM calls):

| Category | Patterns |
|----------|----------|
| **Safety** | `must_precede`, `must_confirm`, `requires_permission`, `no_data_leak`, `destructive_action_gate` |
| **Compliance** | `no_reversal`, `segregation_of_duty`, `always_followed_by`, `required_steps_completion` |
| **Operational** | `rate_limit`, `idempotent`, `cooldown`, `deadline`, `bounded_retry`, `loop_detection` |
| **Exclusion** | `mutual_exclusion`, `tool_allowlist` |
| **Argument / Path** | `arg_blacklist`, `scope_limit`, `arg_length_limit`, `data_intact`, `arg_value_range` |
| **Agentic Security** | `untrusted_source_gate`, `confirm_after_source`, `dangerous_bash_commands`, `dangerous_sql_verbs`, `irreversible_once` |
| **Resource** | `token_budget`, `delegation_depth_limit` |

**7 stochastic evaluators** (LLM-as-judge, for quality properties):
`pii`, `length`, `format`, `content_prohibition`, `tone`, `relevance`, `llm_judge`.

Run `sponsio patterns` to browse the library with NL examples. See the [Contract DSL](docs/contracts.md) for the full grammar.

---

## Benchmarks

Sponsio addresses three layers of agent safety — action, trace, and intent:

| Layer | Benchmark | Result |
|-------|-----------|--------|
| **Action-level safety** — is this tool call dangerous? | RedCode-Exec (1,410 cases) | **80 % bash · 52 % python** |
| **Trace-level compliance** — does the call sequence follow SOP? | tau2-bench (retail + airline) | **76–100 % recall** |
| **Intent-level integrity** — is the agent gaming metrics? | ODCV-Bench (12 LLMs × 80 scenarios) | **80 % protection · 2.7 % strict FP** |

Det pipeline is **<1 ms per check with zero LLM calls** — existing guardrail tools (LlamaFirewall, PromptArmor) rely on LLM-as-judge (~500 ms / check). See [full results](docs/benchmark-results.md).

---

## Architecture

```
NL rules / YAML / scan ──▶ Pattern Library ──▶ LTL Formula AST
                                                      │
                                        ┌─────────────┴──────────────┐
                                        ▼                            ▼
                                  Det Pipeline                 Sto Pipeline
                                  (before tool)                (after tool)
                                  binary pass / fail           scored 0–1
                                        │                            │
                                        ▼                            ▼
                                  Block / Escalate           Retry with feedback
```

Under the hood, Sponsio compiles natural-language rules into Linear Temporal Logic (LTL) formulas and evaluates them against a grounded event trace. That's what lets a contract express *"the refund was actually processed within 3 turns of the policy check"* or *"this tool was never called after that irreversible action"* — temporal properties that regex- or keyword-based guardrails cannot check.

- **Det** (deterministic) — formal LTL evaluation on a grounded trace. Binary verdicts, microsecond latency. Violations route to `DetBlock` or `EscalateToHuman`.
- **Sto** (stochastic) — LLM-scored evaluation (0–1) for fuzzy properties (tone, PII, format). Violations route to `RetryWithConstraint` or `RedirectToSafe`.
- **Zero core dependencies** — the formula engine and pattern library are pure Python. Framework packages (LangGraph, OpenAI, etc.) are optional extras.

Full technical design: [docs/architecture.md](docs/architecture.md).

---

## Contract Discovery

Auto-discover contracts from your code and policy docs:

```bash
sponsio scan src/agents/ -o sponsio.yaml                    # rule-based (AST)
sponsio scan src/agents/ --llm -o sponsio.yaml              # + LLM inference
sponsio scan src/ --policy security.md --llm -o out.yaml    # + policy docs
```

See the [CLI Reference](docs/cli.md) for all subcommands.

---

## Demo & dashboard

```bash
USE_MOCK=1 python examples/demo/demo_walkthrough.py    # 5-stage walkthrough, no API key needed
sponsio demo                                           # CLI demo runner
sponsio serve --dev                                    # API on :8000, dashboard on :5173
```

The dashboard shows live span trees, per-contract pass rates, violation feeds, and enforcement summaries. Point any OTEL-compatible trace collector at `POST /api/otel/v1/traces` to ingest traces from agents running outside the Sponsio runtime.

---

## Docs

- [Getting Started](docs/getting-started.md) — install, configure, integrate
- [Contract DSL](docs/contracts.md) — NL syntax, YAML schema, assume / enforce pairs
- [CLI Reference](docs/cli.md) — `scan`, `validate`, `check`, `serve`, `demo`, `patterns`, `report`
- [Integrations](docs/integrations.md) — per-framework guide
- [Architecture](docs/architecture.md) — formula engine, grounding, dual pipeline
- [Benchmark Results](docs/benchmark-results.md) — ODCV-Bench, RedCode, tau2-bench, AgentDojo

---

## Contributing

Patches, issue reports, and new pattern proposals are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md) — it covers the dev loop, invariants, and how to add a new pattern or integration.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
