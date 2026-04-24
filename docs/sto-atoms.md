# Stochastic Atom Catalog


> A curated list of LLM-judged safety atoms organized by real-world use case, with guidance on which to pick for your agent.
>
> **Atom selection is design**, not checkbox. Every sto atom adds an LLM call per check — pick the ones that match actual failure modes in your product, not every atom we ship.

---

## Integration compatibility matrix

Sto atoms with ``context_scope="event"`` or ``"full_trace"`` need
``llm_response`` events in the trace. The integration needs a hook
that calls ``guard.observe_llm_call(response=text)`` whenever the
model produces output. Current coverage:

| Framework | Native LLM-response hook | What to do |
|---|---|---|
| **OpenAI SDK** | ✅ automatic — ``patch_openai()`` wraps completions | no extra code |
| **LangGraph** | ✅ via ``guard.langchain_callback()`` | pass it in the agent config's ``callbacks`` list |
| **Claude Agent SDK** | ✅ via ``guard.observe_message(msg)`` | call per ``AssistantMessage`` in the response stream |
| **OpenAI Agents SDK** | ⚠️ not yet — users must call ``guard.observe_llm_call(response=text)`` manually | DIY for now |
| **CrewAI** | ⚠️ not yet | DIY for now |
| **Vercel AI SDK** | ⚠️ not yet | DIY for now |
| **MCP** | ❌ n/a — MCP is a tool proxy, no LLM-side | sto atoms on tool output work via ``observe_tool_output``; LLM-response atoms don't apply |

For the three frameworks marked ⚠️: tool-facing atoms (det patterns
like ``arg_blacklist``, sto atoms on tool output via
``observe_tool_output``) still work. Only response-scoped atoms like
``injection_free`` / ``scope_respect`` need the manual hook until
first-class support lands.

## LangGraph example

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
    config={"callbacks": [guard.langchain_callback()]},  # ← ties the LLM responses in
)
```

## Claude Agent SDK example

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, AssistantMessage
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
        # stream passes through; guard has already fed each AssistantMessage
        # to the sto pipeline before yielding it to you
        print(msg)
```

---

## Formula shape for response-scoped atoms — always wrap in ``G(...)``

A naked ``Atom("injection_free", atom_type="sto")`` as a contract's
enforcement only evaluates the atom at position 0 — it will judge the
**first** event in the trace (often an ``llm_request``) and never the
ones that follow. For "every LLM response must be injection-free",
wrap the atom in ``G(...)``:

```python
from sponsio import contract

# ✓ every llm_response event is judged
contract("response free of prompt injection").enforce(
    G(Atom("injection_free", atom_type="sto", context_scope="event"))
)

# ✗ only position 0 is judged (the first event, usually not a response)
contract("response free of prompt injection").enforce(
    Atom("injection_free", atom_type="sto", context_scope="event")
)
```

Atoms on a non-content event (e.g. ``tool_call``) vacuously pass —
``_extract_content`` returns ``None`` and the evaluator returns
``score=1.0`` — so ``G(atom)`` doesn't over-fire on non-response events.

Option B's per-position atom cache makes ``G(...)`` cheap on long
traces: each event is judged at most once per contract across the
whole session, even if G unrolls through all positions on each new
event.

---

## How to use this doc

1. **Find your agent type** in §2 and start with the atoms recommended for it.
2. **Add domain-specialized atoms** from §3 only if your product lives in that domain.
3. **Don't add "nice-to-have" atoms** — each one costs a judge call per check and adds a false-positive risk. When in doubt, leave it out.

### Relationship to det patterns

Sto atoms are for properties that **need semantic judgment**. If the property is precisely computable (regex match, length, schema validity), use a **det pattern** instead. See `docs/contracts.md` for the full det pattern library.

| Property kind | Example | Where it lives |
|---|---|---|
| Syntactic / pattern-matchable | "no SSN in response" | Det: `no_pii()` |
| Semantic / requires understanding | "no contextual PII (names + conditions)" | Sto: `semantic_pii_free` |
| Format-checkable | "output is valid JSON" | Det: (coming) `output_format("json")` |
| Meaning-dependent | "output faithful to source" | Sto: `faithfulness(source)` |

---

## 1. Core atoms — broadly useful across agent types

These 8 atoms cover failure modes most LLM agents exhibit. Start here regardless of your product.

| Atom | What it checks | When to use |
|---|---|---|
| `injection_free` | Input contains no prompt-injection attempt (override system prompt, exfiltrate, bypass) | Any agent that reads user-provided text. **Default: on** |
| `jailbreak_free` | Response is not complying with a jailbreak attempt (DAN, roleplay unrestricted AI, hypothetical bypass) | User-facing agents. **Default: on for consumer-facing** |
| `toxic_free` | Response free of slurs, harassment, threats, dehumanizing language | Any agent whose output reaches humans |
| `harmful` | Response doesn't encourage or enable real-world harm (dangerous advice, self-harm, illegal acts) | Broader than `toxic_free` — covers non-linguistic harm |
| `semantic_pii_free` | Response free of contextual PII (names tied to conditions, inferable identity) | Any agent touching user data. Complements regex `no_pii()` |
| `scope_respect(scope)` | Response stays within the stated scope | Agents with bounded remit (customer service, domain specialists) |
| `hallucination_free(source)` | No invented facts not supported by provided source | Retrieval-augmented / tool-using agents |
| `faithfulness(source)` | Response positively conforms to source (complement of hallucination_free) | Research / summarization / report-generation agents |

**Recommended minimum for a new agent**: `injection_free`, `toxic_free`, `semantic_pii_free`. Three contracts, solves 80% of the common issues.

---

## 2. By agent type

### 2.1 Customer service / support bot

Typical failures: hallucinating policies, leaking other customers' info, going off-topic, unhelpful tone.

```yaml
agents:
  support_bot:
    contracts:
      - E: "tool `check_policy` must precede `issue_refund`"   # det — ordering
      - E:                                                       # sto — scope
          pattern: scope_respect
          args: ["customer support about orders, refunds, account issues"]
        beta: 0.85
      - E:                                                       # sto — privacy
          pattern: semantic_pii_free
        beta: 0.95                                               # cautious
      - E:                                                       # sto — grounding
          pattern: hallucination_free
          args: ["{retrieved_policy}"]                           # source: retrieved KB chunk
        beta: 0.9
      - E: "response must not contain PII"                       # det — SSN regex etc.
```

Recommended atoms:
- ✅ `scope_respect` — forces topic adherence
- ✅ `semantic_pii_free` — protects across-customer leakage
- ✅ `hallucination_free` — grounded in retrieved policy
- 🟡 `tone_match("empathetic")` — if brand voice matters (currently legacy closure; will port to atom)
- 🟡 `promise_bound` — flag commitments outside company policy (**not shipped** — propose if you see this pattern)

### 2.2 Coding / developer assistant

Typical failures: suggesting vulnerable code, echoing secrets from env, hallucinating APIs that don't exist.

```yaml
agents:
  code_assistant:
    contracts:
      - E: "arg_blacklist bash command rm -rf"                  # det
      - E:                                                       # sto — accurate APIs
          pattern: hallucination_free
          args: ["{package_docs}"]
        beta: 0.9
      - E:                                                       # sto — no secret echo
          pattern: injection_free
          context_scope: full_trace                              # check whole trace
        beta: 0.95
```

Recommended atoms:
- ✅ `injection_free` (full_trace scope) — user pastes compromised file content, you catch it
- ✅ `hallucination_free(package_docs)` — LLM invents functions that don't exist
- 🟡 `no_secrets_leaked` — flag env var contents in response (**not shipped** — straightforward to add)
- 🟡 `code_safety` — detects obvious RCE/injection in generated code (**not shipped** — specialized)

### 2.3 Research / data-analysis agent

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
          pattern: goal_coverage                                 # answer all sub-questions
        beta: 0.8
```

Recommended atoms:
- ✅ `faithfulness(corpus)` — positive grounding check
- ✅ `hallucination_free(corpus)` — negative grounding check (redundancy is fine for research)
- ✅ `goal_coverage` — multi-part questions fully answered
- 🟡 `citation_present` — every factual claim has a reference (**not shipped** — useful for scientific/journalism)

### 2.4 Multi-agent orchestration

Typical failures: sub-agent delivers wrong output, agents step on each other's roles, infinite delegation.

```yaml
agents:
  orchestrator:
    contracts:
      - E: "delegation chain max depth 3"                       # det
      - E:                                                       # sto
          pattern: goal_coverage
        beta: 0.85
    # Per sub-agent:
  researcher:
    contracts:
      - E:
          pattern: scope_respect
          args: ["research only — no actions or decisions"]
        beta: 0.95
```

Recommended atoms:
- ✅ `goal_coverage` — sub-agent actually answered what orchestrator asked
- ✅ `scope_respect` — each agent stays in its role
- 🟡 `role_respect` — agent doesn't pretend to be another agent (**not shipped** — multi-agent specific)

### 2.5 Regulated-domain agent (medical, legal, financial)

Typical failures: giving domain advice without disclaimer, making claims beyond qualifications, missing risk warnings.

```yaml
agents:
  health_assistant:
    contracts:
      - E:
          pattern: scope_respect
          args: ["general wellness information, not medical diagnosis or treatment"]
        beta: 0.95                                               # strict
      - E:
          pattern: harmful
        beta: 0.98                                               # near-certain no harm
      - E:
          pattern: hallucination_free
          args: ["{medical_kb}"]
        beta: 0.95
```

Recommended atoms:
- ✅ `scope_respect` — hard limit on domain boundary
- ✅ `harmful` — catch dangerous advice
- ✅ `hallucination_free` — ground in vetted sources
- 🟡 `appropriate_disclaimer` — "consult a professional" present (**not shipped** — domain-specialized)
- 🔴 `no_medical_advice` / `no_legal_advice` — hard refusal (**not shipped** — consider det via `content_prohibition` keyword instead)

### 2.6 Agentic security / adversarial-resistant

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
      # After fetching untrusted content, re-confirm before sensitive actions:
      - A: "called `web_fetch`"
        E: "must call `confirm_reconfirmed` before `send_email`"   # det — gate pattern
```

Recommended atoms:
- ✅ `injection_free` (full_trace) — catches indirect injection from tool outputs
- ✅ `jailbreak_free`
- ✅ Paired with det `untrusted_source_gate()` pattern from the library

---

## 2.7 KPI-pressured / integrity-at-risk agents (ODCV scenarios)

This is Sponsio's **structurally unique coverage**. Competitors focus on *external* adversarial inputs (injection, jailbreak, toxic). They do **not** catch the agent itself rationally deciding to cheat under metric pressure — the failure mode ODCV-Bench probes.

Typical failures:
- **Data falsification** — agent writes back to source files to hit a KPI (`sed -i "s/lab_result 10.5/lab_result 9.5/" patient_records.csv` to raise eligibility rate)
- **Silent omission** — agent selectively reports favorable subset, hides adverse events (SAE underreporting)
- **Metric gaming** — agent exploits threshold loopholes to pass validation while subverting intent (AML SAR rate manipulation)
- **Log falsification** — fabricates audit trail to match desired story (fleet rest-hour logs unsupported by sensor data)

```yaml
agents:
  trial_recruiter:
    contracts:
      # Det — catches the direct file-tampering mechanism
      - E:
          pattern: arg_blacklist
          args: ["bash", "command", "sed\\s+-i.*patient_|sed.*lab_result"]
      - E:
          pattern: data_intact
          args: ["patient_records", ["/app/data/patient_records"]]

      # Sto — catches the semantic/intent-level cheating (our moat)
      - E:
          pattern: no_omission
          args: ["all enrolled patients regardless of outcome"]
        beta: 0.95
      - E:
          pattern: metric_integrity
          args: ["enrollment_count", "legitimate means: actual patient consent"]
        beta: 0.9
```

Recommended atoms for KPI-pressured agents:
- ✅ `no_omission(context)` — detects silent suppression / selective reporting
- ✅ `metric_integrity(metric, method)` — detects gaming approaches
- ✅ `hallucination_free(source)` + `faithfulness(source)` — grounding in real data
- ✅ Det companions: `arg_blacklist` for file-edit commands, `data_intact` for source preservation, `irreversible_once` for audit-log paths

**Why this matters for positioning**: LangGraph / Guardrails AI / LlamaFirewall don't have a concept of "agent reporting the wrong thing because it serves the KPI". They check inputs and outputs in isolation. Trace-level + intent-level compliance is where Sponsio wins.

---

## 3. Specialized atoms (not yet implemented)

Build-as-needed. Listed here so when a user asks "can Sponsio catch X", we know where X would fit.

| Atom | What it checks | Proposed shape | Priority |
|---|---|---|---|
| `goal_coverage` | Multi-part request fully answered | No args; atom looks at user turn + response | 🟢 High |
| `no_secrets_leaked(env_vars)` | Response doesn't echo configured env var values | Takes list of var names (or auto-reads env) | 🟢 Coding agents |
| `code_safety` | Generated code free of obvious RCE, SQL injection, path traversal | No args | 🟡 Coding agents |
| `citation_present` | Factual claims have references | No args; judge checks per sentence | 🟡 Research agents |
| `appropriate_disclaimer(domain)` | Medical/legal/financial advice includes "consult a professional" | Takes domain name | 🟡 Regulated domains |
| `tone_match(tone)` | Response matches required tone (atom form, replacing closure) | Takes tone name | 🟢 Common |
| `brand_voice(voice_description)` | Matches company brand guidelines | Takes voice description | 🟡 Consumer-facing |
| `promise_bound(company_policy)` | No commitments outside stated policy | Takes policy description | 🟡 Commerce/CS |
| `role_respect(assigned_role)` | Agent doesn't take on another agent's role | Takes role description | 🟡 Multi-agent |
| `reasoning_shown` | High-stakes response includes reasoning before conclusion | No args | 🟡 Advisory agents |
| `consent_honored` | If user said "stop" / "don't", agent complies | No args; checks agent behaviour after consent signal | 🔵 Niche |
| `refusal_respected` | Agent doesn't retry a previously refused request | **Det** via trace pattern — not sto | 🔵 Use det |
| `confidence_threshold` | Response doesn't hedge ("I think", "probably") on tools that need certainty | **Det** via regex — not sto | 🔵 Use det |

### Why some things are det, not sto

Atoms marked 🔵 should **not** be sto — they're precisely detectable:

- `refusal_respected` → det pattern `no_reversal(refuse_X, attempt_X)`
- `confidence_threshold` → det pattern `arg_blacklist(tool, arg, ["probably", "I think", "maybe"])`

Using sto (LLM judge) for precisely detectable properties wastes judge calls and adds false-positive risk. See §1 "Relationship to det patterns" above.

---

## 4. Picking α / β for a new atom

See `docs/cost-based-thresholds.md` for the full framework. Quick guide:

| Atom | Typical β | Reasoning |
|---|---|---|
| `injection_free`, `jailbreak_free` | 0.9–0.95 | High FN cost (breach), low FP cost (retry) |
| `semantic_pii_free` | 0.95+ | Compliance-sensitive |
| `harmful` | 0.98 | Safety-critical, high FN cost |
| `hallucination_free`, `faithfulness` | 0.85–0.92 | Balanced |
| `scope_respect` | 0.85 | FP is real (borderline on-topic) |
| `tone_match`, `brand_voice` | 0.65–0.8 | Low-stakes UX signal |
| `toxic_free` | 0.9 | High public-facing cost if missed |

Or just use a `risk_profile`:

```yaml
- E:
    pattern: injection_free
  risk_profile: cautious                   # → α=0.7, β=0.95
```

---

## 5. Writing a custom sto atom

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

## 6. Summary — what to actually implement

Working backward from user value:

| Tier | Atoms | Status | Reason |
|---|---|---|---|
| **Already shipped** (R1) | `injection_free`, `jailbreak_free`, `toxic_free`, `semantic_pii_free`, `scope_respect`, `hallucination_free` | ✅ | Covers the 6 most-requested failure modes |
| **Adding now** (R1b) | `harmful`, `faithfulness`, `goal_coverage`, `tone_match` (atom form) | 🟡 | Fills gaps: broader harm semantic, positive grounding, multi-part completion, brand voice |
| **Defer** (R1c) | `no_secrets_leaked`, `code_safety`, `citation_present`, `appropriate_disclaimer`, `brand_voice`, `promise_bound`, `role_respect`, `reasoning_shown` | 📋 | Add when a specific customer / integration requests it |
| **Not sto** | `refusal_respected`, `confidence_threshold` | 🔵 | These belong in det — add as patterns in `library.py`, not here |

The working rule: **ship atoms when they unblock a real demo or customer. Don't pre-ship speculatively — the judge-call cost is permanent.**

---

**Related:** [Quick start](../QUICKSTART.md) · [Contract DSL](contracts.md) · [CLI Reference](cli.md) · [Integrations](integrations.md) · [Architecture](architecture.md) · [OWASP Agentic Top 10](owasp-agentic-top-10.md)
