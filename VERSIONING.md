# Versioning Policy

> Contract between Sponsio and its users: what the version number *promises*,
> and how we choose the next one.

Sponsio follows **[SemVer 2.0](https://semver.org/)** — `MAJOR.MINOR.PATCH`,
with PEP 440-compliant pre-release suffixes for unstable work.

---

## 1. The version number, piece by piece

```
 0 . 1 . 0   a 1
 │   │   │   │ │
 │   │   │   │ └── serial number within that pre-release channel
 │   │   │   └──── pre-release channel: a (alpha), b (beta), rc (release candidate)
 │   │   └──────── PATCH:  backwards-compatible bug fix
 │   └──────────── MINOR:  backwards-compatible feature
 └──────────────── MAJOR:  breaking change
```

Never invent your own scheme. `0.1.0-alpha`, `0.1.0.a1`, `0.1.0-rc.1` are all
invalid on PyPI — use `0.1.0a1`, `0.1.0rc1` (no dot, no dash).

---

## 2. Decision tree — what should the next version be?

Answer top-to-bottom. The **first** yes wins.

```
Are you in the pre-1.0 / alpha phase (current situation, through 2026-Q2)?
├── Yes → see §3 below
└── No ↓

Does this release break an existing user's working code
(removed API, changed signature, YAML schema change, renamed CLI flag)?
├── Yes  → bump MAJOR:   1.4.2 → 2.0.0     (and write a Migration Guide)
└── No ↓

Does this release add new public API, new CLI subcommand, new pattern, new
integration, or otherwise let a user do something they couldn't before?
├── Yes  → bump MINOR:   1.4.2 → 1.5.0
└── No ↓

Bug fix, doc fix, internal refactor, test-only change?
└── bump PATCH:          1.4.2 → 1.4.3
```

> **Rule of thumb**: if a user's README example from last release stops
> working, that's a MAJOR. If they don't notice, it's MINOR or PATCH.

---

## 3. Pre-1.0 / alpha strategy (now → launch)

We're in the "we're still figuring out the contract DSL and the API surface"
window. SemVer allows breaking changes at any `0.x` boundary, but we will be
more disciplined than that:

| Channel | Meaning | Pip default picks it? | Use for |
|---------|---------|------------------------|---------|
| `0.1.0a0`, `0.1.0a1`, … | **Alpha** — expect breakage, minimal testing | ❌ No (needs `--pre`) | Daily / per-bugfix releases during launch prep |
| `0.1.0rc1`, `0.1.0rc2`  | **Release candidate** — feature-frozen, bug-fix only | ❌ No (needs `--pre`) | Last 24h before launch, once nothing is changing |
| `0.1.0`                 | **Stable** — the launch version | ✅ Yes | Wednesday Apr 22 launch day |
| `0.1.1`, `0.1.2`        | **Patch** — bug fixes, no API change | ✅ Yes | Any time after launch |
| `0.2.0`                 | **Minor** — new patterns / integrations / CLI | ✅ Yes | When a coherent feature chunk ships |

**Concrete plan for the next 4 days:**

| When | Version | Why |
|------|---------|-----|
| Sat Apr 18 (today) | `0.1.0a0` | Claim the name on PyPI. Smoke-test the pipeline. |
| Sun Apr 19 | `0.1.0a1` | Bug fixes as they land (no silent parse failure, API auth, importlib patch) |
| Mon Apr 20 | `0.1.0a2` | Another batch of fixes + README GIF ready |
| Tue Apr 21 | `0.1.0rc1` | **Feature freeze.** From here, only critical bug fixes. |
| Wed Apr 22 | `0.1.0` | 🚀 Launch. `pip install sponsio` works. |
| Post-launch | `0.1.1`, `0.1.2`… | Any follow-up fixes |

---

## 4. What counts as a breaking change?

**Always MAJOR (post-1.0) or explicitly documented (pre-1.0):**

- Removing or renaming a public function / class / method
  (`sponsio.init`, `BaseGuard.*`, everything in `sponsio/patterns/library.py`)
- Changing a positional argument's meaning or adding a required positional argument
- Changing a YAML schema key (`assumptions: → contracts:` was one of these; see CHANGELOG 2026-04-15)
- Changing the JSONL shape written by `SessionLogger` (external tools consume this)
- Changing a CLI subcommand or flag name (`sponsio check` → `sponsio verify` would be a break)
- Changing the default behavior of `sponsio.init(mode=...)` (enforce → observe or vice versa)
- Changing what a built-in pattern name like `must_precede` means semantically

**NOT breaking (safe in MINOR/PATCH):**

- Adding a new optional keyword argument with a safe default
- Adding a new CLI subcommand or flag
- Adding a new pattern to the library
- Adding a new integration adapter
- Changing an internal module (`sponsio.runtime._internal.*`, underscore-prefixed)
  — but the moment someone imports it in a user guide, it's effectively public

**Ambiguous — err toward MAJOR:**

- Changing a default value (e.g. default verbosity 1 → 2). Decide case by case; if
  a reasonable user could have their output-parsing break, it's a MAJOR.
- Changing an error message's exact text. Usually safe, unless tests/docs pin on it.

---

## 5. CHANGELOG coupling

Every release ticks the following, **in one commit** (`release: vX.Y.Z`):

1. `pyproject.toml`: `version = "X.Y.Z"` updated
2. `CHANGELOG.md`: `[Unreleased]` section moved to `[X.Y.Z] — 2026-MM-DD`; new empty `[Unreleased]` on top
3. `README.md`: the test-count badge, if we ship one, matches reality
4. Git tag `vX.Y.Z` on that commit

CI should fail if `pyproject.toml` version does not match `git tag` (`publish.yml`
can grow a check for this — add it post-launch).

---

## 6. Yank vs new release — when to use which

| Situation | Action |
|-----------|--------|
| Released version is broken but not actively harmful (e.g. one import fails) | **Bump + release fix.** `0.1.1` → `0.1.2`. Do not yank. |
| Released version breaks ALL users on import (e.g. `pyproject.toml` syntax error) | **Yank + release fix.** |
| Released version exposes a security issue | **Yank + release fix + GitHub Security Advisory.** |
| Released version was published by mistake (wrong branch, secret leak) | **Yank immediately.** Delete only if leaked credentials can't be rotated. |

PyPI allows **yanking** but not **deleting** once a version has been downloaded
more than trivially. Yank is reversible; delete is not.

---

## 7. TypeScript SDK version sync

`ts-sdk/package.json` and `pyproject.toml` track the **same** SemVer line.
If Python is `0.2.0`, TS is also `0.2.0`. Two consequences:

- A pure-TS bug fix still bumps both (TS `0.2.1`, Python `0.2.1` — no content change).
  Gives users a single "which version am I on" mental model.
- If the TS SDK is behind on a feature, it simply doesn't implement it; version
  number is still the same. Document gaps in README "Integrations" table.

If this becomes too painful, split at v1.0 — but not before.

---

## 8. Long-term commitments

Once we ship `1.0`, the following are **promised stable** (breaking changes
require a `2.0`):

- `sponsio.init(...)` public kwargs and return type
- `BaseGuard` public methods (`wrap`, `guard_before`, `guard_after`, `finish_session`)
- Built-in pattern names and their semantics (listed in README Pattern Library table)
- YAML `contracts: [{A, E}]` schema
- `~/.sponsio/sessions/<agent_id>/*.jsonl` shape
- `sponsio <subcommand>` CLI surface (every command in README "Docs" section)

**Not** stable (can change in any minor):

- Anything in `sponsio._internal` or prefixed `_`
- The exact LTL-AST shape (`Formula`, `Atom` dataclasses) — use `parse_nl_unified` instead
- The span/trace internal format (but **not** the OTEL export, which is stable)

---

## 9. Quick reference card (pin to your monitor)

```
Bug fix only                        → PATCH    0.1.0 → 0.1.1
New feature, no break               → MINOR    0.1.0 → 0.2.0
Removes / renames anything public   → MAJOR    0.1.0 → 1.0.0 (or 2.0.0)
Daily WIP build                     → pre-rel  0.1.0a1 → 0.1.0a2
Feature freeze, launch -24h         → rc       0.1.0rc1
Launch day                          → stable   0.1.0
Wrong button pushed                 → yank, don't delete
```
