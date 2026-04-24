# Sponsio Demos

Three trajectory-replay demos featured in the project README. Each shows a
capable SOTA model going off the rails under KPI pressure — and the
Sponsio contracts that catch it. Each demo uses a different framework
integration so you can see what `from sponsio.<framework> import Sponsio`
looks like in context. Scenarios **backup** and **wire** are sourced
from the [OWASP Top 10 for Agentic Applications (2026)](../../docs/owasp-agentic-top10.md).

Run the packaged mock replays via the CLI. These work from `pip install sponsio`
without API keys or optional framework SDKs:

```bash
sponsio demo --scenario cleanup
sponsio demo --scenario backup
sponsio demo --scenario wire
```

From a source checkout, run the framework-specific examples with:

```bash
sponsio demo --mode integration --scenario cleanup
sponsio demo --mode integration --scenario backup
sponsio demo --mode integration --scenario wire
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

## Contract styles on display

- **Argument-level** — `scope_limit` (path prefixes) and `arg_value_range`
  (numeric bounds) catch the unsafe action at the argument level, not at
  the tool level. Visible in the `backup` + `wire` demos.
- **Ordering** — `must_precede` and `must_confirm` encode "this action is
  gated on an earlier step". Visible in the `wire` demo.
- **Rate / loops** — `rate_limit` via `count_with` + `Le`, visible in
  both `backup` and `wire`, catches runaway deletion loops or wire floods.
- **Bare `G(!called_with(...))` guards** — `cleanup` uses these for
  "never `rm .env/.git/`" and "no `git push --force` to main".

## Walkthrough

For a single-agent, many-contracts walkthrough (soft contracts, retry
loops, dashboard), see [demo_walkthrough.py](demo_walkthrough.py).

```bash
USE_MOCK=1 python3 examples/demo/demo_walkthrough.py
```
