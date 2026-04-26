# TypeScript SDK / Python parity

The TS SDK (`@sponsio/sdk`) and the Python core share the deterministic
runtime (formula AST, evaluator, grounding, pattern library, NL parser).
The deterministic semantics on both sides are identical for the surface
that exists in both — the same `(formula, trace)` pair always produces
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

## What's missing on the TS side

### Formula nodes

| Node | Python | TS | Notes |
|---|---|---|---|
| `Subset` (set-relation) | ✅ | ❌ | Used by data-intact and a few discovery paths. TS code that hits it will throw `unknown formula node`. |

### Patterns

The TS pattern library (`ts-sdk/src/core/patterns.ts`) implements 35
factories; Python (`sponsio/patterns/library.py`) has 41. The 6
unimplemented in TS:

| Pattern | Why it matters |
|---|---|
| `no_pii(fields)` | Output PII guard |
| `no_keywords(words)` | Output content blacklist |
| `max_length(field, n)` | Output length cap |
| Plus three smaller helpers around content / data-flow |

Workaround: express these as raw `Atom` formulas, or run them through
the Python guard via the dashboard / OTEL bridge.

### Grounding predicates (atoms)

TS grounding covers the action-layer predicates: `called`, `count`,
`consecutive_count`, `called_with`, `arg_has`, `arg_field_has`,
`arg_paths_within`, plus a handful more (≈12 total).

Python additionally grounds the LLM-observation layer:

- `prompt_contains(text)`
- `llm_said(text)`
- `output_has(field)`
- `system_prompt_present`
- `context_length(n)`
- `flow(src, dest)` — data-flow predicates
- `perm(P)` / permission predicates
- `data_stores` forward-propagation

Any contract that uses one of these atoms must run on the Python guard.
This is the bigger of the two parity gaps in practice — most semantic /
LLM-observation contracts can't currently be enforced from TS alone.

### NL parser

TS's `parseNl()` (`ts-sdk/src/core/nl-parser.ts`) recognises 8 patterns:
`mustPrecede`, `alwaysFollowedBy`, `rateLimit`, `idempotent`,
`mutualExclusion`, `noReversal`, `cooldown`, `argBlacklist`.

Python's `parse_nl_unified()` recognises all 41 deterministic patterns
plus the stochastic catalog. NL strings that don't match one of the 8
TS patterns will return a parse failure — callers should fall back to
constructing the `DetFormula` directly via the pattern factory, or
parse on the Python side.

## Roadmap

The plan to close these gaps is not yet on a fixed timeline; track
issues tagged [`area:ts-parity`](https://github.com/anthropics/sponsio/labels/area%3Ats-parity)
(or the relevant repo label) for status. Priority order:

1. P2 LLM-observation atoms (`prompt_contains`, `llm_said`,
   `output_has`) — biggest user-visible gap
2. Missing patterns (`no_pii`, `no_keywords`, `max_length`)
3. Expand TS NL parser to match Python's surface
4. `Subset` node + data-flow predicates

## What to do today if you need a missing feature

1. **Run the Python guard alongside the TS app.** The dashboard / OTEL
   bridge accepts traces from either runtime; mixed deployments work.
2. **Construct the AST manually.** If the missing piece is a pattern
   that compiles to existing TS nodes, you can build the `Formula`
   directly — patterns are just factories.
3. **File an issue.** Real user demand reorders the roadmap.
