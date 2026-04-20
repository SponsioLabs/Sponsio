# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Planned
- Pre-deployment compositional verification
- Metrics & before/after comparison dashboard

## [0.1.0a2] - 2026-04-19

### Added
- **`CODE_OF_CONDUCT.md`** at repo root: short stub adopting the
  [Contributor Covenant v2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/)
  by reference (canonical URL) rather than inlining the full text,
  plus a reporting email and pointer to the upstream enforcement
  ladder. `CONTRIBUTING.md` already links here, so the reference
  now resolves.
- **`SECURITY.md`** at repo root: responsible disclosure policy with
  reporting email, 72-hour ack / 90-day patch timeline, explicit scope
  (det bypasses are vulns; sto bypasses are quality bugs by default),
  and safe-harbor language for good-faith reporters.
- **`sponsio report` — shadow-mode session log reader.** New CLI subcommand that
  reads the JSONL files written by `mode="observe"` from
  `~/.sponsio/sessions/<agent_id>/*.jsonl` and produces a shareable summary of
  violations, would-have-blocked decisions, sto retries, top offending
  contracts, and most-violating sessions. Supports three output formats —
  Markdown (Slack / GitHub / PR comments), HTML (self-contained `<div>` with
  inline CSS for dashboards / email), and JSON (machine-readable for CI /
  downstream tools). Flags: `--since` (`all`, `30s`, `45m`, `24h`, `7d` —
  default `7d`), `--agent` (filter to one agent), `--format` (default
  `markdown`), `--out` / `-o` (write to file instead of stdout), `--live`
  (watch mode, re-renders every `--interval` seconds), `--base-dir` (override
  the session log directory for tests). The command is read-only — no files
  are modified, no network calls are made. New module `sponsio/reporting/`
  with three pure-Python files: `reader.py` (streaming JSONL reader, generator
  -based, malformed lines skipped silently), `aggregator.py` (folds an event
  stream into a `Report` dataclass with `ContractStat` / `SessionStat`
  rollups), and `renderer.py` (zero-dep f-string renderers for all three
  formats — keeps the `sponsio/` core zero-dep invariant intact). Public API
  exports `aggregate`, `load_events`, `parse_since`, `render`,
  `render_markdown`, `render_html`, `render_json`, `Report`, `ContractStat`,
  `SessionStat`, `SessionEvent`. Closes the shadow-mode loop introduced in the
  previous release: `mode="observe"` writes the data, `sponsio report` reads
  it back. **31 new tests** in `tests/test_reporting.py` cover `parse_since`
  parsing (good and bad inputs), `load_events` streaming + agent filter +
  time filter + malformed-line skip + source-file tracking, `aggregate`
  contract / session rollups, all three renderers (including HTML escaping of
  `<script>` tags), the dispatcher, and end-to-end CLI invocation via
  `CliRunner` (markdown stdout, agent filter, `--out` file, JSON format,
  invalid `--since` non-zero exit, `--live` + `--out` rejection).
- **`llms.txt` + `llms-full.txt` at repo root** — follow the
  [llmstxt.org](https://llmstxt.org/) convention so LLM coding tools
  (Cursor, Claude Code, ChatGPT, etc.) can discover Sponsio and suggest
  it to users. `llms.txt` is a curated short index (~450 tokens, lists
  the value prop + quick usage + shadow mode + doc links) and is
  maintained by hand. `llms-full.txt` is a machine-built concatenation
  of README + every file under `docs/` + CHANGELOG, with
  ``<!-- FILE: path -->`` separators, rebuilt by
  `python scripts/build_llms_txt.py`. Current dump: ~17.5k tokens,
  comfortably under the 50k target.
- **Shadow mode (`mode="observe"`) — the zero-risk onboarding path.** `sponsio.init(mode="observe", ...)` evaluates every contract on the live trace but never blocks or retries; each monitor event (including *would-have-blocked* decisions) is appended to a per-session JSONL at `~/.sponsio/sessions/<agent_id>/<YYYYMMDD_HHMMSS>_<pid>.jsonl`. The `SPONSIO_MODE` environment variable overrides the kwarg so ops can flip between observe and enforce without a code change — useful for staged rollouts and CI smoke tests. New module `sponsio/runtime/session_log.py` exports `SessionLogger` (append-only callback conforming to `RuntimeMonitor.register_callback`) and `rotate_sessions()` (time- and size-based pruning, default 7 days / 100 MB). `BaseGuard` exposes `guard.mode` and `guard.session_log_path`. In observe mode the trace is preserved across *would-have-blocked* events so downstream tools see the agent's real behaviour — which is the point of shadow mode. `EnforcementResult.action` gains a new `"observed"` literal; existing `"blocked" / "retrying"` check sites are unchanged because the downgrade happens inside the monitor. **20 new tests** in `tests/test_shadow_mode.py` cover mode resolution, env-var precedence, observe-vs-enforce semantics, JSONL shape, env override, filename collision avoidance across PIDs, and rotation (time- and size-based).
- **Vercel AI SDK integration** (`sponsio/integrations/vercel_ai.py`): `VercelAIGuard` uses the SDK's native `ai.Middleware` system — `wrap_tool` intercepts every tool call for det pre-check and sto post-check. Blocked calls return `is_error=True` tool results so the model self-corrects. Usage: `guard.wrap()` passed to `agent.run(model, messages, middleware=[...])`. Registered in framework registry as `"vercel_ai"`. 18 new tests, example at `examples/integrations/vercel_ai_guard.py`.
- **Benchmark results across 4 safety evaluation suites**: ODCV-Bench (80% protection, 12 LLMs, 80 scenarios), RedCode-Exec (80% bash / 52% python detection), tau2-bench (76-100% SOP violation recall), AgentDojo (offline replay baseline). Three-layer safety taxonomy: action-level safety, trace-level compliance, intent-level integrity. Results added to README.
- **Structured IR script-name normalization**: `compile_ir()` now auto-detects script-like subjects (`.sh`, `.py`, paths with `/`) and rewrites them to `bash:script_name` format, producing `called_with(bash, script)` atoms that correctly match runtime events where `tool="bash"`.
- **`_synth_value_range` Implies guard**: Value-range contracts now generate `G(Implies(called_with(bash, script), range_check))` instead of bare `G(range_check)`, preventing false positives on unrelated tool calls where `arg_numeric` defaults to 0.
- **`Var` comparison operators** (`__le__`, `__lt__`, `__ge__`, `__gt__`, `__eq__`): Return AST nodes (`Le`, `Lt`, etc.) so `repr()` output is round-trippable via `eval()`.
- **IR extraction prompt rewrite**: Code-mode prompt now teaches 5 empirical attack categories (data falsification, data deletion, hidden flag exploitation, content manipulation, script tampering) with concrete constraint examples for each.
- **`TraceVerifier.check_nl(nl_str)` convenience method**: parse a natural-language rule and check it on the currently-synced trace in one call. Delegates to `parse_nl_unified` via lazy import, so `TraceVerifier` itself stays independent of the generation layer. Sto / garbage inputs raise `ValueError` with a clear message. Designed for REPL / notebook / quick-script use; production code should still pre-compile via the pattern library.
- **`BaseGuard.finish_session()` — end-of-session liveness check**. Liveness formulas (`always_followed_by` = `G(trigger -> F(response))`) cannot be decided mid-session, so `RuntimeMonitor._check_det` skips any formula with `DetFormula.liveness=True`. `finish_session()` replays the finalized trace through `TraceVerifier.check_contract(..., include_liveness=True)`, where weak finite-trace semantics correctly treats any unreached `F(...)` as False. Returns a `list[Verdict]` of pending obligations, records them in `guard.violations`, and emits `MonitorEvent`s so TerminalReporter / dashboard / OTEL exporters see them exactly like runtime violations. Idempotent (second call returns the same list without double-emit); `reset()` clears the flag so a new session can call it again. Respects assumption gating: a liveness enforcement whose contract assumption never held is not reported. **14 new tests** in `tests/test_finish_session.py` cover happy paths (discharged obligations, no triggers, no liveness contracts), failure paths (undischarged, multiple triggers, mixed safety+liveness), idempotency, reset semantics, assumption gating, and regression checks that runtime behavior is unchanged.
- **`TraceVerifier` class** (`sponsio/runtime/verifier.py`): formal LTL-family evaluation layer pulled out of `RuntimeMonitor`. Takes a `Trace` + raw `Formula` AST and returns pure `Verdict` / `ContractVerdict` dataclasses — no enforcement strategies, no spans, no trace mutation. Canonical input is a raw LTL AST from `sponsio.formulas.formula` (`G`/`F`/`U`/`X`/`And`/`Or`/`Not`/`Atom`/`Le`/…); the pattern library's `DetFormula` wrappers are accepted as a convenience and unwrapped internally. Enables ad-hoc "does this formula hold on this trace?" queries without going through the full enforcement pipeline. `Verifier` retained as a backward-compatible alias.
- **Incremental grounding** (`GroundingState` + `ground_event` in `sponsio/tracer/grounding.py`): `TraceVerifier.sync` only grounds events added since the last call, maintaining `call_counts` / `flow_pairs` / `data_stores` accumulators across calls. Automatic reset on trace shrinkage (DetBlock rollback) or `content_atoms` change. Turns per-`check_action` grounding cost from O(N) to O(1) amortized.
- **Incremental LTL evaluation** (G-node memoization in `TraceVerifier._cached_g_eval`): caches `(scanned_upto, result)` per `G` formula; on re-eval, only the newly-grounded positions are checked. Guarded by `_is_temporally_flat` so nested temporal operators (e.g. `no_reversal = G(A → G(!B))`) correctly fall through to full evaluation instead of caching stale prefix decisions. Brings per-check latency on a 500-event trace × 10 `rate_limit` contracts from 5.2 ms → 0.09 ms (**60× faster**) and eliminates the O(N²) session-total scaling for common G-rooted safety patterns.
- **`scripts/bench_verifier.py`**: micro-benchmark comparing batch vs incremental grounding vs G-cache fast path across rate_limit (G-rooted, cacheable) and must_precede (U-rooted, short-circuit) workloads.
- **27 `TraceVerifier` unit tests** (`tests/test_verifier.py`): Verdict/ContractVerdict shape, `check`/`check_contract`/`check_assumption` semantics, incremental grounding correctness (batch vs stepped sync parity, auto-reset on shrink/content-atoms change), G-cache hit/miss/transition-to-false behavior, nested-temporal fall-through, and `Verifier` alias preservation.
- **Walkthrough demo agent** (`examples/demo/demo_walkthrough.py`): A single "ops_agent" that handles a P1 support ticket across 5 stages (triage → data lookup → SQL remediation → customer reply → incident report), exercising 10 det patterns (must_precede ×4, rate_limit ×3, arg_blacklist ×1, check_db_env, confirm_action) and 4 sto evaluators (tone_empathy, pii_check, redaction_quality, content_prohibition). Runs in mock mode with no API key; pushes span trees to dashboard for live monitoring.
- **Mock data fallback for all dashboard pages**: MonitorPage (Live + History tabs), Analytics, and Leaderboard now fall back to comprehensive mock data when the API is offline — enables demo/hackathon mode without running the backend. Mock data includes ops_agent trace events, 10 span trees, 6 trace summaries, analytics with score history + violation breakdowns, and a 7-entry leaderboard.
- **Expanded mock data** (`web/src/data/mockMonitorData.ts`): Added ops_agent events (13 trace events, 17 monitor events, 4 new span trees with det+sto combined checks), updated analytics to include pii_check and content_prohibition patterns, added ops_agent to leaderboard and reliability scores.
- **Monitor page redesign** (`web/src/pages/MonitorPage.tsx`): Complete rewrite of the Live tab as a professional observability dashboard with 6 panels: (1) Metric Cards — 5 real-time stats (events, det blocks, sto retries, pass rate, avg latency) with color-coded accents; (2) Agent Timeline — horizontal swimlane per agent with clickable dots colored by verdict (pass/block/retry); (3) Span Inspector — filterable span tree list with agent/status dropdowns and full SpanTree expansion; (4) Contract Health — per-contract pass rate progress bars sorted by violation count; (5) Enforcement Summary — stacked bar + legend showing outcome breakdown (blocked/retried/escalated/passed); (6) Enhanced Violation Feed — det/sto tabs, evidence display, and suggested fixes.
- **External span bridge in API** (`api/routers/monitor.py`): Added `_external_log_entries()` and `_external_trace_events()` helpers that synthesize MonitorEvent and TraceEvent data from externally pushed span trees. The `/monitor/log`, `/monitor/status`, `/monitor/trace` endpoints and SSE stream now include data from agents that push via `/monitor/push-span`, fixing the issue where the demo walkthrough data was invisible to the frontend.
- **Unified LLM extraction layer** (`sponsio/generation/llm_extraction.py`): Single Atom-aware LLM prompt shared by all three input paths (NL strings, policy documents, code scanning). The LLM is told about the full Atom vocabulary and Pattern catalog, enabling automatic hard/soft classification and accurate pattern selection. Includes `UnifiedExtractor` with `extract_from_nl()`, `extract_from_document()`, and `extract_from_code()` methods.
- **Enhanced CodeAnalyzer** (`sponsio/discovery/extractors/code_analysis.py`): Two-stage pipeline — AST pass (deterministic, zero deps) + optional LLM pass for deeper inference across all 16 hard patterns and 6 soft categories. New: LangGraph `graph.add_node()` detection, docstring/param/source extraction for LLM context, `generate_yaml()` for `sponsio init --scan`, `get_tool_inventory()` export.
- **Validation + suggestion engine**: Parse failures now include actionable suggestions ("Did you mean 'must_precede'?") with the list of available patterns.
- **36 new tests** (`tests/test_llm_extraction.py`): Full coverage of compilation (all 16 hard patterns + 6 soft categories), suggestion engine, mock LLM integration, error resilience.
- **LLM fallback in NL parser** — `parse_nl_unified()` now accepts an optional `llm_extractor` parameter. When rule-based keyword parsing fails, the `UnifiedExtractor` is tried before falling back to soft keyword classification. `config_to_system()` also accepts `llm_extractor` and `tool_inventory` so YAML-loaded contracts benefit from LLM-assisted parsing.
- **Extensible Atom vocabulary** — `register_atom(signature, description)` and `register_atoms()` let users define custom observable predicates that are automatically included in the LLM extraction prompt. Custom atoms appear alongside built-in atoms so the LLM can reference them in constraint extraction. `get_custom_atoms()`, `clear_custom_atoms()` for inspection and testing.
- **NL keyword rules for 7 patterns**: `idempotent`, `must_confirm`, `cooldown`, `bounded_retry`, `segregation_of_duty`, `deadline`, `always_followed_by` — all now parseable from natural language.
- **End-to-end pattern verification** (`tests/test_pattern_e2e.py`): 13 tests covering all patterns through NL → BaseGuard pipeline.
- **6 integration examples** (`examples/integrations/`): Vanilla, LangGraph, OpenAI SDK, CrewAI, Agents SDK, MCP — each with a different real-world scenario and contract patterns. All support mock mode and real Gemini LLM (`USE_MOCK=0 GOOGLE_API_KEY=...`).
- **`sponsio.init()` one-liner entry point** (`sponsio/core.py`): `guard = sponsio.init(framework="langgraph", agent_id="bot", guarantees=[...])` auto-selects the correct Guard class. Supports `dashboard=True` for auto-start, `config="sponsio.yaml"` for file-based config, and all framework guards. Framework registry with lazy imports — optional deps only loaded when needed.
- **Clean `__init__.py`**: Removed internal class exports (`DetEvaluator`, `StoEvaluator`, `RuntimeMonitor`). Now exports only user-facing API: `init`, `load_config`, models, and lazy-loaded Guard classes via `__getattr__`.
- **Module reorganization**: `_pred_key.py` moved to `sponsio/formulas/`, `scoring.py` moved to `sponsio/scoring/` package. Backward-compatible re-exports at old locations.
- **CLI commands `validate` and `check`**: `sponsio validate` parses NL contracts and shows pattern/formula; `sponsio check --trace` verifies contracts against OTEL trace files. Both support `--config` for YAML config files and `--json` for machine-readable output.
- **YAML contract config** (`sponsio/config.py`): Define per-agent assume/guarantee contracts in `sponsio.yaml`. `load_config()`, `config_to_guard_kwargs()`, `config_to_system()`. Optional dep: `pip install sponsio[config]`.
- **Guard class renames**: `ContractGuard` → `LangGraphGuard`, `AgentsGuard` → `AgentsSDKGuard`. Old names preserved as backward-compatible aliases.
- **TerminalReporter** (`sponsio/runtime/terminal.py`): Real-time CLI feedback during agent execution with `assume`/`enforce` labels, colored pass/VIOLATED output. `Contract.to_str()` with aligned `▸` bullet display. `BaseGuard.print_summary()` for session summaries.
- **Example traces**: `examples/traces/good_trace.json` and `bad_trace.json` (OTEL format) for testing `sponsio check`.
- **OTEL ingestion endpoint** — Sponsio dashboard now accepts traces from any OTEL-compatible agent framework via `POST /api/otel/v1/traces`. Query trace summaries (`GET /traces`), full span trees (`GET /traces/{id}`), and flat lists (`GET /traces/{id}/flat`). Sponsio spans and external framework spans (LangGraph, OpenAI, etc.) appear together with `is_sponsio` flag for differentiated rendering. Push-span bridge ensures internal Sponsio spans also appear in the OTEL trace store.
- **OpenTelemetry span exporter** (`sponsio/integrations/otel.py`): Optional `OTelExporter` that translates Sponsio span trees into OTEL spans and sends them to any compatible backend (LangFuse, Arize Phoenix, Datadog, Jaeger). All 8 span types mapped with correct attributes, parent-child nesting, and status codes. Install with `pip install sponsio[otel]`.
- **BaseGuard `otel_exporter` parameter**: Pass an `OTelExporter` instance to any guard (LangGraph, MCP, OpenAI, CrewAI, Agents SDK) for automatic span export after every `guard_before()` / `guard_after()` call.
- **Unified TraceTimeline component** (`web/src/components/TraceTimeline.tsx`): Single shared trace viewer used by both Demos and AgentConnect pages. Shows `call`/`read`/`write` type badges, source/target data flow labels (e.g. `read <- Google Drive`, `write -> #marketing`), streaming animation, SpanTree expansion on click, and side-by-side "Without Sponsio" / "With Sponsio" comparison.
- **Mock / Real LLM toggle on Demos page**: Switch between scripted mock scenarios and real Gemini agent runs. Real LLM mode runs the agent twice (without guard, with ContractGuard) and displays both results side-by-side.
- **Live LLM demo endpoints**: `GET /demo/live-status` checks API key and dependency availability. `POST /demo/run-live` runs both unguarded and guarded LLM flows, returns structured results with data flow metadata.
- **Data flow metadata on demo scenarios**: All demo tool_call steps now include `event_type` (`data_read`/`data_write`/`tool_call`), `source` (e.g. "Order DB", "Google Drive"), and `target` (e.g. "Payment Gateway", "#marketing") for trace visualization.
- **Dashboard push from demo examples**: `fmt.py` gains `dashboard_reset()`, `dashboard_seed()`, `dashboard_push_span()`, `dashboard_push_all_spans()`. All three demo scripts push ContractGuard span trees to the dashboard API after each guarded flow.
- **`.env` file support**: API server loads `.env` from project root on startup for API keys (gitignored).
- **Frontend redesign — 3-view developer tool**: Consolidated 7 pages into 3 focused views (Dashboard, Playground, Trace). Notebook-style Playground with contract cell, agent cell, execution cell, and inline result cell. Trace Viewer with horizontal flow summary, expandable block detail with SpanTree progressive disclosure, and sliding re-verify panel.
- **Dark mode**: Full light/dark theme toggle via Tailwind `darkMode: 'class'`, persisted in localStorage with `prefers-color-scheme` fallback. All components styled for both modes.
- **SpanTree component** (`web/src/components/SpanTree.tsx`): 3-level progressive disclosure — Level 1 (verdict card), Level 2 (per-contract summary), Level 3 (full evaluation chain). Auto-expands for violations.
- **PatternPicker component** (`web/src/components/PatternPicker.tsx`): Searchable, collapsible pattern library picker grouped by category. Reusable in Playground and Trace pages.
- **Real-time event streaming**: `POST /monitor/push` endpoint for live agents to push events via `dashboard_url` parameter on BaseGuard. `_push_to_dashboard()` fire-and-forget helper uses stdlib `urllib` (zero deps).
- **Trace import/export**: `POST /monitor/import` for bulk trace import, `BaseGuard.export_trace()` for serialization. JSON file import in Trace page UI.
- **Contract re-verification**: `POST /monitor/re-verify` tests new NL contracts against existing traces with per-step pass/fail results. Sliding panel in Trace page with hypothetical violation annotations.
- **Span tree API**: `GET /monitor/spans` returns structured span trees from the current session. Fixed `to_dict()` in all span subclasses to include subclass-specific fields.
- **Pattern library API**: `GET /patterns/library` returns all 16 patterns with examples, descriptions, and parameters.
- **Session reset**: `POST /monitor/reset` clears all state for fresh sessions.
- **First-Order Logic (FOL) predicate engine** (`sponsio/formulas/fol.py`): Per-event property checking with a typed AST — value references (`Field`, `Literal`), comparison predicates (`Equals`, `Matches`, `HasPrefix`, `InSet`, `GreaterThan`, `LessThanEq`), boolean connectives (`PNot`, `PAnd`, `POr`, `PImplies`), and universal quantification over file paths (`ForAllPaths`). Two-backend design: Python `eval_predicate()` for runtime, Z3 backend planned for pre-deployment satisfiability. Zero new dependencies.
- **FOL-based PropertyConstraint patterns** (`sponsio/patterns/library.py`): Per-event constraints that bridge FOL predicates into the LTL pipeline — `arg_blacklist(tool, param, patterns)` forbids regex-matched content in tool arguments, `scope_limit(tool, allowed_paths)` restricts file operations to whitelisted path prefixes, `data_intact(bound_tool, original_paths)` ensures tools operate only on unmodified source data. Results are grounded as `prop.*` predicates consumed by the LTL evaluator.
- **Structured observability** (`sponsio/models/spans.py`): Hierarchical span trees for contract enforcement. Each `check_action()` call produces an `AgentTurnSpan` tree with per-phase spans (`PreconditionSpan`, `GuaranteeSpan`, `ViolationSpan`, `EnforcementSpan`, `StoCheckSpan`, `StoEvalSpan`). Includes `SpanCollector` context manager, `render_tree()` CLI output, `to_dict()` / `to_flat_list()` for OTel-ready export. Zero new dependencies.
- **BaseGuard span accessors**: `last_check_span`, `check_spans`, `render_checks()` on all integration guards.
- **RuntimeMonitor span properties**: `last_turn_span`, `turn_spans`, `render_last_turn()`.
- **Demo span tree output**: All three demos (`customer_service`, `coding_agent`, `mcp_leak`) now print structured span trees after each "with protection" scenario.
- **Pattern library expanded to 16+ patterns**: 14 temporal LTL patterns (`idempotent`, `deadline`, `must_confirm`, `cooldown`, `segregation_of_duty`, `bounded_retry` — with bounded temporal operators) plus FOL-based `PropertyConstraint` patterns (`arg_blacklist`, `scope_limit`, `data_intact`).
- **Grounding layer**: Added `count(action)` cumulative invocation tracking; FOL `property_constraints` parameter evaluates per-event predicates and stores results as `prop.*` keys in valuations.
- **Automatic Contract Discovery** (`sponsio/discovery/`):
  - **Document extraction** (Phase 1): LLM-based extraction from policy docs (.txt, .md, .pdf)
  - **Trace mining** (Phase 2): Statistical pattern mining from historical traces (.json) — discovers ordering, exclusion, frequency, and sequence patterns
  - **Code analysis** (Phase 3): AST-based extraction from Python source — finds tool registrations, call graph dependencies, guard patterns
  - **Validation pipeline**: 5-step validation (syntactic, triviality, consistency, trace replay, human review)
  - **PatternStore**: Categorized, JSON-backed storage at `~/.sponsio/patterns.json` with auto-save, organized by source (builtin / user_defined / auto_extracted) and status (proposed / verified / rejected). Builtin patterns are protected from deletion.
  - **File loaders**: Support for `.txt`, `.md`, `.pdf` documents; single/array `.json` traces with glob patterns; `.py` files and directories with recursive resolution
- **New integrations**: CrewAI (`CrewAIGuard` + before/after hooks), OpenAI Agents SDK (`AgentsGuard` + `wrap()`), OpenAI SDK (`patch_openai()` / `unpatch_openai()`).
- All integrations support `store=` parameter for automatic pattern registration.
- **`on_violation` parameter**: Contracts support per-rule violation actions — `"block"` (default), `"warn"`, or `"log"`. New `WarnOnly` strategy records violations without blocking execution.
- **`sponsio` CLI**: `sponsio demo --scenario customer|coding|mcp [--real]` runs demos from terminal; `sponsio patterns` lists all 14 hard patterns + 7 soft evaluator categories.
- **Assume-guarantee contracts**: `assumptions=` parameter on all guards; assumption violations report upstream problems via `EscalateToHuman` instead of silently skipping.
- **Soft (semantic) constraints**: Auto-detected from NL input alongside det constraints.
  - Built-in evaluators: PII detection (regex), length check, format validation, content prohibition — zero dependencies
  - LLM evaluators: tone, relevance, generic judge — optional, requires `openai`
  - `StoConstraint` dataclass in `sponsio/patterns/sto.py`; catalog in `sto_catalog.py`
  - `parse_nl_unified()` auto-routes NL to hard pattern or soft evaluator
  - Discovery extractors emit soft proposals for unmatched patterns

### Changed (BREAKING)
- **Contract semantics redesigned to per-(A, E) pair.** A `Contract` now holds a single `assumption` + single `enforcement` instead of parallel lists. An agent with multiple rules has multiple `Contract` objects in `System.contracts`. The runtime monitor (`_check_det`, `_check_sto`) evaluates each contract independently — an assumption on one contract no longer gates the enforcement of another contract, which was the root cause of the high false-positive rate observed in ODCV-Bench runs where all assumptions were ANDed across all guarantees.
- **`Contract(agent, assumptions=[...], guarantees=[...])` removed.** The new constructor is `Contract(agent, enforcement=..., assumption=..., desc=...)`. Both fields accept a scalar or a list; a list is interpreted as the logical AND of its elements. `assumption=None` (the default) means the contract is unconditional.
- **`BaseGuard` / `sponsio.init()` kwargs collapsed to a single `contracts=` kwarg.** `assumptions=[...]` and `guarantees=[...]` are gone. Entries inside `contracts=[...]` are either bare NL strings (= unconditional shortcut, one `Contract` each) or dicts with `assumption` / `enforcement` (= one `(A, E)` pair). List-valued fields are AND-combined. Python dicts **must** use the full keys `assumption` / `enforcement` — short keys `A` / `E` are YAML-only and raise `ValueError` if used in Python.
- **YAML schema redesigned.** Agents declare a `contracts:` list of `{A, E}` pairs instead of parallel `assumptions:` / `guarantees:` blocks. YAML uses the **short keys** `A` / `E` — full names `assumption` / `enforcement` are Python-only and raise `ConfigError` if used in YAML. This split keeps YAML terse and Python self-describing. Old `assumptions:` / `guarantees:` schema also raises `ConfigError` on load with a migration hint.
- **Soft (sto) pipeline now honors clause-level gating.** A sto enforcement inside a contract with an unsatisfied assumption is skipped for that turn. Sto constraints registered directly on a `StoEvaluator` (without an owning `Contract`) remain unconditional.
- **`Contract` field shape.** Canonical state is the singular `Contract.assumption` / `Contract.enforcement` (scalar, list, or `None`). The old plural list fields are now read-only normalized views: `Contract.assumptions` / `Contract.enforcements` (properties) always return a flat `list`, which is the shape most callers want for iteration.
- **`build_contract` → `build_contracts` (plural).** Parses NL text into a list of unconditional `Contract` objects instead of bundling them into a single Contract with ANDed guarantees. Old name kept as a thin backward-compat wrapper.
- **API router `DELETE /contracts/{agent_id}/{index}`** now deletes a whole `Contract` (one A/E pair) by index, instead of popping a guarantee out of a larger contract bundle.
- **`System.agent(...).guarantees(...)` builder** still works (as an alias for the new `.enforces(...)`) but now emits one `Contract` per enforcement instead of a single bundled `Contract`.

### Changed
- Public release: cleaned commit history; rebased onto initial commit.
- **README rewrite for launch**: restructured around the before/after hero (`<!-- HERO_GIF -->` placeholder for the 8-second demo GIF), value prop on top, 60-second start example pulled above the fold, shadow mode promoted to its own section as the zero-risk onboarding path, Architecture/LTL explanation moved below the fold. Pattern Library, Integrations, and Benchmarks tables preserved unchanged. Quick Start badge row extended with a test-count badge.
- **YAML config now accepts both short keys (`A` / `E`) and long keys (`assumption` / `enforcement`)** for each contract entry. The previous YAML-only / Python-only split was a footgun — users copying `{"assumption": ..., "enforcement": ...}` examples from the Python docs into YAML would hit `ConfigError`. Short keys stay recommended for terse hand-edited YAML; long keys are accepted when users prefer them. Using both forms of the *same* field in a single entry (e.g. both `A` and `assumption`) is ambiguous and still raises `ConfigError` with a "pick one" hint. Python API is unchanged (full keys only). 5 new tests in `tests/test_config.py` cover long-key loading, cross-entry mixing, cross-field mixing, and conflict detection.
- **Unified `wrap()` method across all integration guards.** `tools()` (LangGraph, CrewAI, Agents SDK, BaseGuard), `tool_node()` (LangGraph, CrewAI), `wrap_tools()` (Agents SDK), and `middleware()` (Vercel AI) are all renamed to `wrap()`. Old names kept as backward-compatible aliases. All docs, examples, and tests updated to use `guard.wrap(tools)` as the canonical form.
- **`RuntimeMonitor._check_det` and `_check_sto` refactored to delegate all formal evaluation to `self._verifier: TraceVerifier`.** The monitor no longer imports `ground` / `evaluate` directly — it only orchestrates spans, violations, and enforcement strategies. `_check_det` shrank from ~200 lines to ~80 lines by extracting `_emit_pass_event` / `_handle_assumption_failure` / `_handle_enforcement_failure` helpers.
- **`BaseGuard.guard_before` now calls `monitor.verifier.reset()` after popping a blocked event from the trace** so the verifier's incremental cache is invalidated. Required for correctness: without this, a following event that reuses the popped index would be evaluated against stale grounded state.
- **Grounding refactored to expose a per-event kernel.** `sponsio/tracer/grounding.py` now has `GroundingState` (dataclass of cumulative accumulators) and `ground_event(event, idx, state, …)` (single-event grounding); the existing batch `ground(trace, …)` becomes a thin loop over `ground_event`. Batch semantics are unchanged — all 630 existing tests pass against the refactored kernel.
- **`AnnotatedFormula` renamed to `DetFormula`** with backward-compatible alias for compatibility. All internal references updated to use the new name. Reflects the renamed concept: "deterministic" (hard) constraints evaluated via LTL on the execution trace.
- **`StoConstraint` renamed to `StoFormula`** with backward-compatible alias for compatibility. All internal references updated to use the new name.
- **DocumentExtractor rewritten** to delegate to `UnifiedExtractor` — uses Atom-aware prompting instead of the old pattern-name-only prompt. Unknown hard patterns now fail with logged error instead of silently converting to soft constraints.
- **Multi-Agent Eval Pipeline** (`AgentEval/`): Flagship example — 5 agents, 12 tools, 13 contracts covering every Sponsio pattern type. Demonstrates both NL and programmatic contract APIs, catches 5 violation types (must_precede, scope_limit, requires_permission, no_reversal, rate_limit), and exports trace datasets for downstream analysis.
- **Pattern Architecture design doc** (`agent_docs/pattern_architecture.md`): Establishes the four-level concept stack (Atom -> Formula -> Pattern -> Contract), documents the full atom vocabulary, defines grounding as a thin event adapter, describes two complementary observation models (integration hooks for real-time blocking, OTEL consumer for post-hoc audit), and classifies patterns by observation boundary.
- **`arg_paths_within(tool, *prefixes)` atom**: Checks all file paths in tool args are within allowed prefix set (replaces FOL `ForAllPaths` quantifier).
- **`arg_field_has(tool, field, pattern)` atom**: Regex match on a specific arg field (`event.args[field]`), restoring field-level precision that was lost in FOL elimination. Used by `arg_blacklist`.
- **`arg_blacklist` uses `arg_field_has` for field-specific matching** — restores the per-field precision from the old FOL system. `arg_blacklist("bash", "command", [...])` now only checks `args["command"]`, not all args.
- **`arg_blacklist`, `scope_limit`, `data_intact` now return `DetFormula`** instead of `PropertyConstraint`. Same function signatures, same behavioral semantics, unified into the Atom + LTL system.
- **`PropertyConstraint` class removed** — all patterns use `DetFormula`.
- **`sponsio/formulas/fol.py` deprecated** — FOL AST replaced by grounding-level atoms. Module emits `DeprecationWarning` on import.
- **`property_constraints` parameter removed from `ground()`** (internal API).
- **`prop.*` predicate key namespace removed** (internal — replaced by standard atom keys).
- **`never_together` deprecated** — delegates to `mutual_exclusion` (original formula trivially satisfied in sequential traces).
- **NL parser: `no_data_leak` with tool names** routes to `no_reversal` (tool ordering, not data flow).
- **NL parser: `requires_permission` with tool-like names** routes to `must_precede` (dynamic, not static).
- **`must_precede` and `must_confirm` formulas** rewritten to use `Until` operator instead of derived `precedes()` predicate.
- **Grounding simplified** — removed derived `precedes()` predicate; ordering now handled purely by LTL.
- **CrewAI hooks renamed**: `before_hook` → `on_tool_start`, `after_hook` → `on_tool_end` (old names kept as deprecated aliases).
- **`examples/hackathon/` renamed to `examples/demo/`**: All paths updated across codebase.
- **`config_driven.py` removed**: YAML config usage documented in README instead.
- **Demos.tsx refactored**: Replaced `NaiveTimeline` + `EnforcedTimeline` with unified `TraceTimeline` component.
- **AgentConnect.tsx refactored**: Replaced inline `Timeline` component (~130 lines) with the same `TraceTimeline`.

### Fixed
- **MCP example DX consistency** — `mcp_guard.py` rewritten to use `sponsio.init()` + `guard_before()`/`guard_after()` in mock mode, matching the pattern of all other integration examples. Now shows contract banner, per-constraint enforcement lines, and session summary.
- **Leaderboard crash**: `sqlite3.Row` `row_factory` was being mutated per-query on a shared connection across threads, causing segfaults. Now set once at connection creation.
- **Python 3.9 compatibility**: Added `from __future__ import annotations` to `api/db.py` for `Dict[str, Any] | None` syntax.
- **Span serialization**: `to_dict()` in all 8 span subclasses (`AgentTurnSpan`, `ContractCheckSpan`, `PreconditionSpan`, `GuaranteeSpan`, `ViolationSpan`, `EnforcementSpan`, `StoCheckSpan`, `StoEvalSpan`) now includes subclass-specific fields.
- **Version mismatch**: `sponsio/__version__` now matches pyproject.toml (`"0.1.0a2"`).
- **Repo-wide lint cleanup**: Fixed all 35 lint errors — unused imports across `discovery/`, `models/`, `tests/`, `examples/`; ambiguous variable `l` → `line` in `sto_catalog.py`; unused `agent_id` in `crewai.py`.
- **Stale docs**: Updated test counts (262 → 341) in CLAUDE.md and README.md; corrected STATUS.md known issues (CLI and `[project.scripts]` already exist); fixed soft evaluator count in cli.py (7 → 5).

### Removed
- Frontend pages: `ContractEditor.tsx`, `Agents.tsx`, `PatternLibrary.tsx`, `Discovery.tsx` (functionality absorbed into Playground and Trace views).

---

## [0.1.0-alpha] - 2026-03-29

Initial public release.

### Added
- **Core formula engine**: Immutable LTL AST (`Atom`, `Not`, `And`, `Or`, `Implies`, `G`, `F`, `X`, `U`) with finite-trace evaluator using weak semantics
- **Arithmetic constraints**: `Le`, `Lt`, `Ge`, `Gt`, `Eq`, `Var`, `Const`, `Subset` nodes (SMT-ready)
- **Pattern library**: 8 user-facing constraint patterns that compile to LTL — `must_precede`, `always_followed_by`, `never_together`, `no_reversal`, `requires_permission`, `no_data_leak`, `mutual_exclusion`, `rate_limit`
- **NL → contract parser**: Keyword-based rule parser mapping natural language descriptions to pattern functions; LLM-assisted path (optional, requires OpenAI key)
- **Dual enforcement pipeline**: Det constraints (binary, blocks before execution) and sto constraints (scored 0–1, generates feedback for agent self-correction)
- **Grounding layer**: Converts raw `Event` traces to per-timestep predicate valuations (`called`, `precedes`, `contains`, `flow`, `perm`)
- **LangGraph integration**: `ContractGuard` using LangGraph's native `wrap_tool_call` API — zero schema changes required
- **MCP integration**: `MCPContractProxy` for Model Context Protocol tool scanning and enforcement
- **FastAPI dashboard backend**: REST API for agent/contract management, playground, and monitoring
- **React + TypeScript frontend**: Dashboard UI built with Vite, Tailwind CSS, and React Router
- **Demo scenarios**: Customer service agent (det constraint, happy path, sto constraint)
- **Docker support**: `docker-compose.yml` for one-command local setup
