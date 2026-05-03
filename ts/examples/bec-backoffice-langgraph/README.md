# BEC Backoffice Agent — LangGraph Variant

Same Business Email Compromise scenario as [`../bec-backoffice/`](../bec-backoffice/), built on **LangGraph** (`@langchain/langgraph`'s `createReactAgent`) instead of Vercel AI SDK. Demonstrates that Sponsio's contract enforcement is framework-agnostic — same `sponsio.yaml`, same canned trajectory, same B1 session-view output, just different agent SDK underneath.

## What's different from the Vercel variant

| | Vercel AI SDK variant | LangGraph variant |
|---|---|---|
| Agent SDK | `ai`, `@ai-sdk/google`'s `generateText` | `@langchain/langgraph`'s `createReactAgent` |
| Tool shape | `tool({ description, parameters: z.object(...), execute })` | `tool(async (args) => …, { name, description, schema })` |
| Sponsio integration | `sponsioMiddleware(guard)` wraps the language model | `wrapTools(tools, guard)` wraps each tool's `invoke` |
| Block reaches LLM as | model output dropped, finishReason=stop | tool returns `"BLOCKED by Sponsio: <reason>"` as a normal tool result, model can react |

The Sponsio yaml, the fixtures (inbox / vendors / employee-policy), the canned trajectory, and the session-view rendering are all **identical**. The only swap is the framework wiring.

## Two ways to run

### Deterministic demo (no API key)

```bash
cd ts && npm install                              # workspace install
cd examples/bec-backoffice-langgraph
npx tsx demo.ts
```

Same output shape as the Vercel variant: 11-step trace, C1 blocks `update_vendor_bank_account`, VERDICT BLOCKED, ACME bank unchanged, $12,500 preserved.

### LLM-driven run

```bash
cd ts && npm install
cd examples/bec-backoffice-langgraph
GOOGLE_API_KEY=AIza... npx tsx agent.ts
```

Default model is `gemini-2.0-flash` (same naive-on-purpose pick as the Vercel variant — newer Gemini refuses the BEC on its own and Sponsio never gets to fire).

## How Sponsio is wired in (LangGraph-specific)

Three steps in `agent.ts` to harden it (see the docstring at the top of `agent.ts` for the diff):

```ts
import { Sponsio } from "@sponsio/sdk";
import { wrapTools } from "@sponsio/sdk/langchain";

const guard = new Sponsio({ config: "sponsio.yaml", agentId: "backoffice" });
const guardedTools = wrapTools(tools, guard);
const agent = createReactAgent({ llm, tools: guardedTools });
// …after the agent run finishes…
guard.finishSession();
```

`wrapTools` clones each tool object and intercepts `.invoke()` with `guard.guardBefore` / `guard.guardAfter`. When Sponsio refuses an action, the wrapped invoke returns a string starting with `BLOCKED by Sponsio: …` — LangGraph treats that as a normal tool result, the model sees it on its next step, and it can decide what to do. Unlike the Vercel middleware path which short-circuits the LLM loop, the LangGraph integration keeps the agent running so the trace can continue past the block.

## Onboarding from scratch

If you want to walk the IDE-agent onboard flow on this folder:

```bash
cd ts/examples/bec-backoffice-langgraph
rm sponsio.yaml                                   # start fresh
npm i -D @sponsio/scan-ts
npx sponsio onboard . --emit-context
# then hand the JSON + `npx sponsio prompt onboard` template to your IDE agent
```

`onboard --emit-context` will detect this as a langgraph project from `package.json` and emit the LangGraph-flavoured wrap snippet in the payload (look for `wrap_snippet` in the JSON).
