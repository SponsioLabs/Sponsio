# Sponsio

Runtime contract enforcement for LLM agent systems. Natural language → LTL formal contracts → dual-pipeline runtime enforcement.

## Build & Test

```bash
pip install -e ".[all]"          # install with all integrations
pytest -v                        # run all 789+ tests
pytest --cov=sponsio -v          # with coverage
ruff check sponsio/ api/ tests/  # lint
ruff format sponsio/ api/ tests/ # format
```

Frontend: `cd web && npm install && npm run dev`
Dashboard: `sponsio serve` (API :8000, Swagger :8000/docs) or `sponsio serve --dev` (+ frontend :5173)

## Architecture Overview

The package and import path is `sponsio`.

```
sponsio/
├── core.py          # Main entry point: sponsio.init() — auto-selects Guard by framework
├── config.py        # YAML config loader: load_config(), config_to_guard_kwargs()
├── cli.py           # CLI: sponsio validate, check, serve, demo, patterns
├── formulas/        # LTL AST + evaluators
│   ├── formula.py       #   Immutable LTL/propositional/arithmetic AST (G, F, X, U, Atom, etc.)
│   ├── evaluator.py     #   Finite-trace LTL evaluator (weak semantics)
│   ├── dfa_evaluator.py #   DFA-based evaluator backend (alternative to tree-walk)
│   ├── parser.py        #   Formula string parser
│   ├── nl_gen.py        #   Formula → natural language description
│   ├── _pred_key.py     #   Canonical predicate key format (shared by formula + grounding)
│   └── fol.py           #   [DEPRECATED] FOL predicate AST — replaced by Atom system
├── models/          # Core dataclasses: Agent, Contract, System, Trace, Event
├── patterns/        # 16 det patterns + sto catalog
│   ├── library.py       #   DetFormula wrappers (must_precede, rate_limit, arg_blacklist, etc.)
│   ├── soft.py          #   Soft constraint types
│   ├── soft_catalog.py  #   Soft constraint catalog helpers
│   ├── sto.py           #   StoFormula dataclass
│   └── sto_catalog.py   #   Built-in sto evaluators (PII, length, format, tone, relevance, content_prohibition)
├── runtime/         # Central enforcement
│   ├── monitor.py   #   RuntimeMonitor — check_action() dispatches to hard + sto pipelines
│   ├── evaluators.py#   DetEvaluator (hard, binary) + StoEvaluator (soft, 0-1)
│   ├── strategies.py#   DetBlock, EscalateToHuman, RetryWithConstraint, RedirectToSafe
│   ├── feedback.py  #   Discriminative feedback generation for soft retry
│   └── terminal.py  #   TerminalReporter — real-time CLI output (assume/enforce labels)
├── scoring/         # Safety scoring for agent tool configurations
│   └── scorer.py    #   score_tools(), ToolDef, ScoringReport
├── generation/      # NL → contract parsing (keyword-based + optional LLM)
│   ├── nl_to_contract.py  #  Three-stage cascade: rule → sto keyword → LLM
│   ├── llm_extraction.py  #  UnifiedExtractor (Gemini / OpenAI)
│   └── structured_ir.py   #  Structured IR compilation (NL → atom-level formula)
├── tracer/          # Trace collection + grounding (events → predicate valuations)
├── integrations/    # Framework adapters — ALL inherit BaseGuard (base.py)
│   ├── base.py      #   Owns: NL parsing → System → Monitor → pre_check/post_check
│   ├── langgraph.py #   LangGraphGuard + wrap() + wrap_graph() + monitor_graph()
│   ├── mcp.py       #   MCPContractProxy + scan_mcp_tools
│   ├── openai.py    #   patch_openai() / unpatch_openai() / OpenAIGuard
│   ├── crewai.py    #   CrewAIGuard + wrap() + before/after hooks
│   ├── agents.py    #   OpenAI Agents SDK: AgentsSDKGuard + wrap()
│   ├── vercel_ai.py #   Vercel AI SDK: VercelAIGuard + wrap()
│   └── otel.py      #   OTelExporter — send span trees to any OTEL backend
└── discovery/       # Auto-extract contracts from docs/traces/code
    ├── store.py     #   PatternStore — JSON-backed at ~/.sponsio/patterns.json
    ├── validation.py#   5-step validation pipeline
    ├── loaders.py   #   File loaders (.txt, .md, .pdf, .json, .py)
    └── extractors/  #   document.py, trace_mining.py, code_analysis.py
```

## Key Conventions

- **Zero core dependencies**: `sponsio/` has no external packages. Framework deps (langgraph, openai) are optional extras.
- **Contract = assume-guarantee pair**: Never use bare assertions. All contracts have `assumptions` + `guarantees`.
- **Det ≠ Sto**: Det violations MUST use DetBlock or EscalateToHuman. Sto violations MUST use RetryWithConstraint or RedirectToSafe. RuntimeMonitor enforces this — don't bypass.
- **Trace is the single source of truth**: Every action appends to a linear trace. Grounding converts events → predicate valuations. Evaluator runs LTL over grounded trace.
- **BaseGuard owns all contract logic**: Subclasses (LangGraph, MCP, etc.) only implement framework-specific interception. Never duplicate pre_check/post_check logic in subclasses.
- **Pattern naming**: Use snake_case for pattern functions (`must_precede`, `rate_limit`). NL strings use backtick-wrapped tool names: `` tool `name` ``.
- **License**: Apache 2.0.

## Python ↔ TypeScript Sync

**Python and TS share a 1:1 module mapping for the Det core.** When you change a Python file on the left, you MUST update the corresponding TS file on the right:

| Python | TypeScript (`ts-sdk/src/`) |
|--------|---------------------------|
| `sponsio/formulas/formula.py` | `core/formula.ts` |
| `sponsio/formulas/evaluator.py` | `core/evaluator.ts` |
| `sponsio/tracer/grounding.py` | `core/grounding.ts` |
| `sponsio/patterns/library.py` | `core/patterns.ts` |
| `sponsio/generation/nl_to_contract.py` | `core/nl-parser.ts` |
| `sponsio/formulas/_pred_key.py` | (inline in `core/formula.ts`) |

Cross-language parity is verified by `tests/cross_language/scenarios.json` — both Python (`tests/cross_language/test_python.py`) and TS (`ts-sdk/src/__tests__/core.test.ts`) read the same scenarios and must produce identical block/allow decisions.

**TS does NOT have**: Sto pipeline, DFA evaluator, TraceVerifier (incremental), OTEL export, dashboard, YAML config loader, LLM extraction. These are Python-only. The TS SDK covers Det runtime enforcement only.

## Gotchas — Things Claude Gets Wrong

- IMPORTANT: The import path is `sponsio`. `from sponsio.integrations.langgraph import LangGraphGuard`. Prefer `sponsio.init(framework="langgraph", ...)` for new code.
- IMPORTANT: `DetFormula` wraps a raw `Formula` + description. Use `.formula` to get the raw LTL, `.desc` for the NL string. (`AnnotatedFormula` is a backward-compatible alias.)
- Do NOT add external dependencies to `sponsio/` core without explicit approval. Optional deps go in `pyproject.toml [project.optional-dependencies]`.
- When adding a new LTL pattern: update BOTH `sponsio/patterns/library.py` AND `sponsio/generation/nl_to_contract.py` (the NL parser must know about it), AND `ts-sdk/src/core/patterns.ts` + `ts-sdk/src/core/nl-parser.ts` for the TS SDK.
- `ground()` returns `list[dict[str, bool]]` — one dict per timestep. The evaluator expects this exact shape. FOL predicates are evaluated per-event and stored under `prop.*` keys.
- FOL predicates (`sponsio/formulas/fol.py`) are per-event, not temporal. They feed boolean results into grounding, which the LTL evaluator consumes. Don't confuse the two layers.
- When modifying `BaseGuard`: all 6 integration subclasses depend on it. Run the full test suite after changes.

## Before Starting Any Task

Read these files at the start of every session or major task, BEFORE writing any code:

1. `STATUS.md` — current state, known issues, architecture invariants
2. `PLAN.md` — sprint priorities and architecture decisions log

Then, depending on what you're working on, read the relevant reference doc:

| If the task involves... | Read first |
|------------------------|------------|
| Architectural changes | `docs/architecture.md` |
| Contract DSL / adding patterns | `docs/contracts.md` |
| New integration adapter | `sponsio/integrations/base.py` (full docstring) |
| CLI changes | `docs/cli.md` |

## Task Completion Protocol

After completing any non-trivial task, do the following:

### Step 1: Read existing docs before updating

IMPORTANT: Before updating ANY doc, read it first to match existing format, structure, and avoid duplicating content.

| Doc to update | Read first to understand... |
|---------------|----------------------------|
| `README.md` | Existing pattern table format, architecture diagram style, Quick Start structure |
| `CHANGELOG.md` | Existing entry format, what's already in `[Unreleased]`, Keep-a-Changelog conventions |
| `STATUS.md` | Current known issues (don't duplicate), milestone dates, architecture invariants |

### Step 2: Update public-facing docs

**README.md** — update if:
- New pattern added → update Pattern Library table (match existing row format)
- New integration → update Architecture diagram + integrations list
- New CLI command → update Quick Start / usage sections
- API surface changed → update code examples

**CHANGELOG.md** — update `[Unreleased]` section if:
- Feature added → `### Added`
- Bug fixed → `### Fixed`
- Behavior changed → `### Changed`
- Something removed → `### Removed`

IMPORTANT: Adding a feature without updating README.md and CHANGELOG.md is an incomplete task.

### Step 3: Update internal docs

**STATUS.md** — update if architecture changed, features added, or new known issues found.

### Step 4: Output Task Summary Block

```
## Task Summary
**What changed**: [1-2 sentence description]
**Files modified**: [list of files touched]
**Files added**: [list of new files, if any]
**Tests**: [pass/fail + count]
**Lint**: [clean / N errors]
**Architecture impact**: [none / low / HIGH — explain if HIGH]
**Docs updated**: [list which of README.md, CHANGELOG.md, CONTRIBUTING.md, STATUS.md were updated, or "none"]
**Open items**: [anything left undone or needing follow-up]
```

## Compaction Rules

When compacting, ALWAYS preserve:
- The full list of modified files from the current session
- Any failing test names and error messages
- Architecture decisions made during the session
- Which public docs (README, CHANGELOG) were or were not updated
- The last Task Summary Block
