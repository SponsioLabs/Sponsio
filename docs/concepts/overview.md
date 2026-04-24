---
title: Concepts overview
description: The concept stack, the trace, the atom vocabulary, and how to pick between deterministic and stochastic contracts.
---

# Concepts overview

The [README](../../README.md) explains what Sponsio does. This page explains how to think about it when you sit down to write contracts.

Four ideas carry most of the system. Once they are straight, the pattern library, the atom catalog, the integrations, and the CLI all read as small variations on the same theme.

1. **The concept stack** — atom → pattern → formula → contract.
2. **The trace** — what contracts are actually evaluated against.
3. **The atom vocabulary** — where Sponsio's observation boundary lies.
4. **Two pipelines** — how to choose between deterministic and stochastic.

For the full design rationale and LTL semantics, see [Architecture](architecture.md). This page is the bridge between the README's three-line architecture summary and that document.

---

## 1. The concept stack

Four layers build on each other. Getting their boundaries right prevents most design confusion.

```
                  ┌─────────────────────────────────────────────┐
                  │  Contract                                   │
                  │  = {assumption, guarantee} bound to agent   │
                  │  = the unit of enforcement                  │
                  ├─────────────────────────────────────────────┤
                  │  Formula                                    │
                  │  = Atoms + LTL + boolean connectives        │
                  │  = what the evaluator actually checks       │
                  ├─────────────────────────────────────────────┤
                  │  Pattern                                    │
                  │  = named factory that emits a Formula       │
                  │  = convenience, not new expressiveness      │
                  ├─────────────────────────────────────────────┤
                  │  Atom                                       │
                  │  = one observable fact about one event      │
                  │  = the vocabulary boundary                  │
                  └─────────────────────────────────────────────┘
```

**Atom** — a binary or integer fact extracted from a single event: `called(X)`, `count(X)`, `arg_has(X, pattern)`, `perm(P)`. This is the observation boundary. If a fact cannot be expressed as an atom, Sponsio cannot observe it.

**Pattern** — a named factory that emits a formula from a short set of arguments: `must_precede("check_policy", "issue_refund")` returns the LTL formula `G(called("issue_refund") → ◆⁻ called("check_policy"))`. Patterns are *sugar*: they do not expand the expressiveness of the language, only the ergonomics.

**Formula** — an LTL expression over atoms. This is what the evaluator actually checks. Anything expressible in LTL over the available atom vocabulary can be enforced.

**Contract** — an (assumption, guarantee) pair bound to one or more agents, with a strategy for what to do on violation (block, escalate, retry, redirect). The assumption tells the engine *when* the rule applies; the guarantee tells it *what must hold* when it does.

```python
contract("policy gate before refund")
    .assume("called `issue_refund`")
    .enforce("must call `check_policy` before `issue_refund`")
```

---

## 2. The trace

A **trace** is the append-only record of what the agent has done in a session — each tool call, LLM response, and state change, with its arguments and result. Every contract is evaluated against the current trace plus the candidate next event.

Three practical consequences:

- **Ordering is checkable** because the trace remembers history. Output-only checkers cannot express *"A before B"* — the string "A" is not in the current output.
- **Enforcement is session-scoped.** Each agent session has its own trace. Contracts do not leak across sessions unless you wire them to.
- **Blocked events are rolled back.** In enforce mode, a hard-blocked event is removed from the trace so it does not poison later checks. In observe (shadow) mode, nothing is blocked — violations are only recorded for reporting. See [Observe vs. enforce](../guides/observe-vs-enforce.md).

The grounding layer sits between raw events and the evaluator. Its only job is to turn each event into a dictionary of atom valuations. The evaluator never sees raw events.

---

## 3. The atom vocabulary

Atoms define the vocabulary in which contracts can be written. Adding a new atom — `http_method(X)`, `sql_verb(X)`, `path_depth()` — expands what Sponsio can reason about. Without a corresponding atom, even a natural-language rule that "reads" obvious cannot be enforced.

Current atom categories:

| Category | Example atoms | What you can express |
|---|---|---|
| Identity | `called(X)`, `agent_is(A)` | Which tool or agent ran this event |
| Counting | `count(X)`, `count_in_window(X, N)` | Rate limits, loops, bounded retries |
| Arguments | `arg_has(X, pattern)`, `arg_length(X) > N` | Blacklists, scope limits, length caps |
| Permissions | `perm(P)` | Static role checks |
| Data flow | `data_from(S)`, `data_to(D)` | No-leak rules across tool boundaries |
| Time | `elapsed_since(X) > T`, `deadline_passed()` | Cooldowns, deadlines |

The full list, with the formal signature of each atom and the patterns that use it, lives in [Architecture § Atoms](architecture.md). When in doubt, start with a pattern from the [pattern catalog](../reference/patterns.md) — the vast majority of real rules map to one.

---

## 4. Choosing between the two pipelines

The README shows the two pipelines side by side. This is where to think about *which one to use* when you are writing a rule.

**Use a deterministic contract when the property is structurally observable.**

- Tool ordering, rate limits, retries, loop detection
- Destructive action gates, irreversible-once rules
- Path or argument blacklists, scope limits, length limits
- Exact-regex PII, format checks
- Permissions, tool allowlists, segregation of duty

If you can state the rule with a counter, a regex, a path match, or an ordering constraint, it belongs in det. The hot path stays microseconds.

**Use a stochastic contract when the property needs semantic judgment.**

- Tone, relevance, scope respect
- Semantic PII (things a regex cannot catch)
- Hallucination, faithfulness, metric integrity
- Any rule whose correctness would require a human to read the text

Stochastic atoms cost one LLM call per check. Pick them for failure modes your regex vocabulary cannot reach, not by default. See [Stochastic atoms](../reference/sto-atoms.md).

**The rule of thumb.** Do not reach for a judge call to check things a regex already checks. Do not force a regex to check things that only make sense semantically.

> One contract, one pipeline. A single contract is evaluated by exactly one pipeline — you do not mix det and sto atoms inside one rule. If you need both, declare two contracts.

---

## How it fits together

```
 ┌─────────────────────────────────────────────────────────────┐
 │  Your agent loop                                            │
 │                                                             │
 │   LLM ──▶ pick tool ──▶ ┌─────────────────┐ ──▶ tool runs   │
 │                         │  Sponsio check  │                 │
 │   result ◀───────────── │                 │ ◀── or blocked  │
 │                         └─────────────────┘                 │
 │                                │                            │
 │                                ▼                            │
 │                         ┌─────────────┐                     │
 │                         │   trace     │  append-only        │
 │                         │  + atoms    │  grounding layer    │
 │                         └─────────────┘                     │
 │                                │                            │
 │                    ┌───────────┴───────────┐                │
 │                    ▼                       ▼                │
 │            ┌───────────────┐       ┌───────────────┐        │
 │            │  det formula  │       │  sto judge    │        │
 │            │   evaluator   │       │  (LLM)        │        │
 │            └───────┬───────┘       └───────┬───────┘        │
 │                    │                       │                │
 │                    └───────────┬───────────┘                │
 │                                ▼                            │
 │                      block / retry / redirect               │
 └─────────────────────────────────────────────────────────────┘
```

Deterministic formulas are evaluated in microseconds. Stochastic atoms are judged only when a contract that uses them triggers. A violation routes through a **strategy** — block, retry with a constraint, redirect to a safe path, or escalate to a human.

---

## Next

- [Architecture](architecture.md) — LTL semantics, grounding internals, why the atom vocabulary is the observation boundary.
- [Deterministic contracts](contracts.md) — the pattern library and how each pattern compiles to LTL.
- [Stochastic contracts](stochastic.md) — the judge pipeline, scoring, and retry strategies.
- [Write your first contract](../getting-started/first-contract.md) — hands-on walkthrough.
- [Integrations](../integrations/index.md) — wire it into your framework (LangGraph, Claude Agent SDK, OpenAI, CrewAI, Google ADK, Vercel AI, MCP, or custom).
