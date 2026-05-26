/**
 * OpenAI Agents SDK integration (``@openai/agents``) — native TypeScript.
 *
 * The Agents SDK is the "native TS" counterpart to Python's
 * ``openai-agents``. It expresses tools as objects with an async
 * ``execute`` (or ``invoke``) function; this adapter intercepts that
 * function with ``guardBefore`` / ``guardAfter`` so contracts run at
 * the action boundary — the same shape as our LangChain and Vercel
 * adapters.
 *
 * Usage::
 *
 *   import { Agent, run, tool } from "@openai/agents";
 *   import { Sponsio } from "@sponsio/sdk";
 *   import { wrapAgentsTools } from "@sponsio/sdk/openai-agents";
 *
 *   const guard = new Sponsio({ config: "sponsio.yaml", agentId: "support" });
 *   const tools = wrapAgentsTools([refundTool, lookupTool, …], guard);
 *   const agent = new Agent({ name: "support", tools });
 *
 * ``wrapAgentsTools`` is non-destructive: it returns a new array of
 * wrapped tool objects. The originals are unmodified so the same
 * tool can be reused across agents or tests without leftover state.
 *
 * Blocked calls throw a thrown ``Error`` with the Sponsio violation
 * message — the Agents SDK surfaces this as a tool failure the model
 * can react to. If your runtime prefers a structured tool-result
 * error instead, wrap the call site.
 */

import type { Sponsio } from "../index.js";

/**
 * Structural shape we rely on: a ``name`` and some sort of async
 * executable. We probe for the Agents SDK's current field name
 * (``execute``) and fall back to ``invoke`` for runtimes that follow
 * the LangChain convention. If neither is present the tool is
 * returned unchanged and a one-shot warning is emitted.
 */
interface AgentsToolLike {
  name: string;
  execute?: (...args: unknown[]) => unknown;
  invoke?: (...args: unknown[]) => unknown;
}

let warnedUnwrappable = false;

export function wrapAgentsTools<T extends AgentsToolLike>(
  tools: T[],
  guard: Sponsio,
): T[] {
  return tools.map((tool) => {
    const field: "execute" | "invoke" | null = tool.execute
      ? "execute"
      : tool.invoke
        ? "invoke"
        : null;
    if (!field) {
      if (!warnedUnwrappable) {
        warnedUnwrappable = true;
        console.warn(
          `[sponsio] wrapAgentsTools: tool '${tool.name}' has neither ` +
            `.execute nor .invoke — returning unchanged`,
        );
      }
      return tool;
    }
    const original = tool[field]!.bind(tool);
    const wrapped = async (...args: unknown[]): Promise<unknown> => {
      const input = args[0];
      const argsObj =
        typeof input === "object" && input !== null
          ? (input as Record<string, unknown>)
          : { input };
      const check = guard.guardBefore(tool.name, argsObj);
      if (check.blocked) {
        throw new Error(check.message);
      }
      const output = await original(...args);
      const asStr =
        typeof output === "string" ? output : safeStringify(output);
      // guardAfter is a no-op in this build (sto pipeline is not
      // supported, the engine is deterministic-only). External
      // subclasses surface tone / llm_judge / injection_free
      // violations here; we propagate them via a thrown Error so the
      // Agents SDK routes them as a tool failure the model can see and
      // react to, matching the pre-check block path above.
      const afterCheck = await guard.guardAfter(tool.name, asStr);
      if (afterCheck.blocked) {
        throw new Error(afterCheck.message);
      }
      return output;
    };
    // Clone so we don't mutate the user's original tool object.
    const next = { ...tool, [field]: wrapped } as T;
    return next;
  });
}

function safeStringify(v: unknown): string {
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}
