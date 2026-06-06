---
title: FAQ
description: Common questions and pitfalls when adopting Sponsio.
---

# FAQ

---

## Positioning

### Is Sponsio a prompt-injection shield?

No. Sponsio checks actions, not text. The main value is blocking unsafe *tool calls* regardless of whether the reason was injection, misalignment, or a plain bug.

### Is it an output-assertion library?

No. Output-assertion libraries check the final text. Sponsio checks the *trace* (what the agent did, in what order, with what arguments) before a side effect happens. Output assertions cannot express "A must precede B" because neither A nor B is in the current output.

### Is it a reliability / drift scoring framework?

No. Those tools score runs after the fact. Sponsio blocks unsafe calls in the hot path.

### "Isn't all of this just prompt engineering?"

Prompt engineering defines intent. Sponsio enforces the action boundary. A well-engineered prompt still leaves room for a fabricated compliance check (e.g. AML, KYC), a retry loop that burns budget, or a sudden decision to wire $800k. Contracts catch those regardless of how the prompt is worded. Use both.

---

## Design

### Can I enforce a property that isn't in the atom vocabulary?

No, by design. An *atom* is one observable fact the engine can read from the trace (for example, "called `tool X`", "tool X was called with argument `path` containing `/etc`"). The set of atoms is the observation boundary. If you need a new one, add it (see [Architecture](../concepts/architecture.md)) and then write patterns over it. The engine can only reason about facts the grounding layer produces.

### Can OTEL do the blocking?

No. OTEL is post-hoc. Blocking has to happen synchronously between the LLM and the tool, which is where the framework integration sits. Use OTEL for observation, not enforcement.

---

## Integration

### Do I need an agent framework?

No. If your LLM app calls tools, APIs, databases, or files, you can use Sponsio directly via `guard.guard_before()` / `guard.guard_after()`. See [the custom-loop example](../integrations/index.md#custom-loop-no-framework).

### Which import path do I use?

`sponsio`, not `Sponsio`. Prefer the framework-specific factory for new code, `from sponsio.langgraph import Sponsio`, `from sponsio.claude_agent import Sponsio`, etc. The generic `sponsio.Sponsio(framework="langgraph", ...)` works but is less idiomatic.

### Python and TypeScript. Same semantics?

For deterministic contracts, yes. The Python and TS engines share the same LTL (linear temporal logic) core and produce identical block/allow decisions over the same trace. The DFA (deterministic finite automaton) verifier, YAML config, discovery, and OTEL export are Python-only today.

---

## Rollout

### How do I know when to flip from observe to enforce?

Two signals: the violation rate has plateaued (you're not discovering new false positives), and every firing in the last week corresponds to something you actually want blocked. See [Observe vs. enforce](observe-vs-enforce.md).

### Will enforce mode break my agent?

It will change behavior. Your agent starts seeing `SponsioBlocked` exceptions and has to react (retry, pick a different tool, escalate). Plan for a day of tuning after the flip.

Three soft-landing options when a hard block is too harsh:

- **`redirect_to_safe(unsafe, safe)`**: substitute the unsafe call with a pre-approved one (e.g. `issue_refund` → `log_refund_request` for review). The agent continues on a safer path instead of bouncing off refusals.
- **`filter_tools(candidates)`**: call this before each model turn to pre-filter the tool menu against the live trace. The model never sees tools that would be blocked, so it does not waste tokens on attempts that will fail.
- **`tool_policy: { default: deny, enforcement: proactive }`**: the wrap-time variant of the above for adapters that own tool binding (LangGraph, CrewAI, OpenAI Agents SDK, Google ADK). Denied tools never reach the agent's bound toolset.

### Can I enforce some contracts while observing others?

Yes. Set the global `mode: observe` and add `mode: enforce` per-contract for the handful of hard-block rules you are already sure of.

---

## Performance

### Is Sponsio in the hot path of every tool call?

Yes. That is the point. The deterministic pipeline is designed to stay there: pure Python, sub-10μs at the 99th percentile (p99), zero LLM calls.

### Does it scale with trace length?

Yes. The evaluator uses per-position caching and DFA-compiled formulas where possible. On a 1000-event trace, det checks stay under 20μs.

---

## Benchmarks

### Where are the numbers from?

`sponsio scan` + offline replay against [ODCV-Bench](https://github.com/your-org/odcv-bench). **95.6%** average protection on high-risk trajectories across 12 mainstream LLMs; **24 of 36 scenarios at 100%** across every model. Full methodology and per-model / per-scenario results: *Benchmarks* (separate report. Contact Sponsio for current numbers).

### Can I reproduce them?

Yes. The eval script is `ODCV-Bench/eval_sponsio.py`; scenarios and replay tooling ship in the repo. Numbers move as models change. Treat them as a snapshot.

---

## Reading order

- **New here?** → [README](../../README.md) for the pitch, then [Quickstart](../getting-started/quickstart.md).
- **Writing your first contract?** → [First contract](../getting-started/first-contract.md).
- **Adopting in an existing repo?** → [Onboarding](onboarding.md).
- **Shipping to production?** → [Observe vs. enforce](observe-vs-enforce.md).
- **Extending the pattern library?** → [Architecture](../concepts/architecture.md).
