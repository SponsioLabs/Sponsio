---
title: Assume / enforce semantics
description: Why Sponsio only enforces guarantees, never assumptions, and what that means for contract authors.
---

# Assume / enforce semantics

Sponsio contracts are written as **`(assumption, guarantee)` pairs** following the Hoare-logic / design-by-contract tradition. This page is about the asymmetric semantics of the two halves and why it matters for runtime enforcement.

## The principle

```
evaluate   = compute the truth value of a formula
enforce    = take an action (block, hint, escalate, ...) when a formula is False
```

Sponsio applies these two operations asymmetrically:

| Position | Evaluated? | Enforced? |
|---|---|---|
| `assumption` (A) | ✓ | ✗ |
| `guarantee` (E) | ✓ | ✓ |

**Assumptions are evaluated only.** Their truth value determines whether the contract *applies*. They are not obligations that the agent must satisfy; they describe the conditions under which the rest of the contract has force.

**Guarantees are evaluated and enforced.** Their truth value drives every enforcement action: violations block (det) or escalate (sto), workflow_step triggers prescriptive hints, unmet F obligations surface as before-close reminders.

## Why this matters

When you write a contract like

```python
contract("policy gate before refund")
    .assume("called `issue_refund`")
    .enforce("must call `check_policy` before `issue_refund`")
```

the assumption `"called issue_refund"` is the **trigger condition**. It is not something the agent is required to do. If the agent never calls `issue_refund`, the contract is vacuously satisfied: no blocking, no hint, no escalation. The whole rule only fires when the trigger actually fires.

The guarantee `"must call check_policy before issue_refund"` is the **obligation**. When the trigger fires, this clause must hold; if it does not, the runtime takes action.

This split is what lets you author contracts like normal English policies: "*if* the agent does X, *then* it must do Y." The "if" part is description, the "then" part is enforcement.

## Where the asymmetry shows up in the runtime

Every enforcement path in Sponsio walks `contract.enforcements` only, never `contract.assumptions`:

| Mechanism | Code path | Walks |
|---|---|---|
| Block on guarantee failure | `RuntimeMonitor._check_det` → `_handle_enforcement_failure` | `contract.enforcements` |
| X-style prescriptive hint | `RuntimeMonitor._emit_prescriptive_hints` | `contract.enforcements` |
| F-style imminent-close hint | `RuntimeMonitor.emit_pending_obligation_hints` (via `guard.before_close`) | `contract.enforcements` |
| End-of-session liveness check | `BaseGuard.finish_session` | `contract.enforcements` |

The assumption path is read-only: the verifier evaluates it to decide *whether* to evaluate the guarantee, and emits a pass / fail diagnostic to the span tree, but never invokes a strategy.

This means an unmet assumption produces no `EnforcementResult` of any action (`blocked`, `warned`, `escalated`, …) under reactive semantics. The agent's next tool call proceeds; the contract simply does not apply.

## Two assumption modes

Sponsio's `Contract` accepts an `activate_at` parameter that selects between two interpretations of "unmet assumption":

### Reactive semantics (`activate_at="first_match"`)

> The assumption is a **trigger**. The contract activates at the first position the assumption holds; before that point, the contract is *vacuously satisfied* and the guarantee is not checked.

This is the Hoare-style reading. An unmet assumption simply means the contract has not been triggered yet, and the agent is free to do anything that is not constrained by a different contract.

Example:

```python
Contract(
    agent=agent,
    assumption=F(Atom("called", "issue_refund")),
    enforcement=must_precede("check_policy", "issue_refund"),
    activate_at="first_match",
)
```

If `issue_refund` is never called, the assumption never activates and the contract reports `holds=False` on the assumption side and `enforcements=[]`. No action. The agent's other tool calls flow through unobstructed.

### Global semantics (`activate_at=None`, the default)

> The assumption is an **invariant**. It is expected to hold throughout the trace; a failure is treated as an upstream problem and escalated.

This is the older interpretation, retained for backward compatibility with contracts that use the assumption side as an assertion ("the user is authenticated", "the caller_id is from the prod SPIFFE domain"). When that assertion fails, the runtime escalates via the configured `_handle_assumption_failure` strategy (default: `EscalateToHuman`).

If your contract reads as "*if* X then Y", prefer `activate_at="first_match"` so an unmet trigger behaves vacuously. Reach for the global default only when the assumption side genuinely encodes a precondition that you want to be *asserted*, not merely waited on.

## Reactive vs global at a glance

| Question | Reactive (`first_match`) | Global (`None`) |
|---|---|---|
| What does an unmet assumption mean? | Contract not triggered yet | Upstream invariant failed |
| Runtime action on unmet assumption | None (vacuous pass event) | `EscalateToHuman` |
| Best for | "If agent does X, must do Y" rules | "X must always hold" assertions |
| When evaluation starts for the enforcement | At the activation point `k = max(k_i)` | At trace start (`pos=0`) |
| Typical authoring shape | `assume = F(trigger)` or atomic | `assume = G(invariant)` or atomic |

## What this means for X and F operators in either position

The X (next) and F (eventually) operators are *formulas*, not enforcement actions; they can appear on either side of a contract. The asymmetry rule still applies:

| Operator placement | Sponsio behaviour |
|---|---|
| X in assumption | Evaluated. Determines whether the next event matches the trigger; no hint, no block. |
| F in assumption | Evaluated. Determines whether the trigger eventually fires; no hint, no block. |
| X in guarantee | Evaluated **and** enforced. When the antecedent fires, the engine emits a hint ("next step: B"). When the next event fails to satisfy B, the call is `blocked` (`workflow_step` pattern). |
| F in guarantee | Evaluated **and** enforced. While the obligation is pending, `guard.before_close()` emits a hint ("before closing, please complete: B"). If B never fires before session close, `guard.finish_session()` raises an `escalated` liveness violation (`always_followed_by` pattern). |

The dual-message symmetry between X and F (hint at obligation incurrence, violation at obligation deadline) applies only to the guarantee side. On the assumption side, both operators are pure observers.

## Practical consequence: choosing the right side for a constraint

A rule of thumb for new contract authors:

1. **Express the trigger on the assume side**, never as part of the enforce side. If the trigger never happens, the rest of your contract should fall away silently.
2. **Express the obligation on the enforce side**. Any clause that you want the runtime to *act on* must live here.
3. **Pair `activate_at="first_match"` with non-trivial assumptions** so reactive vacuity is honoured.

A contract written as

```python
# Anti-pattern: trigger merged into enforce
contract("refund verification")
    .enforce("must call verify before issue_refund")
```

without an assumption will fire even when no refund has been requested, because there is nothing to *not trigger*. The verifier may resolve it vacuously at the LTL (linear temporal logic) level (the formula `G(¬A) ∨ ...` is trivially satisfied when A never holds), but the intent is unclear and the audit trail less informative.

Prefer:

```python
# Clear: trigger in assume, rule in enforce
contract("refund verification")
    .assume("called `issue_refund`")
    .enforce("must call `verify` before `issue_refund`")
```

with `activate_at="first_match"` if you want a clean reactive contract that stays silent until the trigger event.

## How this is enforced in the codebase

Every place in Sponsio that constructs an `EnforcementResult` from a contract walks the `enforcements` list, never the `assumptions` list. The only place that handles assumption failures is `RuntimeMonitor._handle_assumption_failure`, and that path:

- is only reached in global semantics (because reactive contracts short-circuit when the trigger does not activate),
- treats the failure as an *upstream* problem (`EscalateToHuman`, not an agent block),
- and is documented as such in the docstring (`"The upstream agent flow may have a problem."`).

The runtime never blocks an agent action for failing to satisfy an assumption; it only blocks for failing to satisfy a guarantee. This is the asymmetry that lets the same formula language describe both the conditions under which a contract applies and the actions it requires.

---

**Related:** [Contracts](contracts.md) · [Architecture](architecture.md) · [Formal methods](formal-methods.md) · [Pattern catalog](../reference/patterns.md)
