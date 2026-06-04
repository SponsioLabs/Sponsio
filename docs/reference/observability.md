---
title: Observability
description: Local session logs, OTEL export, and the Sponsio span schema.
---

# Observability

Sponsio emits structured events for every check it runs. Two sinks ship out of the box.

## Local session logs (default)

Every session writes a JSONL file to `~/.sponsio/sessions/<agent_id>/<timestamp>.jsonl`. One event per line. No configuration needed.

```bash
ls ~/.sponsio/sessions/support_bot/
# 2026-04-24T10-12-33Z.jsonl
```

`sponsio report` reads these files. `sponsio scan -t '~/.sponsio/sessions/bot/*.jsonl'` mines them for contract candidates. Disable with `SPONSIO_SESSION_LOG=0` or `sessions_dir: null` in `sponsio.yaml`.

## OpenTelemetry

```bash
pip install "sponsio[otel]"
```

Sponsio respects standard OTEL env vars:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://your-collector:4318
export OTEL_SERVICE_NAME=sponsio
```

Every check produces a span tree with stable `sponsio.*` attributes. Datadog, Honeycomb, Grafana Cloud, and any OTLP collector ingest it directly.

## What OTEL cannot do

OTEL-based observation is post-hoc. It records what Sponsio decided. It cannot make blocking decisions. The decision has to live in the synchronous path between the LLM and the tool, which is where the framework integration sits. If a doc suggests "just export OTEL and block from the collector", that is auditing, not enforcement.

## Span schema

Schema version: `1.0.0`. Schema URL: `https://sponsio.dev/schemas/observability/1.0.0`. Stamped on the resource of every export. Detect Sponsio spans by URL match before parsing.

Source of truth: [`sponsio/tracer/semconv.py`](../../sponsio/tracer/semconv.py). Writer: [`sponsio/tracer/otel_writer.py`](../../sponsio/tracer/otel_writer.py).

### Span hierarchy

```
sponsio.agent_turn                 (root, one per check_action)
└── sponsio.contract_check         (one per contract evaluated)
    ├── sponsio.precondition       (assumption phase)
    ├── sponsio.guarantee          (enforcement phase)
    ├── sponsio.violation          (only when a phase fails)
    └── sponsio.enforcement        (only when a strategy fires)
```

### Root: `sponsio.agent_turn`

| Attribute | Description |
|---|---|
| `sponsio.agent_id` | Logical agent (matches `agents:` key in yaml). |
| `sponsio.host` | `cursor`, `claude-code`, `openclaw`, or unset for code-wrapped. |
| `sponsio.conversation_id` | Per-IDE conversation id from the host's hook payload. |
| `sponsio.event.tool` | Tool name the agent tried to call. |
| `sponsio.event.type` | `tool_call`, `llm_response`, `data_write`. |
| `sponsio.event.tool_args` | JSON tool args, optionally redacted, truncated to 4 KB. |
| `sponsio.outcome.blocked` | Did any contract block this turn? |
| `sponsio.outcome.status` | `ok`, `violated`, `error`. |
| `sponsio.contracts_checked` | Total contracts evaluated. |
| `sponsio.det_violations` | Contract violations. |
| `sponsio.turn.duration_ns` | Total time spent in `check_action`. |

### Contract: `sponsio.contract_check`

| Attribute | Description |
|---|---|
| `sponsio.contract.label` | Human description from the yaml `desc:` field. |
| `sponsio.contract.id` | Stable id for cross-session aggregation. |
| `sponsio.contract.source` | `user_policy`, `shipped_pack`, `agent_inferred`, `manual`. |
| `sponsio.contract.assumption_holds` | Final assumption verdict. |
| `sponsio.contract.enforcement_holds` | Final enforcement verdict. |

### Constraint: `sponsio.precondition` / `sponsio.guarantee`

| Attribute | Description |
|---|---|
| `sponsio.constraint.desc` | Formula description. |
| `sponsio.constraint.formula` | Compact LTL AST (optional). |
| `sponsio.constraint.result` | `ok` or `violated`. |
| `sponsio.constraint.fresh` | True iff the just-appended event caused the failure. |
| `sponsio.constraint.eval_pos` | Position the contract was evaluated at. |

### Violation: `sponsio.violation`

| Attribute | Description |
|---|---|
| `sponsio.violation.kind` | `assumption`, `guarantee`, `liveness`. |
| `sponsio.violation.severity` | `HIGH`, `MEDIUM`, `LOW`. |
| `sponsio.violation.evidence` | Human-readable evidence. |
| `sponsio.violation.policy_ref` | Optional traceback to source-of-truth (`policy.md ¶1`). |

### Enforcement: `sponsio.enforcement`

| Attribute | Description |
|---|---|
| `sponsio.enforcement.strategy` | `DetBlock`, `EscalateToHuman`, `WarnOnly`, `RedirectToSafe`. `RetryWithConstraint` emits through the same attribute when the optional sto pipeline is plugged in. |
| `sponsio.enforcement.action` | `blocked`, `escalated`, `redirected`, `warned`, `observed`. `retrying` is reserved for the sto pipeline and not reachable in this OSS build. |
| `sponsio.enforcement.retry_prompt` | Retry-with-lesson prompt, truncated to 2 KB. Only emitted when an external sto evaluator is wired up. |
| `sponsio.enforcement.fallback_action` | Fallback tool name for `RedirectToSafe` (e.g. `log_refund_request` when the model attempted `issue_refund`). |

## Privacy and cost defaults

The writer is conservative by default.

- `redact_args=True` strips values from any key matching `password|token|secret|key|auth` (case-insensitive, leaves key names visible).
- `truncate=True` caps tool args at 4 KB and retry prompts at 2 KB. Truncation marks bytes lost (`(+1.2 KB truncated)`).
- Per-conversation trace files under `~/.sponsio/plugins/<bucket>/conv-*.shield-trace.jsonl` are never exported. They live only on the local filesystem.

For full fidelity (regression test corpora, internal incident replay), opt out:

```python
OtlpHttpExporter(redact_args=False, truncate=False)
```

## What we do not export

| Data | Why |
|---|---|
| Per-conversation `shield-trace.jsonl` | Carries raw tool args from prior subprocesses with no verdict context. Internal cross-process trace state. |
| `~/.sponsio/cursor-subagents.jsonl` | Internal subagent registry, not user-facing. |
| User prompt original text | Default redacted because user prompts can carry PII or secrets. Opt in to `redact_args=False` only after legal sign-off. |

## Versioning

Semantic. Major bumps rename or remove attributes. Minor bumps add. Patch is doc-only. Match `schemaUrl` against the major version you support, ignore unknown attributes, treat absent attributes as `None`.

`SCHEMA_VERSION` in `sponsio/tracer/semconv.py` is authoritative for the build the runtime is shipping. Bumping it without updating this doc is a release-blocking bug.

## See also

- [Reporting](../guides/reporting.md): read back from session logs.
- [Observe vs. enforce](../guides/observe-vs-enforce.md): observability in the rollout.
