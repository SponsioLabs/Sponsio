/**
 * LangChain.js / LangGraph.js integration — native TypeScript.
 *
 * LangChain.js tools expose an ``invoke(input, config?)`` method. This
 * adapter clones tool objects and intercepts ``invoke`` with
 * ``guardBefore`` / ``guardAfter`` so Sponsio contracts run at the same
 * boundary LangChain already uses. The original tools are not mutated,
 * so it's safe to share them between guarded and unguarded code paths.
 *
 * Usage:
 *   import { Sponsio } from "@sponsio/sdk";
 *   import { wrapTools } from "@sponsio/sdk/langchain";
 *   import { ToolNode } from "@langchain/langgraph/prebuilt";
 *
 *   const guard = new Sponsio({ contracts: [...] });
 *   const toolNode = new ToolNode(wrapTools(tools, guard));
 */

import type { Sponsio } from "../index.js";

interface LangChainToolLike {
  name: string;
  invoke?: (input: unknown, config?: unknown) => unknown;
}

let warnedUnwrappable = false;

export function wrapTools<T extends LangChainToolLike>(
  tools: T[],
  guard: Sponsio,
): T[] {
  return tools.map((tool) => wrapTool(tool, guard));
}

export function wrapTool<T extends LangChainToolLike>(
  tool: T,
  guard: Sponsio,
): T {
  if (!tool.invoke) {
    if (!warnedUnwrappable) {
      warnedUnwrappable = true;
      console.warn(
        `[sponsio] wrapTools: tool '${tool.name}' has no .invoke method ` +
          `- returning unchanged`,
      );
    }
    return tool;
  }

  const original = tool.invoke.bind(tool);
  const toolName = tool.name;
  const wrapped = async (input: unknown, config?: unknown): Promise<unknown> => {
    const args = toArgsObject(input);
    const check = guard.guardBefore(toolName, args);
    if (check.blocked) {
      return `BLOCKED by Sponsio: ${check.message}`;
    }

    const output = await original(input, config);
    const afterCheck = await guard.guardAfter(toolName, stringify(output));
    if (afterCheck.blocked) {
      return `BLOCKED by Sponsio: ${afterCheck.message}`;
    }
    return output;
  };

  const next = Object.create(Object.getPrototypeOf(tool)) as T;
  Object.assign(next, tool);
  Object.defineProperty(next, "invoke", {
    value: wrapped,
    writable: true,
    configurable: true,
    enumerable: true,
  });
  return next;
}

function toArgsObject(input: unknown): Record<string, unknown> {
  if (typeof input === "string") return { input };
  if (typeof input === "object" && input !== null && !Array.isArray(input)) {
    return input as Record<string, unknown>;
  }
  return { input };
}

function stringify(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
