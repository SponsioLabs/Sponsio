# Pattern architecture

Internals reference for contributors. For the user-facing concept model (atom → pattern → formula → contract), see [Concepts overview](overview.md). Read this page when you are adding a new pattern, atom, or observation layer.

---

## 1. Atom vocabulary

### Current atoms (in `tracer/grounding.py`)

| Atom | Type | Source event | Truly atomic? | Notes |
|---|---|---|---|---|
| `called(X)` | bool | `tool_call` | Yes, directly from `event.tool` | Core. Present at every timestep where tool X fires. |
| `count(X)` | int | `tool_call` | Yes, cumulative accumulator | LTL cannot count; this must be maintained by grounding. Compared via arithmetic nodes (`Le`, `Gt`). |
| `arg_has(tool, pattern)` | bool | `tool_call` | Yes, regex on serialized `event.args` | Parameterized: grounding only checks patterns it was told about via `collect_content_atoms()`. |
| `arg_field_has(tool, field, pattern)` | bool | `tool_call` | Yes, regex on a specific arg field | Parameterized. Field-specific precision (vs `arg_has` which checks all args). Used by `arg_blacklist`. |
| `arg_paths_within(tool, *prefixes)` | bool | `tool_call` | Yes, checks all file paths in args are within allowed prefixes | Parameterized. Replaces FOL `ForAllPaths` quantifier. |
| `output_has(tool, pattern)` | bool | `tool_call` | Yes, regex on `event.content` | Requires `guard_after()` to populate content. |
| `perm(P)` | bool | `Agent.permissions` | Yes, static lookup | Not derivable from events. Useful for multi-agent RBAC. |
| `contains(field)` | bool | `data_write` | Yes, from `event.contains` | Data flow tracking. |
| `flow(src, dest)` | bool | `data_read`, `message` | Semi: requires cross-event state | Forward-propagated: once true, stays true for the rest of the trace. |
| `llm_said(pattern)` | bool | `llm_response` | Yes, regex on LLM output | Requires integration to emit `llm_response` events. |
| `prompt_contains(pattern)` | bool | `llm_request` | Yes, regex on LLM input | Requires integration to emit `llm_request` events. |
| `system_prompt_present()` | bool | `llm_request` | Yes, structural check | True if LLM request has a system message. |
| `context_length()` | int | `llm_request` | Yes, char count of LLM input | Compared via arithmetic nodes. |

### Proposed additions

| Candidate atom | Source | OTEL span attribute | Use case | Observation model |
|---|---|---|---|---|
| `arg_eq(tool, key, val)` | `tool_call` args | `tool.input.{key}` | Exact match on specific arg field | A + B |
| `llm_input_contains(pattern)` | LLM span | `gen_ai.prompt` | Prompt injection detection | B only (OTEL) |
| `llm_output_contains(pattern)` | LLM span | `gen_ai.completion` | Output safety audit | B only (OTEL) |
| `token_count(type)` | LLM span | `gen_ai.usage.*_tokens` | Cost control | B only (OTEL) |
| `latency_exceeds(tool, ms)` | Any span | span duration | Performance constraints | B only (OTEL) |

Atoms marked "B only" are exclusively available through OTEL consumption (Section 4), not integration hooks. Hooks intercept at the tool level, not the LLM level.

### Design principles

1. **Atoms must be extractable from a single event** (or a simple accumulator like `count`). If computing a value requires reasoning over multiple events, express it as an LTL formula over simpler atoms.
2. **Parameterized atoms** (regex patterns or prefix lists) use `collect_content_atoms()` to tell grounding what to look for. Grounding does not speculatively match; it only checks atoms that appear in the active formulas.
3. **New atoms require registration** in `_CONTENT_PREDICATES` (if parameterized) and extraction logic in `ground()`. This is the only code change needed to extend Sponsio's observation capabilities.

---

## 2. Grounding as thin event adapter

```
Events  ->  Grounding (thin adapter)  ->  list[dict[str, bool|int]]  ->  Evaluator
              |                                                              |
              |- extract atoms from event fields                            |- evaluate formula AST
              |- maintain count() accumulators                              |   over valuations
              |- maintain flow() state tracker                              |
              `- regex-match parameterized atoms                            `- return bool
```

Grounding (`tracer/grounding.py`) is a thin event adapter. Its job:

1. Map `Event` fields to atom truth values.
2. Maintain `count(X)` accumulator (LTL cannot count).
3. Maintain `flow()` state tracker (requires cross-event state).
4. Regex-match parameterized atoms (`arg_has`, `arg_paths_within`, `output_has`, `llm_said`).

No derived predicates. All composition is expressed in the formula AST and handled by the evaluator.

### Why one unified AST

1. **One AST, multiple backends.** A unified formula AST can be consumed by the runtime evaluator today, and by Z3/nuXmv model checkers in the future. Two ASTs means two encodings.
2. **Users learn one concept.** "Everything is an LTL formula over atoms" is a complete mental model.
3. **Extensibility via atoms, not AST nodes.** Adding observation capabilities means registering new atoms in grounding. No new AST node types needed.

---

## 3. Patterns as named templates

Patterns are factory functions, not a new layer.

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

It takes user-friendly arguments, constructs a formula from atoms, and wraps it with metadata (`desc`, `pattern_name`, raw `args` for round-trip).

### Current pattern inventory

**Ordering (temporal)**:
- `must_precede(A, B)`: A before B, using `Until`
- `always_followed_by(A, B)`: A implies eventually B
- `must_confirm(action)`: confirmation required before action
- `no_reversal(A, B)`: B forbidden after A commits

**Frequency / rate**:
- `rate_limit(action, N)`: at most N calls total
- `idempotent(action)`: at most 1 call (special case of `rate_limit`)
- `cooldown(action, N)`: min N steps between consecutive calls
- `bounded_retry(action, N)`: at most N retries
- `deadline(trigger, action, N)`: action within N steps of trigger

**Exclusion**:
- `mutual_exclusion(A, B)`: at most one ever called across entire trace
- `segregation_of_duty(A, B)`: same agent cannot do both

**Access control**:
- `requires_permission(tool, perm)`: tool needs static permission

**Data flow**:
- `no_data_leak(src, dest)`: no cross-agent data flow
- `arg_blacklist(tool, param, patterns)`: forbid regex patterns in tool args
- `scope_limit(tool, paths)`: restrict tool to allowed path prefixes

### Adding a new pattern

1. Write the factory in `patterns/library.py`. Return `DetFormula` and populate `args=(...)` with the raw arguments so the pattern store can round-trip them.
2. If the formula uses atoms not yet in grounding, add the extraction logic to `tracer/grounding.py`.
3. Add DSL keyword rules in `generation/dsl_to_contract.py`.
4. Add tests in `tests/test_pattern_e2e.py` covering NL → guard → enforcement.

A pattern that only uses existing atoms (composing `called()` and `count()`) requires zero grounding changes.

---

## 4. Two observation models

Sponsio has two ways to observe agent behavior. They differ in what they can see and whether they can intervene.

### Model A: integration hooks (realtime, can block)

Each framework integration hooks at tool-call boundaries:

```
LangGraphGuard    -> wraps wrap()                -> sees: tool_name, args, result
OpenAIGuard       -> patches completions.create  -> sees: tool_calls in response
CrewAIGuard       -> on_tool_start/on_tool_end   -> sees: tool_name, args, result
AgentsSDKGuard    -> wraps @function_tool        -> sees: tool_name, args, result
MCPContractProxy  -> wraps call_tool()           -> sees: tool_name, args, result
```

| Property | Value |
|---|---|
| Can observe | tool name, tool args, tool result |
| Cannot observe | LLM input prompt, LLM output text, memory state, retrieval results |
| Can block | Yes. `guard_before()` returns `blocked=True` before tool executes |
| Latency | Microseconds (formula evaluation is pure Python, no I/O) |
| Atoms available | `called`, `count`, `perm`, `arg_has`, `output_has`, `contains`, `flow` |

Real-time enforcement. When a tool call would violate a contract, it is blocked before execution.

### Model B: OTEL consumer (post-hoc, richer observation)

Instead of hooking each framework, consume the OTEL traces frameworks already produce natively:

```
Any LLM framework  ->  framework's OTEL instrumentation  ->  standard OTEL spans
                                                                      |
                                                              Sponsio OTEL consumer
                                                                      |
                                                              atom extraction -> LTL evaluation -> report
```

| Property | Value |
|---|---|
| Can observe | Everything in the OTEL trace: tool calls, LLM I/O, tokens, latency, retrieval |
| Cannot observe | Internal chain-of-thought not emitted as span attributes |
| Can block | No. Observation is after the fact |
| Latency | Batch processing (seconds to minutes, depending on collection interval) |
| Atoms available | All of Model A, plus: `llm_input_contains`, `llm_output_contains`, `token_count`, `latency_exceeds` |

Frameworks already export OTEL traces via standard instrumentation: `langchain-opentelemetry`, `opentelemetry-instrumentation-openai`, CrewAI built-in, `llama-index-instrumentation-opentelemetry`. Sponsio needs a consumer component that receives these spans, extracts atoms from span attributes, and feeds them into the same LTL evaluator.

### Complementary use

| | Can block? | LLM I/O visible? | Framework changes needed? |
|---|---|---|---|
| Integration hooks | Yes | No | None (already built) |
| OTEL consumer | No | Yes | None (framework has OTEL) |

Use both. Integration hooks for real-time enforcement (block dangerous tool calls before execution); OTEL consumer for post-hoc audit (detect prompt injection, PII in outputs, cost overruns).

### Current OTEL components

| Component | Status | Direction | Purpose |
|---|---|---|---|
| `sponsio/tracer/exporters.py` (`OtlpHttpExporter`) | Yes | Sponsio → OTLP | Push contract-checking span tree to any OTLP/HTTP collector (Datadog, Honeycomb, Grafana) |
| OTEL Consumer / Atom Adapter | Not yet | OTEL → Evaluator | Extract atoms from framework OTEL spans, run LTL evaluation |

The outbound exporter works today: ship spans to your own OTLP/HTTP collector. The consumer that closes the loop from OTEL spans back to contract verification is the missing piece.

---

## 5. Pattern classification by observation boundary

Patterns are organized by which atoms they require, which determines which observation model can supply them.

### Category A: tool-call patterns

Atoms used: `called(X)`, `count(X)`, `arg_has(X, pattern)`, `output_has(X, pattern)`, `perm(P)`.

Available via: hooks (realtime, can block) AND OTEL (post-hoc).

Most deterministic patterns fall here. Universally available, enforceable, covers the majority of agent safety constraints.

Examples:
- `must_precede(A, B)` = `Not(called(B)) U called(A)`, uses `called` atoms
- `rate_limit(X, N)` = `G(count(X) <= N)`, uses `count` atom
- `arg_blacklist(X, _, patterns)` = `G(called(X) -> And(Not(arg_has(X, p1)), ...))`, uses `called` + `arg_has`

### Category B: data-flow patterns

Atoms used: `contains(field)`, `flow(src, dest)`.

Available via: hooks only, and only if the agent emits `data_read` / `data_write` / `message` events (not just `tool_call`). Most agents only produce `tool_call` events, making this category niche. The `no_data_leak` pattern lives here.

### Category C: LLM-level patterns

Atoms used: `llm_input_contains(pattern)`, `llm_output_contains(pattern)`, `token_count(type)`.

Available via: OTEL only (post-hoc, cannot block). Not enforceable in real-time. For audit and compliance:

- Prompt injection detection: `G(Not(llm_input_contains("ignore previous instructions")))`
- Output safety: `G(Not(llm_output_contains(ssn_pattern)))`
- Cost control: `G(token_count("total") <= 10000)`

`llm_said` and `prompt_contains` atoms exist in grounding but require integrations to emit `llm_response` / `llm_request` event types. The OTEL consumer would provide these atoms automatically from framework spans.

### Recommendation

Keep the pattern library focused on Category A. These are universal, enforceable, and cover the dominant use case. Categories B and C are documented but not prioritized for pattern library expansion. Category C patterns belong in the OTEL consumer module's analysis layer.
