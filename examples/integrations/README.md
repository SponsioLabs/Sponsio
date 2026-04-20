# Integration Examples

Runnable examples showing Sponsio with every supported framework, in both Python and TypeScript. Each example uses the same scenario pattern: a set of contracts, a sequence of tool calls, and expected block/allow results.

## Directory Structure

```
integrations/
├── python/                     # Python examples (8 frameworks)
│   ├── vanilla_guard.py        # No framework — direct guard_before/after
│   ├── langgraph_guard.py      # LangGraph — guard.wrap(tools)
│   ├── openai_guard.py         # OpenAI SDK — patch_openai()
│   ├── claude_agent_guard.py   # Claude Agent SDK — guard.hooks()
│   ├── vercel_ai_guard.py      # Vercel AI SDK — guard.wrap() (middleware)
│   ├── agents_sdk_guard.py     # OpenAI Agents SDK — guard.wrap(tools)
│   ├── crewai_guard.py         # CrewAI — guard.wrap(tools)
│   ├── mcp_guard.py            # MCP — MCPContractProxy
│   └── shared.py               # Shared mock/real mode toggle
│
└── typescript/                 # TypeScript examples (5 frameworks)
    ├── vanilla_guard.mjs       # No framework — guardBefore/guardAfter
    ├── langgraph_guard.mjs     # LangChain.js — wrapTools(tools, guard)
    ├── openai_guard.mjs        # OpenAI SDK — wrapOpenAI(client, guard)
    ├── claude_agent_guard.mjs  # Claude Agent SDK — sponsioHooks(guard)
    └── vercel_ai_guard.mjs     # Vercel AI SDK — sponsioMiddleware(guard)
```

## Quick Start

### Python

```bash
# Mock mode — no API key needed
python examples/integrations/python/vanilla_guard.py
python examples/integrations/python/claude_agent_guard.py

# Real LLM mode
USE_MOCK=0 GOOGLE_API_KEY=... python examples/integrations/python/langgraph_guard.py
```

### TypeScript

```bash
# Install Pyodide (one time)
cd ts-sdk && npm install && cd ..

# Run examples
node examples/integrations/typescript/vanilla_guard.mjs
node examples/integrations/typescript/claude_agent_guard.mjs
node examples/integrations/typescript/langgraph_guard.mjs
```

## Framework Coverage

| Framework | Python | TypeScript | Integration Style |
|-----------|--------|------------|-------------------|
| No framework | `vanilla_guard.py` | `vanilla_guard.mjs` | `guard.guard_before()` / `guardBefore()` |
| LangGraph / LangChain.js | `langgraph_guard.py` | `langgraph_guard.mjs` | `guard.wrap(tools)` / `wrapTools()` |
| OpenAI SDK | `openai_guard.py` | `openai_guard.mjs` | `patch_openai()` / `wrapOpenAI()` |
| Claude Agent SDK | `claude_agent_guard.py` | `claude_agent_guard.mjs` | `guard.hooks()` / `sponsioHooks()` |
| Vercel AI SDK | `vercel_ai_guard.py` | `vercel_ai_guard.mjs` | `guard.wrap()` / `sponsioMiddleware()` |
| OpenAI Agents SDK | `agents_sdk_guard.py` | — | `guard.wrap(tools)` |
| CrewAI | `crewai_guard.py` | — | `guard.wrap(tools)` |
| MCP | `mcp_guard.py` | — | `MCPContractProxy()` |

Python and TypeScript share the **same LTL engine** — TypeScript runs it via Pyodide (CPython in WASM). Cross-language tests in `tests/cross_language/` verify identical block/allow decisions.

## API Keys

- Most Python examples use `GOOGLE_API_KEY` (Gemini) for real mode
- Agents SDK requires `OPENAI_API_KEY`
- Claude Agent SDK requires `ANTHROPIC_API_KEY`
- TypeScript examples and mock mode need no API key
