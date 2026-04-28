# Contract `activate_at` — when assumption activates, where enforcement starts

The `activate_at` field on `Contract` controls *where on the trace*
enforcement begins when an assumption fires.  Two values:

| `activate_at` | Mode    | Enforcement check window |
|---------------|---------|--------------------------|
| `None` (default) | global   | full trace, position 0 onward (existing behaviour) |
| `"first_match"`  | reactive | from the first position where A's evidence holds, suffix onward |

This page explains the semantic difference and when to pick each.

## Why this exists

Contract authors writing trigger-then-enforce safety rules naturally
write:

```yaml
- desc: "after secret read, no outbound POST"
  A:
    ltl: 'F(secret_read)'
  E:
    ltl: 'G(!outbound_post)'
```

and expect: "if the agent reads a secret, then from that point on it
must not POST.  Pre-secret POSTs are not retroactively flagged."

The default Sponsio semantic doesn't do that.  It evaluates A and E
independently against the full trace from position 0:

* `F(secret_read)` becomes true at position 0 the moment a secret read
  appears anywhere later.
* Then `G(!outbound_post)` is checked against the full trace from
  position 0 — and any POST that happened *before* the secret read is
  flagged as a violation.

For invariants ("if user is admin throughout, log every read"), the
default semantic is correct.  For trigger-then-enforce, it's wrong —
it retroactively flags actions taken before the trigger.

`activate_at: "first_match"` switches to the trigger-then-enforce
semantic explicitly.

## Worked example

Same trace `[Q, P, Q]`, two contracts:

```yaml
# A: docker-compose-config dump
# E: no outbound POST
A: F(P)
E: G(!Q)
```

| Contract                              | pos 0 (Q) | pos 1 (P) | pos 2 (Q) |
|---------------------------------------|-----------|-----------|-----------|
| `activate_at: None` (default)         | allow     | **deny ❌** (retroactively flags pos 0) | allow* |
| `activate_at: first_match` (reactive) | allow     | allow     | **deny ✓** (post-activation) |

(* The default semantic's allow at pos 2 happens because the deny at
pos 1 caused the monitor to roll back, so by pos 2 the trace is just
`[Q]` and F(P) is false again.  Real-world this masks the persistent
issue.)

The reactive semantic is what the user usually means.

## Validation

`activate_at: "first_match"` is well-defined for two assumption shapes:

* **`F(φ)`** — activation = first position where φ holds.
* **Atomic** (`called(X)`, `arg_field_has(...)`, etc.) — activation =
  first position where the atom holds.

Other shapes are rejected at `Contract` construction with a clear
error rather than silently treated as "evaluate at pos 0":

* `G(φ)` — only true at end-of-trace if φ held everywhere; no single
  activation point.
* Arithmetic (`count(X) <= K`) — not evidence-shaped.
* Compound (`P U Q`, `And(...)`) — wrap each conjunct in `F` if you
  want each-arm-fires semantic.

For multi-assumption contracts (`assumption: [A1, A2, ...]`), each
assumption's first-match position is computed; the contract activates
at `max(k1, k2, ...)` since assumptions AND together — all must hold.
If any assumption never activates within the current trace, the
contract is **vacuously satisfied** — `enforcement_violations` is
empty, runtime won't block.

## YAML usage

```yaml
agents:
  my_agent:
    contracts:
      - desc: "after vault-resolution, no exfil-shape POST"
        activate_at: first_match     # ← opt into reactive semantic
        A:
          ltl: 'F(arg_field_has(Bash, command, "(op run|aws-vault) "))'
        E:
          ltl: 'G(!arg_field_has(Bash, command, "(curl|wget).*-d.*@-"))'
```

`activate_at:` can sit alongside any other contract-entry fields
(`alpha`, `beta`, `risk_profile`, `costs`, `desc`, etc.).  It's
independent of stochastic enforcement — works equally for det and
sto contracts.

## Implementation notes

* The verifier dispatches at the top of `check_contract`:
  `activate_at == "first_match"` → `_check_contract_reactive`,
  otherwise `_check_contract_global` (the historical path, unchanged).
* The reactive path bypasses the G-cache (which is keyed at
  position 0) and calls `eval_formula(E, valuations, pos=k)` directly.
  Per-event cost is O(suffix_length) — exactly what the suffix
  semantic implies.  For long traces with many activations, the
  cost stays bounded because activation only happens once per session
  (we don't re-find activation as the trace grows; the position is
  monotonic).
* Default behaviour is byte-for-byte unchanged for any contract that
  doesn't set `activate_at`.  All 2,200+ existing tests pass without
  modification.

## When to choose which

| You're writing… | Use |
|-----------------|-----|
| "If admin throughout, every read logged" | default (global) |
| "Token budget never exceeded across the whole session" | default |
| "After fraud check, refunds always require confirmation" | `first_match` |
| "Once secret-shaped data was read, no outbound POST" | `first_match` |
| "After listing public repo issues, GitHub MCP confined to that repo" | `first_match` |
| "Agent never authenticates twice" (idempotence) | default |

Rule of thumb: if the assumption describes a **moment in time** at
which something switches state ("secret was read", "vault command
ran", "user sent the trigger phrase"), use `first_match`.  If it
describes a **whole-session invariant** ("user is admin", "tokens
under budget"), use the default.

## Test coverage

`tests/test_contract_activate_at.py` pins:

* §1 — semantic difference on `[Q, P]` and `[Q, P, Q]`
* §2 — validation: invalid value, no-assumption case, rejected shapes
  (`G(φ)`)
* §3 — vacuous satisfaction when assumption never activates
* §4 — multi-assumption: activation = max(per-assumption first-match)
* §5 — YAML round-trip and end-to-end through `BaseGuard`
