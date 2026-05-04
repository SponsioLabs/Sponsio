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
