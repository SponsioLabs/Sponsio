# Sponsio's Open Source Promise

Sponsio Labs is a commercial company. We chose Apache 2.0 because we
believe runtime safety infrastructure should be open by default — the
thing checking whether your agent is allowed to wire $1M out should not
be a black box you can't audit.

This page tells you what that means in practice: what stays in OSS
forever, what we sell as Sponsio Cloud, and what we promise about
the boundary between the two.

If you read one section, read [§4 Why this works](#why-this-works).
That's the part that determines whether OSS Sponsio is *actually*
useful to you, or just a teaser for a paid product.

---

## 1. What's permanently in OSS (Apache 2.0)

These ship in this repository today and will continue to ship under
Apache 2.0. We will not relicense, gate, or remove them.

### Engine

- The deterministic contract engine — LTL AST, evaluator, DFA monitor,
  finite-trace evaluation
- The full pattern library — every Tier 0 + Tier 1 deterministic
  pattern (`must_precede`, `rate_limit`, `idempotent`, `arg_blacklist`,
  `arg_allowlist`, `no_data_leak`, `segregation_of_duty`, `cooldown`,
  `must_confirm`, `bounded_retry`, `loop_detection`, `scope_limit`,
  `arg_length_limit`, `data_intact`, `destructive_action_gate`, …)
- Contract bundles — `sponsio:core/*`, `sponsio:capability/*`,
  `sponsio:incident/*`, `sponsio:benchmark/*`
- Configuration loader (`sponsio.yaml`), include resolution,
  workspace + tool rename + overrides

### Framework adapters

- Every framework integration we ship: LangGraph / LangChain.js,
  Claude Agent SDK, OpenAI SDK, OpenAI Agents SDK, Google ADK,
  Vercel AI SDK, CrewAI, MCP, Cursor, Claude Code, OpenClaw — and
  the no-framework `guard_before` / `guard_after` API
- TypeScript SDK (`@sponsio/sdk`) — same engine, same DSL
- Static scanner (`@sponsio/sdk`) — AST-based contract proposal

### CLI

- `sponsio onboard`, `scan`, `validate`, `check`, `report`,
  `refresh`, `eval`, `export`, `export-sessions`
- `sponsio host` group — install / status / list / trace / uninstall
  for the Cursor / Claude Code / OpenClaw plugins
- `sponsio plugin` group — init / install / scan / prompt / guard
- `sponsio packs`, `patterns`, `prompt`, `mode`, `doctor`, `skill`,
  `replay`, `explain`, `demo`

### Local observability

- Session log writer (`~/.sponsio/sessions/<agent_id>/*.jsonl`)
- Live coloured stream (`sponsio host trace --follow`)
- Local report renderer (rich CLI / markdown / **HTML** / JSON)
- Replay (`sponsio replay <session>`)
- OTel HTTP exporter — ship spans to *your own* collector

### Discovery & Generation (single-project)

- AST-based code scan (`sponsio scan`) over your own codebase
- Document parser (`sponsio scan --policy policy.md`) for natural
  language → contract
- Trace mining (`sponsio refresh`) over your own traces — finds
  repeating unsafe patterns and proposes new contracts
- NL → contract parser (deterministic patterns)

### Extension points

- `Judge` interface — bring your own LLM judge for stochastic atoms.
  The OSS engine ships an empty sto registry plus the surrounding
  scaffold; install `sponsio[cloud]` for the managed catalog, or
  implement the interface yourself.

These will never be relicensed. New work in these areas ships under
Apache 2.0.

---

## 2. What we sell as Sponsio Cloud

`pip install sponsio[cloud]`. Opens **mid-May 2026**.

These are the things that genuinely benefit from being a managed
service — they need a backend you don't want to operate yourself, or
they require data that only an aggregator can have.

### Managed stochastic pipeline

- Curated catalog of LLM-judged evaluators: `tone_polite`,
  `injection_free`, `jailbreak_free`, `toxic_free`, `semantic_pii_free`,
  `scope_respect`, `hallucination_free`, `harmful`, `faithfulness`,
  plus pii / length / format / relevance evaluators
- Probabilistic-lifting (α/β-threshold) evaluation
- Judge harness, multi-provider LLM client, threshold calibration
- Replaces the OSS `Judge` extension stub with a hosted catalog —
  no judge prompt engineering, no provider integration, no
  calibration to write yourself

### Cross-customer pattern intelligence

- Trace mining across the anonymized cross-customer pool — pattern
  discovery you can't do from your traces alone
- Threat-intel feed: contracts derived from attacks observed at
  *other* deployments, pushed to your library before you see those
  attacks yourself
- ML-based pattern discovery beyond simple frequency/support thresholds

### Hosted dashboard + retention

- Multi-tenant dashboard backend (FastAPI) and React frontend —
  Monitor, Rulebook, Playground, Integrate, ScanAgent pages
- OTel ingest endpoint for remote agents
- Hosted retention beyond the local session log
- Enterprise SSO, audit logs, role-based access

### Premium content packs

- Bespoke threat patterns derived from internal customer engagements
- Industry-specific pattern bundles (financial, healthcare,
  government) — built from real incidents, with applicability
  metadata for safe portability

---

## 3. What we will not do

We're aware of the OSS-rug-pull pattern (ElasticSearch, MongoDB,
HashiCorp). We've designed our boundary to avoid each failure mode.

- **We will not relicense** anything currently shipped under
  Apache 2.0. The OSS Promise covers the surface listed in §1.
- **We will not move features from OSS into Cloud.** Anything new
  in an OSS-scope directory (per [`docs/reference/oss-scope.md`](docs/reference/oss-scope.md))
  ships under Apache 2.0. New Cloud-scope work doesn't appear in
  this repo at all.
- **We will not artificially limit the OSS engine** to push you to
  Cloud. No "10 contracts max", no "OSS evaluator runs at half
  speed", no "sessions truncated after 24h". The OSS engine is the
  full engine. Cloud sells different things, not a faster engine.
- **We will not require a CLA with relicensing rights.** We use the
  [DCO](https://developercertificate.org/) (see
  [CONTRIBUTING.md](CONTRIBUTING.md)). Your contributions land
  under Apache 2.0 and stay there.
- **We will not block fork operations.** Apache 2.0 permits forks
  and we welcome them — see [BRAND.md](BRAND.md) for the trademark
  boundary (you can fork; you have to rename).

If we ever change any of the above, we will:

1. Announce **6 months** in advance.
2. Continue maintaining the **last Apache 2.0 commit** at the time
   of announcement, indefinitely, for security backports and
   critical bugs.
3. Provide a documented migration path.

This is a public commitment, not just a docs page.

---

## 4. Why this works

The honest test of any open-core company is: *can I run the OSS
version, by myself, and have it actually work — not as a teaser,
but as the thing I deploy to production?*

For Sponsio, the answer is yes:

- **The blocking path is fully in OSS.** Every tool call your agent
  makes, in production, runs through the OSS engine. The 0.0052 ms
  hot path, the deterministic DFA, the framework adapters, the
  pattern library — all open, all yours. We don't run a service
  you depend on for synchronous decisions.
- **Local observability is fully in OSS.** Session logs, live trace
  stream, HTML/markdown/JSON reports, OTel HTTP exporter to ship to
  *your* collector. You can run Sponsio without ever sending us a
  byte.
- **The pattern library is fully in OSS.** Every Tier 0 + Tier 1
  pattern, every contract bundle (`core/*`, `capability/*`,
  `incident/*`, `benchmark/*`). The 84.5% / 92% benchmark numbers
  are achieved with just the OSS library.
- **The contract format is fully in OSS.** YAML you write today,
  on OSS Sponsio, runs unchanged on Cloud Sponsio. There is no
  Cloud-only DSL.
- **The extension points are fully in OSS.** If you want
  stochastic checks but don't want managed Cloud, the `Judge`
  interface lets you plug in your own LLM. The whole sto pipeline
  is something you can build on top of OSS — Cloud is the *managed*
  version, not the *only* version.

What Cloud sells is **convenience and network effects**:

- *Convenience* — pre-built sto catalog, hosted dashboard, hosted
  retention, multi-tenant team UI. None of this is locked
  capability; it's saved time.
- *Network effects* — pattern intelligence drawn from anonymized
  cross-customer traces. This is the only thing genuinely impossible
  to replicate from OSS, because the data only exists when many
  customers run a managed service.

If you don't want either, run OSS forever. We mean that.

---

## 5. The boundary, in one table

| Question | OSS or Cloud? |
|---|---|
| Deterministic LTL contract / pattern | OSS |
| LLM call to score output (managed catalog) | Cloud |
| LLM call to score output (your own judge) | OSS (`Judge` extension) |
| Cross-customer / cross-trace aggregation | Cloud |
| Single-project static scan | OSS |
| Per-host hook adapter (Cursor / Claude Code / OpenClaw) | OSS |
| Single-user web dashboard | Cloud |
| Multi-tenant hosted span ingestion | Cloud |
| Local session log → your own collector | OSS (`sponsio export-sessions`, `OtlpHttpExporter`) |
| Local HTML report | OSS (`sponsio report --format html`) |

The full mapping lives in [`docs/reference/oss-scope.md`](docs/reference/oss-scope.md).

---

## 6. Cloud waitlist

Sponsio Cloud opens **mid-May 2026**. Early-access pricing for the
first 50 teams.

[**Join the waitlist** →](https://sponsio.dev/cloud)

---

*Questions about this document? [Open an issue](https://github.com/SponsioLabs/Sponsio/issues/new) or email [hello@sponsio.dev](mailto:hello@sponsio.dev).*
