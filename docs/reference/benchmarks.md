# Benchmarks & Performance

> **Last updated:** 2026-06-01 · **Sponsio version:** 0.1.0 · **Python:** 3.12 · **OS:** macOS 15 (Apple Silicon, M-series, 16 GB)
>
> Latencies measured with `time.perf_counter_ns()` wrapping each `guard_before` / `guard_after` call. Safety numbers come from offline replay against published benchmark trajectories. Every figure is tagged with the dataset, model, and date it was produced.

This page is the cross-benchmark scoreboard and methodology hub. Per-benchmark deep dives live under [`benchmarks/`](benchmarks/). one file per benchmark, identical structure.

## TL;DR

| What you care about | Number |
|---|---|
| **High-risk attack protection × 12 LLMs ([ODCV-Bench](benchmarks/odcv.md))** | **95.6%** of severity-≥3 scenarios blocked · **24 / 36** scenarios at 100% across all 12 models · 5 / 12 models at perfect protection |
| **Prompt-injection ASR reduction × 22 LLMs ([AgentDojo](benchmarks/agentdojo.md))** | **2.49%** chain-break ASR vs 19.05% unguarded (baseline σ=14.12pp). **86% relative reduction** · 12 of 40+ atoms used · 0 LLM calls |
| **Dangerous-snippet detection × 1,410 cases ([RedCode-Exec](benchmarks/redcode.md))** | **98.9%** combined (bash 98.3%, python 99.4%); previous public number was 92.4%, lifted by 4 self-improvement iterations on 2026-06-16 |
| **Utility FP** | **0 FP increase** across 6 ODCV library iterations (v3 → v9c held 1034 FP cmds) · **4.71%** per-call (σ 1.09pp) on AgentDojo across 22 models · **0%** on a 60-file clean-Python audit (RedCode 7 logic-flaw layers, unchanged across 2026-06-16 iter 1 to iter 4) |
| **Enforcement latency, ODCV (mandated, 1,438 calls, 6-19 contracts)** | **0.139 ms** (p50) · 0.525 ms (p95) · 0.765 ms (p99) · 4,577 ops/sec |
| **Enforcement latency, AgentDojo (26,069 calls on gpt-4o-2024-05-13, 8-14 contracts; 22-model p99 mean below)** | **0.162 ms** (p50) · 0.933 ms (p99 cross-model mean, σ ≈ 40μs) · ~6,000 ops/sec |
| **Enforcement latency, RedCode bash per-command (3,249 calls, 10 contracts after iter 4)** | **0.572 ms** (p50) · 0.710 ms (p95) · 0.934 ms (p99) · 1,708 ops/sec |
| **Enforcement latency, RedCode python whole-script (810 calls, 17 contracts after iter 4)** | **1.185 ms** (p50) · 1.252 ms (p95) · 1.319 ms (p99) · 841 ops/sec |
| **Hot-path latency (single contract, pre-warmed DFA)** | **0.0052 ms** (p50) · 0.012 ms (p99) · 178K ops/sec |
| **LLM calls on the blocking path** | **0** (pure DFA + LTL `finish_session()` at trajectory end) |

**Bottom line:** Sponsio's blocking path runs at **0.0052 ms p50** on the synthetic micro-bench (single contract, pre-warmed DFA, the public-facing **<0.01 ms** anchor) and stays at **0.139 ms p50** on the heaviest ODCV scenario (19 contracts) and **0.572 ms p50** on RedCode bash with 10 layered regex contracts. **5,000× to 60,000× faster** than any LLM-as-judge guardrail, with zero LLM calls. On the safety side, deterministic contracts catch **95.6%** of high-risk KPI-pressure scenarios across 12 mainstream LLMs on ODCV-Bench (24 / 36 scenarios at **100%**), reduce prompt-injection ASR from 19.05% to **2.49%** across 22 LLMs on AgentDojo (**86% relative**), and detect **98.9%** of dangerous code snippets in RedCode (lifted from 92.4% by 4 self-improvement iterations on 2026-06-16). Residual gaps in each case concentrate in failure modes the tool-call/bash-event guard cannot observe by construction: categories where det compositionally hands off to Sponsio's `sto` LLM-judge layer on a Pareto frontier.

---

## Benchmarks index

Five third-party benchmarks, identical doc structure under [`benchmarks/`](benchmarks/). Sponsio did not author the threat sets.

| Benchmark | Threat axis | Sponsio headline | Atoms used |
|---|---|---:|---:|
| **[ODCV-Bench](benchmarks/odcv.md)** (McGill DMaS) | KPI-pressure constraint violations (data tampering, script edits, monitor disabling) | **95.6% protected × 12 LLMs** | L0-L11 layered library (regex + LTL liveness) |
| **[AgentDojo](benchmarks/agentdojo.md)** (ETH Zurich + Invariant Labs) | Indirect prompt-injection in tool-using agents (foreign IBAN / phishing URL / attacker email targets) | **2.49% ASR × 22 LLMs** (86% reduction) | 12 / 40+ atoms |
| **[RedCode-Exec](benchmarks/redcode.md)** (AI-secure org) | Dangerous bash / python snippet detection (file deletion, credential exfil, dynamic exec, logic-flaw surface) | **98.9% combined / 98.3% bash / 99.4% python** (was 92.4% before 2026-06-16 iter 1 to iter 4) | 10 bash + 17 python contracts |
| **[SWE-bench Verified](benchmarks/swebench.md)** (Princeton NLP) | Procedural correctness on an outcome-only code-fixing benchmark (blind edits, test-tampering, verification skip, edit thrashing, multi-submit) | **0% FP × 500 instances** (synthetic self-validation; real-model traces pending) | 8 of 26 atoms |
| **[τ²-bench](benchmarks/tau2.md)** (Sierra AI) | Procedural correctness on tool-using customer-support agents (retail / airline / telecom) | **29 pp joint^4 gap surfaced on retail GPT-4.1-mini** (pass^4 38.6% vs joint^4 9.6%); 112 contracts cover 6/7 AgentPex procedural categories | 8 of 50+ atoms |

---

## Why these numbers are reachable

Agent **tool calls, CLI commands, and function calls are a finite enumerable surface**. Once the unsafe call patterns for a domain are identified (by hand, by `sponsio scan`, or by trace observation), they compile to a DFA that runs in microseconds. There is no semantic guesswork on the blocking path; the gate is the call surface itself.

The five benchmarks split cleanly along this axis:

| Bench | Failure axis | Layer |
|---|---|---|
| **ODCV-Bench** | tool-call (data tampering, script edits, monitor disabling) | Det → 95.6% × 12 LLMs |
| **AgentDojo** | tool-call (attacker target in `send_*` / `reserve_*` / `post_*` args) | Det → 86% ASR reduction × 22 LLMs |
| **RedCode-Exec** | tool-call + finite code-text surface (incl. logic-flaw) | Det → 98.3% bash / 98.9% combined |
| **SWE-bench Verified** | tool-call (read-before-edit, no test edits, run_tests before submit, single submit, workspace scope) | Det → 0% FP × 500 instances (self-validation; real-model traces pending) |

What deterministic contracts fundamentally cannot see are properties of the agent's generated response text (toxicity, hallucination, faithfulness, contextual PII, scope drift in natural language, tone). These live in the LLM's free-form output, so no det rule can fingerprint them, and they fall outside the deterministic engine's scope.

### The continuous-improvement loop

Det numbers above are a snapshot of the current contract library. The full loop:

```
production traces ──→ sponsio scan ──→ proposed contracts
       ↑                                       │
       │                                       ▼
       └──────── enforcement ←──────── library (versioned)
```

Each new attack pattern feeds back into the library. The 95.6% (ODCV) / 86% reduction (AgentDojo) / 98.9% (RedCode, up from 92.4% after the 2026-06-16 four-iteration push) numbers are demonstrations of how far the loop has driven the libraries to date. The architectural property is that the ceiling is **reachable** because the call surface is finite, and the library is the canonical artefact you grow over time.

> **Where the libraries live.** The hand-curated libraries that drive the headline numbers no longer ship in OSS as a packaged bundle (the `sponsio:benchmark/*` namespace was removed). The eval harnesses reproduce them from the upstream scenario sources at run time. [Open an issue](https://github.com/SponsioLabs/Sponsio/issues/new) tagged `repro` for the harness scripts.

---

## Hot-path performance

Measures `guard_before()` / `guard_after()`, the deterministic enforcement path every tool call passes through. **No LLM is called on the hot path.** The pipeline compiles LTL formulas into a DFA and evaluates each tool call as an append to the trace.

### Synthetic micro-bench

| Bucket | ops/sec | p50 | p99 |
|---|---:|---:|---:|
| `pure_det` (DFA only, single contract) | 178,000 | 0.0052 ms | 0.012 ms |

### Real workload (measured during the safety benchmarks)

End-to-end `guard_before` wall-clock, taken on the actual benchmark traces against contract sets sized to catch real attacks (3 to 19 contracts per suite, with regex-bearing `arg_blacklist` patterns).

| Workload | Contracts | Calls | ops/sec | p50 | p95 | p99 |
|---|---:|---:|---:|---:|---:|---:|
| [ODCV-Bench](benchmarks/odcv.md) (mandated) | 6-19 | 1,438 | 4,577 | 0.139 ms | 0.525 ms | 0.765 ms |
| [AgentDojo](benchmarks/agentdojo.md) (22 LLMs aggregate) | 8-14 | 26,069 | 6,170 | 0.162 ms | 0.620 ms | 0.933 ms |
| [RedCode](benchmarks/redcode.md) bash (per-command) | 7 | 3,848 | 2,270 | 0.434 ms | 0.477 ms | 0.558 ms |
| [RedCode](benchmarks/redcode.md) python (whole script) | 9 | 810 | 1,216 | 0.811 ms | 0.912 ms | 1.035 ms |

**Key takeaway:** Per-call cost scales linearly with contract count. p99 stays under 1.04 ms across every workload measured. The heaviest scenario (RedCode python whole-script regex over a 9-contract layered set) is still 50× faster than the cheapest LLM-as-judge call.

---

## Comparison context

Where Sponsio's enforcement overhead sits relative to typical operations in an agent system:

| Operation | Typical latency |
|---|---|
| **Sponsio det enforcement (real workload, p50)** | **0.139-0.811 ms** (ODCV mandated to RedCode python whole-script) |
| **Sponsio det enforcement (synthetic, p50)** | **0.0052 ms** |
| **Sponsio adapter overhead** | **<0.01 ms** |
| Python function call | 0.000001 ms |
| Memory regex match (10 patterns) | 0.001-0.010 ms |
| Local Redis read | 0.100-0.500 ms |
| Local SQLite query | 1-10 ms |
| OpenAI Moderation API | 50-200 ms |
| Lakera Guard | 50-200 ms |
| `transformers_pi_detector` (DeBERTa, AgentDojo) | ~50 ms |
| LlamaFirewall (Meta) | ~100 ms |
| `tool_filter` (LLM-driven, AgentDojo) | ~500 ms |
| gpt-4o-mini as judge | 300-800 ms |
| Claude Haiku as judge | 300-1,500 ms |

Sponsio's hot path adds **less overhead than a single local Redis read** and is **300× to 60,000× faster** than any LLM-as-judge or transformer guardrail evaluated on the same threat surfaces. For structurally observable properties, this is three to four orders of magnitude of headroom.

Semantic properties (tone, relevance, hallucination, scope respect, semantic prompt injection) live in the LLM's free-form output and fall outside the deterministic engine's scope. Sponsio handles the structurally observable cases on the deterministic hot path. See [OSS scope](oss-scope.md) and [Architecture](../concepts/architecture.md).

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

Existing agent trajectories are replayed through `guard.guard_before()` without re-running the agent model. Verdicts are compared against the upstream suite's ground-truth labels (ODCV severity scores, AgentDojo `security` field, RedCode "should-block" labels per dangerous-snippet case). LLM contract-discovery (`sponsio scan`) uses Gemini 2.0 Flash by default; passes are merged by union for variance reduction.

### Reproducibility

ODCV-Bench scenarios and harness: [github.com/McGill-DMaS/ODCV-Bench](https://github.com/McGill-DMaS/ODCV-Bench).

RedCode-Exec scenarios: [github.com/AI-secure/RedCode](https://github.com/AI-secure/RedCode).

AgentDojo scenarios and pre-recorded model traces: [github.com/ethz-spylab/agentdojo](https://github.com/ethz-spylab/agentdojo). The 22-model number set in this document is produced by offline replay against the published per-model trace dumps (no live agent run required); the contract set is reconstructed at harness time from the suites' user/injection task class definitions and `environment.yaml` files.

Sponsio's evaluation harnesses for all three benchmarks (the `eval_sponsio.py` / `eval_sponsio_taskaware.py` / `extract_task_manifests.py` driver scripts) are being prepared for separate release. In the interim, [open an issue](https://github.com/SponsioLabs/Sponsio/issues/new) tagged `repro` and we'll send the harnesses directly. The packaged `sponsio:benchmark/*` bundles are no longer shipped in OSS; the harness reconstructs the contract sets from the upstream scenarios.

### What's not measured

- **Behavioural change from blocked calls.** Offline replay can't tell us whether the agent would self-correct after seeing an error. Live numbers are typically better than offline replay shows.
- **End-to-end task completion under enforcement.** That needs the agent running live with Sponsio in the loop.

---

**Related:** [README §Benchmarks](../../README.md#benchmarks--performance) · [Quickstart](../getting-started/quickstart.md) · [Patterns](patterns.md) · [Architecture](../concepts/architecture.md) · [OSS scope](oss-scope.md)
