# Contributing to Sponsio

Thanks for your interest in Sponsio. This doc covers the practical bits:
how to set up a dev environment, where the seams are, and what we ask
of a patch before it lands on `main`.

Anything not covered here — design decisions, invariants, gotchas —
lives in [`CLAUDE.md`](CLAUDE.md) and [`docs/concepts/architecture.md`](docs/concepts/architecture.md).
Skim those first if you plan to touch the runtime or add a pattern.

---

## Ground rules

- **Apache 2.0.** By submitting a patch you agree your contribution is
  licensed under the repo's [LICENSE](LICENSE).
- **DCO sign-off required.** See [Developer Certificate of
  Origin](#developer-certificate-of-origin) below — every commit
  must end with a `Signed-off-by:` line. `git commit -s` adds it
  for you.
- **Be kind.** See the [Code of Conduct](CODE_OF_CONDUCT.md).
- **Trademarks.** Apache 2.0 covers the code. The Sponsio name and
  logo are separate — see [BRAND.md](BRAND.md) for what you can do
  without asking.
- **Small PRs beat big PRs.** One concern per PR. If a change is
  unavoidably large, split it into a stack and link the commits.
- **Tests are not optional** for any change that touches `sponsio/`
  or `ts/packages/sdk/`. Docs-only and CI-only changes are exempt.

---

## Developer Certificate of Origin

Sponsio uses the [Developer Certificate of Origin (DCO)](https://developercertificate.org/)
v1.1 to track contribution provenance. We do **not** require a CLA;
the DCO is a lightweight per-commit attestation that you have the
right to contribute the code under Apache 2.0.

To sign off your commits, add `-s` to `git commit`:

```bash
git commit -s -m "feat(runtime): your change"
```

This appends a line like:

```
Signed-off-by: Your Name <your.email@example.com>
```

By signing off, you certify the [DCO terms](https://developercertificate.org/) —
in short: you wrote it (or have rights to it) and you're contributing
it under the project's open-source licence.

If you forget the sign-off, amend the commit with `git commit --amend
-s` and force-push to your branch. CI will block PRs that contain
unsigned commits.

---

## Dev environment

Python 3.10+ is required. 3.12 is what CI runs on.

```bash
git clone https://github.com/SponsioLabs/Sponsio.git
cd Sponsio
pip install -e ".[all]"          # core + every optional integration
pip install ruff pytest pytest-cov
```

Optional — if you'll be touching the TypeScript SDK:

```bash
cd ts/packages/sdk && npm install
```

Run the full suite before you start, to make sure your environment is
green:

```bash
pytest -v                        # 789+ tests, ~30s
ruff check sponsio/ tests/       # lint
ruff format --check sponsio/ tests/
```

If `ruff` is not on your `PATH`, `python -m ruff ...` works the same.

---

## Repo layout

High-level map — the full tour is in [`CLAUDE.md`](CLAUDE.md).

```
sponsio/
├── core.py           entrypoint: sponsio.Sponsio()
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

ts/packages/sdk/      TypeScript engine + integrations
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
ruff check sponsio/ tests/
ruff format sponsio/ tests/
```

Branch naming is loose; these prefixes help reviewers scan:
`feat/`, `fix/`, `docs/`, `refactor/`, `perf/`, `test/`, `ci/`.

### 3. Update docs

If you added a user-visible behavior, the task isn't done until you've
touched these:

| Change | Update |
|--------|--------|
| New pattern | `sponsio/patterns/library.py` + `sponsio/generation/nl_to_contract.py` + `README.md` Pattern Library table + `docs/concepts/contracts.md` |
| New integration | `sponsio/integrations/` + `README.md` Integrations table + `docs/integrations/index.md` |
| New CLI subcommand | `sponsio/cli.py` + `docs/reference/cli.md` + `README.md` |
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
   an entry in `docs/concepts/contracts.md` with an NL example and a "what it
   enforces" sentence. Add to the `[Unreleased]` `### Added` block in
   `CHANGELOG.md`.

Stochastic atoms (LLM-judge evaluators) are part of [Sponsio
Cloud](docs/reference/oss-scope.md#in-sponsio-cloud-commercial--pip-install-sponsiocloud);
the OSS engine ships an empty sto registry plus a `Judge` extension
point. Patches that add new sto evaluators land in the cloud repo
and are not accepted here. Patches that improve the OSS extension
point are very welcome.

---

## Adding a new integration

1. Create `sponsio/integrations/<framework>.py` with a `Guard` class
   that inherits from `BaseGuard`.
2. Implement only the framework-specific interception. `BaseGuard`
   already owns pre-check, post-check, rollback, trace management,
   contract compilation, mode resolution, and session logging.
3. Register the framework name in `sponsio/core.py` so
   `sponsio.Sponsio(framework="<name>")` picks up the new class.
4. Add an optional dep to `[project.optional-dependencies]` in
   `pyproject.toml`.
5. Update the integrations table in `README.md` and
   `docs/integrations/index.md`.

---

## Reporting security issues

Do **not** open a public issue for security vulnerabilities. Instead,
email `security@sponsio.dev` with a description and a reproduction
path. We'll acknowledge within 72 hours and coordinate disclosure.

---

## Getting help

- **GitHub Discussions** for open-ended questions and ideas.
- **GitHub Issues** for bugs and concrete feature requests.
- **`docs/`** for anything that's already been written up — please
  check before filing.

---

## What belongs in this repo (and what doesn't)

**Ship with open source:** user-facing guides, contract and architecture reference, design notes (e.g. [`docs/cost-based-thresholds.md`](docs/cost-based-thresholds.md)), and sto calibration concepts.

**Keep out of the public tree** (or redact before publishing):

- Roadmaps, launch checklists, and status dashboards (`STATUS.md`, `PLAN.md`, `LAUNCH_*.md`) — they go stale and can imply commitments.
- Narration scripts for a specific demo or video (`demo-video-script.md` is gitignored for that reason) — not end-user documentation.
- Benchmark result tables and eval lab notebooks — headline figures may be published in the root [`README.md`](README.md#benchmarks); raw tables and model-by-model numbers stay private. Paths under `docs/` that match those names are in `.gitignore`; never `git add -f`.
- Internal agent/project notes under `agent_docs/` or similar.
- Anything with real customer names, private URLs, API keys, or unreleased product detail.

**Runtime data** — the whole `data/` tree is local-only except the stub README; see [`data/README.md`](data/README.md).

---

Thanks for contributing.
