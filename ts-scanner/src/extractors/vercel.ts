/**
 * Vercel AI SDK tool extractor.
 *
 * Matches ``tool({ description, parameters, execute })`` — the shape
 * documented at https://sdk.vercel.ai/docs/ai-sdk-core/tools.  The
 * ``name`` is usually inferred from the variable the call is assigned
 * to (``const lookupCustomer = tool({...})``) because Vercel's SDK
 * uses the object key in ``tools: { foo: tool({...}) }`` as the
 * canonical tool name.
 */

import { Node, SourceFile } from "ts-morph";
import { inferNameFromAssignment, objectArgToTool } from "./common";
import type { OpenAITool, ToolProvenance } from "../types";

export function extractVercelTools(
  sourceFile: SourceFile
): { tool: OpenAITool; provenance: ToolProvenance }[] {
  const found: { tool: OpenAITool; provenance: ToolProvenance }[] = [];

  sourceFile.forEachDescendant((node) => {
    if (!Node.isCallExpression(node)) return;

    const callee = node.getExpression();
    if (!Node.isIdentifier(callee)) return;
    if (callee.getText() !== "tool") return;

    const arg = node.getArguments()[0];
    if (!arg || !Node.isObjectLiteralExpression(arg)) return;

    const name = inferNameFromAssignment(node);
    const tool = objectArgToTool(arg, name, {
      schemaKeys: ["parameters", "inputSchema"],
    });
    if (!tool) return;
    found.push({
      tool,
      provenance: {
        filepath: sourceFile.getFilePath(),
        line: node.getStartLineNumber(),
        extractor: "vercel_ai",
      },
    });
  });

  return found;
}
