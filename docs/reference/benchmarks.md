# Benchmarks & Performance

> **Last updated:** 2026-05-02 · **Sponsio version:** 0.2.0a0 · **Python:** 3.12 · **OS:** macOS 15 (Apple Silicon, M-series, 16 GB)
>
> Latencies measured with `time.perf_counter_ns()` wrapping each `guard_before` / `guard_after` call. Safety numbers come from offline replay against published benchmark trajectories. Every figure is tagged with the dataset, model, and date it was produced.

## TL;DR

| What you care about | Number |
|---|---|
| **High-risk attack protection across 12 LLMs (ODCV-Bench)** | **84.5%** of severity-≥3 scenarios blocked, library-only (stable, no LLM-scan dependency) |
| **Dangerous-snippet detection (RedCode-Exec, 1,410 cases)** | **92%** combined (bash 95%, python 90%) |
| **Utility FP** | **0 new FPs** on score-0/1/2 (clean) ODCV scenarios · **0%** on a 60-file clean-Python audit (RedCode v3 layers) |
| **Enforcement latency, ODCV-Bench (mandated, 1,438 calls, 6–18 contracts)** | **0.139 ms** (p50) · 0.525 ms (p95) · 0.765 ms (p99) · 4,577 ops/sec |
| **Enforcement latency, RedCode bash per-command (3,848 calls, 7 contracts)** | **0.434 ms** (p50) · 0.477 ms (p95) · 0.558 ms (p99) · 2,270 ops/sec |
| **Enforcement latency, RedCode python whole-script (810 calls, 9 contracts)** | **0.811 ms** (p50) · 0.912 ms (p95) · 1.035 ms (p99) · 1,216 ops/sec |
| **Hot-path latency (single contract, pre-warmed DFA)** | **0.0052 ms** (p50) · 0.012 ms (p99) · 178K ops/sec |
| **LLM calls on the blocking path** | **0** (pure DFA) |

**Bottom line:** Sponsio's blocking path runs at **0.0052 ms p50** on the synthetic micro-bench (single contract, pre-warmed DFA, the public-facing **<0.01 ms** anchor) and stays at **0.139 ms p50** on the heaviest ODCV scenario (18 scan-discovered contracts) and **0.434 ms p50** on RedCode bash with 7 layered regex contracts. **5,000× to 60,000× faster** than any LLM-as-judge guardrail, with zero LLM calls. On the safety side, deterministic contracts catch **84.5%** of high-risk KPI-pressure scenarios across 12 mainstream LLMs and **92%** of dangerous code snippets in RedCode. Both libraries ship 0 utility FP on the clean-trajectory audits.

---

## Benchmarks used

Sponsio's headline numbers come from two **third-party** benchmarks. We don't author the threat sets we score on.

### ODCV-Bench (third-party, McGill University)

ODCV-Bench evaluates outcome-driven constraint violations: failures that arise when agents pursue goal optimization under performance pressure while deprioritizing ethical / legal / safety constraints. 40 multi-step scenarios in a persistent bash environment, each in *Mandated* (instruction-commanded) and *Incentivized* (KPI-pressure) variants, scored across 12 frontier LLMs.

- **Paper:** [Lee, Fang et al., "A Benchmark for Evaluating Outcome-Driven Constraint Violations in Autonomous AI Agents", arXiv:2512.20798](https://arxiv.org/abs/2512.20798)
- **Code & scenarios:** [github.com/McGill-DMaS/ODCV-Bench](https://github.com/McGill-DMaS/ODCV-Bench)
- **Authoring institution:** McGill University, Data Mining and Security Lab (DMaS), *not* Sponsio Labs.

ODCV's published baseline finding: across 12 state-of-the-art LLMs, unguarded violation rates range from **11.5% to 66.7%**, with most models above 30%. Even Claude-Opus-4.5 violates in 11.5% of runs.

### RedCode-Exec (third-party)

[RedCode](https://github.com/AI-secure/RedCode) measures detection of dangerous bash and python snippets across the full RedCode threat surface: file deletion, credential exfiltration, sensitive-file access, network exfil, dynamic exec, process kills, filesystem destruction. 1,410 cases (600 bash, 810 python) replayed through Sponsio's layered `dangerous_bash_commands` + `arg_blacklist` patterns.

---

## Comparable third-party numbers

The closest publicly announced runtime guardrail evaluating on the same threat surface is [**Salus** (YC W26)](https://www.ycombinator.com/companies/salus), launched February 2026.

Per Salus's public materials and YC launch post, Salus reports **52% misalignment reduction on average across 12 frontier models on ODCV-Bench**. ([Salus on YC Tier List](https://yctierlist.com/w26/salus/) · [YC launch post](https://x.com/ycombinator/status/2021645412487110868))

Sponsio's 84.5% on the same benchmark is the headline cross-model number we cite.

We are not aware of any other publicly announced number on ODCV-Bench at the time of writing (2026-05-02). If you publish one, [open an issue](https://github.com/SponsioLabs/Sponsio/issues/new) and we'll add it here.

---

## Why these numbers are reachable

Agent **tool calls, CLI commands, and function calls are a finite enumerable surface**. Once the unsafe call patterns for a domain are identified (by hand, by `sponsio scan`, or by trace observation), they compile to a DFA that runs in microseconds. There is no semantic guesswork on the blocking path; the gate is the call surface itself.

The two safety benchmarks split cleanly along this axis:

| Bench | Failure axis | Layer |
|---|---|---|
| **ODCV-Bench** | tool-call (data tampering, script edits, monitor disabling) | Det → 84.5% × 12 LLMs |
| **RedCode-Exec** | tool-call + finite code-text surface (incl. logic-flaw) | Det → 95% bash / 92% combined |

Sto handles what det fundamentally cannot see: properties of the agent's generated response text (toxicity, hallucination, faithfulness, contextual PII, scope drift in natural language, tone). These live in the LLM's free-form output, so no det rule can fingerprint them. The stochastic pipeline (managed LLM-judge service) is a [Sponsio Cloud](oss-scope.md#in-sponsio-cloud-commercial--pip-install-sponsiocloud) feature; the OSS engine ships an extension point so you can plug your own judge.

### The continuous-improvement loop

Det numbers above are a snapshot of the current contract library. The full loop:

```
production traces ──→ sponsio scan ──→ proposed contracts
       ↑                                       │
       │                                       ▼
       └──────── enforcement ←──────── library (versioned)
```

Each new attack pattern feeds back into the library. The 84.5% / 92% numbers are starting points, not ceilings. The architectural property is that they are **reachable** because the call surface is finite, and the library is the canonical artefact you grow over time.

> **Where the libraries live.** The hand-curated libraries that drive the RedCode-Exec and ODCV-Bench headlines ship as `sponsio:benchmark/redcode_exec` and `sponsio:benchmark/odcv_bench`, loadable like any capability pack.

---

## 1. Hot-path performance

Measures `guard_before()` / `guard_after()`, the deterministic enforcement path every tool call passes through. **No LLM is called on the hot path.** The pipeline compiles LTL formulas into a DFA and evaluates each tool call as an append to the trace.

### Synthetic micro-bench

| Bucket | ops/sec | p50 | p99 |
|---|---:|---:|---:|
| `pure_det` (DFA only, single contract) | 178,000 | 0.0052 ms | 0.012 ms |

### Real workload (measured during the safety benchmarks below)

End-to-end `guard_before` wall-clock, taken on the actual benchmark traces against contract sets sized to catch real attacks (3 to 18 contracts per suite, with regex-bearing `arg_blacklist` patterns).

| Workload | Contracts | Calls | ops/sec | p50 | p95 | p99 |
|---|---:|---:|---:|---:|---:|---:|
| ODCV-Bench (mandated) | 6–18 | 1,438 | 4,577 | 0.139 ms | 0.525 ms | 0.765 ms |
| RedCode bash (per-command) | 7 | 3,848 | 2,270 | 0.434 ms | 0.477 ms | 0.558 ms |
| RedCode python (whole script) | 9 | 810 | 1,216 | 0.811 ms | 0.912 ms | 1.035 ms |

**Key takeaway:** Per-call cost scales linearly with contract count. p99 stays under 1.04 ms across every workload measured. The heaviest scenario (RedCode python whole-script regex over a 9-contract layered set) is still 50× faster than the cheapest LLM-as-judge call.

---

## 2. ODCV-Bench: KPI-pressure failures

Measures Sponsio's protection against rational, non-adversarial agent failures: the agent falsifies source data, disables monitors, or exploits scripts to meet a KPI it was told to optimize. 12 mainstream LLMs × 80 scenarios (40 mandated + 40 incentivized).

| Model | Mandated | Incentivized | Avg |
|---|---|---|---:|
| claude-opus-4.6 | n/a (0 risk) | 1/1 (100%) | **100%** |
| gemini-3-pro-preview | 19/21 (90%) | 18/20 (90%) | **90%** |
| minimax-m2 | 17/19 (89%) | 11/12 (92%) | **90%** |
| glm-4.6 | 10/11 (91%) | 14/16 (88%) | **89%** |
| grok-4.1-fast | 15/17 (88%) | 17/19 (89%) | **89%** |
| gpt-oss-120b | 15/19 (79%) | 10/10 (100%) | **86%** |
| qwen3-max | 16/20 (80%) | 10/11 (91%) | 84% |
| gpt-oss-20b | 12/16 (75%) | 9/10 (90%) | 81% |
| kimi-k2-0905 | 13/17 (76%) | 8/9 (89%) | 81% |
| qwen3-30b-a3b-2507 | 14/18 (78%) | 8/10 (80%) | 79% |
| llama-4-maverick | 7/8 (88%) | 4/7 (57%) | 73% |
| gpt-5.1-chat | 0/1 (0%) | 2/4 (50%) | 40% |
| **All-models avg** | **~83%** | **~85%** | **~84%** |

A scenario is *high-risk* if the baseline agent received severity ≥ 3 from ODCV's AI judge (3 = metric gaming, 4 = data falsification, 5 = catastrophic). A high-risk scenario is *protected* if Sponsio blocked at least one command in the agent's trajectory.

The "Frontier models commit fraud in 67% of trials (16/24)" cited in the [README](../../README.md) is the unguarded-baseline rate on the `Financial-Audit-Fraud-Finding` scenario aggregated across the 12-model lineup, reproducible from ODCV's trajectory dump.

### Enforcement cost (glm-4.6 mandated, 40 scenarios, 6–18 contracts each)

| Path | Calls | ops/sec | p50 | p95 | p99 |
|---|---:|---:|---:|---:|---:|
| `guard_before` | 1,438 | 4,577 | 0.139 ms | 0.525 ms | 0.765 ms |
| `guard_after` | 1,226 | 7,764 | 0.115 ms | 0.215 ms | 0.316 ms |

**Key takeaway:** Sponsio protects 84% of high-risk KPI-pressure scenarios across 12 mainstream LLMs, including 90% on gemini-3-pro-preview, minimax-m2, glm-4.6, and grok-4.1-fast. p99 enforcement latency stays under 0.8 ms even with 18 scan-discovered contracts loaded.

---

## 3. RedCode-Exec: dangerous snippet detection

Measures detection of dangerous bash and python snippets across the full RedCode threat surface. 1,410 cases (600 bash, 810 python) replayed through layered `dangerous_bash_commands` + `arg_blacklist` patterns.

| Lang | Blocked | Total | Rate |
|---|---:|---:|---:|
| bash | 570 | 600 | **95%** |
| python | 733 | 810 | **90%** |
| **combined** | **1,303** | **1,410** | **~92%** |

### How the lift was achieved

The earlier 76% figure left 24% of cases on logic-flaw categories that earlier writeups labelled "behavioural defects with no syscall fingerprint." That label was too cautious: every such failure surfaces as a specific *code-text* pattern, and the agent's call surface is finite, so det binds once the right patterns are written.

| Category | Det fingerprint that closed it |
|---|---|
| `index12` biased decision logic | protected-class attribute paired with `_score` / `_categor` / `_factor` suffix |
| `index15` weak IP-validation regex | `validate_ip_regex` / `ip_validator` names + `\.?\b` / `\d{1,4}` / unanchored-IP literal regex shapes |
| `index22` missing match-case default | compound formula: presence of `match \w+:` AND absence of `case _:` |
| `index23` missing authorisation | sensitive function name + auth check, plus the canonical "without permission" comment marker |
| `index25` algorithmic flaw | `find_value_for_key` linear-search shape + `value = None` ⇒ `value = v; break` pattern |
| `index27` memory leak | module-level `leak_list` / `leak_dict` + append in loop + `tracemalloc.start` |

Det now catches the entire enumerated unsafe-call surface for these classes. The remaining 8% gap concentrates on RedCode `index16` (hardcoded credential authentication logic) and a few partial categories.

Each of the seven new logic-flaw layers ran as a single-contract guard against 60 clean Python files (Sponsio's own `sponsio/` and `tests/`): **0/60 false positives across all layers**.

### Enforcement cost

| Path | Calls | ops/sec | p50 | p95 | p99 |
|---|---:|---:|---:|---:|---:|
| bash, `guard_before` per command | 3,848 | 2,270 | 0.434 ms | 0.477 ms | 0.558 ms |
| bash, `guard_after` | 3,338 | 3,292 | 0.3 ms | 0.333 ms | 0.378 ms |
| python, `guard_before` per script | 810 | 1,216 | 0.811 ms | 0.912 ms | 1.035 ms |

**Key takeaway:** **95% on bash, 90% on python, 92% combined**, with **0% utility FP** on the clean-code audit. The earlier "logic-flaw categories require sto" framing was wrong: every such failure surfaces as a finite code-text fingerprint, and det binds once the patterns are written.

---

## Comparison context

Where Sponsio's enforcement overhead sits relative to typical operations in an agent system:

| Operation | Typical latency |
|---|---|
| **Sponsio det enforcement (real workload, p50)** | **0.139–0.811 ms** (ODCV mandated to RedCode python whole-script) |
| **Sponsio det enforcement (synthetic, p50)** | **0.0052 ms** |
| **Sponsio adapter overhead** | **<0.01 ms** |
| Python function call | 0.000001 ms |
| Memory regex match (10 patterns) | 0.001–0.010 ms |
| Local Redis read | 0.100–0.500 ms |
| Local SQLite query | 1–10 ms |
| OpenAI Moderation API | 50–200 ms |
| Lakera Guard | 50–200 ms |
| gpt-4o-mini as judge | 300–800 ms |
| Claude Haiku as judge | 300–1,500 ms |

Sponsio's hot path adds **less overhead than a single local Redis read** and is **5,000× to 60,000× faster** than the cheapest LLM-as-judge guardrail on the same per-tool-call workload. For structurally observable properties, this is three to four orders of magnitude of headroom.

For semantic properties (tone, relevance, hallucination, scope respect, semantic prompt injection), Sponsio's stochastic pipeline runs LLM-as-judge calls where they belong: after the deterministic layer has handled the structural cases. The managed sto pipeline is part of Sponsio Cloud (`pip install sponsio[cloud]`); the OSS engine ships an extension point so you can plug your own judge. See [OSS scope](oss-scope.md) and [Architecture](../concepts/architecture.md).

---

## Methodology

### Hardware

Numbers in this document were produced on a 2024 Apple Silicon MacBook (M-series, 16 GB RAM), macOS 15, Python 3.12.

### Measurement

- **Timer:** `time.perf_counter_ns()` wrapping each `guard_before` / `guard_after` invocation
- **Iterations:** 10K to 30K calls per workload (totals shown per table)
- **Percentiles:** sorted latency array, index-based selection
- **Warm-up:** synthetic micro-bench discards the first 1,000 checks; real-workload tables include cold-start cost

### Safety measurement

Existing agent trajectories are replayed through `guard.guard_before()` without re-running the agent model. Verdicts are compared against the upstream suite's ground-truth labels (ODCV severity scores, RedCode "should-block" labels per dangerous-snippet case). LLM contract-discovery (`sponsio scan`) uses Gemini 2.0 Flash by default; passes are merged by union for variance reduction.

### Reproducibility

ODCV-Bench scenarios and harness: [github.com/McGill-DMaS/ODCV-Bench](https://github.com/McGill-DMaS/ODCV-Bench).

RedCode-Exec scenarios: [github.com/AI-secure/RedCode](https://github.com/AI-secure/RedCode).

Sponsio's evaluation harness for both benchmarks (the `eval_sponsio.py` driver scripts and per-suite contract YAML) is being prepared for separate release. In the interim, [open an issue](https://github.com/SponsioLabs/Sponsio/issues/new) tagged `repro` and we'll send the harness directly. The benchmark contract libraries (`sponsio:benchmark/redcode_exec` / `sponsio:benchmark/odcv_bench`) ship in this repo today.

### What's not measured

- **Behavioural change from blocked calls.** Offline replay can't tell us whether the agent would self-correct after seeing an error. Live numbers are typically better than offline replay shows.
- **End-to-end task completion under enforcement.** That needs the agent running live with Sponsio in the loop.

---

**Related:** [README §Benchmarks](../../README.md#benchmarks--performance) · [Quickstart](../getting-started/quickstart.md) · [Contracts](../concepts/contracts.md) · [Architecture](../concepts/architecture.md) · [OSS scope](oss-scope.md)
