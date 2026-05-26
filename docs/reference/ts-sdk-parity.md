# TypeScript SDK / Python parity

The TS SDK (`@sponsio/sdk`) and the Python core share the deterministic
runtime (formula AST, evaluator, grounding, pattern library, NL parser).
The deterministic semantics on both sides are identical for the surface
that exists in both. The same `(formula, trace)` pair always produces
the same verdict.

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

The TS pattern library (`ts/packages/sdk/src/core/patterns.ts`) implements roughly 35 factories; Python (`sponsio/patterns/library.py`) has 44. The unimplemented in TS:

| Pattern | Why it matters |
|---|---|
| `no_pii(fields)` | Output PII guard |
| `no_keywords(words)` | Output content blacklist |
| `max_length(field, n)` | Output length cap |
| `data_intact(action, paths)` | Path immutability |
| Plus a few smaller helpers around content / data-flow |

Workaround: express these as raw `Atom` formulas, or run them through the Python guard via the OTEL bridge.

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

Python's `parse_nl_unified()` recognises all 44 deterministic patterns. NL strings that don't match one of the 8 TS patterns return a parse failure on TS. Callers should fall back to constructing the `DetFormula` directly via the pattern factory, or parse on the Python side.

### CLI surface

Both Python and TS ship a CLI with the same command set (about 20 subcommands each, including `onboard`, `scan`, `validate`, `check`, `doctor`, `demo`, `report`, `packs`, `patterns`, `init`, `mode`, `explain`, `replay`, `export`, `export-sessions`, `eval`, `skill`, `prompt`). Cross-language scenarios in `tests/cross_language/` validate identical verdicts.

## Roadmap

Track issues tagged `area:ts-parity` for status. Priority order:

1. LLM-observation atoms (`prompt_contains`, `llm_said`, `output_has`). Biggest user-visible gap.
2. Missing patterns (`no_pii`, `no_keywords`, `max_length`, `data_intact`).
3. Expand TS NL parser to match Python's surface.
4. `Subset` node + data-flow predicates.

## What to do today if you need a missing feature

1. **Run the Python guard alongside the TS app.** The OTEL bridge
   accepts traces from either runtime; mixed deployments work.
2. **Construct the AST manually.** If the missing piece is a pattern
   that compiles to existing TS nodes, you can build the `Formula`
   directly. Patterns are just factories.
3. **File an issue.** Real user demand reorders the roadmap.
