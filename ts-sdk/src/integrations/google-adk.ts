/**
 * Google ADK integration (``@google/adk``) - native TypeScript.
 *
 * ADK TypeScript expresses custom tools as ``FunctionTool`` objects
 * with an ``execute`` callback. This adapter clones those tool objects
 * and intercepts ``execute`` with ``guardBefore`` / ``guardAfter`` so
 * Sponsio contracts run at the same boundary ADK already uses.
 *
 * Usage::
 *
 *   import { FunctionTool, LlmAgent } from "@google/adk";
 *   import { Sponsio } from "@sponsio/sdk";
 *   import { wrapGoogleAdkTools } from "@sponsio/sdk/google-adk";
 *
 *   const guard = new Sponsio({ config: "sponsio.yaml", agentId: "support" });
 *   const tools = wrapGoogleAdkTools([lookupTool, refundTool], guard);
 *   export const rootAgent = new LlmAgent({ name: "support", tools, ... });
 */

import type { Sponsio } from "../index.js";

interface GoogleAdkToolLike {
  name: string;
  execute?: (...args: unknown[]) => unknown;
}

let warnedUnwrappable = false;

export function wrapGoogleAdkTools<T extends GoogleAdkToolLike>(
  tools: T[],
  guard: Sponsio,
): T[] {
  return tools.map((tool) => wrapGoogleAdkTool(tool, guard));
}

export function wrapGoogleAdkTool<T extends GoogleAdkToolLike>(
  tool: T,
  guard: Sponsio,
): T {
  if (!tool.execute) {
    if (!warnedUnwrappable) {
      warnedUnwrappable = true;
      console.warn(
        `[sponsio] wrapGoogleAdkTools: tool '${tool.name}' has no ` +
          `.execute callback - returning unchanged`,
      );
    }
    return tool;
  }

  const original = tool.execute.bind(tool);
  const wrapped = async (...args: unknown[]): Promise<unknown> => {
    const callArgs = toArgsObject(args);
    const check = guard.guardBefore(tool.name, callArgs);
    if (check.blocked) {
      return blockedResult(check.message);
    }

    const output = await original(...args);
    const afterCheck = await guard.guardAfter(tool.name, stringify(output));
    if (afterCheck.blocked) {
      return blockedResult(afterCheck.message);
    }
    return output;
  };

  const next = Object.create(Object.getPrototypeOf(tool)) as T;
  Object.assign(next, tool);
  Object.defineProperty(next, "execute", {
    value: wrapped,
    writable: true,
    configurable: true,
    enumerable: true,
  });
  return next;
}

function toArgsObject(args: unknown[]): Record<string, unknown> {
  const first = args[0];
  if (typeof first === "object" && first !== null && !Array.isArray(first)) {
    return first as Record<string, unknown>;
  }
  if (args.length === 0) return {};
  return { args };
}

function blockedResult(message: string): Record<string, string> {
  return {
    status: "error",
    error_message: `BLOCKED by contract: ${message}`,
  };
}

function stringify(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
