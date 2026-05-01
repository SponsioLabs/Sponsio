---
title: FAQ
description: Common questions and pitfalls when adopting Sponsio.
---

# FAQ

---

## Positioning

### Is Sponsio a prompt-injection shield?

No. Sponsio checks actions, not text. If you need prompt-injection detection, use a stochastic contract with the `injection_free` atom — but understand that's one atom in a larger system. The main value is blocking unsafe *tool calls* regardless of whether the reason was injection, misalignment, or a plain bug.

### Is it an output-assertion library?

No. Output-assertion libraries check the final text. Sponsio checks the *trace* — what the agent did, in what order, with what arguments — before a side effect happens. Output assertions cannot express "A must precede B" because neither A nor B is in the current output.

### Is it a reliability / drift scoring framework?

No. Those tools score runs after the fact. Sponsio blocks unsafe calls in the hot path.

### "Isn't all of this just prompt engineering?"

Prompt engineering defines intent. Sponsio enforces the action boundary. A well-engineered prompt still leaves room for a fabricated AML check, a retry loop that burns budget, or a sudden decision to wire $800k. Contracts catch those regardless of how the prompt is worded. Use both.

---

## Design

### Can I enforce a property that isn't in the atom vocabulary?

No, by design. The atom vocabulary is the observation boundary. If you need a new atom, add it (see [Architecture](concepts/architecture.md)) and then write patterns over it. The engine can only reason about facts the grounding layer produces.

### Do det and sto mix inside one contract?

No. A single contract is evaluated by exactly one pipeline. If you need both a structural check and a semantic check for the same situation, declare two contracts.

### Should I turn on every sto atom I can?

No. Every sto atom costs one LLM judge call per check. The recommended minimum is `injection_free`, `toxic_free`, `semantic_pii_free` — then add atoms that match actual failure modes in your product.

### Is sto free?

No. Sto uses an LLM-as-judge. Some atoms are lightweight (logprob-based boolean judges), but LLM-judged sto atoms require a configured judge and incur per-check cost.

### Can OTEL do the blocking?

No. OTEL is post-hoc. Blocking has to happen synchronously between the LLM and the tool, which is where the framework integration sits. Use OTEL for observation, not enforcement.

---

## Integration

### Do I need an agent framework?

No. If your LLM app calls tools, APIs, databases, or files, you can use Sponsio directly via `guard.guard_before()` / `guard.guard_after()`. See [No-framework integration](integrations/index.md#no-framework).

### Which import path do I use?

`sponsio`, not `Sponsio`. Prefer the framework-specific factory for new code — `from sponsio.langgraph import Sponsio`, `from sponsio.claude_agent import Sponsio`, etc. The generic `sponsio.Sponsio(framework="langgraph", ...)` works but is less idiomatic.

### Python and TypeScript — same semantics?

For deterministic contracts, yes. The Python and TS engines share the same LTL core and produce identical block/allow decisions over the same trace. The stochastic pipeline, the DFA/verifier, YAML config, discovery, dashboard, and OTEL export are Python-only today.

---

## Rollout

### How do I know when to flip from observe to enforce?

Two signals: the violation rate has plateaued (you're not discovering new false positives), and every firing in the last week corresponds to something you actually want blocked. See [Observe vs. enforce](guides/observe-vs-enforce.md).

### Will enforce mode break my agent?

It will change behavior. Your agent starts seeing `SponsioBlocked` exceptions and has to react (retry, pick a different tool, escalate). Plan for a day of tuning after the flip.

### Can I enforce some contracts while observing others?

Yes. Set the global `mode: observe` and add `mode: enforce` per-contract for the handful of hard-block rules you are already sure of.

---

## Performance

### Is Sponsio in the hot path of every tool call?

Yes — that's the point. The det pipeline is designed to stay there: pure Python, sub-10μs p99, zero LLM calls.

### Does it scale with trace length?

Yes. The evaluator uses per-position caching and DFA-compiled formulas where possible. On a 1000-event trace, det checks stay under 20μs.

### Does sto scale?

Sto cost is dominated by judge-call latency, which is 100ms–1s per atom. Keep `context_scope="event"` as the default; reserve `full_trace` for the few atoms that need it.

---

## Benchmarks

### Where are the numbers from?

`sponsio scan` + offline replay against [ODCV-Bench](https://github.com/your-org/odcv-bench). ~84% average protection on high-risk trajectories across 12 mainstream LLMs. Full methodology and per-model results: *Benchmarks* (separate report — contact Sponsio for current numbers).

### Can I reproduce them?

Yes. The eval script is `ODCV-Bench/eval_sponsio.py`; scenarios and replay tooling ship in the repo. Numbers move as models change — treat them as a snapshot.

---

## Reading order

- **New here?** → [README](../README.md) for the pitch, then [Quickstart](getting-started/quickstart.md).
- **Writing your first contract?** → [First contract](getting-started/first-contract.md).
- **Adopting in an existing repo?** → [Onboarding](guides/onboarding.md).
- **Shipping to production?** → [Observe vs. enforce](guides/observe-vs-enforce.md).
- **Extending the pattern library?** → [Architecture](concepts/architecture.md).
