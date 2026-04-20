# Framework Integrations

Sponsio works with any agent framework in both Python and TypeScript. Each integration intercepts tool calls at the framework's native hook point.

---

## At a Glance

### Python

| Framework | Init | Tool wrapping | Lines to add |
|-----------|------|--------------|-------------|
| **LangGraph** | `sponsio.init(framework="langgraph")` | `guard.wrap(tools)` | 3 |
| **Claude Agent SDK** | `sponsio.init(framework="claude_agent")` | `guard.hooks()` (zero wrapping) | 2 |
| **OpenAI SDK** | `sponsio.init(framework="openai")` | `patch_openai()` | 2 |
| **Vercel AI SDK** | `sponsio.init(framework="vercel_ai")` | `guard.wrap()` (middleware) | 2 |
| **Agents SDK** | `sponsio.init(framework="agents_sdk")` | `guard.wrap(tools)` | 3 |
| **CrewAI** | `sponsio.init(framework="crewai")` | `guard.wrap(tools)` | 3 |
| **MCP** | `MCPContractProxy(client, ...)` | `proxy.call_tool()` | 3 |
| **No framework** | `sponsio.init(contracts=[...])` | `guard.guard_before()` / `guard_after()` | 3 |

### TypeScript (via Pyodide — same engine, no server)

| Framework | Import | Integration |
|-----------|--------|-------------|
| **Claude Agent SDK** | `@sponsio/sdk/claude-agent` | `sponsioHooks(guard)` |
| **Vercel AI SDK** | `@sponsio/sdk/vercel-ai` | `sponsioMiddleware(guard)` |
| **OpenAI SDK** | `@sponsio/sdk/openai` | `wrapOpenAI(client, guard)` |
| **LangChain.js** | `@sponsio/sdk/langchain` | `wrapTools(tools, guard)` |

All integrations — Python and TypeScript — share the same LTL engine and produce identical block/allow decisions. See `tests/cross_language/` for validation.

---

## LangGraph

```python
import sponsio

guard = sponsio.init(
    framework="langgraph",
    agent_id="my_bot",
    contracts=["tool `check_policy` must precede `issue_refund`"],
)

# Replace ToolNode(tools) with guard.wrap(tools)
agent = create_react_agent(model, guard.wrap(tools))
result = agent.invoke({"messages": [("user", "process refund")]})

guard.print_summary()
```

For existing graphs, use `wrap_graph()`:

```python
guard = sponsio.init(framework="langgraph", config="sponsio.yaml", agent_id="bot")
graph = build_my_graph()
graph = guard.wrap_graph(graph)  # wraps all tool nodes
```

---

## CrewAI

```python
import sponsio

guard = sponsio.init(
    framework="crewai",
    agent_id="moderator",
    contracts=[
        "tools `flag_content` and `delete_content` must never be called together",
        "tool `delete_content` requires permission `admin_permission`",
    ],
)

# Wrap tools for CrewAI
crew = Crew(
    agents=[agent],
    tasks=[task],
    tools=guard.wrap([flag_content, delete_content, notify_user]),
)
```

---

## OpenAI SDK

```python
from sponsio.integrations.openai import OpenAIGuard

guard = OpenAIGuard(
    agent_id="db_admin",
    contracts=[
        "tool `preview_query` must precede `execute_query`",
        "tool `execute_query` at most 5 times",
    ],
)

# Check each response
response = client.chat.completions.create(model="gpt-4", messages=messages, tools=tools)
result = guard.check_response(response)
if result.blocked:
    # handle blocked tool call
    pass

guard.print_summary()
```

---

## OpenAI Agents SDK

```python
import sponsio

guard = sponsio.init(
    framework="agents_sdk",
    agent_id="deploy_bot",
    contracts=[
        "tool `run_tests` must precede `deploy_production`",
        "tool `deploy_staging` at most 3 times",
    ],
)

agent = Agent(
    name="deploy_bot",
    tools=guard.wrap([run_tests, deploy_staging, deploy_production]),
)
```

---

## MCP

MCP is a tool transport protocol, not an agent framework. Use `guard_before()` / `guard_after()` directly:

```python
import sponsio

guard = sponsio.init(
    agent_id="mcp_agent",
    contracts=[
        "tool `read_database` must precede `write_external_api`",
        "tool `send_email` at most 2 times",
    ],
)

# In your MCP tool execution loop:
for tool_call in mcp_tool_calls:
    result = guard.guard_before(tool_call.name, tool_call.args)
    if not result.blocked:
        output = await mcp_client.call_tool(tool_call.name, tool_call.args)
        guard.guard_after(tool_call.name, output)
```

For transparent MCP wrapping, use `MCPContractProxy`:

```python
from sponsio.integrations.mcp import MCPContractProxy

proxy = MCPContractProxy(mcp_client=client, system=system)
result = await proxy.call_tool("send_email", {"to": "user@example.com"})
# Blocked calls return {"error": "Blocked by behavioral contract", ...}
```

---

## No Framework (Vanilla)

For custom agent loops without a framework:

```python
import sponsio

guard = sponsio.init(
    agent_id="my_agent",
    contracts=[
        "tool `verify_identity` must precede `transfer_funds`",
        "tool `transfer_funds` at most 3 times",
    ],
)

# Your custom loop
while not done:
    tool_name, args = llm_decide_next_action()

    result = guard.guard_before(tool_name, args)
    if result.blocked:
        # Feed error back to LLM
        llm_messages.append(f"Action blocked: {result.det_violations[0].message}")
        continue

    output = execute_tool(tool_name, args)
    guard.guard_after(tool_name, output)

guard.print_summary()
```

---

## Config-Driven (All Frameworks)

All integrations support loading contracts from a YAML file:

```python
guard = sponsio.init(
    framework="langgraph",   # or any framework
    config="sponsio.yaml",
    agent_id="my_bot",
)
```

See [Input Formats](input-formats.md) for the YAML specification.

---

## Dashboard

Any integration can push spans to the Sponsio dashboard:

```python
guard = sponsio.init(
    framework="langgraph",
    contracts=[...],
    dashboard=True,           # auto-start on port 8000
    # or: dashboard="http://localhost:8000"  # connect to existing
)
```

## OTEL Export

Any integration can export spans to OTEL backends:

```python
from sponsio.integrations.otel import OTelExporter

exporter = OTelExporter(endpoint="https://your-otel-backend/v1/traces")

guard = sponsio.init(
    framework="langgraph",
    contracts=[...],
    otel_exporter=exporter,
)
```
