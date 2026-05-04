---
title: Observe vs. enforce
description: How to ship Sponsio safely. Start in observe mode, review reports, then flip to enforce.
---

# Observe vs. enforce

Sponsio runs in one of two modes:

- **Observe (shadow)**: contracts are evaluated against every event; violations are recorded; nothing is blocked.
- **Enforce**: violations trigger the contract's strategy (block, escalate, retry, redirect). Side effects are prevented.

The safe rollout is always observe first, then enforce. Observe mode tells you which contracts are too strict, which are too loose, and whether your contract set covers the agent's real behavior before you start blocking calls in production.

---

## Setting the mode

YAML:

```yaml
# sponsio.yaml
mode: observe        # or: enforce
agents:
  bot:
    contracts: [...]
```

Python:

```python
guard = Sponsio(config="sponsio.yaml", agent_id="bot", mode="observe")
```

Per-contract override:

```yaml
contracts:
  - name: "always-on block"
    G: "tool `drop_table` at most 0 times"
    mode: enforce    # enforced even when global mode is observe
```

Useful for a mixed rollout: enforce the handful of hard-block rules you are already sure of, observe the rest.

---

## The staged rollout

```
day 0        day 1–3         day 3–7           day 7+
onboard  ──▶ observe  ──▶  observe + report  ──▶ enforce
```

### Day 0, `onboard`

`sponsio onboard .` writes `sponsio.yaml` in observe mode with a starter-pack of contracts. See [Onboarding](onboarding.md).

### Day 1–3. Run in observe

Deploy with `mode: observe`. The agent behaves exactly as before. Sponsio is not in the hot path of blocking. Every call is checked and violations are appended to the session log.

### Day 3–7. Review and tune

```bash
sponsio report --since 7d
```

Produces an aggregate of violations by contract, by agent, by tool. You are looking for:

| Signal | What it means | What to do |
|---|---|---|
| A contract fires on every session | Too strict, false-positive-heavy | Relax the assumption or guarantee |
| A contract never fires | Maybe not needed, or rule is wrong | Either remove or test with a known-bad trajectory |
| A contract fires once, on a real incident | Working as intended | Promote to enforce |
| A contract fires on a tool you forgot existed | Agent is doing something you didn't expect | Investigate *before* tightening |

### Day 7+. Flip to enforce

Once the violation rate is low and every firing corresponds to something you actually want blocked, flip the global mode:

```yaml
mode: enforce
```

You can also promote per-contract with the `mode: enforce` override. Useful for mixed confidence levels.

---

## What happens on violation

| Mode | Det violation | Sto violation |
|---|---|---|
| Observe | Logged; call passes through | Logged; response passes through |
| Enforce | Strategy runs (`block`, `escalate`, or custom) | Strategy runs (`retry_with_constraint`, `redirect_to_safe`, or custom) |

In enforce mode, a hard-blocked event is **rolled back** from the trace so later checks are not poisoned by it.

---

## Gotchas

- **Observe mode is not free**. Sto contracts still make judge calls in observe. They need the score to log the would-be violation. If judge cost is a concern during shadow, consider a `mode: observe_det_only` override on sto contracts. (Feature-flagged; ask if you need it.)
- **Observe reports are only as good as your session log.** Make sure OTEL or local-disk session logging is configured. See [Observability](../reference/observability.md).
- **Enforce mode changes agent behavior.** Once you flip, the agent will see `SponsioBlocked` exceptions and retry loops it never saw in observe. Plan for a day of re-tuning after the flip.

---

## Next

- [Reporting](reporting.md), `sponsio report` flags and output formats.
- [Observability](../reference/observability.md): wiring OTEL and session logs.
- [CLI reference](../reference/cli.md).
