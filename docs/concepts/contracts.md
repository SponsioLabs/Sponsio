---
title: Deterministic contracts
description: How deterministic contracts are structured, how they compile, and when to reach for one.
---

# Deterministic contracts

Deterministic contracts are binary pass/fail rules evaluated before each tool call. If a contract is violated, Sponsio blocks the call before any side effect happens. This is the hot path — zero LLM calls, microsecond latency.

For the conceptual model (atom → pattern → formula → contract), see [Concepts overview](overview.md). For the full catalog of shipped patterns, see [Pattern catalog](../reference/patterns.md). This page is about how det contracts are structured and when to reach for one.

---

## Shape of a det contract

A det contract has four parts:

```python
contract("policy gate before refund")             # name (for logs, reporting)
    .assume("called `issue_refund`")              # when the rule applies
    .enforce("must call `check_policy` before `issue_refund`")  # what must hold
    .strategy("block")                            # what to do on violation
```

- **Name** — a human-readable label; shows up in logs, reports, and error messages.
- **Assumption (A)** — the condition that triggers the rule. The rule only fires when A holds.
- **Guarantee (G)** — the temporal property that must hold when A is true.
- **Strategy** — what happens on violation: `block`, `escalate`, or a custom callable.

Both A and G are natural-language strings. They compile down to LTL formulas over atoms. You never need to write the LTL by hand, but the engine ultimately checks the LTL.

---

## How it compiles

```
NL rule
  ─▶ NL parser (regex + pattern matching)
      ─▶ Pattern function (must_precede, rate_limit, …)
          ─▶ LTL formula over atoms
              ─▶ Evaluator (pure Python)
                  ─▶ True (pass) / False (block)
```

Three examples:

```python
# "tool `A` must precede `B`"
# → must_precede("A", "B")
# → Not(called("B")) Until called("A")

# "tool `X` at most 3 times"
# → rate_limit("X", 3)
# → G(count("X") <= 3)

# "bash must not contain `rm -rf`"
# → arg_blacklist("bash", "command", ["rm -rf"])
# → G(called("bash") → Not(arg_field_has("bash", "command", "rm -rf")))
```

---

## When to reach for a det contract

Use a det contract when the property is **structurally observable** — expressible with counters, regexes, paths, or ordering. Structural properties do not need semantic judgment, so they do not need an LLM.

Typical det use cases:

- **Ordering** — A must precede B; after X, Y is forbidden; every A must be followed by B.
- **Rate and retry limits** — at most N calls, cooldown between calls, bounded retries, loop detection.
- **Irreversibility gates** — once a commit or approval happens, downstream mutations are forbidden.
- **Argument checks** — blacklisted patterns, path scope limits, length or range caps.
- **Permissions** — static role-based access to certain tools.
- **Exact-regex PII** — SSN, credit card, email patterns that a regex can reliably catch.

Anti-pattern: do not reach for a det contract for properties that need reading the text semantically (tone, relevance, whether something is *truly* PII). Those belong in a [stochastic contract](stochastic.md).

---

## Failure strategies

When a det contract is violated, the call is not passed through. Built-in strategies:

| Strategy | Behavior |
|---|---|
| `block` | Deny the call and raise a `SponsioBlocked` exception to the framework. Agent can react and retry with a different plan. |
| `escalate` | Deny the call and route to a human-in-the-loop callback. Useful for high-stakes actions where silent blocking would confuse the agent. |
| `(callable)` | Custom callback. Gets the violated contract and the candidate event; returns a new strategy decision. |

In **observe mode**, no strategy runs — violations are logged and surfaced in reports, but the call is not blocked. This is how most teams wire Sponsio in first. See [Observe vs. enforce](../guides/observe-vs-enforce.md).

---

## Next

- [Pattern catalog](../reference/patterns.md) — every det pattern that ships, with NL form.
- [Stochastic contracts](stochastic.md) — when structural checks are not enough.
- [Architecture](architecture.md) — LTL semantics, grounding internals, atom vocabulary.
- [Write your first contract](../getting-started/first-contract.md) — hands-on walkthrough.
