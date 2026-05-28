# Contributing to Sponsio

Thanks for your interest in Sponsio. This doc covers the practical bits:
how to set up a dev environment, where the seams are, and what we ask
of a patch before it lands on `main`.

Anything not covered here (design decisions, invariants, gotchas) lives in [`CLAUDE.md`](CLAUDE.md) and [`docs/concepts/architecture.md`](docs/concepts/architecture.md). Skim those first if you plan to touch the runtime or add a pattern.

---

## Ground rules

- **Apache 2.0.** By submitting a patch you agree your contribution is
  licensed under the repo's [LICENSE](LICENSE).
- **DCO sign-off required.** See [Developer Certificate of
  Origin](#developer-certificate-of-origin) below. Every commit
  must end with a `Signed-off-by:` line. `git commit -s` adds it
  for you.
- **Be kind.** See the [Code of Conduct](CODE_OF_CONDUCT.md).
- **Forks & brand.** Apache 2.0 covers the code. The Sponsio name
  and logo are separate; you may fork freely if you rename, don't
  reuse the logo as your project's brand, and don't imply Sponsio
  Labs endorsement. Email hello@sponsio.dev with questions.
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

By signing off, you certify the [DCO terms](https://developercertificate.org/): you wrote it (or have rights to it) and you're contributing it under the project's open-source licence.

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

Optional. If you'll be touching the TypeScript SDK:

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

High-level map. The full tour is in [`CLAUDE.md`](CLAUDE.md).

```
sponsio/
├── core.py           entrypoint: sponsio.Sponsio()
├── config.py         YAML loader
├── cli.py            sponsio scan|validate|check|serve|demo|patterns
├── formulas/         LTL AST + evaluators
├── models/           Agent, Contract, System, Trace, Event
├── patterns/         deterministic pattern library
├── runtime/          RuntimeMonitor, strategies, terminal reporter
├── generation/       NL → contract (rules + optional LLM)
├── tracer/           event collection + grounding
├── integrations/     LangGraph, MCP, OpenAI, CrewAI, Agents, Vercel, Claude Agent
└── discovery/        docs/traces/code → proposed contracts

ts/packages/sdk/      TypeScript engine + integrations
tests/                pytest
docs/                 user-facing documentation
```

Cross-cutting invariants. These MUST hold across any change; reviewers
will reject PRs that break them:

1. `sponsio/` core has zero external dependencies. Framework deps go in
   `[project.optional-dependencies]`.
2. All framework integrations inherit from `BaseGuard`. No duplicated
   pre-check / post-check logic.
3. Det violations route to `DetBlock` or `EscalateToHuman` only.
   `RuntimeMonitor` enforces this routing. Don't bypass it.
4. The trace is append-only during a session. Rollback is only
   permitted on a hard block, and only in `mode="enforce"`.

---

## Making a change

### 1. Open (or find) an issue first

For anything larger than a typo fix, please open an issue before you start. That gives us a chance to steer, especially for new patterns, new integrations, or changes to the runtime.

Three issue templates exist:

- **Bug Report**: unexpected behavior, crashes, wrong verdicts.
- **Feature Request**: new capability or ergonomic improvement.
- **New Constraint Pattern**: proposal for a new deterministic pattern.

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
| New pattern | `sponsio/patterns/library.py` + `sponsio/generation/dsl_to_contract.py` + `README.md` Pattern Library table + `docs/concepts/contracts.md` |
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
explain *why*, not *what*. The diff already shows the *what*.

### 5. Open the PR

Use the PR template (it auto-populates). Fill in:

- What changed and why, in 1–3 sentences.
- Any invariants or design decisions worth calling out.
- Test plan: how you verified it.
- Docs touched (README, CHANGELOG, etc.), or "N/A" if none apply.
- Linked issue(s).

CI runs on every push: pytest across Python 3.10/3.11/3.12, TS SDK
tests, ruff lint + format check. A green CI is required before review.

---

## Adding a new pattern

The mechanical path for a det pattern, end to end. The worked example is `sanitized_before_sink(source, sanitizer, sink)`: after an untrusted source is read, the sanitizer must run before the sink does.

### 1. Implement the formula

Add a factory to [`sponsio/patterns/library.py`](sponsio/patterns/library.py) that returns a `DetFormula`. Use existing atoms (`called`, `count`, `arg_has`, `arg_paths_within`, ...) when you can.

```python
def sanitized_before_sink(
    source: str,
    sanitizer: str,
    sink: str,
    desc: str = "",
) -> DetFormula:
    """After an untrusted source is read, require sanitization before sink use."""
    _ensure_distinct(source, sanitizer, pattern="sanitized_before_sink",
                     arg_a="source", arg_b="sanitizer")
    _ensure_distinct(sanitizer, sink, pattern="sanitized_before_sink",
                     arg_a="sanitizer", arg_b="sink")
    _ensure_distinct(source, sink, pattern="sanitized_before_sink",
                     arg_a="source", arg_b="sink")

    formula = G(Implies(
        _called(source),
        X(_forbidden_until(_called(sanitizer), _called(sink))),
    ))
    return DetFormula(
        formula=formula,
        desc=desc or f"`{source}` must be sanitized by `{sanitizer}` before `{sink}`",
        pattern_name="sanitized_before_sink",
        args=(source, sanitizer, sink),
    )
```

Two conventions matter:
- **`pattern_name`** must match the function name. The pattern store, NL parser, and `customized:` overrides all key off this string.
- **`args=(...)`** captures the raw call arguments so the pattern store can round-trip them. Without it, `sponsio packs`, `sponsio explain`, and dashboard exports lose the user's original parameter values.

### 2. Add atom extraction (only if you need a new atom)

Most patterns compose existing atoms. Skip this step if yours does. If you do need a new atom, add it to [`sponsio/tracer/grounding.py`](sponsio/tracer/grounding.py) with extraction logic, and register it in `_CONTENT_PREDICATES` if it takes parameters (regex, prefixes, ...).

`sanitized_before_sink` only uses `called(...)`, so step 2 is N/A.

### 3. Register the pattern in the text DSL

Add it to [`sponsio/generation/dsl_to_contract.py`](sponsio/generation/dsl_to_contract.py) so users can express the pattern in the Sponsio contract DSL (the small set of phrasings the rule-based parser recognizes). Free-form NL beyond the DSL goes through the optional LLM extractor, not new regex rules here.

```python
# top-of-file import
from sponsio.patterns.library import (
    ...,
    sanitized_before_sink,
)

# _PATTERN_REGISTRY (structured form: `pattern: sanitized_before_sink`)
_PATTERN_REGISTRY = {
    ...,
    "sanitized_before_sink": sanitized_before_sink,
}

# NL trigger rule (regex list + pattern name + expected arg count)
(
    [
        r"sanit(?:ize|ation).*before",
        r"(?:source|input).*sanitizer.*sink",
    ],
    "sanitized_before_sink",
    3,
),

# In the pattern dispatch (after action extraction):
if pattern_name == "sanitized_before_sink":
    if len(actions) < 3:
        return _build_error(
            nl_line, "sanitized_before_sink",
            "sanitized_before_sink needs source, sanitizer, and sink actions",
        )
    formula = sanitized_before_sink(actions[0], actions[1], actions[2], desc=text)
```

### 4. Test both paths

Two test files. Both are required.

[`tests/test_patterns.py`](tests/test_patterns.py) covers formula correctness:

```python
def test_sanitized_before_sink_requires_sanitizer_after_source():
    af = sanitized_before_sink("web_fetch", "sanitize_input", "send_email")
    assert af.pattern_name == "sanitized_before_sink"
    assert evaluate(af.formula, [_called("web_fetch"), _called("send_email")]) is False
    assert evaluate(
        af.formula,
        [_called("web_fetch"), _called("sanitize_input"), _called("send_email")],
    )
```

[`tests/test_nl_parser.py`](tests/test_nl_parser.py) covers the NL round-trip:

```python
def test_sanitized_before_sink(self):
    r = parse_nl_rule_based(
        "`web_fetch` input must be sanitized by `sanitize_input` before `send_email`"
    )
    assert r.ok
    assert r.pattern_name == "sanitized_before_sink"
```

For end-to-end (NL → guard → block / allow), [`tests/test_pattern_e2e.py`](tests/test_pattern_e2e.py) is the right home.

### 5. Mirror in TS (or document the gap)

The TS engine lives at [`ts/packages/sdk/src/core/patterns.ts`](ts/packages/sdk/src/core/patterns.ts). If your pattern composes atoms TS already grounds (`called`, `count`, `arg_has`, `arg_field_has`, `arg_paths_within`), mirror the factory there and add a TS test in `ts/packages/sdk/src/__tests__/patterns.test.ts`.

If your pattern uses an atom TS does not ground (LLM-observation atoms, data-flow predicates), add a row to [`docs/reference/ts-sdk-parity.md`](docs/reference/ts-sdk-parity.md) so TS users know the pattern is Python-only.

### 6. Document

Three places. All required.

| File | What to add |
|---|---|
| [`docs/reference/patterns.md`](docs/reference/patterns.md) | Row in the appropriate category table (Safety / Compliance / Operational / Approval and audit / ...) with NL example and one-line "what it enforces". |
| [`README.md`](README.md) | Pattern Library mention if the pattern is high-leverage enough to feature. Ask in the PR. |
| [`CHANGELOG.md`](CHANGELOG.md) | Entry under `[Unreleased]` `### Added` ("New pattern: `sanitized_before_sink(source, sanitizer, sink)` for taint-tracking gates."). |

### Checklist

Before opening the PR:

- [ ] Factory in `sponsio/patterns/library.py` returns `DetFormula` with correct `pattern_name` and `args`.
- [ ] Pattern registered in `dsl_to_contract.py` (import, registry, dispatch).
- [ ] Formula test in `tests/test_patterns.py`.
- [ ] NL test in `tests/test_nl_parser.py`.
- [ ] TS mirror landed, OR row added to `ts-sdk-parity.md`.
- [ ] Row added to `docs/reference/patterns.md`.
- [ ] `[Unreleased]` entry in `CHANGELOG.md`.
- [ ] `pytest -v` and `ruff check sponsio/ tests/` both green.

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
- **`docs/`** for anything that's already been written up. Please
  check before filing.

---

## What belongs in this repo (and what doesn't)

**Ship with open source:** user-facing guides, contract and architecture reference, and design notes (e.g. [`docs/cost-based-thresholds.md`](docs/cost-based-thresholds.md)).

**Keep out of the public tree** (or redact before publishing):

- Roadmaps, launch checklists, and status dashboards (`STATUS.md`, `PLAN.md`, `LAUNCH_*.md`). They go stale and can imply commitments.
- Narration scripts for a specific demo or video (`demo-video-script.md` is gitignored for that reason), not end-user documentation.
- Benchmark result tables and eval lab notebooks. Headline figures may be published in the root [`README.md`](README.md#benchmarks); raw tables and model-by-model numbers stay private. Paths under `docs/` that match those names are in `.gitignore`; never `git add -f`.
- Internal agent/project notes under `agent_docs/` or similar.
- Anything with real customer names, private URLs, API keys, or unreleased product detail.

**Runtime data**: the whole `data/` tree is local-only except the stub README; see [`data/README.md`](data/README.md).

---

Thanks for contributing.
