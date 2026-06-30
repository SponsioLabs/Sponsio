# OpenClaw social-account actions (TweetClaw)

A small, **deterministic** Sponsio recipe for OpenClaw plugins that act through a
real, public social account. It uses [TweetClaw](https://example.invalid/tweetclaw)
as the concrete plugin path, but the same two contracts apply to any *live,
account-scoped side effect that follows a planning step*.

## The shape

| Action | Kind | Treatment |
| --- | --- | --- |
| `explore` | read-only discovery / planning | **open** — no contract |
| `draft_tweet` | compose locally, no live call | **open** — no contract |
| `tweetclaw` | live, account-scoped post | **gated** — re-confirm + capped |
| `confirm_reconfirmed` | marker emitted on operator approval | referenced by contracts |

The idea: keep endpoint review and drafting friction-free, but require a human
in the loop — and a per-session budget — before anything reaches the live
account.

## The contracts

[`sponsio.yaml`](sponsio.yaml) declares two deterministic guarantees on the
`social_bot` agent:

1. **Re-confirm before posting** — `must_precede(confirm_reconfirmed, tweetclaw)`.
   `tweetclaw` is forbidden until a `confirm_reconfirmed` event has been
   observed. Compiles to:

   ```
   (!called(tweetclaw) U called(confirm_reconfirmed)) | G(!called(tweetclaw))
   ```

2. **Cap live posts** — `rate_limit(tweetclaw, 3)`. At most 3 live posts per
   session. Compiles to `G(count(tweetclaw) <= 3)`.

Both are pure-Python checks — no LLM judge in the runtime hot path.

`explore` and `draft_tweet` carry no contract, so the default allow policy lets
the agent plan freely; only the live action is constrained.

## Validate

With the config loader and the CLI:

```bash
sponsio validate --config examples/recipes/openclaw_social_actions/sponsio.yaml
```

Expected: both guarantees report `✓ DET` and the command exits `0`.

## How it behaves at runtime

| Trace | `tweetclaw` outcome |
| --- | --- |
| `explore → draft_tweet → tweetclaw` | **blocked** — no prior `confirm_reconfirmed` |
| `explore → confirm_reconfirmed → tweetclaw` | allowed |
| `… → tweetclaw × 4` (after confirm) | 4th post **blocked** — cap is 3 |

## Adapting it

- **Layer on the full incident pack.** Add an `include:` to pull in the broader,
  mixed det/sto OpenClaw coverage on top of these two rules:

  ```yaml
  agents:
    social_bot:
      include:
        - sponsio:incident/openclaw
      contracts:
        # ... the two contracts above
  ```

- **Re-confirm *every* post (no reuse).** `must_precede` only requires *one*
  confirmation before the first post. To require a fresh confirmation per post —
  so an early approval can't be replayed — use the count-dominance form from the
  incident pack instead:

  ```yaml
  - desc: "Each live post needs its own fresh confirmation"
    A: "called `confirm_reconfirmed`"
    G:
      ltl: "G(called(tweetclaw) -> count(confirm_reconfirmed) >= count(tweetclaw))"
  ```

- **Different tool name.** Swap `tweetclaw` in both `args` lists for your
  plugin's live-action tool name.
