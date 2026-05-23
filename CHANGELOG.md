# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Granular per-release notes (commits, PRs, individual fix lines) live in
[GitHub Releases](https://github.com/SponsioLabs/Sponsio/releases). This
file keeps the high-level shape: what was added, what changed, what
broke.

---

## [Unreleased]

_Nothing yet._

---

## [0.1.1] — 2026-05-22

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

## [0.1.0] — 2026-05-06

Open-source launch build. Closes the missing-implementation gap in 0.1.0a3
(CLI imported `sponsio.daemon` / `sponsio.plugin.append_ops` but the wheel
shipped without them) and tunes the bundled capability rules.

### Added

- **`sponsio.daemon`** — Unix-socket IPC server + client + handlers; powers
  the privileged-process side of `sponsio plugin append` so a system install
  can give kernel-level (separate-UID) self-modify protection.
- **`sponsio plugin append`** — structurally-additive merge from a staging
  YAML into a host bucket library; the only blessed write path through the
  self-modify pack.

### Changed

- **Capability/shell pack** — drop session-wide `rate_limit(exec, 50)` and
  `loop_detection(exec, 20)`. The 24-hour cross-session trace store turned
  these into rolling caps that false-positived heavy interactive work; the
  targeted `arg_blacklist` and confirm-gate rules already cover the real
  attacks.
- **Capability/self-modify pack** — extend protection to the upstream
  `sponsio` package (contract bundles + engine `.py`) so an editable / `--user`
  / venv install can't be used as an "edit the bundle to silence the rule"
  bypass.  Maintainer workflow: override with `customized: {match: {source:
  "library:tier1.self-modify"}, disabled: true}`.
- **Onboard wizard** — drop redundant trailing "mode flip" hint (axis 3
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

## [0.1.0a3] — 2026-05-02

Pre-launch test build. Sponsio is a runtime contract enforcement layer
for AI agents: deterministic LTL contracts evaluated as a compiled DFA
on every tool call, with framework adapters for the common agent stacks
and a CLI for scanning, mining, and reporting.

### Added

- **Runtime engine** — LTL → DFA compiler, finite-trace evaluator,
  observe / enforce modes, session log writer, OTel exporter.
- **Pattern library** — 44 deterministic patterns (`must_precede`,
  `rate_limit`, `idempotent`, `arg_blacklist`, `arg_allowlist`,
  `no_data_leak`, `segregation_of_duty`, `cooldown`, `must_confirm`,
  `bounded_retry`, `loop_detection`, `scope_limit`,
  `arg_length_limit`, `data_intact`, `destructive_action_gate`, etc.)
  exposed both as Python factories and as natural-language triggers.
- **Contract bundles** — `sponsio:core/runaway`, `sponsio:core/universal`,
  `sponsio:capability/shell`, `sponsio:capability/filesystem`,
  `sponsio:incident/openclaw`.
- **Framework integrations** — LangGraph / LangChain.js, Claude Agent
  SDK, OpenAI SDK, OpenAI Agents SDK, Google ADK, Vercel AI SDK,
  CrewAI, MCP, plus a no-framework `guard_before` / `guard_after` API.
- **CLI** — `sponsio init` (interactive 4-axis wizard), plus the
  underlying `sponsio onboard`, `scan`, `validate`, `check`, `report`,
  `refresh`, `eval`, `export`, `export-sessions`, `host`, `plugin`,
  `packs`, `patterns`, `prompt`, `mode`, `doctor`, `skill`, `replay`,
  `explain`, `demo`.
- **TypeScript SDK** (`@sponsio/sdk`) — deterministic engine + the
  same set of framework integrations.
- **Static scanner** (`@sponsio/sdk`) — AST-based code scanner
  for proposing contracts from a TS / JS codebase.
- **Local observability** — session log JSONL writer,
  `sponsio host trace --follow` live stream, `sponsio report` rich /
  markdown / HTML / JSON output, OTel HTTP exporter for shipping to
  your own collector.
- **Plugins** — Claude Code plugin (production), OpenClaw plugin
  (beta — type definitions track the public OpenClaw plugin docs;
  end-to-end exercise inside a live OpenClaw runtime is in progress).
- **Benchmarks** — ODCV-Bench (84.5% high-risk protection across 12
  LLMs) and RedCode-Exec (92% combined detection across 1,410 cases),
  with 0% utility FP on the 60-file clean-code audit. See
  [`docs/reference/benchmarks.md`](docs/reference/benchmarks.md).

### Boundary

Sponsio Cloud is a separate commercial product layer; the
[OSS / Cloud boundary](docs/reference/oss-scope.md) is documented and committed.
The OSS engine ships everything above; the managed sto pipeline,
cross-customer pattern intelligence, hosted dashboard, and
multi-tenant retention live in `sponsio[cloud]`. The OSS monitor
log-and-skips sto contracts with a one-time warning so libraries that
mix det + sto load cleanly under either install.

### Notes

- Status: alpha. APIs may shift before 1.0; the trace event schema
  and CLI surface follow [SemVer](https://semver.org/) for breaking
  changes from 0.2 onward.
- Apache 2.0 — see [LICENSE](LICENSE) and the
  [OSS Promise](docs/reference/oss-scope.md#versioning--the-oss-promise).
