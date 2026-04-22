# Framework Integrations

Sponsio works with any agent framework in both Python and TypeScript. Each integration intercepts tool calls at the framework's native hook point.

---

## At a Glance

### Python

| Framework | Factory | Tool wrapping | Lines to add |
|-----------|------|--------------|-------------|
| **LangGraph** | `from sponsio.langgraph import Sponsio` | `guard.wrap(tools)` | 3 |
| **Claude Agent SDK** | `from sponsio.claude_agent import Sponsio` | `guard.hooks()` (zero wrapping) | 2 |
| **OpenAI SDK** | `from sponsio.openai import Sponsio` (or `patch_openai`) | automatic response checks | 2 |
| **Vercel AI SDK** | `from sponsio.vercel_ai import Sponsio` | `guard.wrap()` (middleware) | 2 |
| **Agents SDK** | `from sponsio.agents import Sponsio` | `guard.wrap(tools)` | 3 |
| **CrewAI** | `from sponsio.crewai import Sponsio` | `guard.wrap(tools)` | 3 |
| **MCP** | `from sponsio.mcp import MCPContractProxy` | `proxy.call_tool()` | 3 |
| **No framework** | `sponsio.Sponsio(contracts=[...])` | `guard.guard_before()` / `guard_after()` | 3 |

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
from langgraph.prebuilt import create_react_agent

from sponsio import contract
from sponsio.langgraph import Sponsio

guard = Sponsio(
    agent_id="my_bot",
    contracts=[
        contract("policy gate before refund")
            .assume("called `issue_refund`")
            .enforce("must call `check_policy` before `issue_refund`"),
    ],
)

# Replace ToolNode(tools) with guard.wrap(tools)
agent = create_react_agent(model, guard.wrap(tools))
result = agent.invoke({"messages": [("user", "process refund")]})

guard.print_summary()
```

For existing graphs, use `wrap_graph()`:

```python
from sponsio.langgraph import Sponsio

guard = Sponsio(config="sponsio.yaml", agent_id="bot")
graph = build_my_graph()
graph = guard.wrap_graph(graph)  # wraps all tool nodes
```

---

## CrewAI

```python
from crewai import Agent, Crew, Task

from sponsio import contract
from sponsio.crewai import Sponsio

guard = Sponsio(
    agent_id="moderator",
    contracts=[
        contract("delete needs admin permission")
            .assume("called `delete_content`")
            .enforce("permission `admin_permission` granted before `delete_content`"),
        contract("flag and delete are mutually exclusive")
            .enforce("tools `flag_content` and `delete_content` must never be called together"),
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
from openai import OpenAI

from sponsio import contract
from sponsio.openai import patch_openai

client = OpenAI()

guard = patch_openai(
    agent_id="db_admin",
    contracts=[
        contract("preview before executing destructive SQL")
            .assume("called `execute_query`")
            .enforce("must call `preview_query` before `execute_query`"),
        contract("execute_query rate limit")
            .enforce("tool `execute_query` at most 5 times"),
    ],
)

# Every response is checked automatically.
response = client.chat.completions.create(model="gpt-4", messages=messages, tools=tools)

guard.print_summary()
```

---

## OpenAI Agents SDK

```python
from agents import Agent

from sponsio import contract
from sponsio.agents import Sponsio

guard = Sponsio(
    agent_id="deploy_bot",
    contracts=[
        contract("tests gate production deploys")
            .assume("called `deploy_production`")
            .enforce("must call `run_tests` before `deploy_production`"),
        contract("staging deploy rate limit")
            .enforce("tool `deploy_staging` at most 3 times"),
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
from sponsio import contract

guard = sponsio.Sponsio(
    agent_id="mcp_agent",
    contracts=[
        contract("read DB before writing to external API")
            .assume("called `write_external_api`")
            .enforce("must call `read_database` before `write_external_api`"),
        contract("email rate limit")
            .enforce("tool `send_email` at most 2 times"),
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
from sponsio.mcp import MCPContractProxy

proxy = MCPContractProxy(mcp_client=client, system=system)
result = await proxy.call_tool("send_email", {"to": "user@example.com"})
# Blocked calls return {"error": "Blocked by behavioral contract", ...}
```

---

## No Framework (Vanilla)

For custom agent loops without a framework:

```python
import sponsio
from sponsio import contract

guard = sponsio.Sponsio(
    agent_id="my_agent",
    contracts=[
        contract("identity check before transfer")
            .assume("called `transfer_funds`")
            .enforce("must call `verify_identity` before `transfer_funds`"),
        contract("transfer rate limit")
            .enforce("tool `transfer_funds` at most 3 times"),
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
from sponsio.langgraph import Sponsio

guard = Sponsio(
    config="sponsio.yaml",
    agent_id="my_bot",
)
```

See [Input Formats](input-formats.md) for the YAML specification.

---

## Dashboard

Any integration can push spans to the Sponsio dashboard:

```python
from sponsio.langgraph import Sponsio

guard = Sponsio(
    contracts=[...],
    dashboard=True,           # auto-start on port 8000
    # or: dashboard="http://localhost:8000"  # connect to existing
)
```

## OTEL Export

Any integration can export spans to OTEL backends:

```python
from sponsio.integrations.otel import OTelExporter
from sponsio.langgraph import Sponsio

exporter = OTelExporter(endpoint="https://your-otel-backend/v1/traces")

guard = Sponsio(
    contracts=[...],
    otel_exporter=exporter,
)
```
