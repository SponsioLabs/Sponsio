# sponsio-shield trace persistence

How `sponsio shield guard` keeps trace state across the subprocess
boundary that Claude Code's hook protocol forces on us.  Internal
notes — covers design decisions, the session boundary, and the
trade-offs we accepted for the file-based prototype.

## Context — why this is non-trivial

In-process integrations (`sponsio.langgraph`, `sponsio.claude_agent`,
`sponsio.crewai`, …) keep a single `BaseGuard` instance alive for the
agent's whole lifetime.  Trace events accumulate in
`BaseGuard._monitor.trace.events`; the DFA evaluator runs over the
full history on every `guard_before` call.  Trace-aware contracts
(`must_precede`, `cooldown`, A/G temporal) Just Work.

Claude Code (and OpenClaw, and Cline, and any other plugin host that
invokes hooks via `stdin`) is fundamentally different:

```
Claude Code's hook protocol (per PreToolUse):
  fork → exec("sponsio shield guard --stdin")
  child reads JSON event over stdin
  child evaluates one event
  child writes deny payload to stdout (or empty for allow)
  child exits — its memory is reaped
```

Each tool call is a fresh process.  `BaseGuard()` constructed inside
that process starts with an empty trace.  The DFA is fine; it just
never sees more than one event per call.  Result: trace-aware
contracts are silently vacuous on the shield path even though the
in-process path enforces them correctly.

The old `guard_stdin.py` docstring (pre-2026-04-27) acknowledged
this gap:

>   Trace continuity across calls is not yet implemented — every
>   invocation gets a fresh empty trace, so trace-aware contracts
>   (must_precede, rate_limit, cooldown) are silent in this prototype.

## What we ship today (file-based session log)

Every per-plugin library directory now also holds an append-only
JSONL trace log:

```
~/.sponsio/plugins/<plugin>/sponsio.yaml          # contracts
~/.sponsio/plugins/<plugin>/.shield-trace.jsonl   # session events  ← new
```

`evaluate_event()` in `sponsio/guard_stdin.py`:

1. **Load.**  Read every prior line of the JSONL, reconstruct an
   `Event` per line, build a `Trace`.
2. **Inject.**  Construct `BaseGuard(...)` as before, then call
   `guard._monitor.import_trace(prior_trace)`.  This replaces the
   monitor's empty trace with the reconstructed one and resets the
   verifier's grounded-up-to pointer, so the next `check_action`
   re-grounds from index 0 — DFA sees the full history.
3. **Evaluate.**  `guard.guard_before(tool_name, tool_input)` runs
   exactly as in the in-process path.  The new event is appended to
   `monitor.trace.events` (and rolled back internally if blocked).
4. **Persist.**  If the call was *allowed*, append the new event to
   the JSONL using the `ts` BaseGuard actually assigned (read back
   from `monitor.trace.events[-1].ts`, not recomputed locally — see
   "ts collisions" below).  Blocked calls are not persisted because
   the action never ran.

The DFA logic is untouched.  We only thread state across the
process boundary.

## What counts as one "session"

There is no Claude-Code-side session token we can correlate against,
so the shield approximates session boundaries from inactivity.

| Boundary mechanism | Default | Override |
|---|---|---|
| Stale-trace TTL | 24h since last write | `SPONSIO_SHIELD_TRACE_TTL_HOURS` |
| Plugin scope | one trace per plugin id (`_host`, `acme`, `mcp__github__*` → `github`, …) | n/a |
| Manual reset | `rm <plugin>/.shield-trace.jsonl` | always |

When a `PreToolUse` arrives and the trace file's `mtime` is past the
TTL, the file is unlinked before load.  The current call then
behaves as the first call of a fresh session.

This is a **rough** session model.  It deliberately accepts some
slop — see "Known limitations" — because the alternative (correlate
to Claude Code's actual session id) requires a daemon and a richer
hook protocol that isn't available today.

## Trace continuity properties

- **One JSONL line per allowed event** — denied events are never
  persisted, so a downstream re-grounding never fires R(t) on an
  action that didn't actually happen.
- **Append-only with single-write atomicity per line** — concurrent
  hooks won't tear individual records.  Order across concurrent
  hooks is not defined; Claude Code's hook protocol is sequential
  per-session today, so this is a non-issue in practice.
- **`ts` is a logical clock, not wallclock** — assigned by
  `RuntimeMonitor.check_action` as `len(trace.events)` at the
  moment of append.  We persist that value verbatim so the next
  subprocess starts in a numerically consistent state.
- **Plugin isolation** — each plugin id gets its own log.  A
  malicious plugin cannot poison `_host`'s trace because the
  subprocess routes by `derive_plugin_id(tool_name)` *before*
  loading any state.

## ts collisions — the load-bearing detail

`check_action` assigns `ts = len(self._trace.events)` when it builds
the new Event.  If the shield reuses ts values, the verifier's
incremental atom cache (keyed by `(formula_id, position)`) silently
returns stale answers.

The fix is mundane but crucial: when we persist the new event, read
the ts that the monitor *actually assigned* — don't recompute from
the prior-events count.  Concretely:

```python
# guard_stdin.py — after a successful guard_before
last_event = guard._monitor.trace.events[-1]
_append_event(plugin_id, {
    "ts": last_event.ts,            # ← ground-truth, not len(prior_events)+1
    "agent": last_event.agent,
    "type": last_event.event_type,
    "tool": last_event.tool,
    "args": last_event.args,
})
```

Why this matters: imagine call N appends `ts=5` to the log.  If
call N+1 looks up `len(prior_events) + 1 = 6` to write, but the
*next* `check_action` assigns `ts = len(trace.events) = 6` to the
NEW event being checked, both events end up with ts=6 — collision.
Reading from the monitor avoids the off-by-one entirely and stays
consistent regardless of how `check_action` decides to number ts
in the future.

## Path layout (new in 2026-04-27)

```
~/.sponsio/plugins/_host/
├── sponsio.yaml             # contract library (existing)
└── .shield-trace.jsonl      # append-only session log (new)

~/.sponsio/plugins/github/
├── sponsio.yaml
└── .shield-trace.jsonl

~/.sponsio/plugins/<mcp-server>/
├── sponsio.yaml
└── .shield-trace.jsonl
```

`SPONSIO_SHIELD_TRACE_ROOT` overrides the trace root entirely — useful
when libraries live on a read-only mount.  Without it, traces co-locate
with libraries so `SPONSIO_PLUGIN_ROOT` isolation is automatically
inherited (tests don't need to remember a second env var).

## Known limitations

1. **TTL approximates "session" with inactivity.**  A user who
   resumes work after 25h gets a fresh trace, even if it's the
   "same" mental session.  Conversely, two separate work sessions
   spaced an hour apart share one trace.  This is wrong in both
   directions but bounded — for trace contracts that look at
   "secret-emitting tool followed by exfil within session" the
   default of 24h is well over any reasonable single-session
   horizon.

2. **No subprocess can see another's in-flight state.**  If two
   PreToolUse hooks fire concurrently (Claude Code currently
   doesn't, but some plugin hosts might), each loads the trace at
   slightly different states.  Acceptable today; a daemon mode
   would replace this with shared in-memory state.

3. **The trace file size grows linearly with session length.**  A
   pathological agent making 10⁵ tool calls in 24h writes ~10MB.
   Each subsequent hook re-reads the whole file (cold start cost
   scales with N).  In practice, hook throughput stays well under
   the parsing budget — but a daemon mode would address this with
   incremental loads.

4. **`tool_rename:` substring-substitutes regex literals.**  This
   isn't a trace bug per se but it's the load-bearing footgun for
   anyone authoring trace contracts: if your raw LTL regex contains
   the canonical name (e.g. `aws-vault exec` while a sibling rule
   has `tool_rename: { exec: Bash }`), the rename will silently
   rewrite your regex to `aws-vault Bash`.  Workarounds: use
   `arg_field_has(...)` instead of `called_with(...)`, or avoid the
   canonical word in regex literals.  See
   `sponsio/contracts/incident/claude-code-secret-bypass.yaml` for
   a worked example using `arg_field_has`.

## Future direction — daemon mode

A long-running `sponsio shield daemon` process holding state in
memory, with hooks talking to it over a unix socket, would replace
the file-based mechanism while keeping the same per-plugin routing
and contract semantics.  Estimated work: ~1–2 days for the daemon
+ socket protocol; the file-based path becomes the cold-start /
fallback path.  Not blocked on anything — it's a perf optimisation,
not a correctness one.
