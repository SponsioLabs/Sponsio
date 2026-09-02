# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Granular per-release notes (commits, PRs, individual fix lines) live in
[GitHub Releases](https://github.com/SponsioLabs/Sponsio/releases). This
file keeps the high-level shape: what was added, what changed, what
broke.

---

## [Unreleased]

---

## [0.2.0a11]: 2026-09-02

Two more rules that looked armed and were not, and the answer to the one
question an audit trail exists for.

### Fixed

- **A run records which rulebook version it enforced.** Every ingested
  run showed no book at all, so the console could not say which rules
  were in force when it ran; replays showed a version because the server
  assigns theirs. The mechanism was already complete and read one
  source: the env var set while resolving `config="sponsio://project"`.
  An app that pulls the rulebook itself and hands `Sponsio()` the
  resulting file — the shape the console's own wiring instructions
  produce — never went through it. The stamp is the checkout's first
  line, which the YAML parse drops as a comment; `load_config` keeps it
  now and the bridge sends it. A hand-written yaml still records
  nothing, which is honest.

- **The last agent in a checkout list lost its version.** The list is
  bracketed, so `_own_book` returned `zulu@v9]`, which parses as no
  version — a book nobody published. It only bit the agent that sorted
  last.

- **`tool `bash` command must not contain `rm -rf`` never fired.** The
  NL parser took the banned shape as the FIELD NAME, building
  `arg_field_has('bash', 'rm -rf', 'rm -rf')` — a rule asking whether an
  argument *named* `rm -rf` contains `rm -rf`. No such argument exists.
  The field is read from the sentence's cue word now. `query` was also
  missing from the cue list, so `` tool `run_sql` query must not contain
  `DROP` `` did not parse at all.

### Changed

- **TypeScript refuses to enforce a book it could not parse.** A
  contract that does not parse is a rule that is not there. Python
  raises; TypeScript logged one line and ran on, so a TS agent enforced
  a book with holes in it and reported success. Enforce mode throws now,
  naming every contract it could not read. Observe still runs, and says
  the rules are NOT armed.

- **`arg_blacklist`, `requires_permission` and `segregation_of_duty`
  parse in TypeScript**, using Python's own phrasings. Measured over
  fifteen realistic sentences TypeScript went from 11 to 13, level with
  Python. Note that TypeScript already supported the structured
  `pattern:` form for the whole library — the gap was only in NL
  strings.

---

## [0.2.0a10]: 2026-09-02

Found by running seven agent applications against the stack — a support
desk, an ops bot, a load harness, one that passes hostile arguments, a
TypeScript agent — rather than by adding tests to code already believed
correct. Three of these are the same shape: a rule that loads, arms,
shows in the console, and does not do what it says.

### Fixed

- **`scope_limit` was a string prefix, not confinement.** A rule reading
  "writes stay under `/safe`" allowed `/safe/../../root/.ssh/id_rsa`,
  because the check was `path.startswith(prefix)` — the oldest escape
  there is, against a rule whose entire purpose is confinement. The same
  line also counted `/safeguard/evil.txt` as inside `/safe`: a prefix of
  the text is not a prefix of the path. Paths are normalised now and
  containment requires a segment boundary. Identical fix in TypeScript,
  which had the identical line.

- **`never call A after B` compiled the opposite rule.**
  `no_reversal(commitment, contradiction)` takes the committing action
  first, and English puts it last whenever the sentence opens with the
  prohibition. The contract appeared in the console carrying the user's
  own sentence while checking the reverse, and passed on exactly the
  sequence it was written to stop. Word order decides by position now,
  not by a list of fixed phrases. TypeScript had the same inversion with
  no swap at all.

- **`drop table users` walked past `dangerous_sql_verbs`.** The preset
  matched its verbs case-sensitively, and SQL keywords do not have a
  case. Word boundaries went on at the same time, so `DELETE` no longer
  matches the word "deleted" in a comment or a column named `drop_date`.

- **An enforcing run could start with a rule missing.** Strict compile
  read `defaults.mode` and nothing else, so a project that said enforce
  in any of the four other places the runtime honors dropped the broken
  contract, printed one `UserWarning`, and enforced the rest. Strictness
  follows the effective mode now, and a contract carrying its own
  `mode: enforce` raises either way.

- **`sponsio doctor` green-ticked a config that cannot load**, because it
  validated the schema and never compiled the contracts. It compiles them
  now and grades by the project's mode. Its `Runtime mode` line also read
  two of the three places a yaml can say `enforce`, so a blocking project
  was reported as `observe (shadow — safe default)`.

- **`sponsio doctor` failed on a minimal install.** `find_spec` on a
  dotted name imports the parent package and raises when it is absent, so
  the check whose only job is to report what is missing crashed on a
  machine with no `google` — which is what `pip install sponsio` gives
  you. Doctor opened with a red mark on a healthy install.

- **A pattern given the wrong number of arguments** raised a bare
  `TypeError` from inside the compiler, naming the factory's missing
  parameter and nothing about the contract. Errors now name the agent,
  the contract's own description, what it passed, and the signature.

- **`attach(guard, agents=["name"])`** — the obvious reading of the
  parameter — raised `string indices must be integers` from inside the
  bridge. A bare name is the shorthand now.

### Changed

- A contract row sent to a console carries the `pattern` and `args` it
  was built from, so a run whose agent has no published book is no longer
  offered the rules it was just checked against.
- `tool_allowlist` reads the same in both runtimes; TypeScript's NL
  parser understands `scope_limit`, which it did not before.
- CI runs the TypeScript half of `tests/cross_language/scenarios.json`.
  The step was named "Run cross-language tests" and ran the unit tests,
  so the shared file gated Python alone — which is how two of the
  inversions above survived.

### Removed

- The SMT hooks. `base.py` accepted an `smt=` config and imported
  `sponsio.integrations.smt_middleware`; `CloudClient.smt` imported
  `sponsio.cloud.smt`. Neither module is in this build, so opting in
  raised `ModuleNotFoundError` at construction while the public docstring
  documented the feature. They return with the modules that make them
  work.

---

## [0.2.0a9]: 2026-09-02

Follow-up to `0.2.0a8`, all of it found by running a real multi-agent app
against a console that already held other agents' books.

### Added

- **An agent's first run puts its book in the cloud.** Nothing pushed
  before: the checkout only pulls, and the one push an app tends to own
  fires on a 404 — which stops happening the moment the project holds any
  sibling's book. A project's second agent onward ran on rules the console
  had never seen, so every screen built to show what governs an agent had
  nothing to show. When the checkout does not carry the agent you named
  and `./sponsio.yaml` does, that book now goes up: **only that agent's
  block**, as a **draft**, once — the next checkout carries it, and an
  identical push is a server-side no-op. `SPONSIO_NO_AUTO_PUSH=1` turns it
  off; a failed upload prints the manual command and never stops the run.

### Fixed

- **A broken `./sponsio.yaml` says so.** The fallback swallowed the load
  error, so a typo surfaced as "no ./sponsio.yaml defines it" — sending
  the user to look for a file sitting in the working directory defining
  exactly that agent. Both error paths now quote the parse failure.

### Changed

- **`docs/reference/config-yaml.md` names all five places the mode comes
  from and which one wins.** A contract's own `mode:` beats the mode your
  code passes, so a rule armed in the console stops calls inside a run
  started with `mode="observe"`. `@sponsio/sdk` reads `runtime.mode` only,
  so `defaults.mode` governs the Python runtime and not the TypeScript one.
- Every install line in the READMEs, QUICKSTART and `docs/` passes
  `--pre`. Without it pip resolves to `0.1.1`, the last stable — a build
  that predates the cloud console, the evidence lane and the current CLI.

---

## [0.2.0a8]: 2026-09-01

The output lane ships. Alongside the action lane — every tool call
checked before it executes — a run's *claims* are now checked too: typed
assertions compared against an authority by deterministic comparators,
with no LLM anywhere in the hot path.

Two of the fixes below were found by running a real multi-agent app
against a console that already had other agents in it, which is the
configuration that broke.

### Added

- **Evidence middleware.** `observe_llm_call` extracts the typed claims a
  model turn makes, checks each against its authority, and folds the
  verdict into the trace beside the tool calls. Deterministic
  comparators; a claim whose source cannot answer is reported as such
  rather than guessed at.

### Fixed

- **A project's second agent could not run.** A cloud checkout carries
  the whole project, so when a new agent joined a project that already
  had books, the checkout answered `200` with the *siblings'* books —
  nothing 404'd, nothing got pushed, and the agent's own rules sat on
  disk unused while the run died on "agent not found". The local
  `sponsio.yaml` fallback now applies whenever that file defines the
  agent, not only when the config was a `sponsio://` ref. The
  multi-agent error also stopped telling a caller who passed an
  `agent_id` to pass an `agent_id`.
- **Multi-agent runs lost their claim verdicts.** `attach(auto=False)`
  returned before wiring the output lane. `auto` says who attributes the
  tool steps — a statement about the *action* lane — and never meant
  "drop my evidence", but a multi-agent run (the only reason to pass it)
  rendered as a clean trace while the model stated something false.
- **A cloud checkout never rebinds an explicit agent to a sibling.**
  Running under another agent's rules and reporting *as* that agent had
  been a `UserWarning`; it is now an error with the fix in hand.
- **A 32-bit run id silently merged two runs.** Bridge run ids are
  64-bit.
- **A long run uploaded the run squared, then went silent.** The bridge
  coalesces sends under an interval and a byte-rate ceiling.
- **A run names its own book**, not the whole project's checkout stamp.
- Singular/plural agreement in `rate_limit` and `bounded_retry` labels
  ("limited to 1 invocation"), Python and TypeScript in step.

### Changed

- Enforcement routes through a canonical stopping set behind
  `stop_original`, so blocks, redirects and escalations answer one
  question the same way.
- Shadow-mode assumption spans record what actually happened.

### Fixed (carried from `0.2.0a3`)

- **Ordered comparisons now agree across Python and TypeScript on numeric
  string arguments (#108).** Raw tool arguments are grounded as strings, so
  a naturally-written numeric guard like `Not(Gt(ArgValue("pay","amount"),
  Const(1000)))` reached the evaluator as `compare("gt", "5000", 1000)`.
  Python raised `TypeError` and fell through to `False` (guard held → the
  `5000` payment was **allowed**) while TypeScript coerced and returned
  `true` (guard violated → **blocked**) — the same contract failed *open* on
  one runtime and *closed* on the other. Both runtimes now coerce a
  plain-numeric string operand to a number for `Lt`/`Le`/`Gt`/`Ge` when the
  other operand is numeric, so numeric guards compare numerically and fail
  **closed** identically. Non-numeric strings stay incomparable (`False`),
  and `Eq` is unchanged.
- **`@sponsio/sdk` is now edge-runtime safe.** Marked the package
  `sideEffects` (narrowed to the CLI entry) so bundlers can tree-shake
  the Node-only YAML/config-loading path out of edge bundles (Cloudflare
  Workers), complementing the `createRequire` deferral in `0.2.0a3`.
- **Trace mining fails open when its extension isn't bundled.**
  `CodeAnalyzer` imported `TraceMiner` unguarded, crashing the
  trace-mining path with `ModuleNotFoundError` in builds without the
  optional `trace_mining` extension; it now degrades to "no contracts
  mined", matching the other call sites.

### Changed

- Added an explicit `[tool.ruff]` config to `pyproject.toml` so local
  lint matches CI, and synced `docs/reference/cli.md` with the real CLI
  surface (`onboard`/`serve`/`daemon`/`cursor` now documented).
- The CLI now centers on code and policy scanning. `sponsio scan` reads
  source code and policy docs; `sponsio check --trace` and `sponsio eval`
  still replay traces. Trace-derived contract mining (the `sponsio
  refresh` command and `sponsio scan --trace`) is no longer part of this
  distribution.

---

## [0.2.0a3]: 2026-06-08

Security-relevant fix on top of `0.2.0a2`. If you are on `0.2.0a2` and
use any adapter OTHER than LangGraph with a `redirect_to_safe`
contract, you should upgrade.

### Fixed

- **`redirect_to_safe` now fails closed in non-LangGraph adapters**
  (`sponsio/integrations/base.py`, `crewai.py`, `agents.py`,
  `claude_agent.py`, `google_adk.py`, `vercel_ai.py`, `mcp.py`).
  Previously, a `redirect_to_safe` violation returned
  `action="redirected"` with `blocked=False`, and every adapter
  except LangGraph gated on `if check.blocked` — meaning the
  guard rolled the unsafe call out of the trace AND THEN the
  adapter executed the original unsafe tool anyway. A new
  `CheckResult.stop_original` property (`blocked OR redirected`)
  is wired through every non-substituting adapter, so a redirect
  now refuses the unsafe call. LangGraph still branches on
  `redirected` first and performs the substitution. The Cursor
  adapter takes a separate `evaluate_event` path and is tracked
  as follow-up. Regression test added at
  `tests/test_redirect_to_safe.py`.

- **TS `Eq` now matches Python `==` for composite values**
  (`ts/packages/sdk/src/core/evaluator.ts`). The previous `===`
  comparison was reference equality for arrays and objects, so
  `Eq(ArgValue("tool", "field"), CtxValue("expected"))` on
  list- or object-valued args could pass in Python and fail in
  TS on the same trace. New `valuesEqual` does element-/key-wise
  deep comparison; parity test added at
  `ts/packages/sdk/src/__tests__/parity.test.ts`.

- **TS SDK no longer crashes on Cloudflare Workers** at import
  time (`ts/packages/sdk/src/core/config-loader.ts`,
  `pack-loader.ts`). The eager top-level
  `createRequire(import.meta.url)` threw when
  `import.meta.url` was undefined (Workers, some edge runtimes).
  Now built lazily on first YAML load with a
  `?? "file:///sponsio-noop.js"` fallback, so a Worker bundle
  that never loads YAML never calls `createRequire`.

- **Suite-wide pytest setup errors cleared up**
  (`tests/conftest.py`). The autouse rich-style cache reset
  invoked `isinstance(obj, Style)` on every live object; lazy
  proxies from optional SDK imports (notably OpenAI's
  `sounddevice`-pulling submodules) raised from their
  `__class__` getter and errored 1684 of 2312 test setups. Now
  swallows introspection failures.

### Changed

- **`filter_tools` documents O(candidates × trace_length)
  re-grounding cost**
  (`sponsio/integrations/base.py`).
- **`workflow_step` documents the end-of-trace weak-next
  vacuity caveat** for batch verify / replay paths
  (`sponsio/patterns/library.py`).
- **`Var.__eq__`, `_warned_missing_vars`, and `arg_value`
  retention** all get explicit footgun notes
  (`sponsio/formulas/evaluator.py`, `sponsio/formulas/formula.py`,
  `sponsio/tracer/grounding.py`).
- **Test infrastructure** moves off the deprecated
  `asyncio.get_event_loop().run_until_complete` to `asyncio.run`
  (`tests/test_claude_agent_integration.py`).

### Documentation

- Several docstrings repaired (artifacts left over from the v0.2
  em-dash sweep, mostly first-line typos that surfaced in
  `help()` and IDE hover popups).

### Compatibility

No breaking API changes. The `CheckResult` shape is unchanged
(`stop_original` is a new derived property, computed from
existing fields). Existing tests against `blocked` /
`redirected` still hold.

### Credits

Thanks to @donalddellapietra for the review pass that surfaced
the fail-open bug, the TS `Eq` parity gap, and the Worker
runtime crash. PR
[#78](https://github.com/SponsioLabs/Sponsio/pull/78).

---

## [0.2.0a2]: 2026-06-07

### Added

- **`Term` abstraction in the formula AST** (`sponsio/formulas/formula.py`).
  The arithmetic comparison family (`Eq`, `Le`, `Lt`, `Ge`, `Gt`) now
  accepts any `Term`, not just `Var` or `Const`. Four new term subclasses
  unlock contracts that compare runtime values against each other:
  - `ArgValue(tool, field)`: raw value of `args[field]` when the current
    event is a call to `tool`.
  - `CtxValue(key)`: raw value of an externally pushed context fact
    (`guard.observe_context`).
  - `ArgLength(tool, field)`: `len(args[field])` shorthand.
  - `UnaryFn(fn, term)`: apply a Python callable to another term.

  `Var` and `Const` become `Term` subclasses, so their existing
  counter-style semantics (default `0` for missing, numeric-only
  coercion) are preserved. `ArithExpr` is now an alias of `Term` so
  existing type hints keep working.

- **`workflow_step(trigger, next_action)` pattern**
  (`sponsio/patterns/library.py`). Prescriptive counterpart to the
  block-style patterns: when `trigger` holds at the current event, the
  next event must satisfy `next_action`. Both arguments are arbitrary
  atoms, so the same factory covers tool-ordering, ctx-driven
  remediation, and arg-conditional follow-ups. Compiles to
  `G(trigger -> X(next_action))`.

- **Five benchmark contract libraries**
  (`sponsio/contracts/benchmark/*.yaml`). Hand-curated YAML libraries
  that reproduce Sponsio's published benchmark numbers on RedCode-Exec,
  ODCV-Bench, τ²-bench, AgentDojo, and SWE-bench. Loadable via
  `include: [sponsio:benchmark/<name>]` like a capability pack but kept
  separate in intent (benchmark-reproduction artefacts, not auto-selected
  by `onboard`). Documented in
  [`docs/reference/benchmark-libraries.md`](docs/reference/benchmark-libraries.md).

- **NL DSL extensions for the new primitives**
  (`sponsio/generation/dsl_to_contract.py`). The natural-language parser
  recognises `workflow_step` and the new `Term` comparison forms so
  YAML hand-authoring and `sponsio validate` reach the new surface.

### Changed

- **Pattern count is now 46** (was 45). Catalog tables and README
  callouts are updated to match.

### Known limitations

- **TypeScript SDK parity gap.** The `Term` abstraction, the
  `workflow_step` factory, and the five benchmark YAML libraries are
  Python-only in this release. TS will catch up in a follow-up. See
  [`docs/reference/ts-sdk-parity.md`](docs/reference/ts-sdk-parity.md)
  for the tracked gap list.

---

## [0.2.0a1]: 2026-06-06

PyPI-render fix on top of `0.2.0a0`. No runtime changes; if you are
already on `0.2.0a0` there is no functional reason to upgrade.

### Fixed

- **README image references are now absolute GitHub raw URLs**
  (`https://raw.githubusercontent.com/SponsioLabs/Sponsio/main/assets/...`).
  The PyPI / TestPyPI README renderer does not resolve relative paths,
  so the banner / architecture diagram / freeze comparison were
  missing on the project page. Three READMEs (en / zh-CN / ja) are
  updated for consistency; only `README.md` is what PyPI actually
  serves.
- **CI lint regex updated to accept either relative or absolute URL**
  for the banner check, so the old `WYSIWYG-stripped-the-banner`
  warning keeps working under both URL forms.

---

## [0.2.0a0]: 2026-06-03

Three new enforcement primitives plus a sharper failure-strategy
surface. The story: agents shouldn't have to fail catastrophically
when a contract fires. Block is one option, but it's the harshest one.
This release ships three softer-landing options that keep the agent
making progress while still gating the unsafe behavior.

### Added

- **`tool_policy` block (YAML + inline kwarg)**: declarative
  default-deny posture. `default: deny` + `approved: [search, …]`
  synthesizes a `tool_allowlist` contract automatically. Adding a new
  tool to your framework does not auto-trust it: the policy is the
  single source of truth for which tools the agent can reach.
  Available in `sponsio.yaml` and on `Sponsio(tool_policy={…})`. Both
  paths share one synthesis point so the resulting contract is
  identical.
- **`enforcement: proactive` mode**: wrap-time tool filtering on
  LangGraph, CrewAI, OpenAI Agents SDK, and Google ADK adapters.
  Denied tools never reach the agent's bound toolset. Prompt
  injection that tries to call them silently no-ops because the
  model literally cannot name them. `enforcement: reactive` (the
  default) keeps the legacy "block at call time" behavior.
- **`filter_tools(candidates)`**: pure-probe API on `BaseGuard` that
  returns the subset of tool names legal to call given the live
  trace. Custom agent loops (no framework) call this before each
  model turn to pre-filter the tool menu and avoid wasted attempts
  on temporal-precondition tools (`must_precede(A, B)` only allows B
  after A has fired). Side-effect free: no log entry, no callback
  fanout, no perf sample, no observe-mode wrapping. Implemented via
  a `dry_run` flag on `RuntimeMonitor.check_action` that suppresses
  every observable side effect under a depth counter.
- **`redirect_to_safe(unsafe, safe)` pattern + `RedirectToSafe`
  strategy**: substitute a forbidden tool call with a pre-declared
  safe one (`issue_refund` → `log_refund_request`,
  `run_sql_destructive` → `select_only_dryrun`). The model keeps
  making progress; it just can't do the unsafe thing. Trace honestly
  records the substitute call, not the original. LangGraph adapter
  dispatches the substitute transparently; other adapters surface
  `result.redirected_to` for the application loop to invoke.
- **`EscalateToHuman(notify=[…])`**: strategy now accepts a callable
  or a list of notifier callables that fire synchronously on each
  violation. Each notifier gets `(violation, context, reason)`.
  Notifier failures are isolated per-callback: a broken Slack
  webhook does not crash the agent loop and does not silence the
  remaining notifiers; the exception becomes a `RuntimeWarning`
  naming the offending callable.
- **Cross-integration verification script.**
  `scripts/verify_v0_2.py` runs 15 checks across the core runtime
  and four adapters. Skip-on-missing-SDK rather than fail. Run
  before any release to catch the kind of cross-mode bug that
  `pytest` misses (conftest pins `SPONSIO_MODE=enforce`, production
  default is `observe`).
- **Three workflow case studies.**
  `examples/integrations/python/v0_2_*.py`. Refund agent
  (LangGraph + `redirect_to_safe` + `filter_tools`), coding agent
  (CrewAI + `tool_policy` default-deny + proactive), AP automation
  (vanilla `Sponsio` + `EscalateToHuman` with Slack / email /
  PagerDuty notifiers). Each exits 0 on success and surfaces FAIL
  with detail on regression.

### Changed

- **`sponsio mode <observe|enforce>` CLI is now parent-aware.**
  Prefers updating `runtime.mode` (the only line the TS loader
  reads), falls back to `defaults.mode`, refuses to append a fresh
  `enforce` block out of thin air on a yaml without an existing
  mode line, allows appending `observe` only. CI scripts that
  relied on the old exit-1 behavior for malformed configs keep
  working. Walk-and-track replaces the naïve `re.subn`.
- **`EscalateToHuman` action semantics documented.** The class
  docstring now spells out the two patterns: notify-only (agent
  continues, useful for high-stakes-action telemetry) and the
  `DetBlock` + `register_callback` pairing for notify-and-refuse.
  The runtime layer does NOT gate `CheckResult.allowed` on
  `action="escalated"` because the monitor uses
  `EscalateToHuman()` as the default strategy for
  unfired-assumption verdicts; gating on it would break every
  conditional contract whose assumption hasn't fired yet.
- **All pattern factories accept a `desc=` keyword.**
  `redirect_to_safe` was the lonely exception; LLM extraction
  (`llm_extraction.py:535`) always passes `desc=nl` to the pattern
  factory, so the previous signature silently failed any
  LLM-extracted `redirect_to_safe` rule. Now uniform.
- **TS SDK gets a `redirectToSafe` factory.** Formula side only:
  same LTL semantics (`G(Not(called(unsafe)))`) so a TS evaluator
  produces the same verdict as the Python verifier. The strategy
  bundle and adapter dispatch are Python-only for now; documented
  caveat in the TS docstring.
- **`Sponsio` factory + every framework-specific guard class
  synthesize the `tool_policy` deny contract uniformly.** The
  earlier code path only synthesized in the `Sponsio(framework=…)`
  factory; direct framework-specific construction
  (`LangGraphGuard(tool_policy=…)`, the idiomatic Python pattern)
  silently dropped the policy. Centralized into
  `BaseGuard.__init__`.

### Fixed

- **`LangGraphGuard` rejects chained redirects (A → B → C) and
  self-redirects (A → A) loudly.** Previously a chained redirect
  silently executed the intermediate tool, and a self-redirect
  would have infinite-looped. Both now raise `ToolCallBlocked` with
  a clear message naming the chain.
- **`render/components.py:contracts_table` wraps the name column in
  `Text(name)`.** Rich interprets `[…]` as markup; contract descs
  containing brackets (e.g. `only [search, read_file] approved`)
  were having the bracketed segment silently swallowed.
- **`discovery/trace_replay.py` threads `content_atoms` into
  `ground()`.** The previous call site dropped the argument, so
  parameterised content predicates (`contains(pii)`, `arg_has(...)`)
  were silently false-negative during historical-trace replay.

### Documentation

- Per-benchmark deep dives under `docs/reference/benchmarks/`
  (agentdojo, odcv, redcode, swebench, tau2). Cross-reference fixed
  (the index claimed "Four third-party benchmarks" but had five).
- HIGH-priority strategy / pattern enumeration fixes across
  `docs/concepts/contracts.md`, `docs/concepts/overview.md`,
  `docs/concepts/architecture.md`, `docs/reference/oss-scope.md`,
  `docs/reference/config-yaml.md`, `docs/reference/patterns.md`,
  `docs/reference/observability.md`, `docs/guides/observe-vs-enforce.md`,
  `docs/guides/faq.md`. The strategy taxonomy is consistent across
  all of them now: `DetBlock` / `EscalateToHuman` / `WarnOnly` /
  `RedirectToSafe`. `RetryWithConstraint` is an extension point.
- `sponsio/tracer/semconv.py` stale comments updated to match.

---

## [0.1.1]: 2026-05-22

### Fixed

- **`pyyaml` is now a core dependency.** It was previously declared only
  under the `config` / `all` optional-dependency groups, but the config
  loader, the `sponsio host install` path, `sponsiorc`, and plugin
  scan/append all import `yaml` on the core code path. A base
  `pip install sponsio` (or `pipx install sponsio` / `mise use
  pipx:sponsio`) shipped without it, so the onboarding wizard crashed
  with `ModuleNotFoundError: No module named 'yaml'` on the first
  `sponsio host install`. ([#61](https://github.com/SponsioLabs/Sponsio/issues/61))

### Changed

- The build smoke-test in CI now runs `python -c "import yaml"` and
  `sponsio packs` (a YAML-reading command) in the clean-install venv, in
  addition to `--version` / `--help`. The old smoke test only exercised
  click-level commands, which is why the missing core dependency slipped
  through to a release.

---

## [0.1.0]: 2026-05-06

Open-source launch build. Closes the missing-implementation gap in 0.1.0a3
(CLI imported `sponsio.daemon` / `sponsio.plugin.append_ops` but the wheel
shipped without them) and tunes the bundled capability rules.

### Added

- **`sponsio.daemon`**: Unix-socket IPC server + client + handlers; powers
  the privileged-process side of `sponsio plugin append` so a system install
  can give kernel-level (separate-UID) self-modify protection.
- **`sponsio plugin append`**: structurally-additive merge from a staging
  YAML into a host bucket library; the only blessed write path through the
  self-modify pack.

### Changed

- **Capability/shell pack**: drop session-wide `rate_limit(exec, 50)` and
  `loop_detection(exec, 20)`. The 24-hour cross-session trace store turned
  these into rolling caps that false-positived heavy interactive work; the
  targeted `arg_blacklist` and confirm-gate rules already cover the real
  attacks.
- **Capability/self-modify pack**: extend protection to the upstream
  `sponsio` package (contract bundles + engine `.py`) so an editable / `--user`
  / venv install can't be used as an "edit the bundle to silence the rule"
  bypass.  Maintainer workflow: override with `customized: {match: {source:
  "library:tier1.self-modify"}, disabled: true}`.
- **Onboard wizard**: drop redundant trailing "mode flip" hint (axis 3
  already asks); language-aware bare-loop guard API hint
  (`guardBefore`/`guardAfter` for TS, `guard_before`/`guard_after` for Python).

### Fixed

- `sponsio --version` was hardcoded to "0.2.0a0" in the Click
  `version_option`; now reads `sponsio.__version__` so it tracks
  `pyproject.toml` automatically.
- 0.1.0a3 wheel was missing `sponsio/daemon/` and `sponsio/plugin/append_ops.py`,
  causing `sponsio plugin append` and `sponsio daemon …` to ImportError on a
  fresh `pip install`. 0.1.0 ships them.

---

## [0.1.0a3]: 2026-05-02

Pre-launch test build. Sponsio is a runtime contract enforcement layer
for AI agents: deterministic LTL contracts evaluated as a compiled DFA
on every tool call, with framework adapters for the common agent stacks
and a CLI for scanning, mining, and reporting.

### Added

- **Runtime engine**: LTL → DFA compiler, finite-trace evaluator,
  observe / enforce modes, session log writer, OTel exporter.
- **Pattern library**: 44 deterministic patterns (`must_precede`,
  `rate_limit`, `idempotent`, `arg_blacklist`, `arg_allowlist`,
  `no_data_leak`, `segregation_of_duty`, `cooldown`, `must_confirm`,
  `bounded_retry`, `loop_detection`, `scope_limit`,
  `arg_length_limit`, `data_intact`, `destructive_action_gate`, etc.)
  exposed both as Python factories and as natural-language triggers.
- **Contract bundles**: `sponsio:core/runaway`, `sponsio:core/universal`,
  `sponsio:capability/shell`, `sponsio:capability/filesystem`,
  `sponsio:incident/openclaw`.
- **Framework integrations**: LangGraph / LangChain.js, Claude Agent
  SDK, OpenAI SDK, OpenAI Agents SDK, Google ADK, Vercel AI SDK,
  CrewAI, MCP, plus a no-framework `guard_before` / `guard_after` API.
- **CLI**: `sponsio init` (interactive 4-axis wizard), plus the
  underlying `sponsio onboard`, `scan`, `validate`, `check`, `report`,
  `refresh`, `eval`, `export`, `export-sessions`, `host`, `plugin`,
  `packs`, `patterns`, `prompt`, `mode`, `doctor`, `skill`, `replay`,
  `explain`, `demo`.
- **TypeScript SDK** (`@sponsio/sdk`): deterministic engine + the
  same set of framework integrations.
- **Static scanner** (`@sponsio/sdk`): AST-based code scanner
  for proposing contracts from a TS / JS codebase.
- **Local observability**: session log JSONL writer,
  `sponsio host trace --follow` live stream, `sponsio report` rich /
  markdown / HTML / JSON output, OTel HTTP exporter for shipping to
  your own collector.
- **Plugins**: Claude Code plugin (production), OpenClaw plugin
  (beta: type definitions track the public OpenClaw plugin docs;
  end-to-end exercise inside a live OpenClaw runtime is in progress).
- **Benchmarks**: ODCV-Bench (**95.6% high-risk protection across 12
  LLMs**, 24 of 36 scenarios at 100% across every model) and
  RedCode-Exec (92% combined detection across 1,410 cases), with
  **0 FP increase** across 6 ODCV library iterations and 0% utility
  FP on the 60-file clean-code audit. See
  [`docs/reference/benchmarks.md`](docs/reference/benchmarks.md).

### Notes

- Status: alpha. APIs may shift before 1.0; the trace event schema
  and CLI surface follow [SemVer](https://semver.org/) for breaking
  changes from 0.2 onward.
- Apache 2.0: see [LICENSE](LICENSE) and the
  [OSS Promise](OSS_PROMISE.md).
