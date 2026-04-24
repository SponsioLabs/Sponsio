---
title: Observability
description: Wire Sponsio to OTEL and local session logs.
---

# Observability

Sponsio emits structured events for every check it runs. Two sinks are supported out of the box.

---

## Local session logs (default)

Every session writes a JSONL file to `~/.sponsio/sessions/<agent_id>/<timestamp>.jsonl`. One event per line. No configuration needed.

```bash
ls ~/.sponsio/sessions/support_bot/
# 2026-04-24T10-12-33Z.jsonl
# 2026-04-24T10-15-07Z.jsonl
```

`sponsio report` reads these files. `sponsio scan -t '~/.sponsio/sessions/bot/*.jsonl'` mines them for contract candidates.

Disable with `SPONSIO_SESSION_LOG=0` or `sessions_dir: null` in `sponsio.yaml`.

---

## OpenTelemetry

Install the extra:

```bash
pip install "sponsio[otel]"
```

Configure an OTLP endpoint — Sponsio respects standard OTEL env vars:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://your-collector:4318
export OTEL_SERVICE_NAME=sponsio
```

Every check produces a span with these attributes:

| Attribute | Value |
|---|---|
| `sponsio.contract` | Contract name |
| `sponsio.agent_id` | Agent ID |
| `sponsio.pipeline` | `det` or `sto` |
| `sponsio.outcome` | `pass`, `block`, `escalate`, `retry`, `observe_only` |
| `sponsio.tool` | Tool name (for tool-call events) |
| `sponsio.score` | Sto confidence score (for sto pipeline) |

Use these for dashboards (firings per contract, block rate by agent, sto latency p99) and alerts (block rate spiking above baseline).

---

## What OTEL cannot do

OTEL-based observation is **post-hoc** unless combined with framework hooks. You can use it to watch what Sponsio decided. You cannot use it to make blocking decisions — the decision has to live in the synchronous path between the LLM and the tool, which is where the framework integration sits.

If you see a doc or a competitor suggesting "just export OTEL and block from your collector", that is auditing, not enforcement. Sponsio does enforcement where the tool is about to fire.

---

## Next

- [Reporting](reporting.md) — read back from session logs.
- [Observe vs. enforce](observe-vs-enforce.md) — how observability fits in the rollout.
