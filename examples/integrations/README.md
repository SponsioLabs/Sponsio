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
│   └── shared.py               # Shared mock/real mode toggle
│
└── typescript/                 # TypeScript examples (6 — det only)
    ├── vanilla_guard.mjs          # No framework — guardBefore/guardAfter + contract() builder
    ├── langgraph_guard.mjs        # LangChain.js — wrapTools(tools, guard)
    ├── openai_guard.mjs           # OpenAI SDK — patchOpenAI(client, guard)
    ├── openai_agents_guard.mjs    # OpenAI Agents SDK — wrapAgentsTools(tools, guard)
    ├── claude_agent_guard.mjs     # Claude Agent SDK — sponsioHooks(guard)
    └── vercel_ai_guard.mjs        # Vercel AI SDK — sponsioMiddleware(guard)
```

LLM-judged stochastic atoms (`injection_free`, `tone_*`, `semantic_pii_free`, ...) are a Sponsio Cloud feature — install with `pip install sponsio[cloud]`. The Python sto integration examples live in the Cloud repo's `examples/` directory; OSS only ships deterministic guards.

## Quick Start

### Python

```bash
# Mock mode — no API key needed
python3 examples/integrations/python/vanilla_guard.py
python3 examples/integrations/python/claude_agent_guard.py

# Real LLM mode
USE_MOCK=0 GOOGLE_API_KEY=... python3 examples/integrations/python/langgraph_guard.py
```

### TypeScript

```bash
# Build the SDK (one time — dist/ is gitignored)
cd /sdk && npm install && npm run build && cd ..

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
| OpenAI SDK | `openai_guard.py` | `openai_guard.mjs` | `patch_openai()` / `patchOpenAI()` |
| OpenAI Agents SDK | `agents_sdk_guard.py` | `openai_agents_guard.mjs` | `guard.wrap(tools)` / `wrapAgentsTools()` |
| Claude Agent SDK | `claude_agent_guard.py` | `claude_agent_guard.mjs` | `guard.hooks()` / `sponsioHooks()` |
| Vercel AI SDK | `vercel_ai_guard.py` | `vercel_ai_guard.mjs` | `guard.wrap()` / `sponsioMiddleware()` |
| Sto (tone / llm_judge) | Cloud-only — see Sponsio Cloud `examples/` | Cloud-only — see Sponsio Cloud `examples/` | `E: { pattern: tone, args, threshold }` + `judge:` (requires `pip install sponsio[cloud]`) |
| CrewAI | `crewai_guard.py` | — | `guard.wrap(tools)` |

Python and TypeScript share the **same deterministic LTL engine** — TypeScript has its own native port (no Python runtime or WASM). Cross-language tests in `tests/cross_language/` verify identical block/allow decisions. The TS sto pipeline is intentionally minimal (two atoms: `tone`, `llm_judge`); semantic PII, hallucination, scope respect, and metric integrity remain Python-only today.

## API Keys

- Most Python examples use `GOOGLE_API_KEY` (Gemini) for real mode
- Agents SDK requires `OPENAI_API_KEY`
- Claude Agent SDK requires `ANTHROPIC_API_KEY`
- TypeScript examples and mock mode need no API key
