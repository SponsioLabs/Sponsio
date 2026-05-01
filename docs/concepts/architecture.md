# Pattern Architecture

> Design document establishing the conceptual foundations of Sponsio's contract enforcement system.
> Audience: core team, contributors, and anyone extending the pattern library or observation layer.
>
> Last updated: 2026-04-09.

> **Casual reader?** Skip this page on a first pass — start with [concepts/overview.md](overview.md) (5-min read) and [concepts/contracts.md](contracts.md) for the user-facing model. Come back here when you need to add a new pattern, atom, or observation layer.

---

## 1. The Concept Stack

Four concepts build on each other. Getting their boundaries right prevents most design confusion.

```
                  ┌─────────────────────────────────────────────┐
                  │  Contract                                   │
                  │  = {assumptions, guarantees} bound to agent  │
                  │  = the unit of enforcement                  │
                  ├─────────────────────────────────────────────┤
                  │  Formula                                    │
                  │  = Atoms + LTL + boolean connectives        │
                  │  = what the evaluator actually checks       │
                  ├─────────────────────────────────────────────┤
                  │  Pattern                                    │
                  │  = named factory that emits a Formula       │
                  │  = convenience, not new expressiveness      │
                  ├─────────────────────────────────────────────┤
                  │  Atom                                       │
                  │  = one observable fact about one event      │
                  │  = the vocabulary boundary                  │
                  └─────────────────────────────────────────────┘
```

### Atom (原子谓词)

A binary (or integer) variable extracted from a single event. The grounding layer's *only* job is to produce these.

Examples: `called(X)`, `count(X)`, `arg_has(X, pattern)`, `perm(P)`.

Atoms define the **observation boundary** of the system. If a fact cannot be expressed as an atom, Sponsio cannot observe it and therefore cannot enforce constraints over it. When we ask "what can Sponsio enforce?", the precise answer is: *anything expressible as LTL over the available atom vocabulary*.

### Formula (公式)

Atoms combined with:
- **Boolean operators**: `Not`, `And`, `Or`, `Implies` (propositional logic)
- **Temporal operators**: `G` (globally), `F` (eventually), `X` (next), `U` (until) (Linear Temporal Logic)
- **Arithmetic comparisons**: `Le`, `Lt`, `Ge`, `Gt`, `Eq` (for integer atoms like `count`)

The evaluator (`formulas/evaluator.py`) runs formulas over a sequence of atom valuations using weak finite-trace semantics. It knows nothing about events, tools, or agents -- only dictionaries of predicate keys mapping to truth values.

Any user can write arbitrary formulas over the available atoms. The formula AST is the single representation consumed by all backends (runtime evaluator, and future Z3/nuXmv model checkers).

### Pattern (模式)

A **named factory function** that produces a `Formula` from user-friendly arguments.

```python
must_precede("A", "B")  -->  Not(Atom("called", "B")) U Atom("called", "A")
rate_limit("X", 3)      -->  G(Le(Var("count(X)"), Const(3)))
```

Patterns do **not** add expressive power. They are convenience shortcuts that:
1. Save users from writing raw LTL
2. Serve as targets for the NL parser (`generation/nl_to_contract.py`)
3. Carry metadata (description, pattern name) for diagnostics

Each pattern function returns a `DetFormula` -- a `Formula` paired with a human-readable `desc`, the `pattern_name` that produced it, and (where applicable) the numeric/string arguments used to build it so store round-trips don't lose information. `AnnotatedFormula` is kept as a backward-compatible alias but new code should use `DetFormula`.

### Contract (合约)

A set of formulas partitioned into **assumptions** and **guarantees**, bound to an agent.

- **Assumptions** are preconditions the environment must satisfy. If an assumption fails, the monitor reports an upstream problem via `EscalateToHuman` rather than blaming the agent.
- **Guarantees** are postconditions the agent must maintain. If a guarantee fails, the monitor applies enforcement (`DetBlock`, `RetryWithConstraint`, etc.).

The runtime monitor evaluates contracts against the growing trace: check assumptions first, then guarantees (skip guarantees if any assumption fails).

---

## 2. Atom Vocabulary

### Current atoms (as implemented in `tracer/grounding.py`)

| Atom | Type | Source Event | Truly Atomic? | Notes |
|------|------|-------------|---------------|-------|
| `called(X)` | bool | `tool_call` | **Yes** -- directly from `event.tool` | Core. Present at every timestep where tool X fires. |
| `count(X)` | int | `tool_call` | **Yes** -- cumulative accumulator | LTL cannot count; this must be maintained by grounding. Compared via arithmetic nodes (`Le`, `Gt`, etc.). |
| `arg_has(tool, pattern)` | bool | `tool_call` | **Yes** -- regex on serialized `event.args` | Parameterized: grounding only checks patterns it was told about via `collect_content_atoms()`. |
| `arg_field_has(tool, field, pattern)` | bool | `tool_call` | **Yes** -- regex on a specific arg field (`event.args[field]`) | Parameterized. Field-specific precision (vs `arg_has` which checks all args). Used by `arg_blacklist`. |
| `arg_paths_within(tool, *prefixes)` | bool | `tool_call` | **Yes** -- checks all file paths in args are within allowed prefixes | Parameterized. Replaces FOL `ForAllPaths` quantifier. |
| `output_has(tool, pattern)` | bool | `tool_call` | **Yes** -- regex on `event.content` (tool output) | Requires `guard_after()` to populate content. |
| `perm(P)` | bool | `Agent.permissions` | **Yes** -- static lookup | Not derivable from events. Useful for multi-agent RBAC. |
| `contains(field)` | bool | `data_write` | **Yes** -- from `event.contains` | Data flow tracking. |
| `flow(src, dest)` | bool | `data_read`, `message` | **Semi** -- requires cross-event state | Forward-propagated: once true, stays true for the rest of the trace. |
| `llm_said(pattern)` | bool | `llm_response` | **Yes** -- regex on LLM output | Requires integration to emit `llm_response` events. |
| `prompt_contains(pattern)` | bool | `llm_request` | **Yes** -- regex on LLM input | Requires integration to emit `llm_request` events. |
| `system_prompt_present()` | bool | `llm_request` | **Yes** -- structural check | True if LLM request has a system message. |
| `context_length()` | int | `llm_request` | **Yes** -- char count of LLM input | Compared via arithmetic nodes. |

### Proposed additions

| Candidate Atom | Source | OTEL Span Attribute | Use Case | Observation Model |
|---------------|--------|---------------------|----------|-------------------|
| `arg_eq(tool, key, val)` | `tool_call` args | `tool.input.{key}` | Exact match on specific arg field | A + B |
| `llm_input_contains(pattern)` | LLM span | `gen_ai.prompt` | Prompt injection detection | B only (OTEL) |
| `llm_output_contains(pattern)` | LLM span | `gen_ai.completion` | Output safety audit | B only (OTEL) |
| `token_count(type)` | LLM span | `gen_ai.usage.*_tokens` | Cost control | B only (OTEL) |
| `latency_exceeds(tool, ms)` | Any span | span duration | Performance constraints | B only (OTEL) |

Note: atoms marked "B only" are exclusively available through OTEL consumption (Section 5), not integration hooks. This is because hooks intercept at the tool level, not the LLM level.

### Design principles for atoms

1. **Atoms must be extractable from a single event** (or a simple accumulator like `count`). If computing a value requires reasoning over multiple events, it should be expressed as an LTL formula over simpler atoms.
2. **Parameterized atoms** (those requiring regex patterns or prefix lists) use `collect_content_atoms()` to tell grounding what to look for. Grounding does not speculatively match -- it only checks atoms that appear in the active formulas.
3. **New atoms require registration** in `_CONTENT_PREDICATES` (if parameterized) and extraction logic in `ground()`. This is the only code change needed to extend Sponsio's observation capabilities.

---

## 3. Grounding as Thin Event Adapter

### Architecture

```
Events  ──>  Grounding (thin adapter)  ──>  list[dict[str, bool|int]]  ──>  Evaluator
              │                                                              │
              ├── extract atoms from event fields                           ├── evaluate formula AST
              ├── maintain count() accumulators                             │   over valuations
              ├── maintain flow() state tracker                             │
              └── regex-match parameterized atoms                           └── return bool
```

Grounding (`tracer/grounding.py`) is a thin event adapter. Its job:
1. Map `Event` fields to atom truth values
2. Maintain `count(X)` accumulator (LTL cannot count)
3. Maintain `flow()` state tracker (requires cross-event state)
4. Regex-match parameterized atoms (`arg_has`, `arg_paths_within`, `output_has`, `llm_said`, etc.)

**No derived predicates.** All composition is expressed in the formula AST and handled by the evaluator.

### History: FOL elimination (completed)

Previously, three patterns (`arg_blacklist`, `scope_limit`, `data_intact`) used a separate FOL AST (`formulas/fol.py`) with 11 node types and returned `PropertyConstraint` instead of `DetFormula`. This required special-casing in both `RuntimeMonitor` and `ground()`.

The FOL system has been eliminated. The three patterns now use standard atoms:

| Pattern | Formula (Atom + LTL) |
|---------|---------------------|
| `arg_blacklist("bash", "command", ["rm -rf"])` | `G(Implies(Atom("called","bash"), And(Not(Atom("arg_has","bash","rm -rf")), ...)))` |
| `scope_limit("bash", ["/tmp"])` | `G(Implies(Atom("called","bash"), Atom("arg_paths_within","bash","/tmp")))` |
| `data_intact("grep", ["/data"])` | `G(Implies(Atom("arg_has","bash","grep"), Atom("arg_paths_within","bash","/data")))` |

What was removed:
- `PropertyConstraint` class (from `patterns/library.py`)
- `property_constraints` parameter (from `ground()`)
- FOL `eval_predicate()` call (from `ground()`)
- `PropertyConstraint` isinstance branches (from `RuntimeMonitor._check_hard()`)
- `prop.*` predicate key namespace
- `formulas/fol.py` is deprecated (kept for backward compat, emits `DeprecationWarning` on import)

### Why this matters

1. **One AST, multiple backends.** A unified formula AST can be consumed by the runtime evaluator today, and by Z3/nuXmv model checkers in the future. Two ASTs means two encodings.
2. **Users learn one concept.** "Everything is an LTL formula over atoms" is a complete mental model. No need to explain when FOL applies vs LTL.
3. **Extensibility via atoms, not AST nodes.** Adding observation capabilities = registering new atoms in grounding. No new AST node types needed.

---

## 4. Patterns as Named Templates

Patterns are factory functions, not a new layer. This section clarifies their role and constraints.

### What a pattern function does

```python
def must_precede(a: str, b: str) -> DetFormula:
    formula = U(Not(Atom("called", b)), Atom("called", a))
    return DetFormula(
        formula=formula,
        desc=f"tool `{a}` must precede `{b}`",
        pattern_name="must_precede",
        args=(a, b),
    )
```

It takes user-friendly arguments, constructs a formula from atoms, and wraps it with metadata.

### Current pattern inventory (core examples)

**Ordering (temporal)**:
- `must_precede(A, B)` -- A before B, using `Until`
- `always_followed_by(A, B)` -- A implies eventually B
- `must_confirm(action)` -- confirmation required before action
- `no_reversal(A, B)` -- B forbidden after A commits

**Frequency / rate**:
- `rate_limit(action, N)` -- at most N calls total
- `idempotent(action)` -- at most 1 call (special case of rate_limit)
- `cooldown(action, N)` -- min N steps between consecutive calls
- `bounded_retry(action, N)` -- at most N retries
- `deadline(trigger, action, N)` -- action within N steps of trigger

**Exclusion**:
- `mutual_exclusion(A, B)` -- at most one ever called across entire trace
- `never_together(A, B)` -- A and B never at same timestep (deprecated: delegates to `mutual_exclusion`)
- `segregation_of_duty(A, B)` -- same agent cannot do both

**Access control**:
- `requires_permission(tool, perm)` -- tool needs static permission

**Data flow**:
- `no_data_leak(src, dest)` -- no cross-agent data flow
- `arg_blacklist(tool, param, patterns)` -- forbid regex patterns in tool args
- `scope_limit(tool, paths)` -- restrict tool to allowed path prefixes

### Adding a new pattern

1. Write the factory function in `patterns/library.py`. It must return `DetFormula` and populate `args=(...)` with the raw arguments so the pattern store can round-trip them (rate-limit N, deadline N, required-steps ordering, etc.).
2. If the formula uses atoms not yet in grounding, add the atom extraction logic to `tracer/grounding.py`.
3. Add NL keyword rules in `generation/nl_to_contract.py` so the NL parser can route to the pattern.
4. Add tests in `tests/test_pattern_e2e.py` covering NL -> Guard -> enforcement.

A pattern that only uses existing atoms (e.g., composing `called()` and `count()`) requires zero grounding changes.

---

## 5. Two Observation Models

Sponsio has two fundamentally different ways to observe agent behavior. They differ in what they can see and whether they can intervene.

### Model A: Integration Hooks (realtime, can block)

Each framework integration hooks at tool-call boundaries:

```
LangGraphGuard   --> wraps wrap()                --> sees: tool_name, args, result
OpenAIGuard      --> patches completions.create --> sees: tool_calls in response
CrewAIGuard      --> on_tool_start/on_tool_end  --> sees: tool_name, args, result
AgentsSDKGuard   --> wraps @function_tool       --> sees: tool_name, args, result
MCPContractProxy --> wraps call_tool()          --> sees: tool_name, args, result
```

| Property | Value |
|----------|-------|
| **Can observe** | tool name, tool args, tool result |
| **Cannot observe** | LLM input prompt, LLM output text, memory state, retrieval results |
| **Can block** | Yes -- `guard_before()` returns `blocked=True` before tool executes |
| **Latency** | Microseconds (formula evaluation is pure Python, no I/O) |
| **Atoms available** | `called`, `count`, `perm`, `arg_has`, `output_has`, `contains`, `flow` |

This is the model for **real-time enforcement**. When a tool call would violate a contract, it is blocked before execution.

### Model B: OTEL Consumer (post-hoc, richer observation)

Instead of hooking each framework, consume the OTEL traces that frameworks already produce natively:

```
Any LLM framework  -->  Framework's OTEL instrumentation  -->  Standard OTEL spans
                                                                      |
                                                              Sponsio OTEL Consumer
                                                                      |
                                                              Atom extraction --> LTL evaluation --> Report
```

| Property | Value |
|----------|-------|
| **Can observe** | Everything in the OTEL trace: tool calls, LLM I/O, tokens, latency, retrieval |
| **Cannot observe** | Internal chain-of-thought not emitted as span attributes |
| **Can block** | No -- observation is after the fact |
| **Latency** | Batch processing (seconds to minutes, depending on collection interval) |
| **Atoms available** | All of Model A, plus: `llm_input_contains`, `llm_output_contains`, `token_count`, `latency_exceeds` |

Frameworks already export OTEL traces via standard instrumentation:
- LangChain: `langchain-opentelemetry`
- OpenAI: `opentelemetry-instrumentation-openai`
- CrewAI: built-in OTEL support
- LlamaIndex: `llama-index-instrumentation-opentelemetry`

Sponsio needs a **consumer** component that receives these spans, extracts atoms from span attributes, and feeds them into the same LTL evaluator.

### Complementary use

```
                       Can Block?    LLM I/O Visible?    Framework Changes Needed?
Integration Hooks       Yes           No                  None (already built)
OTEL Consumer           No            Yes                 None (framework has OTEL)
```

**Recommended: use both.**
- Integration hooks for real-time enforcement (block dangerous tool calls before they execute)
- OTEL consumer for post-hoc audit (detect prompt injection, PII in outputs, cost overruns)

### Current OTEL components vs what's needed

| Component | Exists? | Direction | Purpose |
|-----------|---------|-----------|---------|
| `sponsio/tracer/exporters.py` (`OtlpHttpExporter` + friends) | **Yes** | Sponsio → OTLP | Push Sponsio's contract-checking span tree to any OTLP/HTTP collector (Datadog / Honeycomb / Grafana / your own) |
| OTLP ingestion at the dashboard | **Sponsio Cloud** | OTLP → Dashboard | Multi-tenant ingest with auth + retention; in OSS, ship spans to your own collector via the exporter above |
| OTEL Consumer / Atom Adapter | **No** | OTEL → Evaluator | Extract atoms from framework OTEL spans, run LTL evaluation |

The exporter (outbound) and ingestion (storage) already work. The missing piece is the **consumer** -- the component that closes the loop from OTEL spans back to contract verification.

### Impact on framework integrations

**None.** Model B requires no changes to any existing integration. Frameworks already emit OTEL traces. The consumer is a new, standalone module that reads those traces.

---

## 6. Pattern Classification by Observation Boundary

Patterns are not organized by "layer" (tool / data / output). They are organized by which atoms they require, which determines which observation model can supply them.

### Category A: Tool-Call Patterns

Atoms used: `called(X)`, `count(X)`, `arg_has(X, pattern)`, `output_has(X, pattern)`, `perm(P)`.

Available via: **Hooks (realtime, can block) AND OTEL (post-hoc)**.

Most deterministic patterns fall in this category. These are the most universally available, the most enforceable (can block), and cover the majority of agent safety constraints.

Examples:
- `must_precede(A, B)` = `Not(called(B)) U called(A)` -- uses `called` atoms
- `rate_limit(X, N)` = `G(count(X) <= N)` -- uses `count` atom
- `arg_blacklist(X, _, patterns)` = `G(called(X) -> And(Not(arg_has(X, p1)), ...))` -- uses `called` + `arg_has` atoms

### Category B: Data-Flow Patterns

Atoms used: `contains(field)`, `flow(src, dest)`.

Available via: **Hooks only**, and only if the agent emits `data_read`/`data_write`/`message` events (not just `tool_call`).

In practice most agents only produce `tool_call` events, making this category niche. The `no_data_leak` pattern lives here.

### Category C: LLM-Level Patterns

Atoms used: `llm_input_contains(pattern)`, `llm_output_contains(pattern)`, `token_count(type)`.

Available via: **OTEL only** (post-hoc, cannot block).

These patterns are not enforceable in real-time. They are for audit and compliance:
- Prompt injection detection: `G(Not(llm_input_contains("ignore previous instructions")))`
- Output safety: `G(Not(llm_output_contains(ssn_pattern)))`
- Cost control: `G(token_count("total") <= 10000)`

Currently, `llm_said` and `prompt_contains` atoms exist in grounding but require integrations to emit `llm_response`/`llm_request` event types. The OTEL consumer would provide these atoms automatically from framework spans.

### Recommendation

Keep the pattern library focused on **Category A**. These are universal, enforceable, and cover the dominant use case (constraining tool-call behavior). Categories B and C are documented but not prioritized for pattern library expansion. Category C patterns belong in the OTEL consumer module's analysis layer.

---

## 7. Compositional Reasoning (Post-MVP)

The architecture is designed to support formal verification beyond runtime checking. This section documents the connection for future implementation.

### The formal structure

Each atom at each timestep is a boolean variable. A formula is a constraint over temporal sequences of these variables. This maps to:

- **SAT/SMT encoding**: Unroll the trace to bounded depth K. Each `atom_i_t` is a Z3 boolean variable. LTL operators become quantified constraints over timestep indices. The evaluator becomes a Z3 satisfiability query.

- **Assume-guarantee composition**: Given agents A and B where A's output feeds B's input:
  - A's guarantees produce atoms that must satisfy B's assumptions
  - **Composability check**: for each assumption phi in B, does some guarantee psi in A entail phi?
  - If `psi |= phi` for all assumptions, the composition is sound
  - If any assumption is uncovered, there is a gap the agent can exploit

- **Coverage analysis**: The set of atoms constrained by an agent's contract defines its "safety coverage." Atoms that appear in no contract represent unconstrained dimensions -- potential risk vectors. This metric quantifies how much of an agent's behavior is formally specified.

### Architecture alignment

The unified formula AST (Section 3) is critical for compositional reasoning. A single AST means a single encoding into Z3/SMT-LIB. The current dual-AST state (formula.py + fol.py) would require two encoders. Eliminating the FOL AST is a prerequisite for this feature.

### Current status

Runtime AG checking is implemented and works correctly. Pre-deployment compositional verification (the Z3-based entailment checking described above) is planned as a proprietary, post-launch feature.

---

## Appendix: Architecture After FOL Unification

```
User writes formula (Python API or NL string):

    "tool `check_policy` must precede `issue_refund`"
                    |
                    v
            NL Parser (generation/nl_to_contract.py)
                    |
                    v
            Pattern Function (patterns/library.py)
            must_precede("check_policy", "issue_refund")
                    |
                    v
            Formula AST (formulas/formula.py)
            U(Not(Atom("called","issue_refund")), Atom("called","check_policy"))
                    |
        +-----------+-----------+
        |                       |
        v                       v
    Runtime Path            Pre-verify Path
    (current)               (future, proprietary)
        |                       |
        v                       v
    Grounding               Z3 Encoder
    (tracer/grounding.py)   (formulas/z3_backend.py)
        |                       |
        v                       v
    list[dict[str, bool]]   Z3 Formula
        |                       |
        v                       v
    Evaluator               Z3 Solver
    (formulas/evaluator.py) sat/unsat/model
        |
        v
    True / False
```

One AST. One atom vocabulary. Multiple backends.
