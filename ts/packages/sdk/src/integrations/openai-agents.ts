/**
 * OpenAI Agents SDK integration (``@openai/agents``) — native TypeScript.
 *
 * The Agents SDK is the "native TS" counterpart to Python's
 * ``openai-agents``. ``tool({ execute })`` returns a ``FunctionTool``
 * whose only entry point is ``invoke(runContext, input, details)``: the
 * user's ``execute`` is captured in a closure and never exposed, and the
 * model's arguments arrive as ``input``, a JSON string, in the *second*
 * position. This adapter intercepts ``invoke`` and reads the arguments
 * from there, so contracts see what the model actually asked for and
 * not the run context.
 *
 * Tools that follow the LangChain convention (``execute(args)`` or
 * ``invoke(args)`` with the arguments first) are handled too; the
 * Agents SDK shape is recognised by its ``type: "function"`` marker.
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
 * Blocked calls throw an ``Error`` with the Sponsio violation
 * message — the Agents SDK surfaces this as a tool failure the model
 * can react to. If your runtime prefers a structured tool-result
 * error instead, wrap the call site.
 */

import type { Sponsio } from "../index.js";

/**
 * Structural shape we rely on: a ``name`` and some sort of async
 * executable. We probe for ``execute`` first and fall back to
 * ``invoke``. If neither is present the tool is returned unchanged and
 * a one-shot warning is emitted.
 */
interface AgentsToolLike {
  name: string;
  type?: string;
  execute?: (...args: unknown[]) => unknown;
  invoke?: (...args: unknown[]) => unknown;
}

let warnedUnwrappable = false;

/**
 * True for the object ``tool()`` from ``@openai/agents`` returns: it
 * carries ``type: "function"`` and dispatches through
 * ``invoke(runContext, input)``.
 */
function isAgentsSdkFunctionTool(tool: AgentsToolLike): boolean {
  return tool.type === "function" && typeof tool.invoke === "function";
}

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
    // Agents SDK: invoke(runContext, input, details) — the arguments are
    // the second parameter. Everything else: arguments first.
    const inputIndex = field === "invoke" && isAgentsSdkFunctionTool(tool) ? 1 : 0;
    const wrapped = async (...args: unknown[]): Promise<unknown> => {
      const argsObj = toArgsObject(args[inputIndex]);
      const check = guard.guardBefore(tool.name, argsObj);
      // ``stopOriginal`` covers a redirect verdict as well as a block:
      // this adapter has no substitution path, so a redirect refuses.
      if (check.stopOriginal) {
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
      if (afterCheck.stopOriginal) {
        throw new Error(afterCheck.message);
      }
      return output;
    };
    // Clone so we don't mutate the user's original tool object.
    const next = { ...tool, [field]: wrapped } as T;
    return next;
  });
}

/**
 * The model's arguments as an object. The Agents SDK hands them over as
 * a JSON string; a string that is not JSON is kept under
 * ``_sponsio_malformed_args`` so coarse regex contracts still see it.
 */
export function toArgsObject(input: unknown): Record<string, unknown> {
  if (typeof input === "string") {
    try {
      const parsed = JSON.parse(input);
      if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
      return { input: parsed };
    } catch {
      return { _sponsio_malformed_args: input };
    }
  }
  if (typeof input === "object" && input !== null && !Array.isArray(input)) {
    return input as Record<string, unknown>;
  }
  return { input };
}

function safeStringify(v: unknown): string {
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}
