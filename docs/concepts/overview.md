---
title: Concepts overview
description: The concept stack, the trace, and the atom vocabulary that define Sponsio's deterministic contracts.
---

# Concepts overview

The [README](../../README.md) explains what Sponsio does. This page explains how to think about it when you sit down to write contracts.

Three ideas carry most of the system. Once they are straight, the pattern library, the atom catalog, the integrations, and the CLI all read as small variations on the same theme.

1. **The concept stack**: atom → pattern → formula → contract.
2. **The trace**: what contracts are actually evaluated against.
3. **The atom vocabulary**: where Sponsio's observation boundary lies.

For the full design rationale and LTL semantics, see [Architecture](architecture.md). This page bridges the README's three-line architecture summary and that document.

---

## 1. The concept stack

Four layers build on each other.

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

**Atom**: a binary or integer fact extracted from a single event: `called(X)`, `count(X)`, `arg_has(X, pattern)`, `perm(P)`. This is the observation boundary. If a fact cannot be expressed as an atom, Sponsio cannot observe it.

**Pattern**: a named factory that emits a formula from a short set of arguments. `must_precede("check_policy", "issue_refund")` returns the LTL formula `G(called("issue_refund") → ◆⁻ called("check_policy"))`. Patterns are *sugar*: they do not expand the expressiveness of the language, only the ergonomics.

**Formula**: an LTL expression over atoms. This is what the evaluator actually checks. Anything expressible in LTL over the available atom vocabulary can be enforced.

**Contract**: an (assumption, guarantee) pair bound to one or more agents, with a strategy for what to do on violation (`DetBlock`, `EscalateToHuman` with optional notifier callbacks, `RedirectToSafe` to substitute a pre-approved tool, or `WarnOnly` to log without blocking). The assumption tells the engine *when* the rule applies; the guarantee tells it *what must hold* when it does.

```python
contract("policy gate before refund")
    .assume("called `issue_refund`")
    .guarantees("must call `check_policy` before `issue_refund`")
```

---

## 2. The trace

A **trace** is the append-only record of what the agent has done in a session: each tool call, LLM response, and state change with its arguments and result. Every contract is evaluated against the current trace plus the candidate next event.

- **Ordering is checkable** because the trace remembers history. Output-only checkers cannot express "A before B" since "A" is not in the current output.
- **Enforcement is session-scoped.** Each agent session has its own trace. Contracts do not leak across sessions unless wired to.
- **Blocked events are rolled back.** In enforce mode, a hard-blocked event is removed from the trace so it does not poison later checks. In observe (shadow) mode, nothing is blocked. Violations are only recorded for reporting. See [Observe vs. enforce](../guides/observe-vs-enforce.md).

The grounding layer sits between raw events and the evaluator. Its only job is to turn each event into a dictionary of atom valuations. The evaluator never sees raw events.

---

## 3. The atom vocabulary

Atoms define the vocabulary in which contracts can be written. Adding a new atom (`http_method(X)`, `sql_verb(X)`, `path_depth()`) expands what Sponsio can reason about. Without a corresponding atom, even a natural-language rule that "reads" obvious cannot be enforced.

| Category | Example atoms | What you can express |
|---|---|---|
| Identity | `called(X)`, `agent_is(A)` | Which tool or agent ran this event |
| Counting | `count(X)`, `count_in_window(X, N)` | Rate limits, loops, bounded retries |
| Arguments | `arg_has(X, pattern)`, `arg_length(X) > N` | Blacklists, scope limits, length caps |
| Permissions | `perm(P)` | Static role checks |
| Data flow | `data_from(S)`, `data_to(D)` | No-leak rules across tool boundaries |
| Time | `elapsed_since(X) > T`, `deadline_passed()` | Cooldowns, deadlines |

The full list, with the formal signature of each atom and the patterns that use it, lives in [Architecture § Atoms](architecture.md). When in doubt, start with a pattern from the [pattern catalog](../reference/patterns.md). Most real rules map to one.

---

## 4. When a deterministic contract fits

Sponsio's deterministic contracts apply when a property is **structurally observable**: expressible with a counter, a regex, a path check, or an ordering relation.

| | Deterministic contract |
|---|---|
| **Ships in** | `pip install sponsio` |
| **Use when** | Property is structurally observable (counter, regex, path, ordering) |
| **Examples** | Tool ordering, rate limits, retries, loop detection, destructive gates, irreversible-once, path / argument blacklists, scope and length limits, exact-regex PII, format checks, permissions, allowlists, segregation of duty |
| **Cost** | Microseconds, zero LLM calls |
| **Pipeline** | LTL evaluator (formal) |

The rule of thumb: keep contracts to things a counter, regex, path, or ordering relation can check. Properties that only make sense semantically (tone, relevance, whether text is *truly* off-topic) are outside the deterministic engine's scope.

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
 │                                ▼                            │
 │                        ┌───────────────┐                    │
 │                        │  det formula  │                    │
 │                        │   evaluator   │                    │
 │                        └───────┬───────┘                    │
 │                                │                            │
 │                                ▼                            │
 │                      block / escalate                       │
 └─────────────────────────────────────────────────────────────┘
```

Deterministic formulas are evaluated in microseconds. A violation routes through a **strategy**: block the call (`DetBlock`), escalate to a human with notifier callbacks (`EscalateToHuman`), substitute a pre-approved safe tool (`RedirectToSafe`), or log without blocking (`WarnOnly`).

---

## Next

- [Architecture](architecture.md): LTL semantics, grounding internals, why the atom vocabulary is the observation boundary.
- [Deterministic contracts](contracts.md): the pattern library and how each pattern compiles to LTL.
- [Write your first contract](../getting-started/first-contract.md): hands-on walkthrough.
- [Integrations](../integrations/index.md): wire it into your framework (LangGraph, Claude Agent SDK, OpenAI, CrewAI, Google ADK, Vercel AI, MCP, or custom).
