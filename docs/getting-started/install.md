---
title: Install
description: Install Sponsio, pick the right extras for your stack, and verify the install.
---

# Install

Sponsio is a pure-Python package with zero required dependencies. The core engine installs in seconds.

```bash
pip install sponsio
```

Verify:

```bash
sponsio --version
sponsio doctor
```

---

## Choosing extras

Extras are optional dependency bundles. Pick what matches your stack; none of them are required to run contracts.

| Extra | What it installs | When to pick it |
|---|---|---|
| `sponsio[yaml]` | `pyyaml` | Loading contracts from `sponsio.yaml` |
| `sponsio[llm]` | provider SDKs (OpenAI, Anthropic, Gemini) | `sponsio scan --llm` and sto judge calls |
| `sponsio[otel]` | OpenTelemetry exporters | Streaming traces to your observability stack |
| `sponsio[langgraph]` | `langgraph`, `langchain-core` | LangGraph integration |
| `sponsio[claude-agent]` | `claude-agent-sdk` | Claude Agent SDK integration |
| `sponsio[openai]` | `openai` | OpenAI SDK integration |
| `sponsio[crewai]` | `crewai` | CrewAI integration |
| `sponsio[google-adk]` | `google-adk` | Google ADK integration |
| `sponsio[vercel-ai]` | `vercel-ai` | Vercel AI SDK (Python) integration |
| `sponsio[mcp]` | `mcp` | MCP proxy integration |
| `sponsio[all]` | everything above | Kitchen-sink install |

```bash
pip install "sponsio[all]"
```

---

## Python support

Python 3.10 and newer. Older versions are not tested.

## TypeScript

The TypeScript deterministic engine ships separately:

```bash
npm install @sponsio/sdk
```

See [TypeScript integrations](../integrations/index.md#typescript) for framework bindings. The Python and TS engines share the same LTL core — they produce identical block/allow decisions over the same trace.

---

## Provider credentials

Sponsio reads API keys from environment variables only. No config file, no keyring.

| Provider | Env var |
|---|---|
| OpenAI | `OPENAI_API_KEY` (optional: `OPENAI_BASE_URL` for Ollama, OpenRouter, DeepSeek, Together, Groq, vLLM, Azure) |
| Anthropic | `ANTHROPIC_API_KEY` |
| Gemini | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |

`sponsio scan --llm` auto-detects the provider from whichever env var is set. Specify `--provider` to override.

---

## Verifying the install

```bash
sponsio doctor
```

Runs a battery of checks — config is valid, framework is detected, provider credentials are reachable, atoms referenced in contracts are registered. Exits non-zero if anything fails.

```bash
sponsio demo --scenario wire --fast
```

Replays a packaged unsafe-agent trajectory locally — no API key, no framework SDK. Sponsio blocks an unverified wire transfer mid-flow. If you see the block, install is working.

---

## Next

- [Quickstart](quickstart.md) — block an unsafe tool call in 60 seconds.
- [First contract](first-contract.md) — write a custom contract against your own agent.
- [Integrations](../integrations/index.md) — plug Sponsio into your framework.
