# Getting Started

Get Sponsio enforcing contracts on your agent in 5 minutes.

---

## Install

```bash
pip install sponsio

# Optional extras
pip install "sponsio[config]"       # YAML config support
pip install "sponsio[llm]"          # LLM-powered contract discovery (OpenAI, Gemini, Anthropic clients)
pip install "sponsio[otel]"         # OpenTelemetry span export
pip install "sponsio[all]"          # everything
```

From a git checkout, use the same with `-e .`, e.g. `pip install -e ".[llm]"`.

### API keys for LLM contract discovery

There is no Sponsio config file for model keys. The CLI reads **process environment variables** in your shell (or CI):

| You use | Set before running `sponsio scan --llm` |
|--------|----------------------------------------|
| **Gemini** (default if `GOOGLE_API_KEY` is set) | `export GOOGLE_API_KEY=...` or `export GEMINI_API_KEY=...` |
| **OpenAI** | `export OPENAI_API_KEY=...` |
| **Anthropic** | `export ANTHROPIC_API_KEY=...` |
| **Ollama / OpenRouter** (OpenAI-compatible HTTP) | `export OPENAI_API_KEY=...` (if the host requires a key) and pass `--base-url https://...` |

Auto-detection order when `--provider` is omitted: `--base-url` → OpenAI client; else Anthropic if `ANTHROPIC_API_KEY`; else Gemini if `GOOGLE_API_KEY` / `GEMINI_API_KEY`; else OpenAI if `OPENAI_API_KEY`. Use `--provider openai` or unset other keys if the wrong provider is selected.

`pip install "sponsio[llm]"` pulls in the SDKs for those paths, including **`google-genai`** for Gemini (`from google import genai`). If you only set a Gemini key, you need that install or you will see an import error and get zero LLM-inferred contracts.

---

## Option A: Inline Contracts (quickest)

Add 3 lines to your existing agent:

```python
from langgraph.prebuilt import create_react_agent

from sponsio import contract
from sponsio.langgraph import Sponsio

guard = Sponsio(
    agent_id="my_bot",
    contracts=[
        # Conditional (A, E) pair — assumption triggers the enforcement
        contract("refund needs prior policy check")
            .assume("called `issue_refund`")
            .enforce("must call `check_policy` before `issue_refund`"),
        # Unconditional rule — no .assume(), only .enforce()
        contract("refund rate limit")
            .enforce("tool `issue_refund` at most 3 times"),
    ],
)

# Wrap your tools — that's it
agent = create_react_agent(model, guard.wrap(tools))
```

When the agent tries to call `issue_refund` without calling `check_policy` first, Sponsio blocks the call and returns an error to the LLM. The LLM then self-corrects.

---

## Option B: Config File (recommended for production)

### Step 1: Scan your code

```bash
sponsio scan src/agents/ -o sponsio.yaml
```

This discovers your tools and infers ordering constraints automatically.

### Step 2: Review and edit

Open `sponsio.yaml` — add, remove, or adjust constraints:

```yaml
version: "1"

tools:
  - name: check_policy
    description: "Verify refund eligibility"
  - name: issue_refund
    description: "Process refund"

agents:
  my_bot:
    contracts:
      - E:
          pattern: must_precede
          args: [check_policy, issue_refund]
          source: scan
      - E: "tool `issue_refund` at most 3 times"
      - E: "response must not contain PII"
```

### Step 3: Validate

```bash
sponsio validate --config sponsio.yaml
```

### Step 4: Use

```python
from sponsio.langgraph import Sponsio

guard = Sponsio(
    config="sponsio.yaml",
    agent_id="my_bot",
)
agent = create_react_agent(model, guard.wrap(tools))
```

---

## What Happens at Runtime

```
  ━━━ Sponsio ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ▎ contract · my_bot
  ▎
  ▎ enforce ▸ check_policy must precede issue_refund
  ▎         ▸ tool `issue_refund` at most 3 times
  ▎         ▸ response must not contain PII
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ✗ enforce check_policy must precede issue_refund — VIOLATED → blocked
  ✓ enforce check_policy must precede issue_refund — pass
  ✓ enforce tool `issue_refund` at most 3 times — pass

  Sponsio Session Summary (my_bot)
  Total checks: 3  |  Det violations: 1  |  Sto violations: 0
```

---

## Next Steps

- [Contract Types & Atoms](contracts.md) — understand det vs sto constraints and the atom vocabulary
- [Input Formats](input-formats.md) — YAML spec, scan workflow, three input sources
- [Integrations](integrations.md) — framework-specific setup (LangGraph, OpenAI, CrewAI, MCP, Agents SDK)
- [CLI Reference](cli.md) — all commands and options
