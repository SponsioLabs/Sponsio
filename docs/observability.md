# Observability — Sponsio Semantic Conventions

This document is the contract between Sponsio's runtime and any
observability platform (Sponsio's own dashboard, Datadog, Honeycomb,
Grafana Cloud, custom OTLP collector) that wants to render contract
verdicts as first-class spans.

It pins:

1. The span shape Sponsio emits per `check_action` call.
2. The stable `sponsio.*` attribute keys on those spans.
3. Which dashboard cards each attribute set powers.
4. What we deliberately do **not** export, and why.

The single source of truth for attribute names is
[sponsio/tracer/semconv.py](../sponsio/tracer/semconv.py); the writer
that emits them is
[sponsio/tracer/otel_writer.py](../sponsio/tracer/otel_writer.py).

> **Schema version:** `1.0.0`
> **Schema URL:** `https://sponsio.dev/schemas/observability/1.0.0`

The schema URL is stamped on the resource of every export. Consumers
should detect Sponsio spans by URL match before parsing — this is the
forward-compatibility contract.

---

## What Sponsio is to your existing observability stack

Sponsio is **not** a generic LLM trace platform. Langfuse / Langsmith /
Helicone / Braintrust already cover prompt → completion → tool_calls →
latency → cost. Sponsio is the **contract verdict layer** on top:

> "The agent tried X. Sponsio said Y. Because of rule Z. Authored by
> the user in paragraph N of their policy."

Per turn, Sponsio emits:

- One **root span** describing what the agent attempted (one tool call
  or one LLM response).
- One **contract_check** child per contract that ran, with verdict.
- Per-contract grandchildren describing the assumption / guarantee /
  sto_eval phases, plus violation and enforcement details if the rule
  fired.

Existing GenAI observability captures *what was generated*. Sponsio
captures *which rules ran against it and what they decided*. Both are
shipped as standard OTLP — your platform can render them side-by-side
or merge them into one trace by `trace_id` correlation.

---

## Span hierarchy

```
sponsio.agent_turn                 (root — one per check_action)
├── sponsio.contract_check         (one per contract evaluated)
│   ├── sponsio.precondition       (assumption phase, det only)
│   ├── sponsio.guarantee          (enforcement phase, det only)
│   ├── sponsio.sto_eval           (sto judge call, sto pipeline)
│   ├── sponsio.violation          (only when a phase fails)
│   └── sponsio.enforcement        (only when a strategy fires)
├── sponsio.sto_check              (container for the sto pipeline)
│   └── sponsio.sto_eval           (per-prop evaluations)
└── …
```

Each span carries the standard OTLP fields (`traceId`, `spanId`,
`parentSpanId`, `startTimeUnixNano`, `endTimeUnixNano`, `status`) plus
the `sponsio.*` attribute namespace.

---

## Attribute reference

### Root span — `sponsio.agent_turn`

The "what was attempted + what happened" summary. Most dashboard cards
can render a per-turn row reading only this span.

| Attribute key | Type | Description |
|---|---|---|
| `sponsio.agent_id` | string | Logical agent (matches the `agents:` key in the yaml that fired). |
| `sponsio.host` | string | `"cursor"` / `"claude-code"` / `"openclaw"` / unset = legacy / code-wrapped. |
| `sponsio.conversation_id` | string | Per-IDE conversation id from the host's hook payload. |
| `sponsio.event.tool` | string | Tool the agent tried to call (`"Bash"`, `"Edit"`, `"mcp__github__create_issue"`, …). |
| `sponsio.event.type` | string | `"tool_call"` / `"llm_response"` / `"data_write"` / … |
| `sponsio.event.ts` | int | Logical sequence number within the trace. |
| `sponsio.event.tool_args` | string | JSON-encoded tool args, optionally redacted, truncated to 4 KB by default. |
| `sponsio.outcome.blocked` | bool | Did *any* contract block this turn? |
| `sponsio.outcome.status` | string | `"ok"` / `"violated"` / `"error"`. |
| `sponsio.contracts_checked` | int | Total contracts evaluated this turn. |
| `sponsio.det_violations` | int | Det-pipeline violations this turn. |
| `sponsio.sto_violations` | int | Sto-pipeline violations this turn. |
| `sponsio.turn.duration_ns` | int | Total time spent in `check_action`. |

### Contract span — `sponsio.contract_check`

| Attribute key | Type | Description |
|---|---|---|
| `sponsio.contract.label` | string | Human-readable description from the yaml `desc:` field. Stable enough to use as a dashboard heatmap row label. |
| `sponsio.contract.id` | string | Stable id for cross-session aggregation. Pack-shipped: source tag (`"library:tier1.shell"`); user-authored: hash of desc + formula. |
| `sponsio.contract.pipeline` | string | `"det"` (formal LTL) or `"sto"` (LLM judge). |
| `sponsio.contract.source` | string | `"user_policy"` / `"shipped_pack"` / `"agent_inferred"` / `"manual"`. |
| `sponsio.contract.alpha` | double | Sto assumption-trigger threshold. (Sto only.) |
| `sponsio.contract.beta` | double | Sto enforcement-pass threshold. (Sto only.) |
| `sponsio.contract.activate_at` | string | `"first_match"` for reactive contracts; unset for global. |
| `sponsio.contract.assumption_holds` | bool | Final assumption verdict for this contract. |
| `sponsio.contract.enforcement_holds` | bool | Final enforcement verdict for this contract. |

### Constraint span — `sponsio.precondition` / `sponsio.guarantee`

| Attribute key | Type | Description |
|---|---|---|
| `sponsio.constraint.desc` | string | Human-readable formula description. |
| `sponsio.constraint.formula` | string | Compact LTL AST. Optional. |
| `sponsio.constraint.result` | string | `"ok"` / `"violated"`. |
| `sponsio.constraint.fresh` | bool | Set on `guarantee` when violated — true iff the just-appended event itself caused the failure (vs a stale violation carried forward). |
| `sponsio.constraint.eval_pos` | int | Position the contract was evaluated at (0 for global, `k_star` for reactive). |

### Sto eval span — `sponsio.sto_eval`

| Attribute key | Type | Description |
|---|---|---|
| `sponsio.constraint.atom` | string | Registered atom name (`"no_pii"`, `"tone_polite"`, …). |
| `sponsio.constraint.score` | double | Judge confidence in [0, 1]. |
| `sponsio.constraint.threshold` | double | Pass/fail threshold (β). |
| `sponsio.constraint.passed` | bool | `score >= threshold`. |
| `sponsio.constraint.result` | string | Mirror of `passed` as `"ok"`/`"violated"`. |
| `sponsio.constraint.evidence` | string | Judge's one-line explanation (truncated to 1 KB). |
| `sponsio.constraint.suggestion` | string | Optional fix hint surfaced into retry prompts. |
| `sponsio.judge.model` | string | LLM model identifier (`"gemini-2.5-flash"`, `"gpt-4o-mini"`, …). |
| `sponsio.judge.latency_ms` | int | Wall-clock judge call latency. |

### Violation span — `sponsio.violation`

| Attribute key | Type | Description |
|---|---|---|
| `sponsio.violation.kind` | string | `"assumption"` / `"guarantee"` / `"sto"` / `"liveness"`. |
| `sponsio.violation.severity` | string | `"HIGH"` / `"MEDIUM"` / `"LOW"`. |
| `sponsio.violation.evidence` | string | Human-readable evidence (the message agents see on deny). |
| `sponsio.violation.policy_ref` | string | Optional traceback to source-of-truth (`"policy.md ¶1"`, `"team-handbook.md §4.2"`). |

### Enforcement span — `sponsio.enforcement`

| Attribute key | Type | Description |
|---|---|---|
| `sponsio.enforcement.strategy` | string | `"DetBlock"` / `"EscalateToHuman"` / `"RetryWithConstraint"` / `"RedirectToSafe"`. |
| `sponsio.enforcement.action` | string | `"blocked"` / `"escalated"` / `"retrying"` / `"redirected"` / `"observed"` (last fires under `mode="observe"`). |
| `sponsio.enforcement.retry_prompt` | string | Sto retry-with-lesson prompt (RetryWithConstraint only, truncated to 2 KB). |
| `sponsio.enforcement.fallback_action` | string | Fallback action name for RedirectToSafe. |

---

## Recommended dashboard cards

### Card A — Today's blocks (business / safety reviewer)

A reverse-chronological list of every blocked turn, each row read from
the `sponsio.agent_turn` root span alone. Filter `outcome.blocked ==
true`, sort by `event.timestamp_ns` desc.

| Time | Host | Tool | Argument preview | Rule | Action |
|---|---|---|---|---|---|
| 14:23 | cursor | Bash | `psql -c "DROP TABLE users"` | freeze ¶1 (DROP/TRUNCATE) | blocked |
| 14:21 | cursor | Edit | `migrations/0042.sql` | freeze ¶2 (no migrations) | blocked |
| 14:18 | cursor | Bash | `git push origin main` | freeze ¶3 (no push main) | blocked |

Attributes used: `event.tool`, `event.tool_args` (truncated to ≤80
chars for the column), `contract.label` (read from the violating
contract's child span — the dashboard's traversal), `outcome.blocked`,
`enforcement.action`, `violation.policy_ref` (for the "rule" tooltip
showing exact policy paragraph).

### Card B — Rule fire heatmap (policy author)

A matrix: rows = rules, columns = time buckets, color = fire frequency.
Helps the policy author judge "which rule is too broad / too narrow /
silent" before flipping enforce.

```
                       9-12  12-15  15-18
freeze ¶1 (DROP)        ░░    ████    ██
freeze ¶3 (push main)   ░░    ██      ██
no_pii                  ████  ████    ████
rate_limit(Bash, 50)    ░░    ░░      ░░
```

Attributes used: `contract.id` (stable aggregation key — `contract.label`
can change with edits), `contract.pipeline`, `event.timestamp_ns`,
`outcome.blocked`. One cell color per `(contract_id, bucket)` group.

### Card C — Sto judge spend (cost control)

Per-model invocation count + estimated cost over the chosen window.

| Atom | Model | Calls | $ est |
|---|---|---|---|
| no_pii | gemini-2.5-flash | 17,432 | $4.21 |
| tone_polite | gpt-4o-mini | 3,201 | $1.07 |

Attributes used: `constraint.atom`, `judge.model`, `judge.latency_ms`,
the dashboard's own per-model price table.

### Card D — Policy source-of-truth audit (compliance)

A reverse map: rules grouped by `violation.policy_ref`. For each
policy paragraph the user authored, which rules trace back to it and
how often did they fire.

Attributes used: `violation.policy_ref`, `contract.label`,
`outcome.blocked`. Powers "show me everything driven by paragraph 1 of
the freeze policy" queries.

---

## Wiring it up

### Sponsio's own dashboard (built-in)

```bash
sponsio serve
```

Already consumes the schema described here — no extra config.

### OTLP/HTTP collector (Datadog, Honeycomb, Grafana Cloud, …)

```python
from sponsio import Sponsio
from sponsio.tracer.otel_writer import span_tree_to_otlp

class OtlpHttpExporter:
    """Push every turn's span tree to your OTLP collector."""
    def __init__(self, endpoint: str, headers: dict | None = None):
        self.endpoint = endpoint
        self.headers = headers or {}

    def export(self, span):  # called per check_action
        import json
        import urllib.request

        payload = span_tree_to_otlp(
            span,
            host="cursor",                # or your runtime
            conversation_id=conv_id,      # from the host's payload
            event_tool=span.action,
            event_args=...,               # the tool_input you passed in
        )
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json", **self.headers},
        )
        urllib.request.urlopen(req, timeout=2.0)

guard = Sponsio(
    agent_id="bot",
    contracts=[...],
    otel_exporter=OtlpHttpExporter(
        endpoint="https://otlp.your-vendor.com/v1/traces",
        headers={"x-api-key": os.environ["OTEL_API_KEY"]},
    ),
)
```

The `BaseGuard` calls `exporter.export(span)` after every
`check_action`. The exporter is responsible for batching, retries, and
backpressure — Sponsio's `_otel_export` only catches and logs errors so
a flaky collector never blocks the agent's hot path.

### Custom collector (your own visibility platform)

The OTLP JSON shape returned by `span_tree_to_otlp` is the universal
ingest contract. Your backend can:

1. Accept POSTs of OTLP traces directly (every observability vendor's
   collector format).
2. Index by `service.name` (= agent_id), `sponsio.host`,
   `sponsio.conversation_id`, plus per-attribute fields.
3. Render the cards above by querying with the attribute names.

Two queries that should be cheap:

- "All blocks for `_host_cursor` in the last 24h":
  `service.name = "_host_cursor" AND sponsio.outcome.blocked = true AND timestamp > now()-24h`
- "Per-rule fire frequency for the freeze policy":
  `GROUP BY sponsio.contract.id WHERE sponsio.outcome.blocked = true`

---

## What we do NOT export

| Data | Where it lives | Why we don't ship it |
|---|---|---|
| `~/.sponsio/plugins/<bucket>/conv-*.shield-trace.jsonl` | runtime trace state | Carries raw tool args from prior subprocesses with no verdict context. Surfacing it as audit would be misleading; it's an implementation detail of cross-process trace continuity. |
| `~/.sponsio/cursor-subagents.jsonl` | subagent registry | Internal mapping table. Not user-facing. |
| atom_caches | in-memory | Score memo, no audit value. |
| user prompt original text | in-memory `Trace.events[*].content` | **Default redacted** in the writer because user prompts can carry PII / secrets. Opt in to `redact_args=False` only if your retention policy + legal team have signed off. |
| Bash command full content | in-memory | **Default truncated** to 4 KB. A runaway agent inlining a 10 MB payload should not blow up the dashboard's cell budget. |

---

## Privacy & cost defaults

The writer is **conservative by default**:

- `redact_args=True` → strips values from any key matching
  `password|token|secret|key|auth` (case-insensitive, per-key, leaves
  key names visible so dashboards still show "args.api_key was
  passed").
- `truncate=True` → caps tool args at 4 KB, sto evidence at 1 KB, sto
  retry prompts at 2 KB. Truncation is byte-based with a visible
  marker (`(+1.2 KB truncated)`) so dashboards never silently lose
  information.
- Per-conversation trace files are **never exported** (they live only
  on the local filesystem under `~/.sponsio/plugins/`).

Operators who need full fidelity (regression test corpora, internal
incident replay, your own retention regime) flip both flags off
explicitly:

```python
OtlpHttpExporter(redact_args=False, truncate=False)
```

That's an explicit "I know what I'm doing" knob; the default optimises
for "any team can ship this without legal review."

---

## Versioning

This schema follows semantic versioning:

- **MAJOR bump** (`2.0.0`): breaking attribute renames or removals.
- **MINOR bump** (`1.1.0`): new attributes added, no renames.
- **PATCH bump** (`1.0.1`): documentation / clarifications only.

Consumers should:

1. Match `schemaUrl` against the major version they support.
2. Ignore unknown attributes (forward compatibility).
3. Treat absent attributes as `None` — not as zero / empty string.

The `SCHEMA_VERSION` constant in `sponsio/tracer/semconv.py` is the
authoritative version for the build the runtime is shipping. Bumping
it without updating this doc is a release-blocking bug.
