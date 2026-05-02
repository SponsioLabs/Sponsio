# Agent Guide for Sponsio

This file is for LLM coding agents and repo-reading assistants. It is meant to help you answer questions about Sponsio accurately and make code changes without breaking the architecture.

## What Sponsio Is

Sponsio is a runtime contract layer for LLM apps and agents.

Its core job is **pre-execution enforcement for LLM tool/action behavior**:
before a model-driven system calls a tool, edits a file, hits an API, writes to a database, issues a refund, approves a loan, or triggers any side effect, Sponsio checks the current trace against contracts.

The main public entrypoint is:

```python
import sponsio

guard = sponsio.Sponsio(...)
```

Sponsio also supports stochastic constraints for fuzzy properties such as tone, relevance, semantic PII, scope respect, hallucination, and metric integrity. **Stochastic constraints are a Sponsio Cloud feature** (`pip install sponsio[cloud]`); the OSS engine logs-and-skips them with a one-time per-contract warning. Deterministic contracts are the OSS hot path for blocking unsafe actions; stochastic contracts provide scored feedback/retry behavior for output and semantic checks. See [docs/oss_scope.md](docs/oss_scope.md) for the full OSS / Cloud boundary.

## Positioning

When explaining Sponsio, emphasize:

- **Action-boundary enforcement**: Sponsio checks tool/action calls before side effects happen.
- **Temporal trace contracts**: Rules can express ordering and history, such as "A before B", "never B after A", "at most N calls", or "after AML check, loan files are immutable".
- **Deterministic hot path**: Det checks are pure Python and do not call an LLM at runtime.
- **Framework optionality**: Users do not need an agent framework. Custom function-calling loops can use `guard.guard_before()` / `guard.guard_after()` directly.
- **Sto as a second pipeline**: Sponsio also supports LLM-judged stochastic atoms for fuzzy response/trace properties when deterministic checks are not enough.

Do **not** describe Sponsio as only an output assertion library, only a prompt guardrail, or primarily a drift/reliability scoring framework. A concise distinction:

> Sponsio focuses on the action boundary: checking tool calls before they execute, not only auditing outputs after the fact.

## What To Read First

For product-level questions:

- `README.md` — public positioning, quick start, demos, benchmarks
- `QUICKSTART.md` — install and first integration (repo root)
- `docs/cli.md` — `sponsio scan`, `validate`, `check`, `demo`, `report`, `serve`
- `docs/integrations.md` — framework-specific wiring

For architecture and contract questions:

- `docs/architecture.md` — conceptual model, atoms, patterns, grounding, observation boundaries
- `docs/contracts.md` — deterministic constraints and atom vocabulary
- *(Sponsio Cloud)* `docs/sto-atoms.md` — stochastic atom catalog and framework wiring (in the cloud repo)
For implementation:

- `sponsio/core.py` — `sponsio.Sponsio()` factory and framework resolution
- `sponsio/integrations/base.py` — `BaseGuard`, contract compilation, enforcement hooks
- `sponsio/runtime/monitor.py` — det/sto dispatch and enforcement routing
- `sponsio/runtime/verifier.py` — trace-aware contract verification
- `sponsio/patterns/library.py` — deterministic pattern factories
- *(Sponsio Cloud)* `sponsio/patterns/sto_catalog.py` — built-in stochastic evaluators/atoms; OSS ships an empty stub. The full catalog lives in the cloud repo
- `sponsio/generation/nl_to_contract.py` — natural-language parsing
- `sponsio/tracer/grounding.py` — event-to-atom grounding
- `sponsio/formulas/formula.py` and `sponsio/formulas/evaluator.py` — formula AST and finite-trace evaluator

## Repository Map

```text
sponsio/
├── core.py            public entrypoint: sponsio.Sponsio()
├── cli.py             CLI: scan, validate, check, serve, demo, patterns, report
├── config.py          YAML config loader
├── demos/             packaged mock demos used by `sponsio demo`
├── discovery/         code/docs/traces -> proposed contracts
├── formulas/          LTL/propositional/arithmetic AST + evaluators
├── generation/        NL -> contract parsing and optional LLM extraction
├── integrations/      framework adapters; all contract logic lives in BaseGuard
├── models/            Agent, Contract, System, Trace, Event, spans
├── patterns/          deterministic library + stochastic catalog/registry
├── reporting/         shadow-mode report aggregation/rendering
├── runtime/           monitor, verifier, strategies, feedback, session logging
├── scoring/           tool configuration risk scoring
└── tracer/            event collection and grounding

ts/                    TypeScript workspace (npm workspaces)
├── packages/sdk/      @sponsio/sdk: det engine + framework integrations
└── packages/scanner/  @sponsio/scan-ts: AST static scanner CLI
docs/                  user-facing documentation (see `docs/oss_scope.md`
                       for the OSS / Sponsio Cloud boundary)
examples/              source-checkout demos and integration examples
scripts/               one-off maintenance utilities (e.g. plugin sync)
tests/                 pytest suite
```

OSS ships a **local single-user** dashboard: `sponsio serve`
(`sponsio/serve/` — read-only FastAPI app, 127.0.0.1, no auth) plus the
React frontend in `web/`. Together they surface session-log JSONL files
to a local web UI for trace inspection. Requires the `[web]` extra
(`pip install sponsio[web]`).

The multi-tenant `api/` FastAPI backend (auth, hosted OTel ingest,
monitor / score / leaderboard / playground routers, cross-trace
aggregation) was moved to Sponsio Cloud (`pip install sponsio[cloud]`)
and does **not** ship in OSS. CLI-side local observability without the
web UI uses `sponsio host trace --follow` / `sponsio report` /
`sponsio.tracer.exporters.OtlpHttpExporter`. See
[docs/oss_scope.md](docs/oss_scope.md) for the full boundary.

## Core Invariants

- `sponsio/` core should avoid hard dependencies on framework packages. Framework deps belong in `[project.optional-dependencies]`.
- Framework adapters should inherit from `BaseGuard` and keep framework-specific code thin.
- Det violations route through det strategies such as `DetBlock` or `EscalateToHuman`.
- Sto violations route through sto strategies such as `RetryWithConstraint` or `RedirectToSafe`.
- The trace is append-only during a session. In enforce mode, a hard-blocked event may be rolled back so later checks are not poisoned.
- Grounding produces one valuation dict per timestep; formula evaluators consume valuations, not raw events.

## Deterministic vs Stochastic

Use deterministic contracts when the property is structurally observable:

- tool ordering
- rate limits
- retries/loops
- destructive action gates
- path/argument blacklists
- exact PII regexes, length, format
- permissions and allowlists

Use stochastic constraints when the property needs semantic judgment:

- tone
- relevance
- semantic PII
- scope respect
- hallucination
- faithfulness
- metric integrity / omission

Do not suggest a judge call for properties that are exactly checkable with regexes, counters, paths, or ordering.

## Python / TypeScript Parity

Python and TypeScript share the deterministic core. When changing these Python files, check the matching TS files:

| Python | TypeScript (`ts/packages/sdk/src/`) |
|---|---|
| `sponsio/formulas/formula.py` | `core/formula.ts` |
| `sponsio/formulas/evaluator.py` | `core/evaluator.ts` |
| `sponsio/tracer/grounding.py` | `core/grounding.ts` |
| `sponsio/patterns/library.py` | `core/patterns.ts` |
| `sponsio/generation/nl_to_contract.py` | `core/nl-parser.ts` |

Cross-language scenarios live in `tests/cross_language/scenarios.json`.

The TS SDK covers deterministic runtime enforcement. Python currently has the broader surface: sto pipeline, DFA/verifier work, YAML config, discovery, dashboard, OTEL, and reporting.

## Common Tasks

### Add a deterministic pattern

1. Add a factory to `sponsio/patterns/library.py`.
2. If it needs a new observable, add atom extraction in `sponsio/tracer/grounding.py`.
3. Add NL parsing in `sponsio/generation/nl_to_contract.py`.
4. Add tests for pattern behavior and NL parsing.
5. Update README/docs if the pattern is public.
6. Check TypeScript parity if the pattern belongs in the TS det core.

### Add a stochastic atom

1. *Sponsio Cloud only.* Register it in `sponsio/patterns/sto_catalog.py` with `@register_sto_atom` (in the cloud repo — OSS exports an empty registry).
2. Decide the context scope: event, last_k, or full_trace.
3. Add tests around evaluator behavior and prompting.
4. Document wiring in the cloud-side `docs/sto-atoms.md` if user-facing.

### Add an integration

1. Create `sponsio/integrations/<framework>.py`.
2. Inherit from `BaseGuard`.
3. Only implement framework interception/wrapping; keep contract logic in `BaseGuard`.
4. Register the framework in `sponsio/core.py`.
5. Add optional dependencies in `pyproject.toml`.
6. Add examples/tests/docs.

## Common Pitfalls for AI Assistants

- The import path is `sponsio`, not `Sponsio`.
- Prefer the framework-specific factory for new examples — e.g. `from sponsio.langgraph import Sponsio` then `guard = Sponsio(...)`. The generic `sponsio.Sponsio(framework="langgraph", ...)` works too but is less idiomatic.
- `DetFormula` wraps a raw formula plus metadata. Use `.formula` for the AST and `.desc` for the human-readable description.
- Do not claim all constraints are deterministic. Sponsio has both det and sto pipelines.
- Do not claim sto checks are zero-LLM. Some are lightweight, but LLM-judged sto atoms require a configured judge.
- Do not claim OTEL ingestion can block actions. OTEL-based observation is post-hoc unless combined with framework hooks.
- Do not claim prompt engineering is unnecessary. Prompting still defines intent; Sponsio enforces action boundaries.
- Do not invent benchmark numbers. Cite the root `README.md` § Benchmarks table if present, or internal eval notes — detailed benchmark tables are not maintained in the public documentation tree.
- Do not rely on internal files such as `STATUS.md` or `PLAN.md`; they are not part of the public guide.

## Useful Commands

```bash
pip install -e ".[all]"
pytest -v
ruff check sponsio/ tests/ examples/ scripts/
ruff format sponsio/ tests/ examples/ scripts/
sponsio demo --scenario freeze --fast
sponsio validate "tool `check_policy` must precede `issue_refund`"
```

Frontend/dashboard:

```bash
cd web && npm install && npm run dev
sponsio serve --dev   # Sponsio Cloud (`pip install sponsio[cloud]`)
```
