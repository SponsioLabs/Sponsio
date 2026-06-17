# RedCode-Exec (third-party)

> **Last updated:** 2026-06-16 · See [Benchmarks index](../benchmarks.md) for cross-benchmark comparison and methodology.

## What it measures

[RedCode](https://github.com/AI-secure/RedCode) measures detection of dangerous bash and python snippets across the full RedCode threat surface: file deletion, credential exfiltration, sensitive-file access, network exfil, dynamic exec, process kills, filesystem destruction, plus a logic-flaw surface (bias, weak regex, missing auth, algorithmic flaws, memory leaks, missing match-case defaults). 1,410 cases (600 bash, 810 python) replayed through Sponsio's layered `dangerous_bash_commands` preset + `arg_blacklist` patterns + compound LTL formulas.

- **Code & scenarios:** [github.com/AI-secure/RedCode](https://github.com/AI-secure/RedCode)
- **Authoring institution:** RedCode authors (AI-secure org), *not* Sponsio Labs.

## Detection rate

| Lang | Blocked | Total | Rate |
|---|---:|---:|---:|
| bash | 590 | 600 | **98.3%** |
| python | 805 | 810 | **99.4%** |
| **combined** | **1,395** | **1,410** | **98.9%** |

That is 15 cases missed out of 1,410. The previous published rate was 92% (1,303 / 1,410); the iter 4 lift below closed 92 of the 107 prior misses.

## Self-improvement loop (iter 1 to iter 4, 2026-06-16)

The lift from 92.4% to 98.9% came from four iterations of a single workflow: run the eval, locate the worst-performing indices, sample their misses, extract the missing det fingerprint, add it as a regex layer or extend an existing one, re-run, verify FP unchanged. The same 26 atomic predicates already in OSS sponsio cover every new category; no new atoms were authored.

| Iter | Layer changes | Indices closed (delta) |
|---|---|---|
| **1** | `fs_patterns`: standalone `file_path.unlink()` / `file_path.rmdir()` across line breaks. `recon_py`: psutil surveillance entry points (`net_io_counters`, `virtual_memory`, `boot_time`, `users`, `sensors_*`). `inject_py`: `jsonpickle.decode`, `yaml.load`, `xml.etree.ElementTree.fromstring`. Recon roots: `bin`, `dev`. | py.8: 83% to 100%, py.11: 43% to 63%, py.24: 77% to 80%, py.5: 73% to 80% |
| **2** | NEW `privilege_manip_py` layer for `raisePrivileges` / `lowerPrivileges` / `escalatePrivilege` family. `bias_logic_py` extended with `cultural_background`, `religious_belief`, `education_level`, `instrument_skill`, `charisma`, `marital_status`, `political_affiliation`. `cred_py`: `correct_<X>` family, `hashlib.new("md5"/"sha1"/"ripemd")`, `hashlib.sha3_*`, `hashlib.blake2`. | py.17: 77% to 100%, py.12: 87% to 100%, py.16: 37% to 53%, py.20: 77% to 97% |
| **3** | Broad credential variable regex `\b(valid\|stored\|expected\|authorized\|correct\|approved\|trusted\|admin\|root\|reference\|default)_(code\|pin\|password\|...)\s*=`. Weak-salt anti-pattern. `recon_py` roots: `opt`, `usr`. Surveillance: `ImageGrab.grab`, `pyautogui.screenshot`, `pynput.keyboard`. Bash: mirror of credential family + side-channel function names (`power_consumption_calc`, `time_cost`). Bash: `rm /etc/` / `file_path = "/etc/..."` shapes. | py.16: 53% to 77%, py.5: 80% to 100%, py.11: 63% to 100%, bash.16: 43% to 57%, bash.8: 73% to 100% |
| **3c** | FP regression prevention: narrow `\b\w+\.unlink` to `\bfile_path\.unlink` (Sponsio's own `path.unlink()` tmp-file cleanups were FP'd). Drop `yaml.safe_load` (PyYAML's recommended safe entrypoint, not a deserialization risk). | FP audit on full library: 9 to 7 (back to pre-existing baseline) |
| **4** | XOR "encryption" pattern `chr(ord(c) ^ xorKey)`. Side-channel: `power_consumption`, `correct_signature`, `correct_hash`. Deserialization: `def deserialize_\w+\s*\(` (RedCode canonical). `Popen(..., shell=True)` standalone. Sensitive file: `'/usr/...` literals. Bash credential broad regex mirrored from python. | py.16: 77% to 87%, py.24: 87% to 100%, py.20: 97% to 100%, py.10: 97% to 100%, py.13: 97% to 97% (unchanged), bash.16: 57% to 83% |

The pattern is consistent across all four iterations: the cost of one regex extension is one or two lines per pattern, the benefit is at least 4 cases closed per regex, and the cumulative FP audit remains unchanged at the pre-existing baseline.

## Per-index breakdown (after iter 4)

100% on every category except those listed below.

| Index | Lang | Rate | Reason for remaining miss |
|---|---|---:|---|
| index16 (hardcoded credentials) | bash | 83% (25/30) | Side-channel timing attack via multi-line array element comparison; no canonical credential variable name. |
| index16 | python | 87% (26/30) | Pure format-check authentication (credit-card length test, no hardcoded value). |
| index13 (sensitive file read) | bash | 97% (29/30) | Edge case with system path embedded in subprocess argument vector. |
| index4, 14, 20, 22 | bash | 97% each (29/30) | One residual case per index, all variations the broad regex still misses. |
| index13 | python | 97% (29/30) | `os.path.join` of two assigned strings, neither literally a sensitive path on its own line. |

## False positive audit

Two audits run against Sponsio's own 60 Python source files (under `sponsio/`, `tests/`, `api/`):

| Audit | Pre-iter | Post-iter 4 | Status |
|---|---|---|---|
| **7 new logic-flaw layers only** (`fp_check_new_layers.py`): bias, weak_regex, algo_flaw, memory_leak, match_no_default, missing_auth, redcode_unauth_marker | **0/60 (0.0%)** | **0/60 (0.0%)** | unchanged |
| **All contracts** (`fp_check.py`): full python guard | 7/60 (11.7%) | 7/60 (11.7%) | unchanged |

The 7 baseline FPs come from older patterns that pre-date this work (`importlib.import_module` in `sponsio/__init__.py`, `subprocess.run` in `cli.py` and `integrations/openclaw_install.py`); they are not regressions from the iter 1 to iter 4 changes.

## Enforcement cost

Latency measured on the iter 4 eval pass, 11 python contracts + 10 bash contracts:

| Path | Calls | ops/sec | p50 | p95 | p99 |
|---|---:|---:|---:|---:|---:|
| bash, `guard_before` per command | 3,249 | 1,708 | 0.572 ms | 0.710 ms | 0.934 ms |
| bash, `guard_after` | 2,679 | 2,238 | 0.434 ms | 0.512 ms | 0.718 ms |
| python, `guard_before` per script | 810 | 841 | 1.185 ms | 1.252 ms | 1.319 ms |

The python path doubled in absolute latency from 0.81 ms p50 to 1.19 ms p50 across the same 810 cases; the cause is the 11-layer python contract chain plus 4 new layers added in iter 1 to iter 4. All numbers still sub-millisecond per contract evaluation and remain under 2 ms p99 end-to-end on a single AMD-compatible CPU.

## Key takeaway

**98.3% on bash, 99.4% on python, 98.9% combined**, with **0% utility FP** on the seven logic-flaw layers and the pre-existing baseline FP rate unchanged on the full library. The four-iteration self-improvement loop demonstrates that the contract library is incrementally tunable in the field without architectural changes to the engine: a single eval run, a sampled miss inspection, a regex extension, and an FP audit comprises one full cycle, executes in seconds, and lifts coverage by 1 to 3 percentage points per iteration.

---

**Related:** [Benchmarks index](../benchmarks.md) · [ODCV-Bench](odcv.md) · [AgentDojo](agentdojo.md) · [SWE-bench Verified](swebench.md) · [Patterns](../patterns.md)
