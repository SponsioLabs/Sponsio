/**
 * LangChain.js tool extractor.
 *
 * Matches:
 *
 *   new DynamicStructuredTool({ name, description, schema, func })
 *   new DynamicTool({ name, description, func })
 *   tool(fn, { name, description, schema })       — LangGraph.js v0.2+
 *
 * The ``schema`` field is a Zod object we convert via ``zod.ts``.
 */

import { Node, SourceFile } from "ts-morph";
import { objectArgToTool, inferNameFromAssignment } from "./common";
import type { OpenAITool, ToolProvenance } from "../types";

const STRUCTURED_NAMES = new Set([
  "DynamicStructuredTool",
  "StructuredTool",
]);
const BASIC_NAMES = new Set(["DynamicTool"]);

export function extractLangChainTools(
  sourceFile: SourceFile
): { tool: OpenAITool; provenance: ToolProvenance }[] {
  const found: { tool: OpenAITool; provenance: ToolProvenance }[] = [];

  // --- 1.  new DynamicStructuredTool({...}) / new DynamicTool({...}) ---
  sourceFile.forEachDescendant((node) => {
    if (!Node.isNewExpression(node)) return;
    const ctor = node.getExpression();
    if (!Node.isIdentifier(ctor)) return;

    const ctorName = ctor.getText();
    if (!STRUCTURED_NAMES.has(ctorName) && !BASIC_NAMES.has(ctorName)) return;

    const arg = node.getArguments()[0];
    if (!arg || !Node.isObjectLiteralExpression(arg)) return;

    const inferred = inferNameFromAssignment(node);
    const tool = objectArgToTool(arg, inferred, {
      schemaKeys: ["schema", "parameters", "inputSchema"],
    });
    if (!tool) return;

    found.push({
      tool,
      provenance: {
        filepath: sourceFile.getFilePath(),
        line: node.getStartLineNumber(),
        extractor: "langchain_js",
      },
    });
  });

  // --- 2.  tool(fn, { name, description, schema }) — LangGraph.js v0.2+
  //
  // Distinguished from Vercel's ``tool({...})`` by the arity and the
  // shape of the *first* argument: LangGraph takes the function first
  // and the config second; Vercel passes a single object.
  sourceFile.forEachDescendant((node) => {
    if (!Node.isCallExpression(node)) return;
    const callee = node.getExpression();
    if (!Node.isIdentifier(callee) || callee.getText() !== "tool") return;

    const args = node.getArguments();
    if (args.length < 2) return;

    const config = args[1];
    if (!Node.isObjectLiteralExpression(config)) return;

    const inferred = inferNameFromAssignment(node);
    const tool = objectArgToTool(config, inferred, {
      schemaKeys: ["schema", "parameters", "inputSchema"],
    });
    if (!tool) return;

    found.push({
      tool,
      provenance: {
        filepath: sourceFile.getFilePath(),
        line: node.getStartLineNumber(),
        extractor: "langchain_js",
      },
    });
  });

  return found;
}
