# Contract Types & Atom Vocabulary

Sponsio enforces two types of constraints on LLM agent behavior:

- **Deterministic (det)** — binary pass/fail, evaluated before tool execution. Can block unsafe actions.
- **Stochastic (sto)** — scored 0-1, evaluated after tool execution. Generates feedback for the agent to retry.

Both types are defined in natural language and compiled to formal representations internally.

---

## Deterministic Constraints

Det constraints are formal temporal logic (LTL) formulas over **atoms** — observable facts extracted from the agent's execution trace. They are evaluated before each tool call and can **block** the action if violated.

### Pattern Library (selected deterministic patterns)

#### Safety

| Pattern | NL Example | What it enforces |
|---------|-----------|-----------------|
| `must_precede(A, B)` | `"tool `check_policy` must precede `issue_refund`"` | A must have been called before B can execute |
| `must_confirm(action)` | `"tool `delete_file` requires confirmation"` | A confirmation step must precede the action |
| `requires_permission(tool, perm)` | `"tool `transfer` requires permission `manager`"` | Agent must hold a static permission to use the tool |
| `no_data_leak(src, dest)` | `"no data leak from `read_db` to `send_email`"` | Data must not flow between two agents/tools |

#### Compliance

| Pattern | NL Example | What it enforces |
|---------|-----------|-----------------|
| `no_reversal(A, B)` | `"after `approve`, tool `reject` is forbidden"` | Once A is called, B is permanently forbidden |
| `segregation_of_duty(A, B)` | `"tool `review` and `approve` must be by different agents"` | Same agent cannot perform both actions |
| `always_followed_by(A, B)` | `"every `refund` must be followed by `notify`"` | Whenever A happens, B must eventually happen |

#### Operational

| Pattern | NL Example | What it enforces |
|---------|-----------|-----------------|
| `rate_limit(action, N)` | `"tool `query_db` at most 5 times"` | Action can be called at most N times total |
| `idempotent(action)` | `"tool `transfer` at most 1 times"` | Action can be called at most once (special case of rate_limit) |
| `cooldown(action, N)` | `"tool `send_email` cooldown of 3 steps"` | At least N steps between consecutive calls |
| `deadline(trigger, action, N)` | `"tool `respond` within 3 steps of `receive`"` | Action must happen within N steps of trigger |
| `bounded_retry(action, N)` | `"tool `deploy` at most 3 retries"` | Action limited to N retries |

#### Exclusion

| Pattern | NL Example | What it enforces |
|---------|-----------|-----------------|
| `mutual_exclusion(A, B)` | `"tools `approve` and `reject` are mutually exclusive"` | At most one of A or B can ever be called |
| `never_together(A, B)` | (deprecated, delegates to `mutual_exclusion`) | |

#### Argument Checking

| Pattern | NL Example | What it enforces |
|---------|-----------|-----------------|
| `arg_blacklist(tool, field, patterns)` | `"bash command must not contain `rm -rf`"` | Specific arg field must not match forbidden regex patterns |
| `scope_limit(tool, paths)` | `"bash may only access files under `/workspace`"` | All file paths in tool args must be within allowed prefixes |

### How Det Constraints Work

```
NL string
  → Pattern function (e.g., must_precede("A", "B"))
    → LTL Formula: Not(called("B")) Until called("A")
      → Grounding: extract atoms from trace events
        → Evaluator: evaluate formula over atom valuations
          → True (pass) or False (block)
```

---

## Stochastic Constraints

Sto constraints evaluate the **quality** of agent outputs — things like tone, PII presence, format compliance. They run after tool execution and produce a score between 0 and 1. If the score falls below a threshold, Sponsio generates discriminative feedback and the agent retries.

### Sto Catalog

| Category | NL Example | Requires LLM? | How it evaluates |
|----------|-----------|---------------|-----------------|
| `pii` | `"response must not contain PII"` | No | Regex patterns for SSN, credit cards, emails, phone numbers |
| `length` | `"response under 200 words"` | No | Word/character count check |
| `format` | `"output must be valid JSON"` | No | Format validation (JSON, XML, markdown) |
| `content_prohibition` | `"must not mention competitors"` | No | Keyword/regex matching |
| `tone` | `"response must be empathetic"` | Yes | LLM judge evaluates tone |
| `relevance` | `"response must address the question"` | Yes | LLM judge evaluates relevance |

### How Sto Constraints Work

```
Agent output
  → StoEvaluator runs each registered evaluator
    → score: 0.0 - 1.0
      → score >= threshold (default 0.7)? → pass
      → score < threshold? → generate feedback → agent retries
```

Sto constraints never block — they guide. The agent receives specific feedback about what to improve and generates a new response.

---

## Atom Vocabulary

Atoms are the fundamental observables that det formulas are built on. Each atom is a fact extracted from a single event in the execution trace.

### Tool Call Atoms

These fire when `event_type == "tool_call"`:

| Atom | Type | What it observes |
|------|------|-----------------|
| `called(tool)` | bool | Tool X was called at this timestep |
| `count(tool)` | int | Cumulative call count of tool X (LTL can't count, so grounding maintains this) |
| `arg_has(tool, pattern)` | bool | Tool args (serialized) match regex pattern |
| `arg_field_has(tool, field, pattern)` | bool | Specific arg field matches regex pattern |
| `arg_paths_within(tool, *prefixes)` | bool | All file paths in tool args are within allowed prefixes |
| `output_has(tool, pattern)` | bool | Tool output matches regex pattern (requires `guard_after()`) |
| `perm(permission)` | bool | Acting agent holds this static permission |

### Data Flow Atoms

These fire on `data_write`, `data_read`, and `message` events:

| Atom | Type | What it observes |
|------|------|-----------------|
| `contains(field)` | bool | A data_write event included this field |
| `flow(src, dest)` | bool | Data flowed from agent src to agent dest |

### LLM Content Atoms

These fire on `llm_response` and `llm_request` events:

| Atom | Type | What it observes |
|------|------|-----------------|
| `llm_said(pattern)` | bool | LLM output matches regex pattern |
| `prompt_contains(pattern)` | bool | LLM input matches regex pattern |
| `system_prompt_present()` | bool | LLM request has a system message |
| `context_length()` | int | Total character count of LLM input |

### How Atoms Connect to Patterns

Patterns are convenience functions that compose atoms into LTL formulas:

```python
# must_precede("A", "B") compiles to:
Not(Atom("called", "B")) Until Atom("called", "A")
#   ↑ uses called() atom          ↑ uses called() atom

# rate_limit("X", 3) compiles to:
G(Le(Var("count(X)"), Const(3)))
#       ↑ uses count() atom

# arg_blacklist("bash", "command", ["rm -rf"]) compiles to:
G(Implies(Atom("called", "bash"), Not(Atom("arg_field_has", "bash", "command", "rm -rf"))))
#         ↑ called() atom                  ↑ arg_field_has() atom
```

Users write NL strings. Patterns compile them to formulas over atoms. Grounding extracts atoms from events. The evaluator checks formulas against atoms. This separation means:

- **New atoms** = new observable capabilities (add extraction logic to grounding)
- **New patterns** = new convenience templates (add a function to the pattern library)
- **New evaluator backends** = new verification modes (runtime, Z3, model checking)

### Parameterized Atoms

Some atoms need to know what patterns to check against (regex, prefixes). These are **parameterized** — grounding only checks patterns that appear in the active formulas:

```python
# This formula uses arg_has("bash", "rm -rf")
formula = G(Not(Atom("arg_has", "bash", "rm -rf")))

# collect_content_atoms() extracts: {"arg_has": {("bash", "rm -rf")}}
# Grounding uses this to know: "check if bash args match 'rm -rf'"
```

This avoids speculatively matching every possible pattern — only atoms referenced in active contracts are evaluated.

### Extending the Atom Vocabulary

To add a new atom:

1. Register in `_CONTENT_PREDICATES` in `tracer/grounding.py` (if parameterized)
2. Add extraction logic in the `ground()` function
3. Use in pattern functions via `Atom("new_atom", ...)`

No changes needed to the formula AST or evaluator.
