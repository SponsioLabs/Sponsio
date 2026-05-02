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
| **Google ADK** | `from sponsio.google_adk import Sponsio` | `guard.wrap(tools)` | 3 |
| **MCP** | `from sponsio.mcp import MCPContractProxy` | `proxy.call_tool()` | 3 |
| **No framework** | `sponsio.Sponsio(contracts=[...])` | `guard.guard_before()` / `guard_after()` | 3 |

### TypeScript (via Pyodide — same engine, no server)

| Framework | Import | Integration |
|-----------|--------|-------------|
| **Claude Agent SDK** | `@sponsio/sdk/claude-agent` | `sponsioHooks(guard)` |
| **Vercel AI SDK** | `@sponsio/sdk/vercel-ai` | `sponsioMiddleware(guard)` |
| **OpenAI SDK** | `@sponsio/sdk/openai` | `wrapOpenAI(client, guard)` |
| **LangChain.js** | `@sponsio/sdk/langchain` | `wrapTools(tools, guard)` |
| **Google ADK** | `@sponsio/sdk/google-adk` | `wrapTools(tools, guard)` |

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

## Google ADK

Google's Agent Development Kit turns Python callables in `Agent(tools=[...])` into function tools. Wrap the callables with `guard.wrap(...)` before passing them to the `Agent` constructor; `functools.wraps` preserves the original signatures and docstrings, so ADK's native introspection still works.

```python
from google.adk.agents.llm_agent import Agent

from sponsio import contract
from sponsio.google_adk import Sponsio

guard = Sponsio(
    agent_id="travel_agent",
    contracts=[
        contract("must search before booking")
            .assume("called `book_flight`")
            .enforce("must call `search_flights` before `book_flight`"),
        contract("charge cap")
            .enforce("tool `charge_payment` at most 1 times"),
    ],
)

root_agent = Agent(
    name="travel_agent",
    model="gemini-flash-latest",
    tools=guard.wrap([search_flights, book_flight, charge_payment]),
)
```

Blocked tool calls return an ADK-friendly error dict (`{"status": "error", "error_message": "BLOCKED by contract: ..."}`) instead of executing the wrapped function, so the model sees a normal tool result and can self-correct. Stochastic retry feedback is surfaced through the same channel when a sto contract fires on the post-call output.

Both sync and `async` tools are supported; the wrapper detects coroutine functions with `inspect.iscoroutinefunction` and dispatches accordingly. For a manual check without wrapping, call `guard.check_tool_call(name, args)`.

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

**Strict tool-argument JSON parsing.** Set
`SPONSIO_OPENAI_STRICT_TOOL_ARGS=1` in the environment to fail closed
when the model returns malformed JSON in `tool_call.function.arguments`
— the request raises `ValueError` instead of silently treating the
arguments as an empty dict. The default (warn-and-degrade) is safer
for most agents, but strict mode is the right choice for
security-critical tools where a hallucinated `arguments` string must
never reach your executor.

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

See [Contract sources](../guides/contract-sources.md) for the YAML specification.

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

## Long-Running Agents — Session Rotation

Sponsio's enforcement semantics are whole-trace: every `check_action`
appends to `monitor.trace.events` and the verifier's atom caches, and
they only shrink on explicit reset. That's correct for short sessions
(one conversation, one task) but unbounded for a **24/7 service agent**
— a customer-service bot that runs for weeks eventually sits on
hundreds of MB of trace data and a proportionally slow
`_check_contract_with_confidence` sweep.

`guard.rotate_session()` is the supported way to cap this:

```python
# Long-running agent loop — rotate every N turns, or every T minutes,
# or at a business boundary like "customer hung up".
for turn_idx, user_msg in enumerate(conversation):
    response = agent_step(user_msg)

    if turn_idx > 0 and turn_idx % 1000 == 0:
        summary = guard.rotate_session()
        audit_logger.info(
            "sponsio.rotate",
            extra={
                "events": summary["events"],
                "turns": summary["turns"],
                "violations": summary["violations_cleared"],
                "pending_liveness": summary["pending_liveness_violations"],
            },
        )
```

**What rotation preserves**: contracts on the underlying `System`,
perf tracker aggregates, callbacks, and dashboard / OTEL wiring. The
guard keeps doing exactly what it was doing — just with a fresh trace
window.

**What rotation clears**: `trace.events`, `monitor.turn_spans`,
`monitor.log`, `_atom_caches`, `guard.violations`, and
`_pending_liveness_violations`.

**Liveness caveat**. Formulas whose semantics depend on the entire
trace — `F(response)`, `always_followed_by(trigger, response)`,
whole-trace `rate_limit(tool, N)` — can't survive a rotation: the
post-rotation verifier doesn't see the original `trigger` and can
never report the missing `response`. `rotate_session` calls
`finish_session` by default *before* wiping, so a pending liveness
obligation is flushed as a violation first. Opt out with
`run_finish_session=False` only if you're running `finish_session` at
a different cadence. Use `require_finish_session=True` to fail loudly
instead of silently rotating when finalisation was skipped.

**How to pick a rotation cadence**. Three common patterns:

- **Turn-based** (`turn_idx % N == 0`): simple, predictable.
  Recommended default for tool-heavy agents. Pick `N` such that
  `N × avg_tool_calls_per_turn ≈ 10_000` events per window.
- **Wall-clock** (every T minutes via APScheduler / cron): matches
  ops dashboards' refresh cadence. Pick `T` so a typical window
  holds one "unit of work" (e.g. one customer conversation).
- **Semantic** (at the natural end of a conversation / task / user
  session): cleanest — liveness obligations naturally complete or
  expire at the boundary, so the caveat above is a non-issue.

Contracts themselves are **not** touched — re-register them only if
you're actually changing the contract set. Rotation is a *trace*
operation, not a *system* operation.
