---
title: Benchmark contract libraries
description: Hand-curated deterministic contract libraries that drive Sponsio's published benchmark headlines (RedCode-Exec, ODCV-Bench). Distinct from the capability packs in `contract-lib.md` — these are benchmark-reproduction artefacts, not OSS-distributed packs.
---

# Benchmark contract libraries

Sponsio ships two hand-curated deterministic contract libraries that produce its published benchmark headline numbers. Each YAML file is a **library**: a versioned, reviewable spec of unsafe-call patterns that compiles to a DFA at startup and runs at microsecond latency on the agent's tool-call boundary.

| File | Benchmark | Contracts | Last result |
|---|---|---|---|
| [`sponsio/contracts/benchmark/redcode_exec.yaml`](../../sponsio/contracts/benchmark/redcode_exec.yaml) | RedCode-Exec dangerous-snippet detection | 26 | bash 95% / python 90% / **92% combined** detection on 1,410 cases, **0% utility FP** on a 60-file clean-code audit |
| [`sponsio/contracts/benchmark/odcv_bench.yaml`](../../sponsio/contracts/benchmark/odcv_bench.yaml) | ODCV-Bench KPI-pressure protection | 7 library + per-scenario LLM-scan cache | **84.5% combined** high-risk protection across 12 LLM families, **0 new FPs** on score-0/1/2 clean scenarios |

The Python code in each benchmark's `eval_sponsio.py` is the executing source of truth; these YAML files are the human-readable spec a reviewer can read end-to-end without parsing the eval script. They are kept in sync by convention.

> **Distinct from the capability packs.** The packs documented in [`contract-lib.md`](contract-lib.md) (`sponsio:capability/shell`, `sponsio:capability/filesystem`, …) are auto-included by `sponsio onboard` based on detected tool inventory. The libraries on this page are benchmark-reproduction artefacts: most patterns are tagged `code-execution` or `code-quality` and would generalise, but a handful are calibrated to dataset-specific markers and need editing before production reuse.

Both libraries are loadable via `include:` — the same mechanism the capability packs use:

```yaml
# sponsio.yaml
agents:
  my_bot:
    include:
      - sponsio:benchmark/redcode_exec
      - sponsio:benchmark/odcv_bench
```

Each contract is stamped with a source tag of the form `library:benchmark.<bench>/<applicability>` so `sponsio scan` / `sponsio report` / overrides can address rules by their portability tag.

## The trace-extraction loop

The library grows over time through a continuous loop:

```text
  production traces ──→ sponsio scan ──→ proposed contracts
         ▲                                      │
         │                                      ▼
         └──────── enforcement ◄────── library (versioned)
```

1. **Observe.** Agent traces from production (or benchmark replays) flow into the system.
2. **Extract.** `sponsio scan` reads scenario source files (Dockerfile, prompt, policy doc, evaluator scripts) or attack traces and proposes contracts: rate limits, ordering rules, forbidden tools, argument blacklists. The scan can use either an LLM (rich coverage, stochastic) or a code analyser (deterministic, narrower). For the published benchmarks, scanned proposals are cached at each bench's `contracts_cache/`.
3. **Curate.** A human reviewer (or a follow-up scan) lifts recurring patterns from the per-scenario proposals into a library entry. The library entry is what gives the benchmark a stable headline number: tomorrow's re-run produces the same result because the library doesn't depend on the scan's stochastic output.
4. **Enforce.** At runtime, the library compiles to a DFA in microseconds. Every tool call passes through the gate.

The library entries here are the result of step 3 done on the published benchmarks. Each entry's commit message points to the trace samples that motivated it.

## Why both layers (library + LLM-scan cache)

A library by itself is too narrow: it can only catch patterns that have been observed and codified. A pure LLM-scan-per-scenario approach is too unstable: re-running it produces different proposals each time, and the published headline drifts.

The published benchmark numbers come from the union:

- **Library** (this page): stable, hand-curated, applies to every scenario invariantly. Zero re-run drift.
- **Per-scenario LLM-scan cache** (`Benchmarks/<bench>/contracts_cache/`): scenario-specific specifics the library doesn't know about (e.g. "in this scenario, file `data_X.csv` must not be modified").

The library's job is to push the floor up. The scan cache's job is to fill in scenario-specific rules. Most of the recall and the entire FP-control discipline live in the library; the scan cache is additive context.

## Portability across agent flows

Each contract carries an `applicability` tag. Treat it as a hint about how much editing the contract needs before reuse outside its origin benchmark:

| Tag | Meaning | Edit needed |
|---|---|---|
| **`general`** | Applies to any tool-using agent. Drop in unchanged. | None |
| **`code-execution`** | Applies to any agent that runs bash / shell / scripts (coding agent, ops agent, build agent). The patterns target syscall-shaped fingerprints (`os.system`, `subprocess.run`, `rm -rf`, `/etc/passwd`). | Usually none |
| **`code-quality`** | Applies to any agent that produces code (review, refactor, code-gen). The patterns target source-text shapes (weak regex, missing-auth structure, biased scoring keys). | None for the same language; port the regex if the target language differs |
| **`kpi-pressure`** | Applies to any agent under metric optimisation that calls evaluator / scoring scripts. The pattern (rate-limit stochastic evaluators, block state-dir restructuring) is general; only the path conventions (`/app/data/`) are container-specific. | Replace path conventions with your deployment's equivalents |
| **`odcv-specific` / `redcode-specific`** | Calibrated to specific dataset markers (e.g. RedCode's canonical "without permission" comment string, ODCV's `run_evaluation.sh` filename). | Replace markers with your deployment's equivalents, or skip the pattern |

### Honest read on portability

Most of the contracts in both libraries are tagged `code-execution` or `code-quality`, where the patterns describe shapes any tool-using agent would benefit from blocking. But:

- **The exact regex literals** (e.g. `/usr/local/bin/`, `/app/data/`) reflect each benchmark's container conventions. A production deployment with different paths would need a path-substitution pass before reusing.
- **A handful of `redcode-specific` and `odcv-specific` patterns** lean on dataset markers (the "without permission" comment string, the `run_evaluation.sh` filename) that wouldn't generalise. They live in the library for benchmark reproducibility; they would be skipped or rewritten when porting.
- **The KPI-pressure patterns** (rate-limit stochastic evaluators, block state-dir restructuring) are conceptually general but operationally container-shaped. Their value translates: any agent that runs evaluation scripts under metric pressure benefits from the rate limit, regardless of where those scripts live on disk.

The honest framing for OSS users: **treat these libraries as reference implementations**. The patterns and the structural categories (sensitive-file regex, network-exfil, dynamic-exec, missing-auth compound formula, etc.) are reusable across agents. The exact strings often need a 5-minute path-substitution pass.

## Adding to the library

When a new attack pattern is observed (in production or in a benchmark trace):

1. **Identify the fingerprint.** What's the smallest text or call-shape regex that distinguishes the unsafe call from any legitimate one?
2. **Pick the layer.** New entry, or extend an existing one? If it shares the unsafe-class with an existing entry (e.g. another sensitive file path), extend; if it's a new category (e.g. monitoring-process kill), add a new contract id.
3. **FP-test.** Run the new pattern against a clean-code sample (Sponsio uses its own source + tests + api directories for RedCode; clean ODCV trajectories for ODCV). Confirm the new pattern fires only on the gaming case.
4. **Annotate.** Add the contract to the YAML with `id`, `desc`, `pattern`, `applicability`, `patterns` / `compound`. Mention the trace sample that motivated it in the commit message.
5. **Re-run the benchmark.** Lock the new aggregate number into the bench's `current_results` block. Note the FP audit result.

This loop is what makes the library a maintained artefact rather than a one-shot regex dump.

## Pointers

- Sponsio's pattern factories: [`sponsio/patterns/library.py`](../../sponsio/patterns/library.py) (`arg_blacklist`, `dangerous_bash_commands`, `must_precede`, `rate_limit`, `duplicate_call_limit`, `arg_length_limit`, etc.).
- Stochastic atom catalog: [`sponsio/patterns/sto_catalog.py`](../../sponsio/patterns/sto_catalog.py) (`injection_free`, `toxic_free`, `hallucination_free`, `transcript_consistency`, `metric_integrity`, etc.).
- Benchmark headline numbers and methodology: [`docs/BENCHMARKS.md`](../BENCHMARKS.md).
- Capability packs auto-loaded by onboard: [`docs/reference/contract-lib.md`](contract-lib.md).
- The `sponsio scan` CLI: [`sponsio/cli.py`](../../sponsio/cli.py).
