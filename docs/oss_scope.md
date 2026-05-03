# Sponsio OSS scope and Cloud boundary

This repository ships the **Sponsio OSS engine** — the deterministic
contract runtime, framework adapters, CLI, and the pattern library
that powers it. Sponsio Cloud is a separate product layer
(`sponsio[cloud]` install extra) that adds the LLM-judge sto pipeline,
cross-customer pattern mining, multi-tenant dashboard backend, and
hosted retention.

The split below is the long-term boundary; this is a permanent
commitment, not a "temporarily open" status.

---

## In OSS (Apache 2.0) — permanently free

### Runtime engine
- `sponsio/formulas/` — LTL AST, evaluator, DFA monitor
- `sponsio/runtime/verifier.py`, `monitor.py` (det path), `strategies.py`,
  `feedback.py`, `session_log.py`, `perf.py`, `evaluators.py`
  (DetEvaluator only)
- `sponsio/tracer/grounding.py`, `otel_writer.py`, `exporters.py`,
  `semconv.py`

### Pattern library
- `sponsio/patterns/library.py` — every Tier 0 + Tier 1 deterministic
  pattern (`must_precede`, `rate_limit`, `idempotent`, `arg_blacklist`,
  `arg_allowlist`, `no_data_leak`, `segregation_of_duty`, `cooldown`,
  `must_confirm`, `bounded_retry`, `loop_detection`, `scope_limit`,
  `arg_length_limit`, `data_intact`, `destructive_action_gate`, etc.)
- `sponsio/contracts/capability/*.yaml` — shell, fs, http, db,
  credentials, self-modify, subagent
- `sponsio/contracts/incident/*.yaml` — public CVE / Reddit-incident
  replicas (Cursor Railway wipe, Claude Code secret bypass, OpenClaw,
  MCP composition, subagent escape)
- `sponsio/contracts/core/*.yaml` — universal core / runaway / llm safety

### Framework adapters (all of them)
- `sponsio/integrations/{langgraph,openai,anthropic,crewai,claude_agent,
  vercel_ai,google_adk,mcp,cursor,openclaw,agents}.py` plus the
  `BaseGuard` core
- TypeScript SDK: `ts/packages/sdk/`
- Static scanner: `ts/packages/scanner/`
- IDE host plugin packaging: `plugins/sponsio-claude-code/`,
  `plugins/sponsio-openclaw/`, `sponsio/plugin/`

### CLI commands
- `sponsio init`, `onboard`, `scan`, `validate`, `check`, `report`
- `sponsio eval` — offline trace-replay, FPR/FNR scoring
- `sponsio export` — Sponsio dump → OTLP for `eval`
- `sponsio export-sessions` — session log → OTLP file or HTTP push
- `sponsio host` group — install / status / list / trace / uninstall /
  guard for Cursor / Claude Code / OpenClaw
- `sponsio plugin` group — init / install / scan / prompt / guard
- `sponsio packs`, `patterns`, `prompt`, `mode`, `doctor`, `skill`,
  `demo`

### Discovery (single-project boundary)
- `sponsio/discovery/extractors/code_analysis.py` — single-project AST
  scan that backs `sponsio scan`
- `sponsio/discovery/extractors/document.py` — single-document NL
  parsing (policy.md → contracts)
- `sponsio/discovery/extractors/tool_inventory.py` — single-project
  tool detection that powers `onboard`
- `sponsio/discovery/loaders.py` — single-file / single-corpus loaders
- `sponsio/discovery/starter_pack.py` — static rule matching for
  starter-pack selection
- `sponsio/discovery/trace_replay.py` — `sponsio eval` replay engine
- `sponsio/refresh.py` + `sponsio refresh` CLI — local trace mining
  over your own `~/.sponsio/sessions/*` (proposes new contracts from
  patterns repeating in your traces). Cloud adds *cross-customer*
  pattern intelligence on top of the same command.

### Generation
- `sponsio/generation/nl_to_contract.py` — NL → contract parser
  (deterministic patterns only; sto patterns require Cloud)
- `sponsio/generation/structured_ir.py` — IR for the deterministic
  pipeline

### Local observability
- `sponsio host trace --follow` — live coloured stream
- `sponsio report --since` — session log summary
- `sponsio replay <session>` — re-render a recorded session view
- `sponsio explain <contract>` — show source + compiled formula + last violation
- Session log writer (`~/.sponsio/sessions/<agent>/*.jsonl`)
- Per-conversation trace state (`~/.sponsio/plugins/<bucket>/conv-*.shield-trace.jsonl`)

---

## In Sponsio Cloud (commercial) — `pip install sponsio[cloud]`

### Sto (stochastic) pipeline — Cloud only, not in OSS at all
- `sponsio.patterns.sto_catalog` — every built-in LLM-judge
  evaluator (`no_pii`, `tone_polite`, `injection_free`,
  `jailbreak_free`, `toxic_free`, `semantic_pii_free`, `scope_respect`,
  `hallucination_free`, `harmful`, `faithfulness`, plus
  pii/length/format/relevance evaluators)
- `sponsio.patterns.sto_registry` — atom registration mechanism
- `sponsio.patterns.sto`, `sponsio.patterns.soft`,
  `sponsio.patterns.soft_catalog` — sto formula AST + legacy aliases
- `sponsio.runtime.sto_lifting` — probabilistic-lifting (α/β-threshold)
  evaluation
- `sponsio.runtime.judge` — judge harness
- `sponsio.runtime.llm_client` — judge LLM call adapters
- `sponsio.runtime.calibrator` — sto threshold calibration
- The OSS engine ships **no** stochastic atoms or sto compilation
  path. A YAML library that names a sto pattern (``pattern: tone_polite``,
  ``pattern: injection_free``, …) raises ``ConfigError`` at load with a
  pointer to ``pip install sponsio[cloud]``. The deterministic patterns
  ``no_pii`` / ``max_length`` / ``no_keywords`` are intentionally
  ship-as-det (regex against ``llm_said``) and remain available in OSS.

### Cross-corpus mining + cloud backend
- *Cross-customer* extension to `sponsio refresh` — anonymized
  pattern intelligence drawn from the cross-deployment trace pool.
  The local version of `sponsio refresh` (single-project mining
  over your own session log) is **OSS** — see above.
- `sponsio.discovery.extractors.trace_mining` — cross-trace pattern
  mining at the corpus level
- `sponsio.discovery.store` — cross-customer pattern store
- `api/` — full FastAPI backend (auth, multi-tenant, OTel ingest,
  monitor / leaderboard / score / playground / discovery routers)
- `web/` — React + Vite frontend (Monitor / Rulebook / Playground /
  Integrate / ScanAgent pages, design tokens, theming)
- `sponsio serve` — dashboard backend + frontend launcher (in OSS this
  is a stub pointing at the cloud install)

### Premium content packs
- `sponsio/contracts/premium/*.yaml` — bespoke threat patterns from
  internal customer engagements (empty in OSS — namespace reserved)

### Bench
- `sponsio bench` CLI — synthetic-benchmark runner (deleted from OSS,
  not currently in Cloud either; reach out for benchmark requests)

---

## Boundary rules of thumb

| Question | OSS or Cloud? |
|---|---|
| Is it a deterministic LTL contract / pattern? | OSS |
| Does it call an LLM at runtime to score output? | Cloud |
| Does it need cross-trace / cross-customer aggregation? | Cloud |
| Is it a single-project static scan? | OSS |
| Is it a per-host hook adapter (Cursor / Claude Code / OpenClaw)? | OSS |
| Does it serve a web dashboard (single- or multi-user)? | Cloud |
| Does it accept hosted span ingestion from remote agents? | Cloud |
| Is it a session-log ship-out to your own collector? | OSS (`sponsio export-sessions` + `sponsio.tracer.exporters`) |
| Is it an in-process OTel exporter that POSTs to your endpoint? | OSS (`sponsio.tracer.exporters.OtlpHttpExporter`) |

---

## What "log-and-skip" looks like in practice

A YAML library that mixes det + sto contracts loads cleanly under OSS.
At evaluation time, det contracts enforce normally; sto contracts emit
a one-time warning per contract:

```
WARNING:sponsio.runtime.monitor:Skipping stochastic contract
'response must be polite' — the sto pipeline (LLM-judge atoms) is a
Sponsio Cloud feature, not bundled with the OSS engine. Det contracts
in the same library continue to enforce. Install ``sponsio[cloud]`` or
contact your account team to enable the sto pipeline.
```

The trace records the contract as "checked, no result" so audits show
the gap explicitly.

---

## Versioning + the OSS Promise

Apache 2.0 is permanent. Anything currently in OSS stays in OSS — we
will not relicense or remove. New work in OSS-scope directories
(per the table above) ships under the same license. New work in
Cloud-scope directories doesn't appear in this repo.

The `SCHEMA_VERSION` in `sponsio/tracer/semconv.py` covers the
observability contract and follows semver: any rename of an existing
attribute key bumps MAJOR; new attributes bump MINOR; doc-only changes
bump PATCH.
