---
title: Integrations
description: Wire Sponsio into your agent framework.
---

# Integrations

Sponsio works with any agent framework in Python and TypeScript. Each integration intercepts tool calls at the framework's native hook point. All integrations share the same LTL engine and produce identical block / allow decisions. See `tests/cross_language/` for cross-language validation.

## At a glance

### Python

| Framework | Factory | Tool wrapping | Lines to add |
|---|---|---|---|
| LangGraph | `from sponsio.langgraph import Sponsio` | `guard.wrap(tools)` | 3 |
| Claude Agent SDK | `from sponsio.claude_agent import Sponsio` | `guard.hooks()` (zero wrapping) | 2 |
| OpenAI SDK | `from sponsio.openai import Sponsio` (or `patch_openai`) | automatic response checks | 2 |
| OpenAI Agents SDK | `from sponsio.agents import Sponsio` | `guard.wrap(tools)` | 3 |
| Vercel AI SDK | `from sponsio.vercel_ai import Sponsio` | `guard.wrap()` (middleware) | 2 |
| CrewAI | `from sponsio.crewai import Sponsio` | `guard.wrap(tools)` | 3 |
| Google ADK | `from sponsio.google_adk import Sponsio` | `guard.wrap(tools)` | 3 |
| MCP | `from sponsio.mcp import MCPContractProxy` | `proxy.call_tool()` | 3 |
| No framework | `sponsio.Sponsio(contracts=[...])` | `guard.guard_before()` / `guard_after()` | 3 |

### TypeScript (via Pyodide, same engine, no server)

| Framework | Import | Integration |
|---|---|---|
| Claude Agent SDK | `@sponsio/sdk/claude-agent` | `sponsioHooks(guard)` |
| Vercel AI SDK | `@sponsio/sdk/vercel-ai` | `sponsioMiddleware(guard)` |
| OpenAI SDK | `@sponsio/sdk/openai` | `wrapOpenAI(client, guard)` |
| OpenAI Agents SDK | `@sponsio/sdk/openai-agents` | `wrapAgent(agent, guard)` |
| LangChain.js | `@sponsio/sdk/langchain` | `wrapTools(tools, guard)` |
| Google ADK | `@sponsio/sdk/google-adk` | `wrapTools(tools, guard)` |

## Python example: LangGraph

```python
from langgraph.prebuilt import create_react_agent
from sponsio import contract
from sponsio.langgraph import Sponsio

guard = Sponsio(
    agent_id="my_bot",
    contracts=[
        contract("policy gate before refund")
            .assume("called `issue_refund`")
            .guarantees("must call `check_policy` before `issue_refund`"),
    ],
)

agent = create_react_agent(model, guard.wrap(tools))
result = agent.invoke({"messages": [("user", "process refund")]})
guard.print_summary()
```

For existing graphs, use `wrap_graph()`:

```python
guard = Sponsio(config="sponsio.yaml", agent_id="bot")
graph = build_my_graph()
graph = guard.wrap_graph(graph)
```

The pattern is the same for CrewAI, Google ADK, OpenAI Agents SDK, and Vercel AI SDK. Swap the import for the matching `sponsio.<framework>` namespace and call `guard.wrap(tools)`.

## TypeScript example: Claude Agent SDK

```typescript
import { ClaudeAgent } from "@anthropic-ai/claude-agent-sdk";
import { Sponsio } from "@sponsio/sdk";
import { sponsioHooks } from "@sponsio/sdk/claude-agent";

const guard = new Sponsio({
  agentId: "my_bot",
  contracts: ["must call `check_policy` before `issue_refund`"],
});

const agent = new ClaudeAgent({
  hooks: sponsioHooks(guard),
});
```

## Custom loop (no framework)

```python
import sponsio
from sponsio import contract

guard = sponsio.Sponsio(
    agent_id="my_agent",
    contracts=[
        contract("identity check before transfer")
            .assume("called `transfer_funds`")
            .guarantees("must call `verify_identity` before `transfer_funds`"),
    ],
)

while not done:
    tool_name, args = llm_decide_next_action()
    result = guard.guard_before(tool_name, args)
    if result.blocked:
        llm_messages.append(f"Action blocked: {result.det_violations[0].message}")
        continue
    output = execute_tool(tool_name, args)
    guard.guard_after(tool_name, output)
```

## Tool policy: default-deny + proactive filtering (v0.2)

Sponsio's `tool_policy` section lets you declare an allow-list once and have it surface either reactively (the AI tries a denied tool, gets blocked at call time) or proactively (the denied tool never reaches the AI's tool menu).

```yaml
tool_policy:
  default: deny             # allow (default) | deny
  approved: [search, read_file, list_dir]
  enforcement: reactive     # reactive (default) | proactive
```

Or inline:

```python
guard = sponsio.Sponsio(
    contracts=[...],
    tool_policy={"default": "deny", "approved": ["search"], "enforcement": "proactive"},
)
```

### What `proactive` does per adapter

The adapter matrix below reflects the real listing surface each framework exposes. Filtering is honest: where an adapter can drop tools before the agent sees them, it does; where it can't, the rule still fires reactively via `guard_before`.

| Adapter | `proactive` behavior |
|---|---|
| LangGraph, CrewAI, OpenAI Agents SDK, Google ADK | One-shot static filter in `guard.wrap(tools)`. Denied tools never get bound to the agent. Temporal rules (`must_precede`, `count_at_most`) still apply reactively at call time. |
| Claude Agent SDK | Hooks-based: the SDK owns the tool list. `enforcement: proactive` is a no-op here; reactive blocking via `guard.hooks()` is the supported path. |
| OpenAI SDK, Vercel AI SDK | Per-call by user: filter the `tools=[...]` array before each request with `guard.filter_tools([t.name for t in ALL_TOOLS])` (see custom-loop snippet below). |
| Custom loop (no framework) | Per-turn filter using `guard.filter_tools(...)` — see snippet. Catches everything including temporal rules. |
| MCP | `MCPContractProxy` already reactive-blocks at `call_tool`. Per-turn filtering of `list_tools` is on the roadmap. |

### Custom loop with per-turn proactive filtering

`guard.filter_tools(candidates)` returns the subset of candidate tool names whose call would not be blocked right now — it's pure (no events, logs, callbacks, or perf samples) and evaluates *all* contracts including temporal ones. Call it before each LLM turn:

```python
import sponsio

guard = sponsio.Sponsio(
    agent_id="my_agent",
    contracts=["must call `verify_identity` before `transfer_funds`"],
    tool_policy={"default": "deny", "approved": ["verify_identity", "transfer_funds"]},
)

ALL_TOOLS = [verify_identity_tool, transfer_funds_tool, debug_tool, ...]
ALL_NAMES = [t.name for t in ALL_TOOLS]

while not done:
    # Per-turn refresh: returns only tools legal under the current trace.
    legal_names = set(guard.filter_tools(ALL_NAMES))
    legal_tools = [t for t in ALL_TOOLS if t.name in legal_names]
    tool_name, args = llm_decide_next_action(messages, tools=legal_tools)
    result = guard.guard_before(tool_name, args)
    if result.blocked:
        messages.append(f"Action blocked: {result.det_violations[0].message}")
        continue
    output = execute_tool(tool_name, args)
    guard.guard_after(tool_name, output)
```

The difference from `wrap()`-time filtering: `filter_tools` is called each turn and consults the live trace, so `must_precede(A, B)` opens B in the menu only *after* A fires. This is the strongest proactive behavior Sponsio offers; it requires you own the agent loop.

## Redirect to safe (v0.2)

`redirect_to_safe(unsafe, safe)` substitutes a forbidden tool call with a pre-approved one instead of slamming the door on the agent. The model keeps making progress; it just can't do the unsafe thing.

```python
from sponsio import contract
from sponsio.patterns import redirect_to_safe

guard = sponsio.Sponsio(
    contracts=[
        contract("trash instead of rm")
            .guarantees(redirect_to_safe("rm_rf", "trash")),

        # Conditional redirect: only large refunds get rerouted.
        contract("large refunds go to review")
            .assume("called `issue_refund`")
            .guarantees(redirect_to_safe("issue_refund", "log_refund_request")),
    ],
)
```

When the agent calls `rm_rf`, Sponsio:

1. Rolls back the `rm_rf` event from the trace so downstream counters (`rate_limit`, `count_at_most`) don't tick on the attempted call.
2. Surfaces `result.redirected=True` + `result.redirected_to="trash"` from `guard_before`.
3. The adapter invokes `trash` with the model's original arguments. The trace honestly records the `trash` call (via the normal `guard_before(safe, args)` path), so audit shows what executed.

The model sees the safe tool's result, not an error. Substitution is transparent unless the safe tool returns something the model can't make sense of (schema mismatch).

### Constraints

- Both `unsafe` and `safe` must be registered with your framework — Sponsio does NOT synthesize tools.
- The safe tool should accept the same arguments as the unsafe one. If schemas diverge, the adapter passes args verbatim; the user is responsible for compatibility.
- A `redirect → blocked` chain (safe tool also violates a different contract) raises a hard block. Sponsio does not chain redirects to avoid loops.
- Self-redirect (`unsafe == safe`) is rejected loudly via `ToolCallBlocked`. The pattern factory already rejects `redirect_to_safe("X", "X")` at construction; this guard catches the case where a user wired `RedirectToSafe(safe="X")` directly via `policy={}` and bound it to a contract that triggers on tool `X`.
- A `redirect → redirect` chain (`safe` tool itself has a `redirect_to_safe` contract pointing elsewhere) is also rejected. Resolve the chain by pointing the original `unsafe` directly at the final safe tool.

### Interaction with other contracts on the same tool

If a tool has both a `redirect_to_safe` contract AND another contract (e.g. `must_precede`, `count_at_most`) that fires on the same call, the LangGraph adapter takes the **redirect path first** before checking for a block. The model never sees the block message because the call gets substituted; the substitute call is then checked against everything else.

This means a `must_precede(check_policy, issue_refund)` contract paired with `redirect_to_safe("issue_refund", "log_refund_request")` will effectively skip the ordering check for `issue_refund` (the call gets redirected to `log_refund_request` immediately, and `must_precede` only applies to `issue_refund`'s actual execution which never happens). This is by design: redirecting and refusing are conflicting outcomes, and the redirect was your explicit intent for that tool.

If you want both behaviors, write the `must_precede` against the safe tool (`must_precede(check_policy, log_refund_request)`), or use the framework-agnostic `guard.guard_before(unsafe_tool, args)` inspection in a custom loop where you can branch on `check.blocked` before `check.redirected`.

### What `redirect_to_safe` does per adapter

| Adapter | Redirect behavior |
|---|---|
| LangGraph | Built in. `wrap()` indexes tools by name; on redirect the wrapped `ToolNode` invokes the safe tool's `func` / `coroutine` with the model's original kwargs. Unknown safe tool name raises `ToolCallBlocked`. |
| CrewAI, OpenAI Agents SDK, Google ADK, Vercel AI, Claude Agent SDK | Surface only — `result.redirected_to` is set on the `CheckResult`. Adapter-side dispatch lands in a follow-up release; for now, custom loops can read `result.redirected_to` and call the substitute tool themselves. |
| Custom loop (no framework) | Read `check.redirected_to`, look up the safe tool in your registry, call it with the same args. See snippet below. |

```python
# Custom loop pattern that honors redirect_to_safe outcomes
check = guard.guard_before(tool_name, args)
if check.redirected and check.redirected_to:
    actual = check.redirected_to
    check2 = guard.guard_before(actual, args)
    if check2.allowed:
        output = registry[actual](**args)
        guard.guard_after(actual, output)
elif check.blocked:
    messages.append(f"blocked: {check.det_violations[0].message}")
elif check.allowed:
    output = registry[tool_name](**args)
    guard.guard_after(tool_name, output)
```

## Framework-specific notes

### Claude Agent SDK

`guard.hooks()` plugs into `ClaudeAgentOptions(hooks=...)` directly. No tool wrapping needed.

### OpenAI SDK

`patch_openai()` returns a guard whose every `client.chat.completions.create(...)` is checked automatically. Set `SPONSIO_OPENAI_STRICT_TOOL_ARGS=1` to fail closed when the model returns malformed JSON in `tool_call.function.arguments`. Default warns and degrades.

### Google ADK

`functools.wraps` preserves the original signatures, so ADK's introspection still works. Both sync and `async` tools are supported. Blocked calls return `{"status": "error", "error_message": "BLOCKED..."}` instead of executing the wrapped function, so the model sees a normal tool result and can self-correct.

### MCP

MCP is a tool transport, not an agent framework. Use `guard_before()` / `guard_after()` directly, or wrap an MCP client transparently:

```python
from sponsio.mcp import MCPContractProxy

proxy = MCPContractProxy(mcp_client=client, system=system)
result = await proxy.call_tool("send_email", {"to": "user@example.com"})
```

## Config-driven (every framework)

All integrations support loading contracts from a YAML file:

```python
from sponsio.langgraph import Sponsio

guard = Sponsio(config="sponsio.yaml", agent_id="my_bot")
```

See [Contract sources](../guides/contract-sources.md) for the YAML specification.

## Long-running agents

The trace is append-only during a session. For 24/7 services, call `guard.rotate_session()` periodically to cap memory and keep the verifier's atom caches fresh.

```python
for turn_idx, user_msg in enumerate(conversation):
    response = agent_step(user_msg)
    if turn_idx > 0 and turn_idx % 1000 == 0:
        guard.rotate_session()
```

Rotation preserves the contract set, perf tracker aggregates, callbacks, and dashboard or OTEL wiring. It clears `trace.events`, atom caches, violations, and pending liveness obligations. Whole-trace formulas like `F(response)` cannot survive rotation. `rotate_session()` flushes pending liveness as violations before wiping. Pass `require_finish_session=True` to fail loudly when finalisation is skipped.

Pick a cadence: turn-based (every N turns), wall-clock (every T minutes), or semantic (at the natural end of a conversation). Aim for `N × avg_tool_calls ≈ 10000` events per window.

## OTEL export

```python
from sponsio.integrations.otel import OTelExporter
from sponsio.langgraph import Sponsio

exporter = OTelExporter(endpoint="https://your-otel-backend/v1/traces")
guard = Sponsio(contracts=[...], otel_exporter=exporter)
```

Schema and dashboard wiring: [reference/observability.md](../reference/observability.md).
