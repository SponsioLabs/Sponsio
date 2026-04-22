# `sponsiolabs/eval-action`

Run [`sponsio eval`](https://docs.sponsio.dev/cli#eval) as a pull-request
check and block merges on contract regressions.

One-liner pitch: *if one of your labelled traces starts getting misclassified,
this action fails the PR and tells you which contract moved.*

## Quick start

```yaml
name: Sponsio eval gate

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write    # needed for the sticky PR comment

jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: sponsiolabs/eval-action@v1
        with:
          traces: traces/
          config: sponsio.yaml
          baseline: .sponsio/baseline.json
          max-fpr-delta: "0.05"
          max-fnr-delta: "0.02"
```

## Inputs

| name | default | notes |
|---|---|---|
| `traces` | **required** | Directory of `safe_*.json` / `unsafe_*.json` OTLP traces. Produced by `sponsio export` or hand-curated. |
| `config` | `sponsio.yaml` | Contracts source. |
| `baseline` | *(empty)* | Prior `--json` report. Omit on first run; the action will skip the gate and just emit current metrics. |
| `max-fpr-delta` | `0.05` | Allowed FPR increase (fraction, 0.05 = 5pp). |
| `max-fnr-delta` | `0.02` | Allowed FNR increase. Tighter than FPR because a missed unsafe trace is usually worse than a false alarm. |
| `write-baseline` | `false` | If the gate passes, overwrite `baseline` with the current report. Only enable on `push: main`. |
| `sponsio-version` | *(latest)* | e.g. `"==0.4.2"` to pin. |
| `python-version` | `3.12` | |
| `working-directory` | `.` | |
| `report-path` | `sponsio-eval-report.json` | Where to write the JSON report (relative to `working-directory`). |
| `comment-on-pr` | `true` | Post a sticky summary comment on PR events. |
| `upload-artifact` | `true` | Attach the report to the workflow run. |

## Outputs

| name | |
|---|---|
| `exit-code` | 0 = gate passed, 1 = regression. |
| `fpr` / `fnr` | Overall metrics on the current corpus. |
| `fpr-delta` / `fnr-delta` | Deltas vs baseline (empty if no baseline supplied). |
| `report-path` | Absolute path to the written JSON report. |

## Typical workflow split

Running the same action in two workflows with different settings is the
cleanest pattern:

**`pr-eval.yml`** — gate every PR, never mutate the baseline:

```yaml
on: pull_request
jobs:
  gate:
    steps:
      - uses: actions/checkout@v4
      - uses: sponsiolabs/eval-action@v1
        with:
          traces: traces/
          baseline: .sponsio/baseline.json
```

**`main-eval.yml`** — after merge, ratchet the baseline forward so future
PRs are compared against the latest-known-good metrics:

```yaml
on:
  push:
    branches: [main]
jobs:
  ratchet:
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - uses: sponsiolabs/eval-action@v1
        with:
          traces: traces/
          baseline: .sponsio/baseline.json
          write-baseline: "true"
      - name: Commit updated baseline
        run: |
          git config user.name  "sponsio-bot"
          git config user.email "bot@sponsio.dev"
          git add .sponsio/baseline.json
          git diff --staged --quiet || \
            git commit -m "chore(sponsio): ratchet eval baseline" && git push
```

## What the PR comment looks like

```
## Sponsio eval gate

✓ No regression vs baseline

| metric | current | Δ vs baseline |
|---|---:|---:|
| FPR | 0.0333 | -1.67pp  |
| FNR | 0.0000 | +0.00pp  |
| cases | 60   |          |

<details><summary>Per-contract breakdown</summary>
 … table …
</details>
```

On failure the header flips to `❌ Regression gate failed` with the
specific gate reasons listed above the table.

## Design notes

* Composite action (no Docker, no JS bundle) — you can `vendor` it by
  copying `action.yml` into `.github/actions/` in your own repo if you
  don't want to depend on a marketplace publish cadence.
* The PR comment is *sticky*: each new run edits the same comment
  rather than appending, so the PR page doesn't accumulate a wall of
  historical eval results.
* The gate step is placed **last** so failure reasons (PR comment,
  artifact upload, step log) are always emitted before the action
  returns non-zero. A silent red X with no explanation is a worse DX
  than a noisy one.
* `write-baseline` mutates the tree but does not commit — the caller
  decides whether/how to push. This keeps the action token-scope
  minimal for the common PR case.
