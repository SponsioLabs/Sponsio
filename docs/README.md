# Documentation index

| Document | What it is |
|----------|------------|
| [getting-started.md](getting-started.md) | Install and first guard in a few minutes |
| [cli.md](cli.md) | `sponsio` CLI: scan, init, serve, calibrate, … |
| [contracts.md](contracts.md) | Contract model, det vs sto, LTL, patterns |
| [sto-atoms.md](sto-atoms.md) | Stochastic atom catalog and shapes |
| [input-formats.md](input-formats.md) | How scan/discovery read your codebase |
| [integrations.md](integrations.md) | Python and TypeScript framework hooks |
| [architecture.md](architecture.md) | Design boundaries: atoms, trace, LTL, OTEL |
| [cost-based-thresholds.md](cost-based-thresholds.md) | Choosing α/β for sto contracts from cost ratios |

**Benchmarks:** Headline figures in the root [README](../README.md#benchmarks) may be published. **Raw eval tables, full model-by-model numbers, and long-form benchmark write-ups are private** — do not commit them; paths under `docs/` that match those names are in `.gitignore` (never `git add -f`).

## What belongs in the public repo vs. internal-only

**Ship with open source (this tree):** user-facing guides, contract and architecture reference, design notes (e.g. [cost-based-thresholds.md](cost-based-thresholds.md)), and sto calibration concepts. These help contributors without exposing internal eval artifacts.

**Keep out of the public tree** (or redact before publishing):

- **Roadmaps, launch checklists, and status dashboards** (e.g. one-off `STATUS.md`, `PLAN.md`, `LAUNCH_*.md`) — they go stale and can imply commitments.
- **Narration scripts** for a specific demo or video (`demo-video-script.md` is listed in `.gitignore` for that reason) — not end-user documentation; update and publish only if you want a canonical marketing line.
- **Benchmark result tables / eval lab notebooks** — keep only the short summary in the root README (or nothing). Do not add detailed results to this repository.
- **Internal agent/project notes** under `agent_docs/` or similar.
- **Anything with real customer names, private URLs, API keys, or unreleased product detail.**

**Runtime data** — see [../data/README.md](../data/README.md). The whole `data/` tree is local-only except the stub README.
