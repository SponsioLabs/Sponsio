---
title: Benchmark contract libraries
description: Hand-curated deterministic contract libraries that drive Sponsio's published benchmark headlines (RedCode-Exec, ODCV-Bench, AgentDojo, SWE-bench). Distinct from the capability packs in `contract-lib.md` — these are benchmark-reproduction artefacts.
---

# Benchmark contract libraries

Sponsio ships four hand-curated deterministic contract libraries that produce its published benchmark headline numbers. Each YAML file is a **library**: a versioned, reviewable spec of unsafe-call patterns that compiles to a DFA at startup and runs at microsecond latency on the agent's tool-call boundary.

| File | Benchmark | Contracts | Last result |
|---|---|---|---|
| [`sponsio/contracts/benchmark/redcode_exec.yaml`](../../sponsio/contracts/benchmark/redcode_exec.yaml) | RedCode-Exec dangerous-snippet detection | 26 | bash 95% / python 90% / **92% combined** detection on 1,410 cases, **0% utility FP** on a 60-file clean-code audit |
| [`sponsio/contracts/benchmark/odcv_bench.yaml`](../../sponsio/contracts/benchmark/odcv_bench.yaml) | ODCV-Bench KPI-pressure protection | 19 library + per-scenario LLM-scan cache + per-scenario liveness/provenance/rate-limit | **95.6% combined** high-risk protection across 12 LLM families · **24 / 36 scenarios at 100%** · **0 FP increase** across v3 → v9c library iterations (1034 CLEAN-cmd blocks held flat) |
| [`sponsio/contracts/benchmark/agentdojo.yaml`](../../sponsio/contracts/benchmark/agentdojo.yaml) | AgentDojo prompt-injection / lethal-trifecta defence (banking / workspace / travel / slack) | 17 per-suite hand-crafted + 14 task-aware generic = **31** | Chain-break ASR reductions on the order of **56.3% → 5.7%** (gpt-4-0125-preview), **34.9% → 4.9%** (claude-3-5-sonnet) across 22 frontier LLMs, with **< 7% utility FP** on injection-free traces |
| [`sponsio/contracts/benchmark/swebench.yaml`](../../sponsio/contracts/benchmark/swebench.yaml) | SWE-bench Verified procedural-correctness library (universal layer only) | 8 universal + `capability/filesystem` (~9) + ~3 per-instance scope contracts = **~20 / instance** | Model-invariant by construction (references only trace-event fields, no model identity); **10,123 total contract fires per full 500-instance eval pass** |

Each bundle is a yaml documentation-of-record. The Python sources used to produce the reported numbers remain the executing source of truth and are kept in sync by convention.

> **Distinct from the capability packs.** The packs documented in [`contract-lib.md`](contract-lib.md) (`sponsio:capability/shell`, `sponsio:capability/filesystem`, …) are auto-included by `sponsio onboard` based on detected tool inventory. The libraries on this page are benchmark-reproduction artefacts: most patterns are tagged `code-execution` or `code-quality` and would generalise, but a handful are calibrated to dataset-specific markers and need editing before production reuse.

All four libraries are loadable via `include:` — the same mechanism the capability packs use:

```yaml
# sponsio.yaml
agents:
  my_bot:
    include:
      - sponsio:benchmark/redcode_exec
      - sponsio:benchmark/odcv_bench
      - sponsio:benchmark/agentdojo
      - sponsio:benchmark/swebench
```

> **τ²-bench bundle not yet shipped.** The τ²-bench procedural-correctness library (78 pattern sites compiling to 120 contracts across retail / airline / telecom) currently ships only as Python in `Benchmarks/tau2-bench/procedure_correctness/`. The 63 raw-AST contracts need an LTL-syntax conversion pass before the yaml can round-trip through the OSS loader; that work is queued.

Each contract is stamped with a source tag of the form `library:benchmark.<bench>/<applicability>` so `sponsio scan` / `sponsio report` / overrides can address rules by their portability tag.

## Portability across agent flows

Each contract carries an `applicability` tag. Treat it as a hint about how much editing the contract needs before reuse outside its origin benchmark:

| Tag | Meaning | Edit needed |
|---|---|---|
| **`general`** | Applies to any tool-using agent. Drop in unchanged. | None |
| **`code-execution`** | Applies to any agent that runs bash / shell / scripts (coding agent, ops agent, build agent). The patterns target syscall-shaped fingerprints. | Usually none |
| **`data-exfiltration`** | Public-mailbox / attacker-IBAN / phishing-URL blacklists. Patterns are general; the regex literals need updating to your deployment's attacker corpus. | Replace regex literals; structure stays |
| **`prompt-injection`** | Injection-side recipient / URL / argument scrubbing. Shape ports across deployments; the specific markers (suffixes, domains) are AgentDojo-calibrated. | Replace markers with your threat model's equivalents |
| **`action-ordering`** | Tool-call ordering and rate-limit gates (single-transfer cap, cooldown, no_reversal, mutex). | None for the same domain; rebind tool names |
| **`agentdojo-specific`** | Calibrated to AgentDojo's hardcoded injection payloads (attacker IBAN, fixed phishing addresses, attacker hotel names). Useful for reproducing the published headline numbers; rarely useful outside the benchmark. | Replace payload markers, or skip and rely on the portable layer |
| **`swebench-specific`** | Calibrated to a code-fixing workflow with `read` / `edit` / `run_tests` / `submit_patch` tool names. Patterns (exploration-depth, anti-thrash, test-protection) generalise to any patch-then-test workflow. | Rename tool atoms; the regex set for test-protection (`def test_`, `@pytest.mark.skip`, `assert\s`) ports across Python codebases unchanged |
| **`kpi-pressure`** | Applies to any agent under metric optimisation that calls evaluator / scoring scripts. The pattern (rate-limit stochastic evaluators, block state-dir restructuring) is general; only the path conventions (`/app/data/`) are container-specific. | Replace path conventions with your deployment's equivalents |
| **`code-quality`** | Applies to any agent that produces code (review, refactor, code-gen). The patterns target source-text shapes (weak regex, missing-auth structure, biased scoring keys). | None for the same language; port the regex if the target language differs |
| **`odcv-specific` / `redcode-specific`** | Calibrated to specific dataset markers (e.g. RedCode's canonical "without permission" comment string, ODCV's `run_evaluation.sh` filename). | Replace markers with your deployment's equivalents, or skip the pattern |

## Pointers

- Sponsio's pattern factories: [`sponsio/patterns/library.py`](../../sponsio/patterns/library.py) (`arg_blacklist`, `must_precede`, `rate_limit`, `loop_detection`, `arg_length_limit`, etc.).
- Capability packs auto-loaded by onboard: [`docs/reference/contract-lib.md`](contract-lib.md).
- The `sponsio scan` CLI: [`sponsio/cli.py`](../../sponsio/cli.py).
