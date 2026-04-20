# Sponsio

Runtime contract enforcement for LLM agent systems. Natural language → LTL formal contracts → dual-pipeline runtime enforcement.

> This file lives at the root of the **public** `SponsioLabs/Sponsio` repo. Every line here ships to the world. Do NOT reference internal-only files (anything under `internal/`, or `STATUS.md`, `PLAN.md`, `LAUNCH_*.md`, `sponsio-code-review.md`) from this doc — they are gitignored and not visible to OSS contributors.

## Repo State

- **Latest PyPI release**: `0.1.0a2` (alpha — users install with `pip install --pre sponsio`).
- **Branch protection on `main`**: direct pushes blocked; all changes go through PRs. Force-push and branch deletion disabled. Required approvals = 0 (maintainers can self-merge after the PR checklist; external PRs need a maintainer to merge).
- **GitHub defaults enabled**: Secret scanning, push protection, Dependabot alerts.
- **CI**: `.github/workflows/ci.yml` runs `pytest` + ruff on Python 3.10/3.11/3.12, plus the TypeScript SDK cross-language parity tests. Required before merge.

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
TS SDK: `cd ts-sdk && npm install && npx tsc && node dist/__tests__/core.test.js`

## Architecture Overview

The package and import path is `sponsio`.

```
sponsio/
├── core.py          # Main entry point: sponsio.init() — auto-selects Guard by framework
├── config.py        # YAML config loader: load_config(), config_to_guard_kwargs()
├── cli.py           # CLI: sponsio validate, check, serve, demo, patterns, report
├── formulas/        # LTL AST + evaluators
│   ├── formula.py       #   Immutable LTL/propositional/arithmetic AST (G, F, X, U, Atom, etc.)
│   ├── evaluator.py     #   Finite-trace LTL evaluator (weak semantics)
│   ├── dfa_evaluator.py #   DFA-based evaluator backend (alternative to tree-walk)
│   ├── parser.py        #   Formula string parser
│   ├── nl_gen.py        #   Formula → natural language description
│   ├── _pred_key.py     #   Canonical predicate key format (shared by formula + grounding)
│   └── fol.py           #   [DEPRECATED] FOL predicate AST — replaced by Atom system
├── models/          # Core dataclasses: Agent, Contract, System, Trace, Event
├── patterns/        # 29 det patterns + sto catalog
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
│   ├── terminal.py  #   TerminalReporter — real-time CLI output (assume/enforce labels)
│   ├── verifier.py  #   TraceVerifier — pure LTL evaluation, incremental grounding + G-cache
│   └── session_log.py #  Shadow-mode JSONL session logger
├── reporting/       # sponsio report — shadow-mode log reader (reader/aggregator/renderer)
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
│   ├── crewai.py    #   CrewAIGuard + wrap() + on_tool_start/on_tool_end hooks
│   ├── agents.py    #   OpenAI Agents SDK: AgentsSDKGuard + wrap()
│   ├── vercel_ai.py #   Vercel AI SDK: VercelAIGuard + wrap()
│   ├── claude_agent.py #  Claude Agent SDK guard
│   └── otel.py      #   OTelExporter — send span trees to any OTEL backend
└── discovery/       # Auto-extract contracts from docs/traces/code
    ├── store.py     #   PatternStore — JSON-backed at ~/.sponsio/patterns.json
    ├── validation.py#   5-step validation pipeline
    ├── loaders.py   #   File loaders (.txt, .md, .pdf, .json, .py)
    └── extractors/  #   document.py, trace_mining.py, code_analysis.py
```

## Key Conventions

- **Zero core dependencies**: `sponsio/` has no external packages beyond `click` for the CLI. Framework deps (langgraph, openai, etc.) are optional extras.
- **Contract = assume-guarantee pair**: Never use bare assertions. All contracts have an `assumption` + `enforcement` (or an unconditional enforcement with `assumption=None`).
- **Det ≠ Sto**: Det violations MUST use `DetBlock` or `EscalateToHuman`. Sto violations MUST use `RetryWithConstraint` or `RedirectToSafe`. `RuntimeMonitor` enforces this — don't bypass.
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
- `ground()` returns `list[dict[str, bool]]` — one dict per timestep. The evaluator expects this exact shape. Per-event atoms are stored under the canonical predicate keys (the old `prop.*` namespace was removed).
- When modifying `BaseGuard`: all integration subclasses depend on it. Run the full test suite after changes.

## Before Starting Any Task

Read the public docs relevant to what you're touching, BEFORE writing code:

| If the task involves... | Read first |
|------------------------|------------|
| Architectural changes | `docs/architecture.md` |
| Contract DSL / adding patterns | `docs/contracts.md` |
| New integration adapter | `sponsio/integrations/base.py` (full docstring) |
| CLI changes | `docs/cli.md` |
| Contribution process | `CONTRIBUTING.md` |

## Security Rules — Hard Blockers

This is a **public** repo. Every commit is visible to the world, forever.

**Never commit:**

- Credentials or tokens: `sk-*`, `pypi-*`, `ghp_*`, `github_pat_*`, AWS keys, `-----BEGIN*` private keys, `.env` files.
- Customer or user PII.
- Unpublished security vulnerabilities — report those through GitHub Security Advisories (see `SECURITY.md`), not in a commit or public issue.
- Internal strategy docs: roadmap, pricing plans, launch docs, investor materials, code review notes. Those live under `internal/` which is gitignored.

The `Pre-push checklist` section below is how this gets enforced in practice.

## Pre-push checklist

Run through this before every merge to `main` — whether you're merging a PR or (as a maintainer with admin bypass) pushing directly. Steps are fast and skipping them has never saved time in aggregate.

### Hard checks — every push

```bash
# 1. What am I about to ship?
git log --oneline origin/main..HEAD
git diff --stat origin/main..HEAD

# 2. Secret scan — output MUST be empty.
git diff origin/main..HEAD | grep -iE "sk-|pypi-|ghp_|github_pat_|api[_-]?key|-----BEGIN|aws_secret"

# 3. Internal / ignored-file scan — output MUST be empty.
git diff --stat origin/main..HEAD | grep -E "internal/|STATUS\.md|PLAN\.md|\.env($|[^.])"

# 4. Tests + lint.
pytest -v
ruff check sponsio/ api/ tests/
ruff format --check sponsio/ api/ tests/
```

### Sanity pass

- Is the diff scope what you expected? Any file you don't remember touching?
- If `sponsio/__init__.py` or `pyproject.toml` is in the diff, version strings must match: `grep -E '^version|__version__' pyproject.toml sponsio/__init__.py`.
- Commit message matches the diff intent — no leftover `WIP`, `debug`, `temp`.
- If the diff adds a public API, pattern, integration, or CLI command: `README.md` and `CHANGELOG.md` updated.

### Pushing via Claude Code

If you're driving the push through Claude Code, start the session with this prompt:

```
Before pushing, walk through the pre-push checklist in CLAUDE.md:

1. `git status` and `git log --oneline origin/main..HEAD` — what's being shipped?
2. `git diff --stat origin/main..HEAD` — blast radius.
3. Run:
   git diff origin/main..HEAD | grep -iE "sk-|pypi-|ghp_|github_pat_|api[_-]?key|-----BEGIN|aws_secret"
   If output is non-empty, STOP and show me the hit. Do NOT push.
4. Run:
   git diff --stat origin/main..HEAD | grep -E "internal/|STATUS\.md|PLAN\.md|\.env"
   If output is non-empty, STOP and show me the hit. Do NOT push.
5. `pytest -v` and `ruff check sponsio/ api/ tests/` — both must be clean.
6. If `sponsio/__init__.py` or `pyproject.toml` is in the diff, confirm the
   version strings match each other.
7. Summarize: files touched, lines +/-, any concerns.

Wait for my explicit "push" before running `git push`. If any step fails,
surface it and wait — do not "fix and retry" silently.
```

If any hard check fails, fix the root cause and restart the checklist. Never push through a red step.

## PR Flow

Branch protection requires a pull request before merging to `main` for all contributors. Maintainers hold admin bypass for trivial changes (docs typos, CHANGELOG edits, one-line fixes); anything non-trivial still goes through the standard PR flow below. External contributors always go through a PR.

```bash
git checkout main && git pull
git checkout -b <prefix>/<name>      # feat, fix, docs, refactor, test, chore, perf, security
# ... make changes ...
ruff format sponsio/ api/ tests/
ruff check sponsio/ api/ tests/
pytest -v
git commit -m "feat(area): description"    # Conventional Commits
git push -u origin <prefix>/<name>
gh pr create --fill                   # maintainers can self-merge after checks
# OR for risky changes, open as Draft:
gh pr create --draft --fill           # green Merge button disabled until `gh pr ready`
gh pr merge --squash --delete-branch
```

**Use Draft PR when any of these are true:**

- `pyproject.toml` dependencies changed
- `.github/workflows/*` changed
- Core runtime (`sponsio/runtime/`, `sponsio/formulas/`) changed
- Diff deletes > 100 lines or is > 500 lines total
- Security-adjacent change
- You're uncertain about any part of the change

## External PR Review Protocol

Treat PRs from outside the core team as **potentially adversarial** until proven otherwise. Any red flag below → request changes and loop in a human maintainer. Never auto-merge external PRs.

**Supply-chain / dependency:**

- New entries in `pyproject.toml` (core or optional extras), `ts-sdk/package.json`, or any lockfile.
- Typosquat-adjacent package names.
- Dependencies pinned to a git commit or a pre-release tag.

**CI / release hijack:**

- Any change to `.github/workflows/*`.
- New entries in `[project.scripts]` or install-time hooks (`setup.py` hooks, `MANIFEST.in` additions, `build-backend` changes).

**Code execution / network / filesystem:**

- `exec`, `eval`, `compile`, `__import__`, `importlib.import_module` with a non-literal argument.
- `subprocess`, `os.system`, shell-injection-shaped string concatenation.
- New outbound network calls from runtime code (anything not already in an integration shim).
- File writes outside the expected path (anything not `~/.sponsio/`, `tests/`, or explicitly user-supplied).
- User-controlled paths without `Path.resolve()` + an allowed-prefix check.

**Obfuscation:**

- Base64 / hex blobs, long one-liners, pickled objects, non-ASCII comments that obscure intent, binary files that aren't obvious assets.

**Testing red flags:**

- Deletes or disables existing tests.
- `assert True` tests or trivially passing tests for a non-trivial change.
- Weakly justified `pytest.mark.skip`.

## Release Protocol

Releases go through a PR — branch protection blocks direct pushes to `main`.

```bash
git checkout main && git pull
git checkout -b release/v<X.Y.Z>
# 1. Bump version in pyproject.toml AND sponsio/__init__.py (must match)
# 2. Move CHANGELOG.md [Unreleased] contents under a new [X.Y.Z] - YYYY-MM-DD section
git commit -am "release: v<X.Y.Z>"
git push -u origin release/v<X.Y.Z>
gh pr create --fill --title "release: v<X.Y.Z>"
# ... CI green, reviewer approves ...
gh pr merge --squash --delete-branch

# After merge:
git checkout main && git pull
git tag v<X.Y.Z> && git push origin v<X.Y.Z>
rm -rf dist/ build/ *.egg-info/
python -m build
twine upload dist/*
```

**PyPI versions are immutable.** Once a version is uploaded it cannot be overwritten — only yanked (which blocks new installs but leaves existing pins working). Double-check the version in all three places (`pyproject.toml`, `sponsio/__init__.py`, `CHANGELOG.md`) before `twine upload`.

## Task Completion Protocol

After completing any non-trivial task:

### Step 1: Read existing docs before updating

Before modifying any doc, read it first to match its format and avoid duplicating content.

| Doc to update | Read first to understand... |
|---------------|----------------------------|
| `README.md` | Pattern table format, architecture diagram style, Quick Start structure |
| `CHANGELOG.md` | Keep-a-Changelog conventions, what's already in `[Unreleased]` |
| `CONTRIBUTING.md` | Existing section layout before adding new contributor-facing guidance |

### Step 2: Update public-facing docs

**README.md** — update if:

- New pattern added → update Pattern Library table (match existing row format).
- New integration → update Architecture diagram + Integrations table.
- New CLI command → update Quick Start / usage sections.
- Public API changed → update code examples.

**CHANGELOG.md** — add an entry under `[Unreleased]` if:

- Feature added → `### Added`
- Bug fixed → `### Fixed`
- Behavior changed → `### Changed`
- Something removed → `### Removed`
- Security fix → `### Security`

Adding a user-visible change without updating `README.md` and `CHANGELOG.md` is an incomplete task.

### Step 3: Output a Task Summary Block

```
## Task Summary
**What changed**: [1-2 sentence description]
**Files modified**: [list of files touched]
**Files added**: [list of new files, if any]
**Tests**: [pass/fail + count]
**Lint**: [clean / N errors]
**Architecture impact**: [none / low / HIGH — explain if HIGH]
**Docs updated**: [README.md / CHANGELOG.md / CONTRIBUTING.md / none]
**Security review**: [N/A / clean / flagged — required for any change touching runtime, integrations, or dependencies]
**Open items**: [anything left undone or needing follow-up]
```

## Compaction Rules

When compacting, ALWAYS preserve:

- The full list of modified files from the current session.
- Any failing test names and error messages.
- Architecture decisions made during the session.
- Which public docs (README, CHANGELOG) were or were not updated.
- Any security flags raised during the session.
- The last Task Summary Block.
