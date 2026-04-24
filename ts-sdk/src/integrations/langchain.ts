/**
 * LangChain.js / LangGraph.js integration — native TypeScript.
 *
 * Usage:
 *   import { Sponsio } from "@sponsio/sdk"
 *   import { wrapTools } from "@sponsio/sdk/langchain"
 *   import { ToolNode } from "@langchain/langgraph/prebuilt"
 *
 *   const guard = new Sponsio({ contracts: [...] })
 *   const toolNode = new ToolNode(wrapTools(tools, guard))
 */

import type { Sponsio } from "../index.js";

interface LangChainTool {
  name: string;
  invoke: (input: unknown, config?: unknown) => Promise<unknown>;
}

export function wrapTools<T extends LangChainTool>(tools: T[], guard: Sponsio): T[] {
  return tools.map((tool) => {
    const originalInvoke = tool.invoke.bind(tool);
    const toolName = tool.name;

    tool.invoke = async function (input: unknown, config?: unknown) {
      const args = typeof input === "string" ? { input } : (input as Record<string, unknown>);
      const result = guard.guardBefore(toolName, args);

      if (result.blocked) {
        return `BLOCKED by Sponsio: ${result.message}`;
      }

      const output = await originalInvoke(input, config);
      await guard.guardAfter(toolName, String(output));
      return output;
    } as typeof tool.invoke;

    return tool;
  });
}
