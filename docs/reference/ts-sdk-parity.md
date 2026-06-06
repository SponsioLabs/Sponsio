# TypeScript SDK / Python parity

The TS SDK (`@sponsio/sdk`) and the Python core share the deterministic
runtime: the formula AST (the LTL syntax tree the engine evaluates), the
evaluator, the grounding layer (the step that turns trace events into
the observable facts the engine reasons over), the pattern library, and
the NL parser. The deterministic semantics on both sides are identical
for the surface that exists in both. The same `(formula, trace)` pair
always produces the same verdict.

The TS surface, however, is **smaller** than Python's. Python is the
reference implementation; TS lags. This page is the authoritative list
of what is and isn't available in TS so users can plan around it.

## What's identical

- `Formula` AST: `G`, `F`, `U`, `X`, `And`, `Or`, `Not`, `Implies`, `Atom`,
  `Le`, `Lt`, `Ge`, `Gt`, `Eq`
- Recursive LTL evaluator with weak finite-trace semantics
- Per-event grounding for the core temporal/structural predicates
- Det-only contract enforcement at the action boundary
- Cross-language test scenarios in [`tests/cross_language/scenarios.json`](../../tests/cross_language/scenarios.json)
  pass on both runtimes

## Both sides are det-only

The engine is deterministic on both Python and TS. So the parity gap discussed below is the gap between two **det** runtimes.

## What's missing on the TS side

### Formula nodes

| Node | Python | TS | Notes |
|---|---|---|---|
| `Subset` (set-relation) | ✅ | ❌ | Used by data-intact and a few discovery paths. TS code that hits it throws `unknown formula node`. |

### Patterns

The TS pattern library (`ts/packages/sdk/src/core/patterns.ts`) implements roughly 36 factories; Python (`sponsio/patterns/library.py`) has 45. The unimplemented in TS:

| Pattern | Why it matters |
|---|---|
| `no_pii(fields)` | Output PII guard |
| `no_keywords(words)` | Output content blacklist |
| `max_length(field, n)` | Output length cap |
| `data_intact(action, paths)` | Path immutability |
| Plus a few smaller helpers around content / data-flow |

Workaround: express these as raw `Atom` formulas, or run them through the Python guard via the OTEL bridge.

### v0.2 enforcement strategies (partial parity)

The v0.2 `redirect_to_safe` pattern factory exists on TS (`redirectToSafe` in `ts/packages/sdk/src/core/patterns.ts`). It compiles to the same `G(Not(called(unsafe)))` LTL formula as the Python side, so a TS evaluator produces the same violation verdict as the Python verifier on a given trace.

What's NOT on TS yet:

- **Strategy system + dispatch.** The Python `DetFormula` carries an `enforcement_strategy` field; the runtime monitor honours pattern-attached strategies (`RedirectToSafe`, `EscalateToHuman`, `WarnOnly`) before falling back to `DetBlock`. TS has no equivalent layer, so every violation surfaces as a plain block on TS.
- **LangGraph adapter redirect dispatch.** Python's `LangGraphGuard._invoke_safe_tool` invokes the substitute tool transparently and records the safe call in the trace. The `@sponsio/sdk/langchain` adapter does not implement this; a redirect-style contract on TS today blocks the call, the application loop has to invoke the substitute manually.
- **`tool_policy` config layer.** Python's `BaseGuard` synthesizes a `tool_allowlist` contract from a `tool_policy: { default: deny, approved: [...] }` block. TS does not yet have this convenience; you build the same `toolAllowlist([...])` formula by hand.
- **`filter_tools(candidates)` API + `dry_run` probe.** Python's per-turn proactive filter is a pure probe with no log / callback / perf pollution. TS does not yet expose this. Adapter `wrap()`-time filtering on TS is also not implemented; the same effect can be achieved by filtering the tool list before passing to `wrapTools`.
- **`EscalateToHuman` notifier callbacks.** Python's `EscalateToHuman(notify=[...])` fires user-supplied notifiers with isolation. TS lacks the strategy class entirely (see above).

Workaround: for redirect-style contracts on TS today, read the violation outcome and dispatch the substitute tool from your application loop; the formula side fires correctly.

### Grounding predicates (atoms)

TS grounding covers the action-layer predicates: `called`, `count`, `consecutive_count`, `called_with`, `arg_has`, `arg_field_has`, `arg_paths_within`, plus a handful more (about 12 total).

Python additionally grounds the LLM-observation layer:

- `prompt_contains(text)`
- `llm_said(text)`
- `output_has(field)`
- `system_prompt_present`
- `context_length(n)`
- `flow(src, dest)` data-flow predicates
- `perm(P)` permission predicates
- `data_stores` forward-propagation

Any contract that uses one of these atoms must run on the Python guard. This is the bigger of the two parity gaps in practice. Most LLM-observation contracts cannot currently be enforced from TS alone.

### NL parser

TS's `parseNl()` (`ts/packages/sdk/src/core/nl-parser.ts`) recognises 8 patterns: `must_precede`, `always_followed_by`, `rate_limit`, `idempotent`, `mutual_exclusion`, `no_reversal`, `cooldown`, `deadline`.

Python's `parse_nl_unified()` recognises all 45 deterministic patterns. NL strings that do not match one of the 8 TS patterns return a parse failure on TS. Callers should fall back to constructing the `DetFormula` directly via the pattern factory, or parse on the Python side.

### CLI surface

Both Python and TS ship a CLI with the same command set (about 20 subcommands each, including `onboard`, `scan`, `validate`, `check`, `doctor`, `demo`, `report`, `packs`, `patterns`, `init`, `mode`, `explain`, `replay`, `export`, `export-sessions`, `eval`, `skill`, `prompt`). Cross-language scenarios in `tests/cross_language/` validate identical verdicts.

## Roadmap

Track issues tagged `area:ts-parity` for status. Priority order:

1. LLM-observation atoms (`prompt_contains`, `llm_said`, `output_has`). Biggest user-visible gap.
2. v0.2 strategy system port: `EnforcementStrategy` protocol, `RedirectToSafe` dispatch in the `@sponsio/sdk/langchain` adapter, `EscalateToHuman.notify` callback hooks.
3. v0.2 `tool_policy` config block + `filter_tools(candidates)` API parity.
4. Missing patterns (`no_pii`, `no_keywords`, `max_length`, `data_intact`).
5. Expand TS NL parser to match Python's surface.
6. `Subset` node + data-flow predicates.

## What to do today if you need a missing feature

1. **Run the Python guard alongside the TS app.** The OTEL bridge
   accepts traces from either runtime; mixed deployments work.
2. **Construct the AST manually.** If the missing piece is a pattern
   that compiles to existing TS nodes, you can build the `Formula`
   directly. Patterns are just factories.
3. **File an issue.** Real user demand reorders the roadmap.
