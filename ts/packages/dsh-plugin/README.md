# dsh-plugin-sponsio

Deterministic contract enforcement for [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness).

Every tool call is checked against a `sponsio.yaml` before it dispatches. The check is pure TypeScript over the call history: no model runs, and a check costs microseconds.

## Why a rulebook and not a predicate

The harness already ships two guards. `repeat-tool-reminder` notices the same call twice; `timeout-policy` stops a call that hangs. Both are single, hard-coded properties.

What a rulebook adds is **history**. A rule can read what already happened in the session:

```yaml
version: "1"
agents:
  main:
    contracts:
      - G: "tool `check_policy` must precede `issue_refund`"
      - G: "tool `issue_refund` at most 1 times"
      - G: "never call `write_file` after `aml_check`"
      - G: "tool `write_file` restricted to `/workspace`"
```

"Check the policy before issuing a refund" is one line here, not a paragraph of prompt that the model may or may not follow. A repeat detector is one instance of that shape; this is the general case.

## Install

```sh
npm install dsh-plugin-sponsio
```

## Configure

```yaml
plugins:
  sponsio:
    config: sponsio.yaml   # the rulebook
    agentId: main          # which agent block governs this harness
    mode: observe          # observe | enforce
    exclude: []            # tool-name patterns to ignore, `*` wildcards
```

**Start in `observe`.** It records what would have been stopped and stops nothing. A rule written against an agent nobody has traced yet is a guess, and a guess that blocks is worse than one that logs. Read what it caught, then flip to `enforce`.

## What the model sees

A denied call returns the rule's own reason, written for a model to act on:

```
The action `send_email` was rejected by policy (rate_limit):
tool `send_email` at most 2 times. Choose a different approach.
```

## How it hooks in

One listener on `tools/pre-execute`. A clean verdict delegates with `next()`, so every listener registered after this one still gets its say; a violation in `enforce` mode returns `{ kind: 'deny', reason }`.

There is no post-execute half. `guardBefore` both judges the call and appends it to the trace the temporal rules count over, so a second hook would double-count.

## Two limits worth knowing

**A malformed rulebook fails at plugin load, not at the first call.** A guard that loads against an unreadable config and then allows everything is the one outcome an enforcement layer must never produce.

**`redirect_to_safe` denies rather than substitutes.** The harness's `PreToolDecision` is `allow | deny | ask` with no rewrite variant, by design: arguments are already logged and presented. So a rule that would swap `rm_rf` for `trash` refuses the call and names `trash` in the reason, which the model can act on.

## Writing rules

`npx sponsio patterns` lists the catalog. Rules can be written as English sentences (`tool \`a\` must precede \`b\``) or as structured entries (`{pattern: rate_limit, args: [send_email, 2]}`).

The [pattern catalog](https://github.com/SponsioLabs/Sponsio/blob/main/docs/reference/patterns.md) has every form, and each one is checked by a test that it still parses.

## License

Apache-2.0
