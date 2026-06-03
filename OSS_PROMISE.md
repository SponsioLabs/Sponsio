# Sponsio's Open Source Promise

Sponsio Labs chose Apache 2.0 because we believe runtime safety
infrastructure should be open by default: the thing checking whether
your agent is allowed to wire $1M out should not be a black box you
can't audit.

This page tells you what that means in practice: what stays open
forever, and what we promise about keeping it stable.

If you read one section, read [§3 Why this works](#why-this-works).
That's the part that determines whether OSS Sponsio is *actually*
useful to you.

---

## 1. What's permanently open (Apache 2.0)

These ship in this repository today and will continue to ship under
Apache 2.0. We will not relicense, gate, or remove them.

### Engine

- The deterministic contract engine: LTL AST, evaluator, DFA monitor,
  finite-trace evaluation
- The full pattern library: every Tier 0 + Tier 1 deterministic
  pattern (`must_precede`, `rate_limit`, `idempotent`, `arg_blacklist`,
  `arg_allowlist`, `no_data_leak`, `segregation_of_duty`, `cooldown`,
  `must_confirm`, `bounded_retry`, `loop_detection`, `scope_limit`,
  `arg_length_limit`, `data_intact`, `destructive_action_gate`, …)
- Contract bundles: `sponsio:core/*`, `sponsio:capability/*`,
  `sponsio:incident/*`
- Configuration loader (`sponsio.yaml`), include resolution,
  workspace + tool rename + overrides

### Framework adapters

- Every framework integration we ship: LangGraph / LangChain.js,
  Claude Agent SDK, OpenAI SDK, OpenAI Agents SDK, Google ADK,
  Vercel AI SDK, CrewAI, MCP, Cursor, Claude Code, OpenClaw, and
  the no-framework `guard_before` / `guard_after` API
- TypeScript SDK (`@sponsio/sdk`): same engine, same DSL
- Static scanner (`@sponsio/sdk`): AST-based contract proposal

### CLI

- `sponsio init` (interactive wizard, the user-facing entry),
  plus the underlying `sponsio onboard`, `scan`, `validate`,
  `check`, `report`, `refresh`, `eval`, `export`,
  `export-sessions`
- `sponsio host` group: install / status / list / trace / uninstall
  for the Cursor / Claude Code / OpenClaw plugins
- `sponsio plugin` group: init / install / scan / prompt / guard
- `sponsio packs`, `patterns`, `prompt`, `mode`, `doctor`, `skill`,
  `replay`, `explain`, `demo`

### Local observability

- Session log writer (`~/.sponsio/sessions/<agent_id>/*.jsonl`)
- Live coloured stream (`sponsio host trace --follow`)
- Local report renderer (rich CLI / markdown / **HTML** / JSON)
- Replay (`sponsio replay <session>`)
- OTel HTTP exporter: ship spans to *your own* collector

### Discovery & Generation (single-project)

- AST-based code scan (`sponsio scan`) over your own codebase
- Document parser (`sponsio scan --policy policy.md`) for natural
  language → contract
- Trace mining (`sponsio refresh`) over your own traces: finds
  repeating unsafe patterns and proposes new contracts
- NL → contract parser (deterministic patterns)

These will never be relicensed. New work in these areas ships under
Apache 2.0.

---

## 2. What we will not do

We're aware of the OSS-rug-pull pattern (ElasticSearch, MongoDB,
HashiCorp). We've designed our commitments to avoid each failure mode.

- **We will not relicense** anything currently shipped under
  Apache 2.0. The OSS Promise covers the surface listed in §1.
- **We will not artificially limit the OSS engine.** No "10 contracts
  max", no "OSS evaluator runs at half speed", no "sessions truncated
  after 24h". The OSS engine is the full engine.
- **We will not require a CLA with relicensing rights.** We use the
  [DCO](https://developercertificate.org/) (see
  [CONTRIBUTING.md](CONTRIBUTING.md)). Your contributions land
  under Apache 2.0 and stay there.
- **We will not block fork operations.** Apache 2.0 permits forks
  and we welcome them; you can fork, you just have to rename
  (see [CONTRIBUTING.md](CONTRIBUTING.md)).

If we ever change any of the above, we will:

1. Announce **6 months** in advance.
2. Continue maintaining the **last Apache 2.0 commit** at the time
   of announcement, indefinitely, for security backports and
   critical bugs.
3. Provide a documented migration path.

This is a public commitment, not just a docs page.

---

## 3. Why this works

The honest test of any open project is: *can I run it, by myself, and
have it actually work, as the thing I deploy to production?*

For Sponsio, the answer is yes:

- **The blocking path is fully open.** Every tool call your agent
  makes, in production, runs through the OSS engine. The 0.0052 ms
  hot path, the deterministic DFA, the framework adapters, the
  pattern library: all open, all yours. We don't run a service
  you depend on for synchronous decisions.
- **Local observability is fully open.** Session logs, live trace
  stream, HTML/markdown/JSON reports, OTel HTTP exporter to ship to
  *your* collector. You can run Sponsio without ever sending us a
  byte.
- **The pattern library is fully open.** Every Tier 0 + Tier 1
  pattern, every contract bundle (`core/*`, `capability/*`,
  `incident/*`, `benchmark/*`). The 95.6% (ODCV) / 92% (RedCode) /
  86% relative ASR reduction (AgentDojo) benchmark numbers are
  achieved with just the OSS library.
- **The contract format is fully open.** The YAML you write today
  runs unchanged tomorrow. There is no proprietary DSL.

If you don't want anything else, run OSS forever. We mean that.

---

*Questions about this document? [Open an issue](https://github.com/SponsioLabs/Sponsio/issues/new) or email [hello@sponsio.dev](mailto:hello@sponsio.dev).*
