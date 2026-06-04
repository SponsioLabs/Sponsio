# Sponsio OSS scope

This repository ships the Sponsio OSS engine: the deterministic
contract runtime, framework adapters, CLI, and the pattern library
that powers it. Everything here is Apache 2.0 and deterministic. There
is no LLM call on the enforcement path.

---

## What ships (Apache 2.0)

### Runtime engine
- `sponsio/formulas/`: LTL AST, evaluator, DFA monitor
- `sponsio/runtime/verifier.py`, `monitor.py`, `strategies.py`,
  `feedback.py`, `session_log.py`, `perf.py`, `evaluators.py`
- `sponsio/tracer/grounding.py`, `otel_writer.py`, `exporters.py`,
  `semconv.py`

### Pattern library
- `sponsio/patterns/library.py`: every Tier 0 + Tier 1 deterministic
  pattern (`must_precede`, `rate_limit`, `idempotent`, `arg_blacklist`,
  `arg_allowlist`, `no_data_leak`, `segregation_of_duty`, `cooldown`,
  `must_confirm`, `bounded_retry`, `loop_detection`, `scope_limit`,
  `arg_length_limit`, `data_intact`, `destructive_action_gate`,
  `tool_allowlist`, `redirect_to_safe`, etc.)
- `sponsio/runtime/strategies.py`: every Tier 0 + Tier 1 deterministic
  enforcement strategy (`DetBlock`, `EscalateToHuman` with notifier
  callbacks, `WarnOnly`, `RedirectToSafe`). The sto-pipeline
  `RetryWithConstraint` is an extension point not included here.
- `sponsio/contracts/capability/*.yaml`: shell, fs, http, db,
  credentials, self-modify, subagent
- `sponsio/contracts/incident/*.yaml`: public CVE / Reddit-incident
  replicas (Cursor Railway wipe, Claude Code secret bypass, OpenClaw,
  MCP composition, subagent escape)
- `sponsio/contracts/core/*.yaml`: universal core / runaway / llm safety

### Framework adapters (all of them)
- `sponsio/integrations/{langgraph,openai,anthropic,crewai,claude_agent,
  vercel_ai,google_adk,mcp,cursor,openclaw,agents}.py` plus the
  `BaseGuard` core
- TypeScript SDK: `ts/packages/sdk/`
- Static scanner: `ts/packages/sdk/`
- IDE host plugin packaging: `plugins/sponsio-claude-code/`,
  `plugins/sponsio-openclaw/`, `sponsio/plugin/`

### CLI commands
- `sponsio init`, `onboard`, `scan`, `validate`, `check`, `report`
- `sponsio eval`: offline trace-replay, FPR/FNR scoring
- `sponsio export`: Sponsio dump → OTLP for `eval`
- `sponsio export-sessions`: session log → OTLP file or HTTP push
- `sponsio host` group. Install / status / list / trace / uninstall /
  guard for Cursor / Claude Code / OpenClaw
- `sponsio plugin` group. Init / install / scan / prompt / guard
- `sponsio packs`, `patterns`, `prompt`, `mode`, `doctor`, `skill`,
  `demo`

### Discovery (single-project boundary)
- `sponsio/discovery/extractors/code_analysis.py`: single-project AST
  scan that backs `sponsio scan`
- `sponsio/discovery/extractors/document.py`: single-document NL
  parsing (policy.md → contracts)
- `sponsio/discovery/extractors/tool_inventory.py`: single-project
  tool detection that powers `onboard`
- `sponsio/discovery/loaders.py`: single-file / single-corpus loaders
- `sponsio/discovery/starter_pack.py`: static rule matching for
  starter-pack selection
- `sponsio/discovery/trace_replay.py`: `sponsio eval` replay engine
- `sponsio/refresh.py` + `sponsio refresh` CLI. Local trace mining
  over your own `~/.sponsio/sessions/*` (proposes new contracts from
  patterns repeating in your traces).

### Generation
- `sponsio/generation/dsl_to_contract.py`: text DSL → contract parser
  (deterministic patterns; free-form NL goes through the optional
  LLM extractor in `parse_contract`)
- `sponsio/generation/structured_ir.py`: IR for the deterministic
  pipeline

### Local observability
- `sponsio host trace --follow`: live coloured stream
- `sponsio report --since`: session log summary
- `sponsio replay <session>`: re-render a recorded session view
- `sponsio explain <contract>`: show source + compiled formula + last violation
- Session log writer (`~/.sponsio/sessions/<agent>/*.jsonl`)
- Per-conversation trace state (`~/.sponsio/plugins/<bucket>/conv-*.shield-trace.jsonl`)

---

## Deterministic only

The OSS engine evaluates deterministic LTL contracts: ordering, rate
limits, retries/loops, destructive-action gates, path/argument
blacklists, exact PII regexes, length, format, permissions, and
allowlists. The deterministic patterns `no_pii` / `max_length` /
`no_keywords` are regex against `llm_said` and remain available. There
is no judge or LLM call on the enforcement path.

### Session-log ship-out
- `sponsio export-sessions` + `sponsio.tracer.exporters`: write your
  session log to your own collector.
- `sponsio.tracer.exporters.OtlpHttpExporter`: in-process OTel exporter
  that POSTs to your endpoint.

---

## Versioning + the OSS Promise

Apache 2.0 is permanent. Anything in OSS stays in OSS. We will not
relicense or remove. New work in OSS-scope directories ships under the
same license.

The `SCHEMA_VERSION` in `sponsio/tracer/semconv.py` covers the
observability contract and follows semver: any rename of an existing
attribute key bumps MAJOR; new attributes bump MINOR; doc-only changes
bump PATCH.
