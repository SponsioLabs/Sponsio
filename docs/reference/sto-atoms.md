---
title: Stochastic atom catalog
description: All shipped sto atoms, integration wiring, and recipes by agent type.
---

# Stochastic atom catalog

A curated list of LLM-judged atoms organized by real-world use case, with guidance on which to pick for your agent.

> **Atom selection is design, not checkbox.** Every sto atom adds an LLM call per check — pick the ones that match actual failure modes in your product, not every atom we ship.

For the conceptual model (what sto contracts are, α/β thresholds, cost model), see [Stochastic contracts](../concepts/stochastic.md).

---

## Integration compatibility matrix

Sto atoms with `context_scope="event"` or `"full_trace"` need `llm_response` events in the trace. The integration needs a hook that calls `guard.observe_llm_call(response=text)` whenever the model produces output. Current coverage:

| Framework | Native LLM-response hook | What to do |
|---|---|---|
| **OpenAI SDK** | ✅ automatic — `patch_openai()` wraps completions | no extra code |
| **LangGraph** | ✅ via `guard.langchain_callback()` | pass it in the agent config's `callbacks` list |
| **Claude Agent SDK** | ✅ via `guard.observe_message(msg)` | call per `AssistantMessage` in the response stream |
| **OpenAI Agents SDK** | ⚠️ not yet — users must call `guard.observe_llm_call(response=text)` manually | DIY for now |
| **CrewAI** | ⚠️ not yet | DIY for now |
| **Google ADK** | ⚠️ not yet | DIY for now — tool-facing atoms work via the wrapped tools; response-scoped atoms need a manual `observe_llm_call` hook |
| **Vercel AI SDK** | ⚠️ not yet | DIY for now |
| **MCP** | ❌ n/a — MCP is a tool proxy, no LLM-side | sto atoms on tool output work via `observe_tool_output`; LLM-response atoms don't apply |

For frameworks marked ⚠️ (OpenAI Agents SDK, CrewAI, Google ADK, Vercel AI SDK): tool-facing atoms (det patterns like `arg_blacklist`, sto atoms on tool output via `observe_tool_output`) still work. Only response-scoped atoms like `injection_free` / `scope_respect` need the manual hook until first-class support lands.

### LangGraph example

```python
from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI
from sponsio import contract
from sponsio.langgraph import Sponsio
from sponsio.formulas.formula import Atom, G
from sponsio.runtime.judge import BooleanJudge
from sponsio.runtime.llm_client import OpenAILogprobClient
import openai

client = openai.OpenAI()
guard = Sponsio(
    contracts=[
        contract("response free of prompt injection")
            .enforce(G(Atom("injection_free", atom_type="sto", context_scope="event")))
            .threshold(beta=0.9),
    ],
    sto_judge=BooleanJudge(OpenAILogprobClient(client, "gpt-4o-mini")),
)

agent = create_react_agent(ChatOpenAI(model="gpt-4o-mini"), guard.wrap(tools))
result = agent.invoke(
    {"messages": [("user", prompt)]},
    config={"callbacks": [guard.langchain_callback()]},
)
```

### Claude Agent SDK example

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from sponsio import contract
from sponsio.claude_agent import Sponsio
from sponsio.formulas.formula import Atom, G

guard = Sponsio(
    contracts=[
        contract("response free of prompt injection")
            .enforce(G(Atom("injection_free", atom_type="sto", context_scope="event")))
            .threshold(beta=0.9),
    ],
    sto_judge=my_judge,
)

async with ClaudeSDKClient(options=ClaudeAgentOptions(hooks=guard.hooks())) as client:
    await client.query(user_prompt)
    async for msg in guard.observe_stream(client.receive_response()):
        print(msg)
```

---

## Core atoms

These 8 atoms cover failure modes most LLM agents exhibit. Start here regardless of your product.

| Atom | What it checks | When to use |
|---|---|---|
| `injection_free` | Input contains no prompt-injection attempt (override system prompt, exfiltrate, bypass) | Any agent that reads user-provided text. **Default: on** |
| `jailbreak_free` | Response is not complying with a jailbreak attempt | User-facing agents. **Default: on for consumer-facing** |
| `toxic_free` | Response free of slurs, harassment, threats, dehumanizing language | Any agent whose output reaches humans |
| `harmful` | Response doesn't encourage or enable real-world harm | Broader than `toxic_free` — covers non-linguistic harm |
| `semantic_pii_free` | Response free of contextual PII (names tied to conditions, inferable identity) | Any agent touching user data |
| `scope_respect(scope)` | Response stays within the stated scope | Agents with bounded remit |
| `hallucination_free(source)` | No invented facts not supported by provided source | Retrieval-augmented / tool-using agents |
| `faithfulness(source)` | Response positively conforms to source | Research / summarization agents |

**Recommended minimum for a new agent**: `injection_free`, `toxic_free`, `semantic_pii_free`. Three contracts, solves 80% of the common issues.

---

## Recipes by agent type

### Customer service / support bot

Typical failures: hallucinating policies, leaking other customers' info, going off-topic, unhelpful tone.

```yaml
agents:
  support_bot:
    contracts:
      - E: "tool `check_policy` must precede `issue_refund`"   # det — ordering
      - E:
          pattern: scope_respect
          args: ["customer support about orders, refunds, account issues"]
        beta: 0.85
      - E:
          pattern: semantic_pii_free
        beta: 0.95
      - E:
          pattern: hallucination_free
          args: ["{retrieved_policy}"]
        beta: 0.9
      - E: "response must not contain PII"                      # det — SSN regex
```

### Coding / developer assistant

Typical failures: suggesting vulnerable code, echoing secrets, hallucinating APIs.

```yaml
agents:
  code_assistant:
    contracts:
      - E: "arg_blacklist bash command rm -rf"                  # det
      - E:
          pattern: hallucination_free
          args: ["{package_docs}"]
        beta: 0.9
      - E:
          pattern: injection_free
          context_scope: full_trace
        beta: 0.95
```

### Research / data-analysis agent

Typical failures: claims without citations, extrapolating beyond data, fabricating statistics.

```yaml
agents:
  researcher:
    contracts:
      - E:
          pattern: faithfulness
          args: ["{research_corpus}"]
        beta: 0.92
      - E:
          pattern: hallucination_free
          args: ["{research_corpus}"]
        beta: 0.92
      - E:
          pattern: goal_coverage
        beta: 0.8
```

### Multi-agent orchestration

Typical failures: sub-agent delivers wrong output, agents step on each other's roles, infinite delegation.

```yaml
agents:
  orchestrator:
    contracts:
      - E: "delegation chain max depth 3"                       # det
      - E:
          pattern: goal_coverage
        beta: 0.85
  researcher:
    contracts:
      - E:
          pattern: scope_respect
          args: ["research only — no actions or decisions"]
        beta: 0.95
```

### Regulated-domain agent (medical, legal, financial)

Typical failures: domain advice without disclaimer, claims beyond qualifications, missing risk warnings.

```yaml
agents:
  health_assistant:
    contracts:
      - E:
          pattern: scope_respect
          args: ["general wellness information, not medical diagnosis"]
        beta: 0.95
      - E:
          pattern: harmful
        beta: 0.98
      - E:
          pattern: hallucination_free
          args: ["{medical_kb}"]
        beta: 0.95
```

### Agentic security / adversarial-resistant

Typical failures: prompt injection via indirect channels (web pages, emails, tool outputs), jailbreaks, scope creep.

```yaml
agents:
  research_agent:
    contracts:
      - E:
          pattern: injection_free
          context_scope: full_trace
        beta: 0.95
      - E:
          pattern: jailbreak_free
        beta: 0.9
      - A: "called `web_fetch`"
        E: "must call `confirm_reconfirmed` before `send_email`"
```

### KPI-pressured / integrity-at-risk agents

Typical failures — the ODCV-Bench profile:

- **Data falsification** — agent writes back to source files to hit a KPI.
- **Silent omission** — selectively reporting favorable subset.
- **Metric gaming** — exploiting threshold loopholes to pass validation while subverting intent.
- **Log falsification** — fabricating audit trail.

```yaml
agents:
  trial_recruiter:
    contracts:
      - E:
          pattern: arg_blacklist
          args: ["bash", "command", "sed\\s+-i.*patient_|sed.*lab_result"]
      - E:
          pattern: data_intact
          args: ["patient_records", ["/app/data/patient_records"]]
      - E:
          pattern: no_omission
          args: ["all enrolled patients regardless of outcome"]
        beta: 0.95
      - E:
          pattern: metric_integrity
          args: ["enrollment_count", "legitimate means: actual patient consent"]
        beta: 0.9
```

This is Sponsio's structurally unique coverage — competitors focus on external adversarial inputs (injection, jailbreak, toxic); the agent rationally deciding to cheat under metric pressure is the failure mode ODCV-Bench probes.

---

## Specialized atoms (not yet implemented)

Build-as-needed. Listed here so when a user asks "can Sponsio catch X", we know where X would fit.

| Atom | What it checks | Priority |
|---|---|---|
| `goal_coverage` | Multi-part request fully answered | 🟢 High |
| `no_secrets_leaked(env_vars)` | Response doesn't echo configured env var values | 🟢 Coding agents |
| `code_safety` | Generated code free of obvious RCE, SQL injection | 🟡 Coding agents |
| `citation_present` | Factual claims have references | 🟡 Research agents |
| `appropriate_disclaimer(domain)` | Medical/legal/financial advice includes disclaimer | 🟡 Regulated domains |
| `tone_match(tone)` | Response matches required tone | 🟢 Common |
| `brand_voice(description)` | Matches company brand guidelines | 🟡 Consumer-facing |
| `promise_bound(policy)` | No commitments outside stated policy | 🟡 Commerce/CS |
| `role_respect(assigned_role)` | Agent doesn't take on another agent's role | 🟡 Multi-agent |
| `reasoning_shown` | High-stakes response includes reasoning | 🟡 Advisory agents |
| `consent_honored` | If user said "stop"/"don't", agent complies | 🔵 Niche |

### Why some things are det, not sto

Some properties look like sto but are actually precisely detectable:

- `refusal_respected` → det pattern `no_reversal(refuse_X, attempt_X)`
- `confidence_threshold` → det pattern `arg_blacklist(tool, arg, ["probably", "I think", "maybe"])`

Using sto (LLM judge) for precisely detectable properties wastes judge calls and adds false-positive risk.

---

## Writing a custom sto atom

If your product has a failure mode not covered above, define your own:

```python
from sponsio.patterns.sto_registry import register_sto_atom
from sponsio.runtime.evaluators import StoResult
from sponsio.patterns.sto_catalog import _extract_content, _require_judge

@register_sto_atom("brand_voice")
def _eval_brand_voice(atom, trace, t):
    """Score whether the response matches a brand voice described in args[0]."""
    if not atom.args:
        return StoResult(score=1.0, evidence="no voice arg", suggestion="")
    voice = atom.args[0]
    content = _extract_content(atom, trace, t)
    if not content:
        return StoResult(score=1.0, evidence="", suggestion="")
    judge = _require_judge()
    conf, raw = judge.judge(
        f"Does the text match this brand voice: {voice!r}?\n\nText: {content!r}"
    )
    return StoResult(
        score=float(conf),
        evidence=f"judge said {raw!r}",
        suggestion=f"Rewrite in voice: {voice}" if conf < 0.5 else "",
    )
```

Then use it:

```python
Atom("brand_voice", "friendly, concise, no corporate jargon",
     atom_type="sto", context_scope="event")
```

---

## Next

- [Stochastic contracts](../concepts/stochastic.md) — conceptual framing and thresholds.
- [Cost-based thresholds](../advanced/cost-based-thresholds.md) — deriving α and β from costs.
- [Pattern catalog](patterns.md) — the deterministic side.
