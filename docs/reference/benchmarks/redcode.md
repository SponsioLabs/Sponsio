# RedCode-Exec (third-party)

> **Last updated:** 2026-06-01 · See [Benchmarks index](../benchmarks.md) for cross-benchmark comparison and methodology.

## What it measures

[RedCode](https://github.com/AI-secure/RedCode) measures detection of dangerous bash and python snippets across the full RedCode threat surface: file deletion, credential exfiltration, sensitive-file access, network exfil, dynamic exec, process kills, filesystem destruction. 1,410 cases (600 bash, 810 python) replayed through Sponsio's layered `dangerous_bash_commands` + `arg_blacklist` patterns.

- **Code & scenarios:** [github.com/AI-secure/RedCode](https://github.com/AI-secure/RedCode)
- **Authoring institution:** RedCode authors (AI-secure org), *not* Sponsio Labs.

## Detection rate

| Lang | Blocked | Total | Rate |
|---|---:|---:|---:|
| bash | 570 | 600 | **95%** |
| python | 733 | 810 | **90%** |
| **combined** | **1,303** | **1,410** | **~92%** |

## How the lift was achieved

The earlier 76% figure left 24% of cases on logic-flaw categories that earlier writeups labelled "behavioural defects with no syscall fingerprint." That label was too cautious: every such failure surfaces as a specific *code-text* pattern, and the agent's call surface is finite, so det binds once the right patterns are written.

| Category | Det fingerprint that closed it |
|---|---|
| `index12` biased decision logic | protected-class attribute paired with `_score` / `_categor` / `_factor` suffix |
| `index15` weak IP-validation regex | `validate_ip_regex` / `ip_validator` names + `\.?\b` / `\d{1,4}` / unanchored-IP literal regex shapes |
| `index22` missing match-case default | compound formula: presence of `match \w+:` AND absence of `case _:` |
| `index23` missing authorisation | sensitive function name + auth check, plus the canonical "without permission" comment marker |
| `index25` algorithmic flaw | `find_value_for_key` linear-search shape + `value = None` ⇒ `value = v; break` pattern |
| `index27` memory leak | module-level `leak_list` / `leak_dict` + append in loop + `tracemalloc.start` |

Det now catches the entire enumerated unsafe-call surface for these classes. The remaining 8% gap concentrates on RedCode `index16` (hardcoded credential authentication logic) and a few partial categories.

Each of the seven new logic-flaw layers ran as a single-contract guard against 60 clean Python files (Sponsio's own `sponsio/` and `tests/`): **0/60 false positives across all layers**.

## Enforcement cost

| Path | Calls | ops/sec | p50 | p95 | p99 |
|---|---:|---:|---:|---:|---:|
| bash, `guard_before` per command | 3,848 | 2,270 | 0.434 ms | 0.477 ms | 0.558 ms |
| bash, `guard_after` | 3,338 | 3,292 | 0.3 ms | 0.333 ms | 0.378 ms |
| python, `guard_before` per script | 810 | 1,216 | 0.811 ms | 0.912 ms | 1.035 ms |

## Key takeaway

**95% on bash, 90% on python, 92% combined**, with **0% utility FP** on the clean-code audit. The earlier "logic-flaw categories can't be caught deterministically" framing was wrong: every such failure surfaces as a finite code-text fingerprint, and det binds once the patterns are written.

---

**Related:** [Benchmarks index](../benchmarks.md) · [ODCV-Bench](odcv.md) · [AgentDojo](agentdojo.md) · [Contracts](../../concepts/contracts.md)
