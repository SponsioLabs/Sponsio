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

Sponsio ships **only deterministic contracts**: pure-Python checks over the trace, with no LLM in the runtime hot path. Patterns covering syntactic PII / response length / response keyword bans (`no_pii`, `max_length`, `no_keywords`) are regex-against-`llm_said` and need no judge.

## Positioning

When explaining Sponsio, emphasize:

- **Action-boundary enforcement**: Sponsio checks tool/action calls before side effects happen.
- **Temporal trace contracts**: Rules can express ordering and history, such as "A before B", "never B after A", "at most N calls", or "after AML check, loan files are immutable".
- **Deterministic hot path**: Det checks are pure Python and do not call an LLM at runtime.
- **Framework optionality**: Users do not need an agent framework. Custom function-calling loops can use `guard.guard_before()` / `guard.guard_after()` directly.

Do **not** describe Sponsio as only an output assertion library, only a prompt guardrail, or primarily a drift/reliability scoring framework. A concise distinction:

> Sponsio focuses on the action boundary: checking tool calls before they execute, not only auditing outputs after the fact.

## What To Read First

For product-level questions:

- `README.md` — public positioning, quick start, demos, benchmarks
- `QUICKSTART.md` — install and first integration (repo root)
- `docs/reference/cli.md` — `sponsio scan`, `validate`, `check`, `demo`, `report`
- `docs/integrations/index.md` — framework-specific wiring

For architecture and contract questions:

- `docs/concepts/architecture.md` — conceptual model, atoms, patterns, grounding, observation boundaries
- `docs/concepts/contracts.md` — deterministic constraints and atom vocabulary

For implementation:

- `sponsio/core.py` — `sponsio.Sponsio()` factory and framework resolution
- `sponsio/integrations/base.py` — `BaseGuard`, contract compilation, enforcement hooks
- `sponsio/runtime/monitor.py` — det dispatch and enforcement routing
- `sponsio/runtime/verifier.py` — trace-aware contract verification
- `sponsio/patterns/library.py` — deterministic pattern factories
- `sponsio/generation/dsl_to_contract.py` — text DSL → pattern-library calls (rule-based; LLM extractor is a separate, opt-in stage in `parse_contract`)
- `sponsio/tracer/grounding.py` — event-to-atom grounding
- `sponsio/formulas/formula.py` and `sponsio/formulas/evaluator.py` — formula AST and finite-trace evaluator

## Repository Map

```text
sponsio/
├── core.py            public entrypoint: sponsio.Sponsio()
├── cli.py             CLI: scan, validate, check, demo, patterns, report
├── config.py          YAML config loader
├── demos/             packaged mock demos used by `sponsio demo`
├── discovery/         code/docs/traces -> proposed contracts
├── formulas/          LTL/propositional/arithmetic AST + evaluators
├── generation/        text DSL -> contract parsing and optional LLM extractor for free-form NL
├── integrations/      framework adapters; all contract logic lives in BaseGuard
├── models/            Agent, Contract, System, Trace, Event, spans
├── patterns/          deterministic pattern library
├── reporting/         shadow-mode report aggregation/rendering
├── runtime/           monitor, verifier, strategies, feedback, session logging
├── scoring/           tool configuration risk scoring
└── tracer/            event collection and grounding

ts/                    TypeScript workspace (npm workspaces)
├── packages/sdk/      @sponsio/sdk: det engine + framework integrations
└── packages/sdk/  @sponsio/sdk: AST static scanner CLI
docs/                  user-facing documentation
scripts/               one-off maintenance utilities (e.g. plugin sync)
tests/                 pytest suite
```

The `api/` and `web/` directories are not part of this repo. Local
single-user observability uses `sponsio host trace --follow` / `sponsio
report` / `sponsio replay <session>` / `sponsio explain <contract>` /
`sponsio.tracer.exporters.OtlpHttpExporter`.

## Core Invariants

- `sponsio/` core should avoid hard dependencies on framework packages. Framework deps belong in `[project.optional-dependencies]`.
- Framework adapters should inherit from `BaseGuard` and keep framework-specific code thin.
- Det violations route through det strategies such as `DetBlock` or `EscalateToHuman`.
- The trace is append-only during a session. In enforce mode, a hard-blocked event may be rolled back so later checks are not poisoned.
- Grounding produces one valuation dict per timestep; formula evaluators consume valuations, not raw events.

## What Deterministic Contracts Cover

Deterministic contracts handle properties that are structurally observable:

- tool ordering
- rate limits
- retries/loops
- destructive action gates
- path/argument blacklists
- exact PII regexes, length, format
- permissions and allowlists

These are exactly checkable with regexes, counters, paths, or ordering, with no LLM judge in the loop.

## Python / TypeScript Parity

Python and TypeScript share the deterministic core. When changing these Python files, check the matching TS files:

| Python | TypeScript (`ts/packages/sdk/src/`) |
|---|---|
| `sponsio/formulas/formula.py` | `core/formula.ts` |
| `sponsio/formulas/evaluator.py` | `core/evaluator.ts` |
| `sponsio/tracer/grounding.py` | `core/grounding.ts` |
| `sponsio/patterns/library.py` | `core/patterns.ts` |
| `sponsio/generation/dsl_to_contract.py` | `core/nl-parser.ts` |

Cross-language scenarios live in `tests/cross_language/scenarios.json`.

The TS SDK covers deterministic runtime enforcement. Python currently has the broader surface: DFA/verifier work, YAML config, discovery, OTEL, and reporting.

## Common Tasks

### Add a deterministic pattern

1. Add a factory to `sponsio/patterns/library.py`.
2. If it needs a new observable, add atom extraction in `sponsio/tracer/grounding.py`.
3. Add DSL parsing in `sponsio/generation/dsl_to_contract.py`.
4. Add tests for pattern behavior and NL parsing.
5. Update README/docs if the pattern is public.
6. Check TypeScript parity if the pattern belongs in the TS det core.

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
- Do not claim OTEL ingestion can block actions. OTEL-based observation is post-hoc unless combined with framework hooks.
- Do not claim prompt engineering is unnecessary. Prompting still defines intent; Sponsio enforces action boundaries.
- Do not invent benchmark numbers. Cite the root `README.md` § Benchmarks table if present, or internal eval notes — detailed benchmark tables are not maintained in the public documentation tree.
- Do not rely on internal files such as `STATUS.md` or `PLAN.md`; they are not part of the public guide.

## Useful Commands

```bash
pip install -e ".[all]"
pytest -v
ruff check sponsio/ tests/ scripts/
ruff format sponsio/ tests/ scripts/
sponsio demo --scenario freeze --fast
sponsio validate "tool `check_policy` must precede `issue_refund`"
```
