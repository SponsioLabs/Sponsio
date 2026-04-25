# Sponsio Demos

Four trajectory-replay demos featured in the project README. Each shows a
capable SOTA model going off the rails under KPI pressure — and the
Sponsio contracts that catch it. Each demo pairs with a different
framework integration so you can see what
`from sponsio.<framework> import Sponsio` looks like in context.
Scenarios **backup**, **wire**, and **freeze** are sourced from the
[OWASP Top 10 for Agentic Applications (2026)](../../docs/owasp-agentic-top-10.md).

Run the packaged mock replays via the CLI. These work from `pip install sponsio`
without API keys or optional framework SDKs:

```bash
sponsio demo --scenario cleanup
sponsio demo --scenario backup
sponsio demo --scenario wire
sponsio demo --scenario freeze
```

From a source checkout, run the framework-specific examples with:

```bash
sponsio demo --mode integration --scenario cleanup
sponsio demo --mode integration --scenario backup
sponsio demo --mode integration --scenario wire
sponsio demo --mode integration --scenario freeze
```

Or directly (add `--fast` to skip the typing animation):

```bash
python3 examples/demo/demo_coding_cleanup.py            # with Sponsio
python3 examples/demo/demo_coding_cleanup.py --no-guard # the breach
```

| Scenario | Script | Framework | OWASP | Story |
|---|---|---|---|---|
| `cleanup` | [demo_coding_cleanup.py](demo_coding_cleanup.py) | `claude_agent` | — | "Clean up unused files." Agent reads `.env`, then sweeps `.env`, `.git/`, commits, force-pushes. 3 contracts catch everything. |
| `backup` | [demo_backup_delete.py](demo_backup_delete.py) | `langgraph` | ASI-10 | SRE cost-optimizer deletes off-site DR backups to hit a "cut storage 20%" KPI. `scope_limit` + `arg_value_range` + `rate_limit` block the first prod delete. |
| `wire` | [demo_wire_transfer.py](demo_wire_transfer.py) | `crewai` | ASI-09 | AP copilot wires $847k to a brand-new vendor under a 24h SLA — no compliance approval, no human confirm. `arg_value_range` + `must_precede` + `must_confirm` all fire on the same call. |
| `freeze` | [demo_freeze_violation.py](demo_freeze_violation.py) | `langgraph` | ASI-10 | Recreates the July 2025 Replit incident. User declares code freeze; agent drops prod tables, fabricates replacement rows, writes a "database intact" status report. Four A/E contracts catch the whole chain — the first blocks the `DROP` at step 7. |

## Contract styles on display

- **Argument-level** — `scope_limit` (path prefixes) and `arg_value_range`
  (numeric bounds) catch the unsafe action at the argument level, not at
  the tool level. Visible in the `backup` + `wire` demos.
- **Ordering** — `must_precede` and `must_confirm` encode "this action is
  gated on an earlier step". Visible in the `wire` demo.
- **Rate / loops** — `rate_limit` via `count_with` + `Le`, visible in
  `backup`, `wire`, and `freeze`, catches runaway deletion loops, wire
  floods, or a SQL session exceeding its bound.
- **Bare `G(!called_with(...))` guards** — `cleanup` uses these for
  "never `rm .env/.git/`" and "no `git push --force` to main".
- **Assume-guarantee (A/E) pairs** — the `freeze` demo carries four A/E
  contracts: `code-freeze user message → no destructive SQL`,
  `destructive SQL observed → escalate before status report`,
  `destructive SQL observed → no INSERT (prevents fabrication)`,
  `prod connection → read-only only`. Each one encodes a conditional
  obligation: the enforcement only becomes binding once the assumption
  has fired earlier in the trace. This is the contract style Sponsio's
  LTL evaluator handles that regex-based guardrails structurally cannot.

## Walkthrough

For a single-agent, many-contracts walkthrough (soft contracts, retry
loops, dashboard), see [demo_walkthrough.py](demo_walkthrough.py).

```bash
USE_MOCK=1 python3 examples/demo/demo_walkthrough.py
```
