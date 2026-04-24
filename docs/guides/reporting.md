---
title: Reporting
description: Aggregate violation reports from shadow-mode runs.
---

# Reporting

`sponsio report` aggregates violations from session logs into a per-contract, per-agent, per-tool summary. Most useful during [observe mode](observe-vs-enforce.md) — it tells you which contracts are firing and whether the firings are real.

```bash
sponsio report --since 7d
sponsio report --since 7d --format json > report.json
sponsio report --agent support_bot
```

---

## Output shape

```
Contract                               Fires   Sessions   Agents   Tools
─────────────────────────────────────  ──────  ─────────  ───────  ──────────────
policy gate before refund              3       3          1        issue_refund
bash must not contain rm -rf           1       1          1        bash
token_budget(50000)                    0       —          —        —
```

Columns:

- **Fires** — total violation count in the window.
- **Sessions** — distinct sessions where it fired. A contract firing once in three sessions is different from firing three times in one session.
- **Agents** — distinct agents that tripped it.
- **Tools** — the tool calls that triggered the firing.

---

## Flags

| Flag | Default | Effect |
|---|---|---|
| `--since` | `7d` | Time window. Accepts `Nd`, `Nh`, `Nm`, or an ISO timestamp. |
| `--agent` | all | Filter to one agent. |
| `--contract` | all | Filter to one contract by name. |
| `--format` | `table` | `table`, `json`, or `markdown`. |
| `--sessions-dir` | `~/.sponsio/sessions/` | Where to read session logs. |

See [CLI reference](../reference/cli.md#sponsio-report) for the full flag list.

---

## What to look for

| Pattern | Likely meaning |
|---|---|
| A contract fires in most sessions | Too strict. Relax the assumption or the threshold. |
| A contract never fires | Maybe not needed, or not reachable from the current trace. |
| A contract fires once, on a real incident | Working. Promote to enforce. |
| Violations clustered on one tool | Narrow the rule to that tool instead of tool-wide. |
| Violations clustered on one agent | Investigate that agent's prompt or tool set. |

---

## Next

- [Observe vs. enforce](observe-vs-enforce.md) — where reports fit in the rollout.
- [Observability](observability.md) — wiring the session logs reports read from.
