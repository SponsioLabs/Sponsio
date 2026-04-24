---
title: Stochastic contracts
description: How Sponsio's LLM-as-judge pipeline works, when it earns its cost, and how to pick thresholds.
---

# Stochastic contracts

Stochastic contracts handle properties that need **semantic judgment** — tone, relevance, scope respect, hallucination, whether the agent leaked PII that a regex cannot catch. They are evaluated after tool execution or LLM response, score the output between 0 and 1, and on violation generate feedback for the agent to retry.

For the conceptual model, see [Concepts overview](overview.md). For the full catalog of shipped and recipes-by-agent-type, see [Stochastic atom catalog](../reference/sto-atoms.md). This page is about how to think about sto contracts and when to reach for one.

---

## Shape of a sto contract

```python
contract("response free of prompt injection")
    .enforce(G(Atom("injection_free", atom_type="sto", context_scope="event")))
    .threshold(alpha=0.5, beta=0.9)
    .strategy("retry_with_constraint")
```

- **Atom** — an LLM-judged predicate. Declared with `atom_type="sto"`. The judge returns a confidence score in [0, 1].
- **Context scope** — `event` (judge sees the current event only), `last_k` (current + k previous events), or `full_trace` (entire session). Full-trace is expensive; event scope is the default.
- **Thresholds (α, β)** — α is how confident the assumption must be; β is how confident the guarantee must be. A contract passes if `conf(A) ≥ α ⇒ conf(G) ≥ β`. See [cost-based thresholds](../advanced/cost-based-thresholds.md) for deriving these from operational costs.
- **Strategy** — `retry_with_constraint` (default), `redirect_to_safe`, or a custom callable. Sto contracts do not hard-block — they guide.

---

## Always wrap response atoms in `G(...)`

A naked atom evaluates only at position 0 — often an `llm_request`, not the response you meant to judge. For "every LLM response must satisfy X", wrap in `G(...)`:

```python
# ✓ every llm_response event is judged
contract("response free of prompt injection").enforce(
    G(Atom("injection_free", atom_type="sto", context_scope="event"))
)

# ✗ only position 0 is judged — usually not a response
contract("response free of prompt injection").enforce(
    Atom("injection_free", atom_type="sto", context_scope="event")
)
```

Atoms on a non-content event (e.g. `tool_call`) vacuously pass — the extractor returns `None` and the evaluator returns `score=1.0` — so `G(atom)` does not over-fire on non-response events. Per-position caching means each event is judged at most once per contract across the whole session.

---

## When to reach for a sto contract

Use sto when the property **needs a model to read the text**. Typical use cases:

- **Prompt injection and jailbreaks** — adversarial content in user input or tool outputs.
- **Tone, brand voice, scope respect** — does the response sound right and stay on topic.
- **Semantic PII** — names tied to conditions, inferable identity. Regex cannot catch this.
- **Hallucination and faithfulness** — is the response grounded in the provided source.
- **Metric integrity, no-omission** — is the agent gaming a KPI or silently hiding adverse data.

Anti-pattern: do not use sto for properties a regex already checks. SSN regexes, length caps, path scope, format validity all belong in [deterministic contracts](contracts.md). Every sto atom adds an LLM call per check — pick the ones that match actual failure modes, not every atom we ship.

---

## Sto vs. det at a glance

| Property kind | Example | Where it lives |
|---|---|---|
| Syntactic / pattern-matchable | "no SSN in response" | Det: `no_pii()` |
| Semantic / needs understanding | "no contextual PII (names + conditions)" | Sto: `semantic_pii_free` |
| Format-checkable | "output is valid JSON" | Det: `output_format("json")` |
| Meaning-dependent | "output faithful to source" | Sto: `faithfulness(source)` |
| Ordering / rate | "A before B" | Det: `must_precede` |
| Multi-part answer complete | "all sub-questions answered" | Sto: `goal_coverage` |

---

## Picking α and β

Two quick entry points, in order of effort:

1. **Risk profile** — pick one of `cautious`, `balanced`, `permissive` and Sponsio sets thresholds for you.

    ```yaml
    - E:
        pattern: injection_free
      risk_profile: cautious                    # → α=0.7, β=0.95
    ```

2. **Typical β by atom** — reasonable starting points if you want to set them directly:

    | Atom | Typical β | Reasoning |
    |---|---|---|
    | `injection_free`, `jailbreak_free` | 0.9–0.95 | High FN cost (breach), low FP cost (retry) |
    | `semantic_pii_free` | 0.95+ | Compliance-sensitive |
    | `harmful` | 0.98 | Safety-critical, high FN cost |
    | `hallucination_free`, `faithfulness` | 0.85–0.92 | Balanced |
    | `scope_respect` | 0.85 | FP is real (borderline on-topic) |
    | `tone_match`, `brand_voice` | 0.65–0.8 | Low-stakes UX signal |
    | `toxic_free` | 0.9 | High public-facing cost if missed |

3. **Cost-based derivation** — when the first two are not good enough. See [cost-based thresholds](../advanced/cost-based-thresholds.md) for the full framework: pick β from the false-positive and false-negative costs of the contract.

---

## Failure strategies

Sto contracts do not block outright. On violation:

| Strategy | Behavior |
|---|---|
| `retry_with_constraint` | Feed the judge's suggestion back to the agent as a constraint; agent retries with revised output. |
| `redirect_to_safe` | Replace the response with a safe fallback (e.g. a canned refusal). |
| `(callable)` | Custom callback. Receives the score, the judge's evidence, and the candidate event; returns a new strategy decision. |

The feedback loop is the entire point of the sto pipeline. A det check says "stop"; a sto check says "try again, and here is what is wrong."

---

## Cost model

Every sto atom costs one LLM judge call per check. Contract design is therefore a budgeting exercise:

- **Start with 2–3 atoms**, not a dozen. The recommended minimum for a new agent is `injection_free`, `toxic_free`, `semantic_pii_free`. Three contracts, most of the common issues.
- **Prefer `context_scope="event"`** over `full_trace`. Event scope judges one event; full-trace scope judges across the whole history and is noticeably slower.
- **Pair with det** where you can. Det patterns are free at runtime; sto is the scalpel for what det cannot reach.

---

## Next

- [Stochastic atom catalog](../reference/sto-atoms.md) — every shipped atom, integration wiring, and recipes by agent type.
- [Cost-based thresholds](../advanced/cost-based-thresholds.md) — derive α and β from operational costs.
- [Deterministic contracts](contracts.md) — the other pipeline.
- [Architecture](architecture.md) — where the sto pipeline sits in the engine.
