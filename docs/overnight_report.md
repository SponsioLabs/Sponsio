# Overnight Report — fix/enforcement-and-evidence-mw

Branch: `fix/enforcement-and-evidence-mw` (off `main`). No push, no merge.
Baseline before any change: **2399 passed, 26 skipped**.

---

## Phase 1 — canonical stopping set + stop_original gating

Status: **complete, suite green (2407 passed, 26 skipped; +8 new tests).**
Commit: `fix(enforcement): canonical stopping set + stop_original gating`.

### What changed

* `integrations/base.py`: added the canonical predicate next to
  `CheckResult` — `STOPPING_ACTIONS = frozenset({"blocked", "redirected"})`
  and `is_stopping_action(action)`. `CheckResult.stop_original` now
  derives from it. This is the ONE definition; mcp and bridge consume it.
* Fixed every fail-open gating site (full list below).
* `bridge/spans.py` re-exports the canonical set (counting change: see 1b).

### DECISION THE INSTRUCTIONS DID NOT COVER (deliberate deviation)

The instructions said: *"mcp must stop on escalated (behavior change,
matching base)"*. **I did not make mcp stop on escalated**, because the
premise "matching base" is contradicted by the code:

* `CheckResult.stop_original` (base.py) documents *"``escalated`` is
  intentionally excluded"*, and `guard_before`'s long comment explains
  why: the monitor uses `EscalateToHuman()` as the **default strategy for
  unfired-assumption verdicts**, so `escalated` results are routinely
  vacuous (`monitor.py::_handle_assumption_failure`, strategy default at
  monitor.py:721). Base enforcement never stops on escalated.
* Making the MCP proxy stop on escalated would refuse **every** tool call
  while any conditional contract's assumption is simply not yet satisfied
  — a fleet-wide false-block regression through the proxy.

So the canonical set is `{blocked, redirected}` — which is also exactly
what mcp already stopped on, meaning mcp's behavior is unchanged and now
single-sourced. If you want escalated to stop calls, the right fix is
upstream (stop using EscalateToHuman as the vacuous-assumption default),
not in the stopping set. Flagging for your morning review.

Related observation: the semantics table in `runtime/strategies.py`
(EnforcementResult docstring) claims `escalated → tool runs? no`, which
disagrees with actual base enforcement. Not touched (docs-only conflict,
out of scope tonight), but it is the third place this ambiguity lives.

### 1a. Call-site inventory (every CheckResult gating site + disposition)

| Site | Before | Disposition |
|---|---|---|
| openai.py `check_response` (~:257) | `check.blocked and self.on_violation` | **Fixed** → `stop_original` (callback fires on redirect refusals too) |
| openai.py `_filter_blocked_calls` (~:401) | `results[tc_idx].blocked` | **Fixed** → `stop_original` (redirected tool_calls stripped too) |
| openai.py `patched_create` / `patched_async_create` (~:505/:516) | `any(r.blocked …)` | **Fixed** → `any(r.stop_original …)` |
| google_adk.py sync `guarded_sync` (~:112) | `check.blocked` | **Fixed** → `stop_original` (now matches async twin) |
| google_adk.py async `guarded_async` (~:97) | `stop_original` | Already compliant — unchanged |
| langgraph.py `_guard_check` (~:178, currently uncalled) | `check.blocked` | **Fixed** → `stop_original` + comment |
| langgraph.py `guarded_func` / `guarded_coro` (~:243/:265) | redirect-substitution branch, then `check.blocked` | **Fixed** final gate → `stop_original` (substitution branch kept first; also fail-closes a redirect verdict with no usable target) |
| langgraph.py `_invoke_safe_tool` (~:355) | `check.blocked`, then explicit chained-redirect raise | **Justified as-is** (both stopping actions covered explicitly); comment added |
| langgraph.py callback `on_tool_start` (~:422) | `result.blocked and self._block` | **Fixed** → `stop_original` |
| langgraph.py `_MonitoredGraph._check_node` (~:730) | `not result.blocked` | **Fixed** → `not result.stop_original` (note: callers still discard the bool — pre-existing, out of scope tonight) |
| crewai.py :108/:193 | `stop_original` | Already compliant — unchanged |
| vercel_ai.py :125 | `stop_original` | Already compliant — unchanged |
| claude_agent.py :119 | `stop_original` | Already compliant — unchanged |
| agents.py :137/:162 | `stop_original` | Already compliant — unchanged |
| base.py `guard_before` rollback (~:1314) | `blocked or (mode != observe and redirected)` | **Justified as-is** + comment: observe-mode redirects must keep their trace event (shadow-mode semantics), so `stop_original` would be wrong here |
| guard_stdin.py verdict gate (~:672) | `if result.allowed:` | **Fixed** → `if not result.stop_original:` (redirect now denies); deny-reason collector widened from `action == "blocked"` to `is_stopping_action` |
| demos/replay.py :166 | `result.blocked` | **Fixed** → `stop_original` (display-only replay, aligned for consistency) |
| mcp.py `call_tool` (~:149) | inline `("blocked", "redirected")` | **Fixed** → consumes `is_stopping_action` (same membership, now single-sourced; escalated still non-stopping — see decision above) |
| bridge/spans.py `STOPPING_ACTIONS` | local `("blocked","escalated","redirected")` | **Fixed** → re-export of canonical set |
| Non-CheckResult `.blocked` readers (reporting/, render/, eval_runner, otel, models/spans, monitor span attr) | — | Not gating sites (report counters / span attrs on other types); untouched |

### 1b. Stopping-set consumers

One definition in base.py; mcp gates through `is_stopping_action`;
bridge re-exports `STOPPING_ACTIONS`. **Bridge counting change:** steps
with an `escalated` enforcement action are no longer counted in
`detBlocks` / step `blocked` booleans — previously telemetry showed
"stopped" for calls that actually ran.

### Tests added (tests/test_stopping_set.py, 8)

Canonical membership; `stop_original` ≡ canonical for all 7 verdict
values; bridge consumes the same frozenset object; mcp agrees for all 7
values (proxy-level, mocked monitor); redirected-stops regressions for
openai (tool_call stripped + callback fires), google_adk sync (original
never executes), langgraph `_guard_check` (raises `ToolCallBlocked`),
guard_stdin (deny with redirect reason).

Ruff note: ruff auto-deleted the mid-file canonical import in spans.py as
unused (it is a re-export for session.py); reinstated at the top with
`# noqa: F401` and a comment.

---

*(Phase 2 section appended when reached.)*
