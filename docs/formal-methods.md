# Formal methods in Sponsio

When the README says Sponsio's deterministic contracts are "backed by formal methods" or "machine-checkable", here's what's actually happening — written for engineers who haven't taken a graduate verification course.

## What "formal" means here

Most agent-safety tools enforce rules by asking another LLM whether a tool call looks bad. That answer is probabilistic — it depends on the judge model's mood, prompt, and prior context. Two identical traces can get different verdicts.

Sponsio's deterministic path doesn't ask a model anything. Each contract you write — *"after `run_aml_check`, no edits to loan files"*, *"`run_tests` must precede `deploy_production`"*, *"`issue_refund` at most 3 times"* — gets compiled into a **Linear Temporal Logic (LTL) formula**, then into a **deterministic finite automaton (DFA)** that walks the trace one event at a time and outputs **pass** or **fail**. No randomness. Same trace in → same verdict out, every time.

That's what "formal" buys you: instead of "the judge thinks this is fine" you get "by the structure of your trace, this rule is provably satisfied or provably violated."

## What gets compiled, end to end

```
"after run_aml_check, no edits to loan files"
        │
        │  (1) sponsio's NL parser → AST
        ▼
        Atom "called(run_aml_check)"  ⊃  G ¬called(edit_loan_file)
        │
        │  (2) LTL formula construction
        ▼
        ◇ called(run_aml_check) → G (¬called(edit_loan_file))
        │
        │  (3) DFA compilation (Vardi-Wolper / tableau)
        ▼
        States: { S0_clean, S1_after_aml, S2_violation_trap }
        │
        │  (4) Runtime: walk the live trace
        ▼
        Each tool call → DFA step → S0/S1/S2
        S2 reached → BLOCK (det violation)
```

**Step 1 — parser.** Natural-language rules are tokenized, then mapped onto a vocabulary of atoms (`called(tool, args)`, `arg_matches(...)`, `permission_granted(...)`, etc.) and temporal connectives (always `G`, eventually `◇`, until `U`, next `X`).

**Step 2 — formula.** The result is a closed-form LTL expression. Same expressive power as the LTL used in hardware verification (Intel's Pentium FPU correctness proofs, Amazon's TLA+ models for S3, model-checking in Coq/Isabelle work).

**Step 3 — compilation.** The formula compiles to a DFA via standard automata-theoretic construction. The DFA is small (typically a handful of states per contract) and stateless to walk.

**Step 4 — runtime.** Every tool call appends one event to the trace. Each contract's DFA advances by one step. If any DFA enters its violation trap state, Sponsio raises and blocks the call.

This all runs in pure Python (Sponsio core has zero core deps). On a current laptop, p99 per-event check is ~12μs across the whole contract set. No LLM, no network, no cache.

## Why this matters in practice

**Determinism.** A contract that passed yesterday for trace T will pass today for trace T. Replay-based regression testing (`sponsio check --trace`) is meaningful — same input, same verdict.

**Auditability.** When a contract fires, Sponsio can point at the exact event sequence and the exact DFA transition that flagged it. Not "the judge said so" — *"event #14 advanced this DFA from state S1 to the trap."*

**Coverage.** Temporal properties — "A before B", "never B after A", "A then later C", "at most N B" — are exactly what LTL was designed to express. Regex over output text and "the LLM judge thinks…" approaches cannot soundly express these. They check single events or single responses; they miss order and history.

**Cost.** Zero LLM tokens on the hot path. A `pure_det` check is microseconds and runs offline.

## What it doesn't give you

Formal methods do not magic away every failure mode:

- **Spec correctness.** Sponsio guarantees your contract is enforced *as written.* If you write the wrong rule (missed an edge case, bad atom name), the engine enforces the wrong thing — fast and reliably. Spec review is your job; we just give you a tight loop to iterate (write rule → `sponsio check --trace` → adjust).
- **Atom grounding.** The DFA reasons over events, not raw English. Your tool wrappers / framework hooks have to emit the right events for the rule to fire. `sponsio onboard` and the integration adapters handle the common case; custom atoms need a one-time mapping.
- **Semantic checks.** Some properties are inherently fuzzy — "is this response off-topic?", "did the agent omit a material fact?", "does this PII look exfiltrated?". Those go through the **stochastic pipeline** (LLM-judged atoms), not the formal one. Sponsio offers both, clearly separated.

## Further reading

- Pnueli, *The temporal logic of programs* (1977) — original LTL paper, very readable.
- Baier & Katoen, *Principles of Model Checking* (2008) — chapters 5–7 cover LTL → automata in depth.
- [`docs/architecture.md`](architecture.md) — how Sponsio's grounding, monitor, and verifier components fit together.
- [`docs/contracts.md`](contracts.md) — the actual atom vocabulary and contract DSL syntax.
- [`docs/sto-atoms.md`](sto-atoms.md) — the complementary stochastic pipeline.

---

**Related:** [Quick start](../QUICKSTART.md) · [Contract DSL](contracts.md) · [Stochastic atoms](sto-atoms.md) · [CLI Reference](cli.md) · [Integrations](integrations.md) · [Architecture](architecture.md) · [OWASP Agentic Top 10](owasp-agentic-top-10.md)
