# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Granular per-release notes (commits, PRs, individual fix lines) live in
[GitHub Releases](https://github.com/SponsioLabs/Sponsio/releases). This
file keeps the high-level shape: what was added, what changed, what
broke.

---

## [Unreleased]

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
