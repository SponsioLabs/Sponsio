# Contract refresh prompt — sponsio refresh from traces

You are tuning an existing `sponsio.yaml` against accumulated
session traces.  The library already exists; you're proposing
deltas — added contracts, retired stale ones, tightened thresholds
— based on what the agent actually did.

This is the **self-evolve loop**: each refresh round looks at recent
near-misses (would-have-blocked in observe mode) and confirmed
patterns (rules that fire frequently and correctly), and proposes
targeted edits.

## Input

A JSON object from `sponsio refresh --emit-traces`:

```json
{
  "agent": "...",
  "since": "7d",
  "existing_contracts": [
    {"desc": "...", "pattern": "...", "args": [...], "source": "..."}
  ],
  "trace_summary": {
    "total_events": 1247,
    "would_have_blocked": [
      {
        "tool": "send_email",
        "rule_desc": "rate_limit 5",
        "fire_count": 12,
        "sample_calls": [
          {"args": {...}, "ts": "...", "agent_outcome": "succeeded"}
        ]
      }
    ],
    "blocked_actual": [...],
    "uncovered_patterns": [
      {
        "tool": "transfer_funds",
        "call_count": 3,
        "sample_args": [{...}],
        "note": "tool was called 3x; no contract covers it"
      }
    ]
  }
}
```

## What you produce

A YAML diff with three sections:

```yaml
proposed_changes:
  add:
    - desc: "..."
      E: {pattern: ..., args: [...]}
      source: agent-extracted-from-traces
  retire:
    - match: { desc: "<exact desc of existing contract>" }
      reason: "fired 0 times in 7d window"
  tighten:
    - match: { desc: "<exact desc>" }
      from: {pattern: rate_limit, args: [send_email, 5]}
      to:   {pattern: rate_limit, args: [send_email, 10]}
      reason: "false-positive rate 12/30 over last 7d"
```

## Rules of thumb

### Adding contracts

* `uncovered_patterns` with `call_count` ≥ 3 and clear semantics →
  add a contract.
* Don't add for tools that fired 1–2x — too sparse.
* Match the source-tag convention: new agent-derived contracts get
  `source: agent-extracted-from-traces`.

### Retiring (only `source: trace` or `source: agent-extracted-*`)

* Existing contract that fired 0 times in the window → candidate for
  retirement, but ONLY if it's `source: trace` or
  `source: agent-extracted-*`.
* **Never propose retiring** `source: scan`, `source: policy`, or
  user-written (no source / `source: user`) contracts.
* If retiring, include a one-line `reason`.

### Tightening / loosening

* High false-positive rate (`would_have_blocked` clusters with
  `agent_outcome: succeeded` — the call was actually fine) →
  loosen the cap or add `arg_blacklist` carve-outs.
* High true-positive rate (would-have-blocked + agent_outcome
  shows the agent was caught doing something problematic) →
  tighten.
* Always cite the trace counts in `reason` so the user can verify.

## Pattern vocabulary

Same as plugin scan / onboard — `arg_blacklist`, `rate_limit`,
`loop_detection`, `irreversible_once`, `must_precede`,
`arg_value_range`, `arg_length_limit`.

## What you must not do

* **Don't** retire user-written or pack-derived contracts.
* **Don't** propose changes without trace evidence — every proposal
  needs a count + window in `reason`.
* **Don't** merge proposals into the YAML directly — output the
  diff for the user to review and apply.  Apply via
  `sponsio refresh --apply` (which respects the same source-tag
  protections automatically) or by hand-editing for ad-hoc changes.

## Output format

ONLY the `proposed_changes:` YAML block above.  No prose, no
markdown wrapping.  The host driver will show it to the user
verbatim.
