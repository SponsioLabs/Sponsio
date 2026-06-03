# τ²-bench (third-party, Sierra Research)

> **Last updated:** 2026-06-02 · See [Benchmarks index](../benchmarks.md) for cross-benchmark comparison and methodology.

## What it measures

[τ²-bench](https://github.com/sierra-research/tau2-bench) evaluates customer-service LLM agents on multi-turn dual-control conversations: an agent simulator plays the customer service representative against a user simulator across three domains (retail, airline, telecom), each with a written policy.md the agent is expected to follow. The benchmark's public leaderboard reports `pass^k`, the fraction of tasks where all `k` independent retries reached the correct final database state.

Three domains × four reference models (Claude 3.7 Sonnet, GPT-4.1, GPT-4.1-mini, o4-mini) × ~114 tasks × 4 trials per task = **4,464 simulation traces**.

- **Paper:** [Yao et al., "τ²-bench: Evaluating Conversational Agents in a Dual-Control Environment", 2025](https://arxiv.org/abs/2506.07982)
- **Code & traces:** [github.com/sierra-research/tau2-bench](https://github.com/sierra-research/tau2-bench)
- **Authoring institution:** Sierra Research, *not* Sponsio Labs.

τ²-bench's native `pass^k` is an **outcome metric**: it checks whether the agent's final `db_check` matches the ground-truth final state and whether the final assistant message matches expected `communicate_info`. It does **not** measure procedural compliance during the turn-by-turn execution. AgentPex ([Sharma, Barke, Zorn 2026](https://arxiv.org/abs/2603.23806)) showed that **48 / 58 (83%) of `reward = 1.0` Claude traces violate at least one procedural rule** in the policy.md, demonstrating that procedure compliance and outcome reward are independent axes of agent quality.

## Sponsio's contribution

Sponsio adds the procedural axis as a **deterministic contract library** that operates in two modes from a single set of `DetFormula` definitions:

- `mode="observe"`: replay an existing trace, emit per-rule fire counts (offline evaluation, what this page reports)
- `mode="enforce"`: wrap a live agent loop, block tool calls that violate the contract at `guard_before` (runtime enforcement, what no LLM-judge approach structurally supports)

The same 112 contracts cover 6 of AgentPex's 7 procedural-evaluator categories (Output Spec, Transition Spec, Forbidden Edges, Argument Spec, Argument Groundedness, Predicted Plan); the 7th (Predicted Final State) is already captured by τ²-bench's native `db_check` and is not duplicated.

**The runtime enforcement mode is the key structural differentiator** vs LLM-judge approaches like AgentPex: an LLM-judge runs only after the trace completes; a contract guard runs before each tool call executes. The same contracts run in either mode. This page reports the offline evaluation numbers; the enforcement counterfactual (§ *Counterfactual enforcement* below) shows what the same contracts would have prevented on live agent loops.

## Reliability matrix (k = 4, τ²-bench task-level convention)

The headline result. `pass^4` is the τ²-bench native outcome metric (all 4 retries reach the correct final state). `proc-clean^4` is the procedural mirror introduced here (all 4 retries had **zero Sponsio rule fires**). `joint^4` is the deployment-ready signal (`pass AND proc-clean` on every trial).

| Domain | Model | pass^4 (outcome) | proc-clean^4 (procedure) | joint^4 (deployment) |
|---|---|---:|---:|---:|
| **retail** | Claude 3.7 Sonnet | 59.6% | **0.0%** | 0.0% |
| | GPT-4.1 | 52.6% | **78.1%** | 43.9% |
| | GPT-4.1-mini | 38.6% | 17.5% | 9.6% |
| | o4-mini | 45.6% | 77.2% | 34.2% |
| **airline** | Claude 3.7 Sonnet | 36.0% | 0.0% | 0.0% |
| | GPT-4.1 | 40.0% | 34.0% | 24.0% |
| | GPT-4.1-mini | 26.0% | 14.0% | 14.0% |
| | o4-mini | 38.0% | 48.0% | 30.0% |
| **telecom** | Claude 3.7 Sonnet | 25.4% | 0.0% | 0.0% |
| | GPT-4.1 | 19.3% | 4.4% | 2.6% |
| | GPT-4.1-mini | 17.5% | 0.9% | 0.9% |
| | o4-mini | 26.3% | 7.0% | 7.0% |

Two findings the matrix surfaces that `pass^k` alone cannot:

- **Claude reaches `proc-clean^4 = 0%` across all three domains.** Claude consistently violates the τ²-bench protocol rule that an assistant message must not contain both user-facing text and a tool call (449 / 723 violations in AgentPex's audit). Every task has at least one trial that fires this contract, so no task is procedurally clean on all 4 retries. Outcome `pass^4` is still in the 25 to 60% range, but `joint^4 = 0%`: there is no task on which Claude can be deployed without a contract layer flagging at least one procedure violation per 4 attempts.
- **`proc-clean^4 > pass^4` on retail GPT-4.1 (78.1% vs 52.6%) and retail o4-mini (77.2% vs 45.6%).** These two cells fail tasks for content-quality reasons (wrong product variant, wrong refund amount) rather than procedural violations. Outcome reliability and procedural reliability are independent axes; the deterministic contract layer captures the latter, semantic content quality remains in the optional `sto` layer (see *Remaining gap*).

The gap between `pass^4` and `joint^4` is the procedural reliability tax that outcome-only metrics cannot see. The largest gap in this matrix is **retail GPT-4.1-mini (29 pp): pass^4 = 38.6% but joint^4 = 9.6%**, meaning four out of every five "passing" tasks would fail a procedural-correctness audit on at least one trial.

## Procedure-violation detection (sim-level)

Aggregated per-domain blind-spot and procedure-recall rates. Blind-spot = tau2-passing sims that nonetheless fire ≥1 Sponsio rule (AgentPex's central finding generalised across the matrix). Proc-recall = tau2-failing sims caught by Sponsio.

| Domain | Model | Blind-spot rate (tau2_pass × violated) | Proc-recall on tau2_fail |
|---|---|---:|---:|
| **retail** | Claude 3.7 | 98.6% (354 / 359) | 100.0% (97 / 97) |
| | GPT-4.1 | 9.5% (32 / 338) | 15.3% (18 / 118) |
| | GPT-4.1-mini | 48.2% (145 / 301) | 57.4% (89 / 155) |
| | o4-mini | 14.1% (46 / 326) | 13.1% (17 / 130) |
| **airline** | Claude 3.7 | 97.0% (97 / 100) | 100.0% (100 / 100) |
| | GPT-4.1 | 27.7% (31 / 112) | 63.6% (56 / 88) |
| | GPT-4.1-mini | 47.5% (48 / 101) | 90.9% (90 / 99) |
| | o4-mini | 21.2% (25 / 118) | 46.3% (38 / 82) |
| **telecom** | Claude 3.7 | 99.6% (224 / 225) | 100.0% (231 / 231) |
| | GPT-4.1 | 62.2% (97 / 156) | 82.7% (248 / 300) |
| | GPT-4.1-mini | 80.5% (161 / 200) | 94.9% (243 / 256) |
| | o4-mini | 66.7% (128 / 192) | 90.5% (239 / 264) |

The Claude column independently reproduces AgentPex's published blind-spot finding (48 / 58 = 83% on Claude 3.5 Sonnet); the matrix extends it to 3 domains × 4 models without further LLM calls.

## Per-category fingerprints

Each contract is tagged with one of 6 AgentPex-style evaluator categories. The percentage in each cell is the fraction of sims in the cell that triggered ≥1 contract in that category.

### Retail (456 sims per cell)

| Category | Claude 3.7 | GPT-4.1 | GPT-4.1-mini | o4-mini |
|---|---:|---:|---:|---:|
| output_spec (same-turn protocol, transfer-summary format) | **98.9%** | 2.0% | 36.8% | 0.0% |
| transition_spec (identity, get-X-before-modify) | 2.4% | 3.7% | 4.4% | 3.1% |
| forbidden_edges (cancel reason enum, etc.) | 0.0% | 0.0% | 0.0% | 0.0% |
| argument_spec (state preconditions, item-count match) | 9.4% | 6.4% | 12.9% | 3.7% |
| arg_groundedness (payment in profile, owner match) | 2.2% | 1.1% | 9.4% | 7.2% |
| predicted_plan (rate-limits) | 0.2% | 1.3% | 0.9% | 0.2% |

### Airline (200 sims per cell)

| Category | Claude 3.7 | GPT-4.1 | GPT-4.1-mini | o4-mini |
|---|---:|---:|---:|---:|
| output_spec | **98.0%** | 9.0% | 46.5% | 0.0% |
| transition_spec | 7.0% | 17.5% | 26.5% | 15.5% |
| forbidden_edges (cancel-reason enum, basic-economy ban) | **42.5%** | 30.5% | 37.0% | 21.5% |
| argument_spec | 0.0% | 0.0% | 0.0% | 0.0% |
| arg_groundedness (reservation owner) | 3.0% | 13.0% | 20.0% | 13.0% |
| predicted_plan | 3.5% | 3.0% | 3.5% | 2.0% |

### Telecom (456 sims per cell)

| Category | Claude 3.7 | GPT-4.1 | GPT-4.1-mini | o4-mini |
|---|---:|---:|---:|---:|
| output_spec | **99.1%** | 38.4% | **74.1%** | 0.0% |
| transition_spec (diagnostic-decision tree, identity, account active) | **42.7%** | 31.1% | **23.7%** | 84.1% |
| forbidden_edges (overdue-bill payment, roaming idempotency) | 0.9% | 2.6% | 10.7% | **52.4%** |
| argument_spec (dob, refuel range) | 0.0% | 0.0% | 13.4% | 0.0% |
| arg_groundedness | 0.0% | 0.0% | 0.0% | 0.0% |
| predicted_plan | 0.0% | 0.0% | 0.7% | 0.9% |

Each model has a distinct procedural fingerprint visible in the matrix. Claude is dominated by `output_spec` (same-turn text + tool_call). o4-mini is the only model that completely avoids same-turn protocol violations in retail / telecom, but is the worst on telecom workflow contracts (`transition_spec 84.1%`, `forbidden_edges 52.4%`: e.g. `send_payment_request` against a non-Overdue bill, roaming toggles without prior `get_data_usage`).

## Contract library composition

```
sponsio.patterns.library factories used (8 of 50+):
  must_precede                           . A must happen before B
  arg_allowlist / arg_blacklist          . value-set / regex on tool args
  arg_value_range                        . numeric bounds
  rate_limit / idempotent                . invocation count caps
  workflow_step                          . G(observed-state → X(next-action))
  ctx_required                           . fact-substrate gates
```

Plus the **Term layer** introduced for this benchmark (`ArgValue`, `CtxValue`, `ArgLength`, `UnaryFn`) for cross-call value comparison; e.g. `Eq(ArgLength("modify_pending_order_items", "item_ids"), ArgLength("modify_pending_order_items", "new_item_ids"))` for item-count match.

| Domain | Contracts | Notes |
|---|---:|---|
| retail | 42 | includes 6 post-modification verification (`workflow_step`) and 2 Term-based item-count-match contracts |
| airline | 31 | includes Term-based max-passengers (`ArgLength ≤ 5`) and 4 post-modification verification contracts |
| telecom | 36 | includes **10 prescriptive workflow_step contracts** for the diagnostic decision tree (airplane mode, SIM, roaming, APN, VPN, app permissions, network mode, data saver, Wi-Fi calling) |

Three contract families warrant mention:

- **Term-based cross-call value contracts.** `ArgValue` / `CtxValue` / `ArgLength` let `Eq` / `Le` / `Lt` / `Ge` / `Gt` compose with arg-derived values and ctx-derived facts. Unlocks Cat-C constraints (item-count match, distinct payment method, max-5-passengers) that were structurally impossible to express as deterministic LTL before.
- **Prescriptive `workflow_step (G(A → X(B)) shape).`** Triggers an **obligation hint** at runtime when the antecedent fires (e.g. observe airplane mode → "next step: call toggle_airplane_mode") in addition to the standard violation message when the next event fails to satisfy B. Same enforcement hook as the block path, opposite verdict.
- **Telecom diagnostic decision tree (10 workflow_step contracts).** Maps observed phone-state diagnostic results (extracted from free-form TOOL message text via regex sweep) to the remediation tool the user simulator must call next. Catches "agent observed the diagnostic but never instructed the user to act on it" failures, the most common τ²-bench telecom failure mode.

## Enforcement cost (12-cell matrix, 4,464 traces total)

| Metric | Sponsio (det LTL) | AgentPex (LLM-as-judge) |
|---|---|---|
| LLM calls per trace | **0** | 9 |
| Cost per trace | **$0** | $0.019 |
| Wall-clock per trace | **0.4 sec** | 139 sec |
| Total cost (4,464 traces) | **$0** | ~$85 |
| Total wall-clock | **33 min** | ~172 h |
| Determinism | bit-perfect | LLM noise |
| Static auditability | every rule is an LTL formula | LLM prompt |
| Runtime enforcement | **yes** (`mode="enforce"`) | structurally not possible |

The cost / latency / determinism comparison is stark: zero LLM calls vs nine per trace; bit-perfect reproduction vs LLM nondeterminism; static formula vs unauditable prompt. **Runtime enforcement is the load-bearing difference**: an LLM-judge fundamentally runs after the fact; a guard runs before the fact.

## Counterfactual enforcement (telecom o4-mini)

For 20 τ²-failed telecom o4-mini sims sampled at random, Sponsio's first-violation event was identified and the downstream trace inspected.

| Metric | Value |
|---|---|
| τ²-fail sims sampled | 20 |
| Sims firing ≥1 contract | 16 / 20 (80%) |
| First violation has downstream tool calls | **16 / 16 (100%)** |
| Top blocked tool | `send_payment_request` (10 calls) |
| Top blocked rule | `transition.get_bills_before_payment_request` (8 instances) |

100% of the firing sims have additional tool calls after the first contract violation, in a trace where the agent commits further state on top of the bad first call. Runtime enforcement (`mode="enforce"`) would have blocked at the first violation, plausibly averting the cascading failure on every one of these 16 sims. This is the structural runtime-enforcement value that LLM-judge methods cannot provide.

## AgentPex parity check (for comparability with their Fig 8 baseline)

AgentPex paper reports their Output Spec → tau2-fail AUC = **0.680** on Claude traces (Fig 8). This is their internal evaluator-quality metric: how well does their LLM judge's score predict tau2 outcome failure? It is not what Sponsio is trying to optimise. Sponsio's job is to **catch procedure violations specified in policy.md**, not to predict outcome, but it is the only quantity AgentPex published that can be computed identically on the same trace data, so we report it for direct comparability.

Treating per-sim Sponsio fire count as a classifier score for `tau2_reward = 0`, computed via the Wilcoxon–Mann–Whitney rank-sum formulation:

| Domain | Claude 3.7 | GPT-4.1 | GPT-4.1-mini | o4-mini |
|---|---:|---:|---:|---:|
| retail | 0.597 | 0.531 | 0.546 | 0.495 |
| **airline** | **0.692** | **0.692** | **0.744** | 0.637 |
| telecom | 0.562 | 0.638 | 0.591 | 0.623 |

Sponsio matches or exceeds the AgentPex 0.680 baseline on **5 of 12 cells**, including all three airline cells where the cell's procedural and outcome axes happen to overlap. Cells where the AUC sits near 0.5 are not a Sponsio failure: AUC measures the correlation between procedural violations and outcome failures in that specific cell, and the two axes are independent by construction (a model can violate procedure consistently while still reaching correct outcomes, or vice versa). Reporting AUC here is for parity with AgentPex's published metric, not as a primary measure of Sponsio's contribution.

## Remaining gap

The 4,464-trace matrix has Sponsio firing on **76.4% combined sim-level block rate** of τ²-failed sims. The residual 23.6% miss falls in three classes the deterministic contract layer cannot resolve by construction:

- **Class A. Cross-call semantic value matching** (e.g. retail "agent modified the order to a product variant whose `product_id` doesn't match what the user asked about"). The Term layer (`ArgValue`, `CtxValue`) covers structural equality (item-count match, distinct payment method) but not the semantic match between a user description and a `product_id`. The latter requires content understanding.
- **Class B. Wrong-args-but-correct-tool** (e.g. retail "agent called `modify_pending_order_items` with the correct order but the wrong new `item_ids`"). The deterministic layer sees the call is `modify_pending_order_items` with valid arg shape; tau2's `action_check` knows the expected args came from the task's `evaluation_criteria.actions`, which is semantic ground-truth not exposed to the agent at runtime.
- **Class C. Final response content quality** (e.g. retail "the agent's last message reported $54.04 refund but should have reported $54.40"). τ²'s `communicate_info` checks the final assistant message; deterministic contracts cannot evaluate free-form content claims.

All three closure paths route through Sponsio's optional `sto` (stochastic atomic proposition) layer, which adds an LLM-judge per atom at tunable β threshold. The 76.4% deterministic floor + sto layered on top is the natural extension; this benchmark page reports the deterministic floor alone.

## Reproduce

```sh
cd Benchmarks/tau2-bench
python3.12 -m procedure_correctness.cli --all-domains
```

The full 12-cell matrix completes in **33 minutes** at **$0 cost** with **0 LLM calls**, deterministically. Detailed contracts, adapter, and evaluation CLI live under `procedure_correctness/` in the `Benchmarks/tau2-bench/` workspace.

## Key takeaway

Sponsio is the **first procedure-correctness layer for τ²-bench that runs in both observe and enforce modes from a single deterministic contract library**, at **$0 LLM cost** and **0.4 sec per trace**. The headline result is the task-level reliability matrix (`pass^4` × `proc-clean^4` × `joint^4`) which introduces the procedural-reliability dimension that outcome-only `pass^k` cannot see, quantifying the gap between τ²-bench leaderboard performance and deployment readiness (e.g. Claude retail `pass^4 = 59.6%` but `joint^4 = 0.0%`). On AgentPex's published evaluator-quality metric (AUC = 0.680), the same deterministic layer achieves parity or exceeds on 5 / 12 cells without any LLM call. The runtime enforcement mode is the structural differentiator from all LLM-judge approaches: a guard runs before each tool call executes; an LLM judge runs only after the trace completes.

---

**Related:** [Benchmarks index](../benchmarks.md) · [AgentDojo](agentdojo.md) · [ODCV-Bench](odcv.md) · [RedCode-Exec](redcode.md) · [SWE-bench](swebench.md) · [Contracts](../../concepts/contracts.md) · [Architecture](../../concepts/architecture.md)
