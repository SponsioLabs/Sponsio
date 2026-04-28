---
title: Pattern catalog
description: The full deterministic pattern library — each pattern's NL form, what it enforces, and the LTL it compiles to.
---

# Pattern catalog

Patterns are named factories that emit LTL formulas over the atom vocabulary. You write a natural-language rule; the parser matches it against these patterns and hands back a compiled contract. Patterns are *sugar* — they do not expand the expressiveness of the language, only the ergonomics.

Run `sponsio patterns` on the CLI to browse this catalog interactively with NL examples.

For the conceptual model (atom → pattern → formula → contract) see [Concepts overview](../concepts/overview.md). For the full atom vocabulary see [Architecture § Atoms](../concepts/architecture.md).

---

## Safety

| Pattern | NL example | What it enforces |
|---|---|---|
| `must_precede(A, B)` | `"tool `check_policy` must precede `issue_refund`"` | A must have been called before B can execute |
| `must_confirm(action)` | `"tool `delete_file` requires confirmation"` | A confirmation step must precede the action |
| `requires_permission(tool, perm)` | `"tool `transfer` requires permission `manager`"` | Agent must hold a static permission to use the tool |
| `no_data_leak(src, dest)` | `"no data leak from `read_db` to `send_email`"` | Data must not flow between two agents/tools |
| `destructive_action_gate(action)` | `"destructive action `drop_table` requires confirmation"` | A destructive tool needs an explicit gate step |

## Compliance

| Pattern | NL example | What it enforces |
|---|---|---|
| `no_reversal(A, B)` | `"after `approve`, tool `reject` is forbidden"` | Once A is called, B is permanently forbidden |
| `segregation_of_duty(A, B)` | `"tools `review` and `approve` must be by different agents"` | Same agent cannot perform both actions |
| `always_followed_by(A, B)` | `"every `refund` must be followed by `notify`"` | Whenever A happens, B must eventually happen |
| `required_steps_completion(steps)` | `"`aml_check` must complete before `issue_loan`"` | All steps must have completed before a gate is passed |

## Operational

| Pattern | NL example | What it enforces |
|---|---|---|
| `rate_limit(action, N)` | `"tool `query_db` at most 5 times"` | Action can be called at most N times total |
| `idempotent(action)` | `"tool `transfer` at most 1 times"` | Action can be called at most once (special case of rate_limit) |
| `cooldown(action, N)` | `"tool `send_email` cooldown of 3 steps"` | At least N steps between consecutive calls |
| `deadline(trigger, action, N)` | `"tool `respond` within 3 steps of `receive`"` | Action must happen within N steps of trigger |
| `bounded_retry(action, N)` | `"tool `deploy` at most 3 retries"` | Action limited to N retries |
| `loop_detection(action, N)` | `"tool `search` must not loop more than 5 times"` | Detects repeated calls with similar args |

## Exclusion

| Pattern | NL example | What it enforces |
|---|---|---|
| `mutual_exclusion(A, B)` | `"tools `approve` and `reject` are mutually exclusive"` | At most one of A or B can ever be called |
| `tool_allowlist(tools)` | `"agent may only call `search`, `summarize`"` | Only listed tools may be called |

## Argument and path checks

| Pattern | NL example | What it enforces |
|---|---|---|
| `arg_blacklist(tool, field, patterns)` | `"bash command must not contain `rm -rf`"` | An arg field must not match forbidden regex patterns |
| `scope_limit(tool, paths)` | `"bash may only access files under `/workspace`"` | All file paths in tool args must be within allowed prefixes |
| `arg_length_limit(tool, field, N)` | `"`sql.query` at most 500 chars"` | Argument length cap |
| `arg_value_range(tool, field, lo, hi)` | `"`transfer.amount` between 0 and 10000"` | Numeric argument range |
| `data_intact(tool, field)` | `"`aml_report` must not be edited after `aml_check`"` | Payload field is immutable once written |

## Agentic security

| Pattern | NL example | What it enforces |
|---|---|---|
| `untrusted_source_gate(tool)` | `"content from untrusted sources requires review"` | Data from untrusted origin must pass a gate before use |
| `confirm_after_source(tool)` | `"confirmation required after reading from `web_search`"` | A confirmation step must follow a source-read |
| `dangerous_bash_commands()` | `"bash must not run `rm -rf /`, `:(){:|:&};:`..."` | Built-in bash command blacklist |
| `dangerous_sql_verbs()` | `"sql must not issue `DROP`, `TRUNCATE`, `ALTER`"` | Built-in SQL verb blacklist |
| `irreversible_once(action)` | `"`post_tweet` at most once per session"` | Irreversible actions capped to a single call |

## Resource

| Pattern | NL example | What it enforces |
|---|---|---|
| `token_budget(N)` | `"total LLM tokens under 50000"` | Session-wide token cap |
| `delegation_depth_limit(N)` | `"sub-agent delegation at most 3 levels"` | Bounds recursive agent delegation |

---

## How patterns compile

```
NL string
  ─▶ Pattern function (e.g., must_precede("A", "B"))
      ─▶ LTL formula: Not(called("B")) Until called("A")
          ─▶ Grounding: extract atoms from trace events
              ─▶ Evaluator: evaluate formula over atom valuations
                  ─▶ True (pass) or False (block)
```

A few concrete compilations:

```python
# must_precede("A", "B") compiles to:
Not(Atom("called", "B")) Until Atom("called", "A")

# rate_limit("X", 3) compiles to:
G(Le(Var("count(X)"), Const(3)))

# arg_blacklist("bash", "command", ["rm -rf"]) compiles to:
G(Implies(
    Atom("called", "bash"),
    Not(Atom("arg_field_has", "bash", "command", "rm -rf")),
))
```

---

## Adding a new pattern

1. Add a factory to [`sponsio/patterns/library.py`](../../sponsio/patterns/library.py).
2. If it needs a new observable, add atom extraction in [`sponsio/tracer/grounding.py`](../../sponsio/tracer/grounding.py).
3. Add NL parsing in [`sponsio/generation/nl_to_contract.py`](../../sponsio/generation/nl_to_contract.py).
4. Tests for pattern behavior and NL parsing.
5. If the pattern belongs in the TS det core, mirror in [`ts/packages/sdk/src/core/patterns.ts`](../../ts/packages/sdk/src/core/patterns.ts).
   If you don't mirror it, add a row to [`/sdk-parity.md`](.//sdk-parity.md) so users know.
6. Document it here.
