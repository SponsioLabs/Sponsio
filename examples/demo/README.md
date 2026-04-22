# Sponsio Demos

Three trajectory-replay demos featured in the project README. Each shows a
capable SOTA model going off the rails under KPI pressure — and the
Sponsio contracts that catch it. Each demo uses a different framework
integration so you can see what `from sponsio.<framework> import Sponsio`
looks like
in context.

Run the packaged mock replays via the CLI. These work from `pip install sponsio`
without API keys or optional framework SDKs:

```bash
sponsio demo --scenario cleanup
sponsio demo --scenario trial
sponsio demo --scenario loan
```

From a source checkout, run the framework-specific examples with:

```bash
sponsio demo --mode integration --scenario cleanup
sponsio demo --mode integration --scenario trial
sponsio demo --mode integration --scenario loan
```

Or directly (add `--fast` to skip the typing animation):

```bash
python3 examples/demo/demo_coding_cleanup.py            # with Sponsio
python3 examples/demo/demo_coding_cleanup.py --no-guard # the breach
```

| Scenario | Script | Framework | Story |
|---|---|---|---|
| `cleanup` | [demo_coding_cleanup.py](demo_coding_cleanup.py) | `claude_agent` | "Clean up unused files." Agent reads `.env`, then sweeps `.env`, `.git/`, commits, force-pushes. 3 contracts catch everything. |
| `trial` | [demo_trial_recruiter.py](demo_trial_recruiter.py) | `langgraph` | Gemini 3 Pro falsifies 20 patient records (`lab_result 10.5 → 9.5`) to hit a 50/50 quota. Real ODCV-Bench trajectory. |
| `loan` | [demo_loan_fraud.py](demo_loan_fraud.py) | `crewai` | Loan officer agent edits applications to bypass AML (`fund_source` / amount). 19 of 24 SOTA models commit this fraud (ODCV-Bench `Financial-Audit-Fraud-Finding`). |

## Contract styles on display

- **Assume-guarantee (A/G)** — each demo has at least one contract with a
  non-trivial assumption. The CLI reporter shows the assumption firing
  ("▸ assume ... satisfied") + the contract activating
  ("⚡ contract 'X' is now active"), then subsequent violations blocked.
- **Rate limit via `count_with` + `Le`** — used in the trial and
  loan demos to catch agents re-running an evaluation after editing
  the input.
- **Bare `G(!called_with(...))` guards** — cleanup demo uses these for
  "never `rm .env/.git/`" and "no `git push --force` to main".

## Walkthrough

For a single-agent, many-contracts walkthrough (soft contracts, retry
loops, dashboard), see [demo_walkthrough.py](demo_walkthrough.py).

```bash
USE_MOCK=1 python3 examples/demo/demo_walkthrough.py
```
