---
title: Contract library
description: Pre-built contract packs that ship with Sponsio — what each pack covers, when to include it, and how to override individual rules.
---

# Contract library

Sponsio ships a set of pre-built contract **packs** under [`sponsio/contracts/`](../../sponsio/contracts/). Each pack is a YAML file that declares a reusable block of contracts against a placeholder agent id (`"*"`). Users `include:` a pack from their own `sponsio.yaml`; the config loader substitutes the real agent id, applies workspace + tool renames, and merges the pulled contracts with any rules the user wrote by hand.

> **Two "libraries", two meanings.** The [pattern catalog](patterns.md) is the set of *Python factories* you call to build an LTL formula (`must_precede`, `scope_limit`, …). **This page** is about the *YAML packs* that already wire those patterns into ready-to-use contract bundles.

## The packs

| Spec | Tier | Contracts | When to include |
|---|---|---|---|
| `sponsio:core/universal` | 0 | 5 (sto) | Any LLM agent. Injection, jailbreak, toxic, harmful, semantic-PII response checks. |
| `sponsio:core/runaway` | 0 | 5 (det) | Any agent with token usage, sub-agent delegation, or tool loops. Token budgets, delegation depth, loop caps. |
| `sponsio:capability/filesystem` | 1 | 13 (det) | Agent exposes `read` / `write` / `edit` / `apply_patch`. Sensitive-path denies, workspace scoping, self-modification gate. |
| `sponsio:capability/shell` | 1 | 11 (det) | Agent exposes `exec` / `bash`. `rm -rf /`, fork bomb, curl\|bash, reverse shell, confirmation gates. |
| `sponsio:incident/openclaw` | 2 | 45 (mixed) | Reference pack mirroring real 2026 OpenClaw incidents (CVE-2026-25253, ClawHavoc, weather-skill). Mostly a worked example — pick individual rules from it. |

Tier 0 is the default-on baseline. Tier 1 is capability-indexed — include it when your agent exposes that capability. Tier 2 is scenario-specific.

## Using a pack

```yaml
# sponsio.yaml
agents:
  my_bot:
    workspace: "/srv/my-bot"               # substituted for <workspace>/
    include:
      - sponsio:core/runaway
      - sponsio:core/universal
      - sponsio:capability/filesystem
    contracts:                             # your own rules, added on top
      - desc: "no commits after reading .env"
        A: { pattern: called, args: [read, ".env"] }
        E: { ltl: "G(!called(git_commit) & !called(git_push))" }
```

At load time the config loader:

1. Resolves `sponsio:...` specs to the bundled YAML path.
2. Substitutes `"*"` → `my_bot`, `<workspace>/` → `/srv/my-bot/`.
3. Applies any `tool_rename:` you declared (e.g. `exec: bash` for agents whose shell tool has a different name).
4. Stamps each pulled contract with `pack_source: sponsio:core/runaway` so `overrides:` can address them.
5. Appends your hand-written `contracts:` last.

## Overriding a pack rule

Don't fork the pack. Use `overrides:` to disable or retune a specific rule:

```yaml
agents:
  my_bot:
    include:
      - sponsio:core/runaway
    overrides:
      - match: { desc: "Session total tokens must not exceed 200k" }
        disabled: true
      - match: { pack_source: "sponsio:capability/shell" }
        disabled: true                     # kill-switch the whole pack
      - match: { pattern: injection_free }
        threshold: 0.85                    # loosen beta on one sto rule
```

Supported `match:` keys: `desc`, `pack_source`, `source` (the `library:tier*` tag in each rule), `pattern`. Supported effects: `disabled`, `threshold`, `prompt_override`, `context_scope`. An override that matches nothing is surfaced by `sponsio validate` — no silent misses.

## Renaming tools

Packs ship with canonical tool names: `read`, `write`, `edit`, `apply_patch`, `exec`, `send_message`. If your agent calls them something else, map them at include time:

```yaml
agents:
  cursor_bot:
    tool_rename:
      read: read_file
      edit: edit_file
      exec: run_terminal
    include:
      - sponsio:capability/filesystem
      - sponsio:capability/shell
```

The rename rewrites every `args:` tool reference and every `ltl:` string inside the pulled contracts.

## Markers packs assume

Some pack rules reference *marker* tool names that your integration must emit:

| Marker | Emitted by | Used in |
|---|---|---|
| `confirm_reconfirmed` | integration, on human approval | `capability/shell` §3 §4, `capability/filesystem` §4 |
| `audit_log` | integration, on audit-log write | `incident/openclaw` |

Until a marker is ever emitted, any rule gated on it is either vacuously true (for `A:`-conditional rules) or fails closed. Check each pack's trailing "Integration requirements" section before enabling enforce mode.

## Pack-by-pack reference

### `sponsio:core/universal` — 5 sto contracts

Response-scoped checks for any LLM agent. No `tools:` section — these evaluate against `llm_response` events, not tool calls.

| Rule (β default) | Pattern |
|---|---|
| Response free of prompt-injection compliance (β=0.95) | `injection_free` (full_trace) |
| No jailbreak compliance — DAN, roleplay, hypothetical bypass (β=0.90) | `jailbreak_free` |
| Must not encourage or enable real-world harm (β=0.95) | `harmful` |
| Free of toxic / harassing language (β=0.90) | `toxic_free` |
| Free of contextual PII — names tied to conditions, inferable identity (β=0.95) | `semantic_pii_free` |

`scope_respect` is deliberately **not** shipped on by default — it needs an agent-specific scope string. See the YAML header comment for a template.

### `sponsio:core/runaway` — 5 det contracts

"While(true) with a credit card" defense. All trace-level; per-request guardrails (NeMo, LlamaFirewall, Guardrails AI) can't express these.

| Rule | Pattern |
|---|---|
| Session total tokens ≤ 200k | `token_budget` |
| Session input tokens ≤ 150k | `token_budget` |
| Session output tokens ≤ 50k | `token_budget` |
| Agent-to-agent delegation depth ≤ 5 | `delegation_depth_limit` |
| No tool may repeat >10 times consecutively | raw LTL on `consecutive_count` |

### `sponsio:capability/filesystem` — 13 det contracts

Split into five sections inside the YAML: (§1) sensitive-path hard denies on `read`, (§2) write/edit/apply_patch denies, (§3) workspace scoping, (§4) read → edit ordering, (§5) bootstrap-file self-modification gate (`AGENTS.md`, `SOUL.md`, `CLAUDE.md`, `.cursorrules`).

Backed by real incidents: OpenClaw weather-skill `.env` exfil, AMOS stealer, Cursor `.cursorignore` bypass, Claude Code Issue #10077 recursive-delete.

### `sponsio:capability/shell` — 11 det contracts

Four sections: (§1) destructive-command blacklist — `rm -rf /`, fork bomb, `curl | bash`, reverse shells, line-continuation evasion, undefined-variable expansion; (§2) blast-radius limits — command length cap, 50 execs/session, 5 consecutive; (§3) privileged commands require `confirm_reconfirmed`; (§4) 1:1 confirmation-to-exec ratio.

Backed by: Claude Code Issue #10077 / #49464, Replit prod-DB wipe (Jul 2025), Ansible `rm -rf {foo}/{bar}` on 1,535 servers, OpenClaw CVE-2026-28460.

### `sponsio:incident/openclaw` — 45 mixed contracts

Full contract set against the OpenClaw local gateway, covering every documented 2026 incident: CVE-2026-25253 (WebSocket RCE), CVE-2026-22708 (indirect prompt injection), CVE-2026-29607 (approval wrapper bypass), CVE-2026-28460 (line-continuation bypass), CVE-2026-32048 (sandbox inheritance), issue-12515 (`--yolo` flag), ClawHavoc (1,184 malicious skills), weather-skill (`.env` exfil), wired-guilttrip (emotional manipulation).

Mostly a worked example of tier-0 + tier-1 + incident tags composed together. Fork individual rules into your own config rather than including the whole pack.

## Inspecting / validating

```bash
sponsio validate sponsio.yaml   # resolves every include, reports unmatched overrides, type-checks patterns
```

Source attribution is preserved through loading: every compiled contract knows its `pack_source` and `library:tier*` tag, which surfaces in `sponsio scan`, `sponsio report`, and the dashboard's Contract Library panel.

## See also

- [`sponsio.yaml` reference](config-yaml.md) — top-level schema, `include:` / `overrides:` / `tool_rename:` / `workspace:` mechanics.
- [Pattern catalog](patterns.md) — the Python factories each pack rule compiles into.
- *Sto atom catalog* (Sponsio Cloud) — the LLM-judged atoms used by `core/universal`.
- [Onboarding guide](../guides/onboarding.md) — `sponsio onboard` auto-selects tier-0 packs based on detected tools.
- *Benchmark contract libraries* (no longer shipped in OSS) — hand-curated libraries that drive Sponsio's published RedCode-Exec and ODCV-Bench headlines. Distinct from the capability packs above: benchmark-reproduction artefacts, not auto-included by `onboard`.
