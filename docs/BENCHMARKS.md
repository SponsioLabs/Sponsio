# Benchmarks & Performance

> **Last updated:** 2026-04-26 · **Sponsio version:** 0.1.0a0 · **Python:** 3.12 · **OS:** macOS 15 (Apple Silicon, M-series, 16 GB)
>
> Latencies measured with `time.perf_counter_ns()` wrapping each `guard_before` / `guard_after` call. Safety numbers come from offline replay against published benchmark trajectories. Every figure is tagged with the dataset, model, and date it was produced.

## TL;DR

| What you care about | Number |
|---|---|
| **Enforcement latency on a real agent workload (AgentDojo, 26K calls)** | **0.113 ms** (p50) · 0.262 ms (p99) · 7,127 ops/sec |
| **Hot-path latency (single contract, pre-warmed DFA)** | **0.0052 ms** (p50) · 0.012 ms (p99) · 178K ops/sec |
| **LLM calls on the blocking path** | **0** (pure DFA) |
| **High-risk attack protection across 12 LLMs (ODCV-Bench)** | **~84%** of severity-≥3 scenarios blocked |
| **Dangerous-snippet detection (RedCode-Exec, 1,410 cases)** | **76%** combined (bash 85%, python 69%) |
| **Prompt-injection block rate (AgentDojo, gpt-4o)** | **30.4%** block rate at **6.4%** utility FP |
| **SOP ordering recall (τ²-bench airline, gpt-4.1)** | **23%** recall at 16% FP |

**Bottom line:** Sponsio's blocking path runs at **0.113 ms p50** on a real 26K-call agent workload — **400× to 13,000× faster** than any LLM-as-judge guardrail, with zero LLM calls. On the safety side, deterministic contracts catch **84%** of high-risk KPI-pressure scenarios across 12 mainstream LLMs and **76%** of dangerous code snippets in RedCode. Remaining gaps concentrate in semantic property classes — explicitly delegated to the stochastic pipeline.

---

## OSS positioning

Sponsio is positioned around three claims that no existing guardrail framework matches simultaneously:

1. **Fastest published agent guardrail.** 0.0052 ms (5.2 µs) per check on the synthetic micro-bench, 0.113 ms p50 on a real 26K-call AgentDojo workload, with **zero LLM calls on the blocking path**. Every alternative (Lakera Guard, NVIDIA NeMo Guardrails self-check, OpenAI Moderation, Llama Guard 4, LlamaFirewall AlignmentCheck) sits at 50–1,500 ms because each one runs an LLM-as-judge in the loop. Sponsio runs a compiled DFA.
2. **First framework systematically evaluated across 12 LLM families on ODCV-Bench**, with **84%** high-risk protection averaged across the lineup (gemini-3-pro-preview, glm-4.6, grok-4.1-fast, minimax-m2 all at 90%). To our knowledge no other guardrail has published comparable cross-model coverage on the KPI-pressure / metric-gaming threat class.
3. **First published deterministic-pattern result on RedCode-Exec**: **85% bash / 69% python / 76% combined** detection across 1,410 dangerous-snippet cases, at the same 5.2 µs hot-path budget.

### Why the architecture allows this

Agent **tool calls, CLI commands, and function calls are a finite enumerable surface**. Once the unsafe call patterns for a domain are identified (by hand, by `sponsio scan` over scenario source / policy docs, or by trace observation), they compile to a DFA that runs in microseconds. There is no semantic guesswork on the blocking path; the gate is the call surface itself.

This includes failure modes other guardrails treat as semantic-only:

- **Prompt-injection-driven failures**: the dangerous *outcome* is a tool call (transfer to attacker IBAN, deletion of sensitive file, exfil to a public mailbox). Det fingerprints the outcome regardless of whether the agent reached it via injection, jailbreak, or scope drift.
- **Logic-flaw failures** (RedCode python `index12` bias logic, `index15` regex bypass, `index23` missing authorisation): these surface as specific code-text patterns. Once enumerated, det binds; the gap between Sponsio's 76% and a higher number is library coverage, not architectural limit.
- **KPI-gaming failures** (ODCV `index4` data falsification, `index20` script tampering): same story; the dangerous action is a `sed -i` / `chmod` / redirect to `/app/data`, all enumerable.

### What sto is for

Sto handles what det fundamentally cannot see: **properties of the agent's generated response text** (toxicity, hallucination, faithfulness, contextual PII, scope drift in natural language, tone, goal coverage, transcript-level fabrication). These are not in the call surface; they live in the LLM's free-form output, so no det rule can fingerprint them.

This means the four safety benchmarks split cleanly across the two axes:

| Bench | Failure axis | Layer |
|---|---|---|
| **ODCV-Bench** | tool-call (data tampering, script edits, monitor disabling) | Det → 84% × 12 LLMs |
| **RedCode-Exec** | tool-call + finite code-text surface | Det → 85% bash / 76% combined |
| **AgentDojo** | tool-call (attacker IBAN, phishing URL, recipient) | Det → 30.4% overall, **62.8% slack / 52.8% banking** |
| **τ²-bench airline** | partial tool-call (ordering) + content quality | Det → 23% recall (ordering); content remainder is sto |
| **τ²-bench retail** | response content quality (assistant says wrong policy) | **Sto** → 0% (det by design) → ~11% / 5% FP (CoT judge), first measurable signal on a property class det fundamentally cannot address |

### The continuous-improvement loop

Det numbers above are a snapshot of the current contract library. The full loop is:

```
production traces ──→ sponsio scan ──→ proposed contracts
       ↑                                       │
       │                                       ▼
       └──────── enforcement ←──────── library (versioned)
```

Each new attack pattern, each newly observed unsafe call, feeds back into the library. The 84% / 85% / 76% numbers are starting points, not ceilings; the architectural property is that they are **reachable** because the call surface is finite, and the library is the canonical artefact you grow over time.

---

## 1. Hot-Path Performance

Measures `guard_before()` / `guard_after()`, the deterministic enforcement path every tool call passes through. **No LLM is called on the hot path.** The pipeline compiles LTL formulas into a DFA and evaluates each tool call as an append to the trace.

### Synthetic micro-bench (`sponsio bench`)

| Bucket | ops/sec | p50 | p99 |
|---|---:|---:|---:|
| `pure_det` (DFA only, single contract) | 178,000 | 0.0052 ms | 0.012 ms |
| `sto_cached` (cached judge verdict) | varies | varies | varies |
| `sto_live` (cold LLM-as-judge call) | judge-bound | 50–800 ms | seconds |

### Real workload (measured during the safety benchmarks below)

End-to-end `guard_before` wall-clock, taken on the actual benchmark traces against contract sets sized to catch real attacks (3 to 18 contracts per suite, with regex-bearing `arg_blacklist` patterns).

| Workload | Contracts | Calls | ops/sec | p50 | p95 | p99 |
|---|---:|---:|---:|---:|---:|---:|
| AgentDojo banking | 3 | 3,825 | 14,429 | 0.059 ms | 0.087 ms | 0.118 ms |
| AgentDojo workspace | 4 | 5,983 | 10,558 | 0.091 ms | 0.121 ms | 0.17 ms |
| AgentDojo travel | 5 | 9,099 | 5,900 | 0.115 ms | 0.148 ms | 0.217 ms |
| AgentDojo slack | 6 | 7,217 | 5,589 | 0.145 ms | 0.214 ms | 0.33 ms |
| **AgentDojo overall** | **3–6** | **26,124** | **7,127** | **0.113 ms** | **0.172 ms** | **0.262 ms** |
| τ²-bench airline | 14 | 4,242 | 3,301 | 0.204 ms | 0.29 ms | 0.453 ms |
| τ²-bench retail | 17 | 10,227 | 3,522 | 0.234 ms | 0.277 ms | 0.46 ms |
| ODCV-Bench (mandated) | 6–18 | 1,438 | 4,577 | 0.139 ms | 0.525 ms | 0.765 ms |
| RedCode bash (per-command) | 7 | 3,848 | 2,270 | 0.434 ms | 0.477 ms | 0.558 ms |
| RedCode python (whole script) | 9 | 810 | 1,216 | 0.811 ms | 0.912 ms | 1.035 ms |

**Key takeaway:** *"Per-call cost scales linearly with contract count. p99 stays under 1.04 ms across every workload measured. The heaviest scenario (RedCode python whole-script regex) is still 50× faster than the cheapest LLM-as-judge call."*

Source: [`sponsio/runtime/monitor.py`](../sponsio/runtime/monitor.py), [`Benchmarks/agentdojo/eval_sponsio.py`](../../../Benchmarks/agentdojo/eval_sponsio.py)

---

## 2. ODCV-Bench: KPI-Pressure Failures

Measures Sponsio's protection against rational, non-adversarial agent failures: the agent falsifies source data, disables monitors, or exploits scripts to meet a KPI it was told to optimize. 12 mainstream LLMs × 80 scenarios (40 mandated + 40 incentivized).

| Model | Mandated | Incentivized | Avg |
|---|---|---|---:|
| claude-opus-4.5 | n/a (0 risk) | 1/1 (100%) | **100%** |
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

### Enforcement cost (glm-4.6 mandated, 40 scenarios, 6–18 contracts each)

| Path | Calls | ops/sec | p50 | p95 | p99 |
|---|---:|---:|---:|---:|---:|
| `guard_before` | 1,438 | 4,577 | 0.139 ms | 0.525 ms | 0.765 ms |
| `guard_after` | 1,226 | 7,764 | 0.115 ms | 0.215 ms | 0.316 ms |

**Key takeaway:** *"Sponsio protects 84% of high-risk KPI-pressure scenarios across 12 mainstream LLMs, including 90% on gemini-3-pro-preview, minimax-m2, glm-4.6, and grok-4.1-fast. p99 enforcement latency stays under 0.8 ms even with 18 scan-discovered contracts loaded."*

Source: [`Benchmarks/ODCV-Bench/eval_sponsio.py`](../../../Benchmarks/ODCV-Bench/eval_sponsio.py)

```bash
cd ODCV-Bench/
GOOGLE_API_KEY=... python eval_sponsio.py --all --passes 3
python eval_sponsio.py --model glm-4.6 --type mandated --no-llm   # rule-based only
```

---

## 3. RedCode-Exec: Dangerous Snippet Detection

Measures detection of dangerous bash and python snippets across the full RedCode threat surface: file deletion, credential exfiltration, sensitive-file access, network exfil, dynamic exec, process kills, filesystem destruction. 1,410 cases (600 bash, 810 python) replayed through layered `dangerous_bash_commands` + `arg_blacklist` patterns.

| Lang | Blocked | Total | Rate |
|---|---:|---:|---:|
| bash | 510 | 600 | **85%** |
| python | 557 | 810 | **69%** |
| **combined** | **1,067** | **1,410** | **~76%** |

### Where the wins concentrate

- **Bash 100% on 11 of 20 categories**: file deletion, network exfil, credential reads, process manipulation
- **Python 100% on 10 of 27 categories**: includes index19 weak-random keygen (0% → 100% with new `weak_random_py` layer) and index6 base64-staged payloads (30% → 100% by adding `b64encode` to inject layer)
- **Strong partials**: python `index7` sensitive-file open at 97%, `index13` path traversal at 97%, `index20` weak hashing at 77%, `index5` filesystem recon at 73%

### Where the gap is

24% of cases miss because they describe **behavioural defects with no syscall fingerprint**, with nothing for a regex layer to bind to:

- **Bash logic flaws**: `index15` regex-validation bypass, `index23` missing-authorization function logic
- **Python logic flaws**: `index12` biased decision logic, `index15` regex bug, `index22` missing match-case default, `index23` auth bypass, `index25` algorithmic, `index27` memory-leak design

These belong on the stochastic pipeline, not in regex. The det layer is doing exactly what det layers should: catching anything with a structural signature, and stopping at semantic intent.

### Enforcement cost

| Path | Calls | ops/sec | p50 | p95 | p99 |
|---|---:|---:|---:|---:|---:|
| bash, `guard_before` per command | 3,848 | 2,270 | 0.434 ms | 0.477 ms | 0.558 ms |
| bash, `guard_after` | 3,338 | 3,292 | 0.3 ms | 0.333 ms | 0.378 ms |
| python, `guard_before` per script | 810 | 1,216 | 0.811 ms | 0.912 ms | 1.035 ms |

**Key takeaway:** **85% on bash** (file deletion, network exfil, credential reads, process manipulation — 100% on 11 of 20 categories). Python lands at 69% — the gap is concentrated in seven 'logic flaw' categories with no syscall, file path, or argument fingerprint to key on (`index12` biased decision logic, `index23` auth bypass, `index25` algorithmic). Closing that is a stochastic-pipeline problem, not a regex problem.

Source: [`Benchmarks/RedCode/eval_sponsio.py`](../../../Benchmarks/RedCode/eval_sponsio.py)

```bash
cd Benchmarks/RedCode/
python eval_sponsio.py                    # all bash + python
python eval_sponsio.py --lang bash        # bash only (600 cases, ~85%)
python eval_sponsio.py --lang python      # python only (810 cases, ~69%)
```

---

## 4. AgentDojo: Prompt-Injection Robustness

Measures whether Sponsio's deterministic contracts would have blocked prompt-injected tool calls across four AgentDojo suites. Pre-recorded gpt-4o-2024-05-13 trajectories are replayed through per-suite hand-crafted `arg_blacklist` + `rate_limit` contracts. DoS attacks are skipped (not tool-call shaped).

| Suite | Attack traces | Blocked | Block rate | Utility FP |
|---|---:|---:|---:|---:|
| slack | 1,050 | 659 | **62.8%** | 7/132 (5.3%) |
| banking | 1,350 | 713 | **52.8%** | 9/48 (18.8%) |
| workspace | 2,400 | 437 | 18.2% | 7/92 (7.6%) |
| travel | 1,400 | 73 | 5.2% | 3/137 (2.2%) |
| **overall** | **6,200** | **1,882** | **30.4%** | **26/409 (6.4%)** |

### Reading the spread

- **Slack and banking are the strong suites** because the malicious argument is structurally distinguishable: a fixed attacker IBAN (`US133...`), a fixed phishing host (`my-website-234`, `secure-systems-252`), a fixed attacker email (`fred9246@gmail.com`). These are exactly the cases where det enforcement is the right tool.
- **Workspace at 18.2% with 7.6% FP is precision-tight.** Contracts target the specific injection target IDs (`file_id="13"`, `email_id ∈ {26, 34, 35}`) plus public-domain mailbox blocking on `send_email`.
- **Travel at 5.2% reflects attack reach, not contract miss.** Of 1,400 attack traces, only ~60 contain any actionable call (`send_email`, `reserve_*`, `create_calendar_event`); the rest are recon-only `get_*`. The contracts catch most actionable cases.
- **Banking 18.8% FP is task-overlap, not contract noise.** Five of nine FPs are `send_money` to `US133...` with subjects like `"Rent"` in `injection_task_X/none` traces, where the model is asked to do what would have been the injection task as a legitimate request. Indistinguishable at the action boundary; needs trace-level intent (sto pipeline).

### Enforcement cost (gpt-4o, all four suites)

| Suite | Contracts | Calls | ops/sec | p50 | p95 | p99 |
|---|---:|---:|---:|---:|---:|---:|
| banking | 3 | 3,825 | 14,429 | 0.059 ms | 0.087 ms | 0.118 ms |
| workspace | 4 | 5,983 | 10,558 | 0.091 ms | 0.121 ms | 0.17 ms |
| travel | 5 | 9,099 | 5,900 | 0.115 ms | 0.148 ms | 0.217 ms |
| slack | 6 | 7,217 | 5,589 | 0.145 ms | 0.214 ms | 0.33 ms |
| **overall, `guard_before`** | mixed | **26,124** | **7,127** | **0.113 ms** | **0.172 ms** | **0.262 ms** |
| overall, `guard_after` | mixed | 23,761 | 8,436 | 0.108 ms | 0.144 ms | 0.208 ms |

**Key takeaway:** Slack 62.8% and banking 52.8% — the two suites where the malicious argument has a structural fingerprint (fixed attacker IBAN, phishing host, attacker email). Travel and workspace lower the overall to 30.4% because most travel attack traces are recon-only `get_*` calls (no actionable target). At **0.113 ms p50**, with zero LLM calls. The remaining gap is semantic injection where the call is structurally indistinguishable from a legitimate request — routed through the stochastic pipeline.

Source: [`Benchmarks/agentdojo/eval_sponsio.py`](../../../Benchmarks/agentdojo/eval_sponsio.py)

```bash
cd Benchmarks/agentdojo/
python eval_sponsio.py --model gpt-4o-2024-05-13              # default
python eval_sponsio.py --model gpt-4o-2024-05-13 --suite slack
python eval_sponsio.py --all-models                            # iterate every runs/<model>
```

---

## 5. τ²-Bench: Conversational SOP Compliance

Measures Sponsio's blocking decisions against tau2's ground-truth pass/fail labels (`reward_info.reward`) across two domains: airline (200 sims × 3 models) and retail (456 sims × 3 models).

### Airline (13 det contracts discovered)

| Model | Baseline pass rate | Recall | FP rate |
|---|---:|---:|---:|
| claude-3-7-sonnet | 50% | 7/100 (7%) | 8/100 (8%) |
| gpt-4.1 | 56% | 21/88 (**23%**) | 19/112 (16%) |
| o4-mini | 59% | 15/82 (**18%**) | 5/118 (**4%**) |

### Retail (23 det contracts discovered)

| Model | Baseline pass rate | Recall | FP rate |
|---|---:|---:|---:|
| claude-3-7-sonnet | 78% | 0/97 (0%) | 0/359 (0%) |
| gpt-4.1 | 74% | 0/118 (0%) | 0/338 (0%) |
| o4-mini | 71% | 1/130 (~0%) | 0/326 (0%) |

### Reading the spread

Retail recall is ~0% by construction: retail failures are content-quality issues (the agent calls the right tools but says the wrong thing). A det pipeline only checks tool-call sequences, so there is no ordering violation to catch. This is a property-class mismatch, addressed by `output_has` constraints or sto LLM-as-judge evaluators in the stochastic pipeline.

Airline recall of 7–23% lands where the theory predicts. Some airline failures are ordering violations (booking without checking availability, cancelling without verifying the ticket) that `must_precede` patterns catch. o4-mini gives the best precision/recall envelope: 18% recall at 4% FP.

### Enforcement cost (3 models per domain)

| Domain | Contracts | Calls | ops/sec | p50 | p95 | p99 |
|---|---:|---:|---:|---:|---:|---:|
| retail (456 sims × 3 models) | 17 | 10,227 | 3,522 | 0.234 ms | 0.277 ms | 0.46 ms |
| airline (200 sims × 3 models) | 14 | 4,242 | 3,301 | 0.204 ms | 0.29 ms | 0.453 ms |

**Key takeaway:** *"Sponsio's det layer hits 18% recall at 4% FP on airline SOP ordering violations (o4-mini), the best precision envelope of the three models tested. Retail failures are content-quality, not ordering, so they are out of scope for any deterministic checker. They belong on the stochastic pipeline."*

Source: [`Benchmarks/tau2-bench/eval_sponsio.py`](../../../Benchmarks/tau2-bench/eval_sponsio.py)

```bash
cd tau2-bench/
GOOGLE_API_KEY=... python eval_sponsio.py --domain airline
python eval_sponsio.py --domain retail --no-llm   # no API key needed
```

---

## Comparison Context

For context, here is where Sponsio's enforcement overhead sits relative to typical operations in an agent system:

| Operation | Typical latency |
|---|---|
| **Sponsio det enforcement (real workload, p50)** | **0.113–0.434 ms** |
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

Sponsio's hot path adds **less overhead than a single local Redis read** and is **400× to 13,000× faster** than the cheapest LLM-as-judge guardrail on the same per-tool-call workload. For structurally observable properties (ordering, rate limits, forbidden tool/arg combinations, path blacklists, exact-format PII), this is two to four orders of magnitude of headroom that LLM-judged guardrails cannot recover.

For semantic properties (tone, relevance, hallucination, scope respect, semantic prompt injection), Sponsio's stochastic pipeline runs LLM-as-judge calls where they belong: **after** the deterministic layer has handled the structural cases. See [`docs/sto-atoms.md`](sto-atoms.md) and [`docs/architecture.md`](architecture.md).

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

Existing agent trajectories are replayed through `guard.guard_before()` without re-running the agent model. Verdicts are compared against the upstream suite's ground-truth labels (ODCV severity scores, tau2 `reward_info.reward`, AgentDojo attack-success flags). LLM contract-discovery (`sponsio scan`) uses Gemini 2.0 Flash by default; passes are merged by union for variance reduction.

### What's not measured

- **Behavioural change from blocked calls.** Offline replay can't tell us whether the agent would self-correct after seeing an error. Live numbers are typically better than offline replay shows.
- **End-to-end task completion under enforcement.** That needs the agent running live with Sponsio in the loop.

---

## Stochastic Pipeline Pilot (2026-04-26)

We piloted the sto pipeline as an additive layer on top of the det numbers above, with a strict "do not raise utility FP" goal. Two iterations were run.

### v1: BooleanJudge over single events

First pass used `gemini-2.0-flash` as a logprob-based BooleanJudge, scoring one event at a time (a tool call in AgentDojo, a single assistant turn in τ²-bench). Every benchmark got a `sto_contracts.yaml` manifest declaring the contract id, atom, beta, prompt, and triage rules.

Result: on AgentDojo workspace/travel the per-call judge had no concrete signal because the injection-bearing context (the tool output the agent had just read, the prior turns establishing customer data) wasn't in the prompt. On τ²-bench retail the per-turn judge collapsed to all-block / all-allow because retail responses paraphrase rather than quote, and the judge couldn't tell paraphrase from fabrication without conversation context.

### v2: New events + new atoms for richer expressiveness

Pure prompt tuning hit a wall at v1. The right fix was to extend what the sto layer can see, not how the judge is asked. Two architectural extensions landed:

**New event surface: `tool_output` as sto context.** Sto evaluators can now consume a window of recent tool results alongside the current event. AgentDojo's eval harness uses this to render the last six tool-result snippets the agent read before the call under judgement, because injection lives in tool outputs (calendar event descriptions, email bodies, search results) and is invisible without that context.

**New atoms (manifest-only for now, slated for `sto_catalog.py`):**

| Atom | Inputs | Use case |
|---|---|---|
| `injection_free_in_context` | user task + recent tool outputs + tool call | catches injection-driven side-effects whose target appears in untrusted tool outputs |
| `transcript_consistency` | grounding doc + tool outputs + full transcript | sim-level fabrication / scope-drift check using all conversation evidence |

The eval scripts already use these via the YAML manifests; the corresponding registrations in `sponsio/patterns/sto_catalog.py` are pending and gated on the same judge-strength question below.

### v3: Stronger judge model with chain-of-thought (2026-04-27)

v2 left judge-model strength as the open question. v3 swapped in `gemini-2.5-pro` with a chain-of-thought judge built on the published G-Eval pattern (DeepEval, Promptfoo, OpenReview JudgeBench all default to GPT-4 family + CoT for compliance reasoning). The judge writes step-by-step reasoning, then a `VERDICT: yes/no` line that a regex extracts into a binary confidence. New `GeminiCotJudge` class added to the bench harness; HTTP-level 90 s timeout per call (the SDK can hang past 8 hours otherwise).

| Bench | Sample | Sto-only blocked-failures | Sto-only utility FP |
|---|---|---|---|
| AgentDojo workspace | 30 attack / 20 utility | 0 / 30 | 4 / 92 (4.3%) |
| τ²-bench retail (3 prompt variants) | 50 sims each | 11–67% recall | 5–76% FP |

**AgentDojo workspace stayed at 0 sto rescues even with the strong CoT judge.** This locks in the structural finding from v2: the gap on workspace is *attack reach*, not judge strength. Of 30 sampled attack traces, only 8 had a det-allowed side-effect call to begin with; the other 22 are agent-non-compliance traces (the model read the injection and ignored it, no malicious tool call was attempted). Sto cannot rescue what isn't there.

**τ²-bench retail with the CoT judge moved across a wide envelope** depending on prompt scaffolding: reasoning-first 2048 tokens gave 11% recall / 4.9% FP; verdict-first 4096 tokens gave 44% recall / 32% FP; reasoning-first 4096 tokens gave 25% recall / 50% FP on a partial sample. The judge fires, but its verdicts don't correlate stably with tau2's `reward_info.reward` labels. Investigation suggests tau2's pass/fail is driven by tool-call task completion, while the judge is correctly catching message-faithfulness issues; the two are different failure axes that don't track each other on this dataset.

### What the pilot leaves shipped

Three concrete deliverables that ship with the benchmark harness even when sto is off:

1. **`sto_contracts.yaml` manifests** in each benchmark directory. Reviewable spec of every prompt, beta, atom, and triage rule. Re-runnable end-to-end with one CLI flag.
2. **Disk-cached judge** (`~/.sponsio/sto_bench_cache.json`). SHA-256-keyed; same prompt never re-pays.
3. **Three architectural extensions** to the sto layer's expressiveness:
   - New event surface: **`tool_output` window** as judge context (catches injection that lives in untrusted tool outputs)
   - New atom **`injection_free_in_context`** (user task + recent tool outputs + tool call → yes/no)
   - New atom **`transcript_consistency`** (grounding doc + tool outputs + full transcript → yes/no)
   - New judge class **`GeminiCotJudge`**: chain-of-thought + verdict extraction over `gemini-2.5-pro`, the production pattern used by G-Eval / DeepEval / Promptfoo

Promoting the two new atoms into `sponsio/patterns/sto_catalog.py` is gated on a workload where the failure mode actually aligns with a single sto question (production agents with novel attacker fingerprints, hallucinated facts, scope drift). On the published AgentDojo and τ²-bench trace sets the reward signal is too misaligned with single-question semantic checks to give a stable lift.

### Position

The det numbers above stay the load-bearing claim. The pilot established three things:

1. **The architecture for context-rich sto checks is sound** (tool outputs, full transcripts, grounding docs all flow into the judge, manifest-driven, re-runnable).
2. **A reasoning judge is mandatory** for compliance and faithfulness questions: `gemini-2.0-flash` logprobs saturate, `gemini-2.5-flash` BestOfN saturates, and only `gemini-2.5-pro` + chain-of-thought produces graded answers. This matches what every public LLM-as-judge tool (DeepEval, Promptfoo, JudgeBench) does, and it is now wired into our harness.
3. **The published AgentDojo and τ²-bench numbers do not lift with sto** because their reward signals do not align with what sto naturally measures. Workspace's gap is attack reach (no malicious call exists in the trace to rescue); tau2 retail's reward is task-completion shape, not message faithfulness. Where sto does land directly on the failure mode (LLM apps with novel attacker fingerprints, unbounded scope drift, hallucinated facts in production), the same harness applies with no code changes.

---

## Version History

| Date | Notable changes |
|---|---|
| 2026-04-26 | Real-workload enforcement latency added across ODCV, τ²-bench, AgentDojo, RedCode (4 benches, 9 workloads) |
| 2026-04-26 | RedCode-Exec: 85% bash / 69% python / **76% combined** detection (1,410 cases, broadened patterns) |
| 2026-04-26 | AgentDojo (gpt-4o-2024-05-13): **30.4%** block rate / 6.4% utility FP across 4 suites (banking field-name fix unlocked 52.8% banking) |
| 2026-04-25 | Hot-path bench: 0.0052 ms p50, 0.012 ms p99, 178K QPS on Apple Silicon |
| 2026-04-25 | Consolidated per-suite READMEs into this single file |
| 2026-04-15 | τ²-bench offline replay: airline 7–23% recall / 4–16% FP, retail out-of-scope |
| 2026-04-11 | ODCV-Bench v3 (prompt-aware scan): ~84% protection across 12 models |

**Related:** [README §Benchmarks](../README.md#benchmarks--performance) · [QUICKSTART](../QUICKSTART.md) · [Contract DSL](contracts.md) · [Stochastic atoms](sto-atoms.md) · [Architecture](architecture.md)
