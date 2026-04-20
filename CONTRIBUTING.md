# Contributing to Sponsio

Thanks for your interest in Sponsio. This doc covers the practical bits:
how to set up a dev environment, where the seams are, and what we ask
of a patch before it lands on `main`.

Anything not covered here — design decisions, invariants, gotchas —
lives in [`CLAUDE.md`](CLAUDE.md) and [`docs/architecture.md`](docs/architecture.md).
Skim those first if you plan to touch the runtime or add a pattern.

---

## Ground rules

- **Apache 2.0.** By submitting a patch you agree your contribution is
  licensed under the repo's [LICENSE](LICENSE).
- **Be kind.** See the [Code of Conduct](CODE_OF_CONDUCT.md).
- **Small PRs beat big PRs.** One concern per PR. If a change is
  unavoidably large, split it into a stack and link the commits.
- **Tests are not optional** for any change that touches `sponsio/`,
  `api/`, or `ts-sdk/`. Docs-only and CI-only changes are exempt.

---

## Dev environment

Python 3.10+ is required. 3.12 is what CI runs on.

```bash
git clone https://github.com/SponsioLabs/Sponsio.git
cd Sponsio
pip install -e ".[all]"          # core + every optional integration
pip install ruff pytest pytest-cov
```

Optional — if you'll be touching the dashboard or TypeScript SDK:

```bash
cd web && npm install            # React frontend
cd ../ts-sdk && npm install      # TypeScript engine
```

Run the full suite before you start, to make sure your environment is
green:

```bash
pytest -v                        # 789+ tests, ~30s
ruff check sponsio/ api/ tests/  # lint
ruff format --check sponsio/ api/ tests/
```

If `ruff` is not on your `PATH`, `python -m ruff ...` works the same.

---

## Repo layout

High-level map — the full tour is in [`CLAUDE.md`](CLAUDE.md).

```
sponsio/
├── core.py           entrypoint: sponsio.init()
├── config.py         YAML loader
├── cli.py            sponsio scan|validate|check|serve|demo|patterns
├── formulas/         LTL AST + evaluators
├── models/           Agent, Contract, System, Trace, Event
├── patterns/         det patterns + sto catalog
├── runtime/          RuntimeMonitor, strategies, terminal reporter
├── generation/       NL → contract (rules + optional LLM)
├── tracer/           event collection + grounding
├── integrations/     LangGraph, MCP, OpenAI, CrewAI, Agents, Vercel, Claude Agent
└── discovery/        docs/traces/code → proposed contracts

api/                  FastAPI dashboard backend
web/                  React frontend
ts-sdk/               TypeScript engine + integrations
tests/                pytest
docs/                 user-facing documentation
```

Cross-cutting invariants — these MUST hold across any change; reviewers
will reject PRs that break them:

1. `sponsio/` core has zero external dependencies. Framework deps go in
   `[project.optional-dependencies]`.
2. All framework integrations inherit from `BaseGuard`. No duplicated
   pre-check / post-check logic.
3. Det violations route to `DetBlock` or `EscalateToHuman` only.
   Sto violations route to `RetryWithConstraint` or `RedirectToSafe`
   only. `RuntimeMonitor` enforces this separation — don't bypass it.
4. The trace is append-only during a session. Rollback is only
   permitted on a hard block, and only in `mode="enforce"`.

---

## Making a change

### 1. Open (or find) an issue first

For anything larger than a typo fix, please open an issue before you
start. That gives us a chance to steer — especially for new patterns,
new integrations, or changes to the runtime.

Three issue templates exist:

- **Bug Report** — unexpected behavior, crashes, wrong verdicts.
- **Feature Request** — new capability or ergonomic improvement.
- **New Constraint Pattern** — proposal for a new det or sto pattern.

### 2. Branch, write, test

```bash
git checkout -b feat/short-descriptive-name
# ... make your change ...
pytest -v
ruff check sponsio/ api/ tests/
ruff format sponsio/ api/ tests/
```

Branch naming is loose; these prefixes help reviewers scan:
`feat/`, `fix/`, `docs/`, `refactor/`, `perf/`, `test/`, `ci/`.

### 3. Update docs

If you added a user-visible behavior, the task isn't done until you've
touched these:

| Change | Update |
|--------|--------|
| New pattern | `sponsio/patterns/library.py` + `sponsio/generation/nl_to_contract.py` + `README.md` Pattern Library table + `docs/contracts.md` |
| New integration | `sponsio/integrations/` + `README.md` Integrations table + `docs/integrations.md` |
| New CLI subcommand | `sponsio/cli.py` + `docs/cli.md` + `README.md` |
| Public API change | `CHANGELOG.md` under `[Unreleased]` with `### Changed` or `### Added` |
| Bug fix | `CHANGELOG.md` under `[Unreleased]` with `### Fixed` |

We follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) for
`CHANGELOG.md` and [SemVer](https://semver.org/) for versioning.

### 4. Commit messages

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(runtime): shadow mode — observe contracts without blocking
fix(patterns): rate_limit off-by-one on sliding window
docs: clarify assume/enforce semantics in contracts.md
refactor(integrations): consolidate pre_check into BaseGuard
```

Scope is optional but encouraged. The body (when present) should
explain *why*, not *what* — the diff already shows the *what*.

### 5. Open the PR

Use the PR template (it auto-populates). Fill in:

- What changed and why, in 1–3 sentences.
- Any invariants or design decisions worth calling out.
- Test plan: how you verified it.
- Docs touched (README, CHANGELOG, etc.) — or "N/A" if none apply.
- Linked issue(s).

CI runs on every push: pytest across Python 3.10/3.11/3.12, TS SDK
tests, ruff lint + format check. A green CI is required before review.

---

## Adding a new pattern

The mechanical path, end-to-end, for a det pattern:

1. **Implement the formula.** Add a function to
   `sponsio/patterns/library.py` that returns a `DetFormula` (an LTL
   `Formula` + NL description). Use the existing atom vocabulary when
   you can; register new atoms via `register_atom()` if you must.
2. **Wire up NL parsing.** Add the keyword trigger + argument
   extraction to `sponsio/generation/nl_to_contract.py` so users can
   write the pattern in natural language.
3. **Test both paths.** Add cases to
   `tests/test_patterns_library.py` (formula correctness) and
   `tests/test_nl_to_contract.py` (NL round-trip).
4. **Document it.** Add a row to the pattern table in `README.md` and
   an entry in `docs/contracts.md` with an NL example and a "what it
   enforces" sentence. Add to the `[Unreleased]` `### Added` block in
   `CHANGELOG.md`.

For a sto pattern, swap step 1 for a new evaluator in
`sponsio/patterns/sto_catalog.py` and step 2 for the sto keyword list.
The test path is `tests/test_sto_*.py`.

---

## Adding a new integration

1. Create `sponsio/integrations/<framework>.py` with a `Guard` class
   that inherits from `BaseGuard`.
2. Implement only the framework-specific interception. `BaseGuard`
   already owns pre-check, post-check, rollback, trace management,
   contract compilation, mode resolution, and session logging.
3. Register the framework name in `sponsio/core.py` so
   `sponsio.init(framework="<name>")` picks up the new class.
4. Add an optional dep to `[project.optional-dependencies]` in
   `pyproject.toml`.
5. Add a runnable example under `examples/integrations/<framework>/`.
6. Update the integrations table in `README.md` and
   `docs/integrations.md`.

---

## Reporting security issues

Do **not** open a public issue for security vulnerabilities. See
[`SECURITY.md`](SECURITY.md) for the disclosure process — the preferred
channel is a private [GitHub Security Advisory](https://github.com/SponsioLabs/Sponsio/security/advisories/new),
with email as a fallback. We acknowledge within 48 hours.

---

## Getting help

- **GitHub Discussions** for open-ended questions and ideas.
- **GitHub Issues** for bugs and concrete feature requests.
- **`docs/`** for anything that's already been written up — please
  check before filing.

Thanks for contributing.
