<!--
Thanks for the patch. A short, well-scoped PR is easier and faster
to review than a long one. If this change is large, consider splitting
it — one concern per PR.

See CONTRIBUTING.md for the full guide.
-->

## Summary

<!-- What changed, in 1–3 sentences. Focus on *why*, not *what*. -->

## Type of change

<!-- Keep the one(s) that apply, delete the rest. -->

- feat: new user-visible capability
- fix: bug fix
- refactor: no behavior change
- perf: performance improvement
- docs: documentation only
- test: adds or reworks tests only
- ci: CI / tooling only
- chore: repo hygiene

## Linked issues

<!-- e.g. Closes #123, Refs #456 -->

## Design notes

<!--
Optional. Call out anything a reviewer should know:
- Invariants touched or preserved
- Trade-offs considered and the path not taken
- Anything explicitly out of scope for this PR
-->

## Test plan

<!--
How did you verify this? Paste the commands you ran.
Example:
    pytest tests/test_shadow_mode.py -v
    pytest -k "not slow" -v
    python -m sponsio.cli check --config sponsio.yaml
-->

## Checklist

- [ ] Tests added or updated (or N/A — docs/CI only).
- [ ] `pytest -v` passes locally.
- [ ] `ruff check sponsio/ api/ tests/` is clean.
- [ ] `ruff format --check sponsio/ api/ tests/` is clean.
- [ ] `README.md` updated if a user-visible surface changed (new
      pattern, integration, CLI command, or public API).
- [ ] `CHANGELOG.md` `[Unreleased]` section updated if this is a
      user-visible change.
- [ ] No new external dependency added to `sponsio/` core (framework
      deps go in `[project.optional-dependencies]`).
- [ ] For new patterns: both `patterns/library.py` and
      `generation/nl_to_contract.py` updated.
- [ ] For new integrations: inherits `BaseGuard`; no duplicated
      pre/post-check logic.
