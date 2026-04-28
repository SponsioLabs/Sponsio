/**
 * Generic / framework-agnostic tool extractor.
 *
 * Catches the long-tail of homegrown agent toolkits and lesser-known
 * libraries that follow the same conventions as Vercel AI / LangChain
 * but use a slightly different name.  This deliberately runs *after*
 * the framework-specific extractors so that the more-specific ones
 * win (their provenance is more useful: ``"vercel_ai"`` vs
 * ``"generic"``).
 *
 * Two patterns are recognised:
 *
 * 1. **Decorator** — ``@tool`` / ``@createTool`` / ``@defineTool`` /
 *    ``@registerTool`` applied to a function declaration or class
 *    method.  Function name → tool name, leading JSDoc → description,
 *    first object-literal argument to the decorator (if any) carries
 *    the schema / overrides.
 *
 * 2. **Call expression** — ``createTool({...})``, ``defineTool({...})``,
 *    ``makeTool({...})``, ``registerTool({...})``, ``tool({...})``
 *    — anything that *looks* like a tool factory.  The ``tool``
 *    identifier overlaps with Vercel AI; the dedupe layer in
 *    ``index.ts`` (keyed on name + file + line) silently discards
 *    the duplicate.
 *
 * The fallback is conservative: an object literal must carry at
 * least two of ``{name, description, schema/parameters/inputSchema}``
 * before we treat it as a tool, otherwise an unrelated function call
 * like ``tool({ id: 5 })`` would emit garbage entries.
 */

import {
  Decorator,
  FunctionDeclaration,
  MethodDeclaration,
  Node,
  ObjectLiteralExpression,
  SourceFile,
} from "ts-morph";
import {
  findProperty,
  inferNameFromAssignment,
  objectArgToTool,
} from "./common";
import type { JSONSchema, OpenAITool, ToolProvenance } from "../types";

/** Identifier names that look like a tool factory. */
const FACTORY_NAMES = new Set([
  "tool",
  "createTool",
  "defineTool",
  "makeTool",
  "registerTool",
  "buildTool",
  "Tool",
  "FunctionTool",
]);

/** Decorator names treated as a tool registration marker. */
const DECORATOR_NAMES = FACTORY_NAMES;

/** Required-fields heuristic: the object literal must carry at least
 *  two of these keys.  Without this guard a benign call like
 *  ``tool({ id: 5 })`` would produce a bogus tool entry. */
const TOOL_SHAPE_KEYS = [
  "name",
  "description",
  "parameters",
  "schema",
  "inputSchema",
];

function looksLikeToolObject(obj: ObjectLiteralExpression): boolean {
  let hits = 0;
  for (const key of TOOL_SHAPE_KEYS) {
    if (findProperty(obj, key)) hits += 1;
    if (hits >= 2) return true;
  }
  return false;
}

function jsdocDescription(node: Node): string {
  // ``getJsDocs`` exists on most declaration nodes — guarded so we
  // can call it from the union type without a ts-morph cast.
  const getter = (node as any).getJsDocs;
  if (typeof getter !== "function") return "";
  const docs = getter.call(node);
  if (!Array.isArray(docs) || docs.length === 0) return "";
  const text: string = docs[0].getDescription?.() ?? "";
  return text.trim();
}

/** Extract via ``createTool({...})`` / ``tool({...})`` / ``new FunctionTool({...})`` / etc. */
function extractCallExpressions(
  sourceFile: SourceFile
): { tool: OpenAITool; provenance: ToolProvenance }[] {
  const found: { tool: OpenAITool; provenance: ToolProvenance }[] = [];

  sourceFile.forEachDescendant((node) => {
    if (!Node.isCallExpression(node) && !Node.isNewExpression(node)) return;

    const callee = node.getExpression();
    let calleeName: string | undefined;
    if (Node.isIdentifier(callee)) calleeName = callee.getText();
    // Member access like ``Sponsio.tool({...})`` — use the property
    // segment so dotted exports still get picked up.
    else if (Node.isPropertyAccessExpression(callee))
      calleeName = callee.getName();
    if (!calleeName || !FACTORY_NAMES.has(calleeName)) return;

    const arg = node.getArguments()[0];
    if (!arg || !Node.isObjectLiteralExpression(arg)) return;
    if (!looksLikeToolObject(arg)) return;

    const name = inferNameFromAssignment(node);
    const tool = objectArgToTool(arg, name, {
      schemaKeys: ["parameters", "schema", "inputSchema"],
    });
    if (!tool) return;

    found.push({
      tool,
      provenance: {
        filepath: sourceFile.getFilePath(),
        line: node.getStartLineNumber(),
        extractor: "generic",
      },
    });
  });

  return found;
}

/** Extract via ``@tool`` / ``@createTool`` decorators. */
function extractDecorators(
  sourceFile: SourceFile
): { tool: OpenAITool; provenance: ToolProvenance }[] {
  const found: { tool: OpenAITool; provenance: ToolProvenance }[] = [];

  sourceFile.forEachDescendant((node) => {
    if (!Node.isDecorator(node)) return;
    const dec = node as Decorator;

    // Resolve the decorator identifier: ``@tool`` or ``@tool({...})``
    let decName: string | undefined;
    const exprText = dec.getExpression();
    if (Node.isIdentifier(exprText)) decName = exprText.getText();
    else if (Node.isCallExpression(exprText)) {
      const inner = exprText.getExpression();
      if (Node.isIdentifier(inner)) decName = inner.getText();
    }
    if (!decName || !DECORATOR_NAMES.has(decName)) return;

    const target = dec.getParent();
    if (
      !Node.isFunctionDeclaration(target) &&
      !Node.isMethodDeclaration(target)
    ) {
      return;
    }
    const fn = target as FunctionDeclaration | MethodDeclaration;
    const name = fn.getName();
    if (!name) return;

    // Description: first try the decorator's config object (if it's
    // a call), then fall back to a JSDoc comment on the function.
    let description = "";
    let schema: JSONSchema = { type: "object", properties: {} };
    if (Node.isCallExpression(exprText)) {
      const cfg = exprText.getArguments()[0];
      if (cfg && Node.isObjectLiteralExpression(cfg)) {
        const built = objectArgToTool(cfg, name, {
          schemaKeys: ["parameters", "schema", "inputSchema"],
        });
        if (built) {
          description = built.function.description;
          schema = built.function.parameters;
        }
      }
    }
    if (!description) description = jsdocDescription(fn);

    found.push({
      tool: {
        type: "function",
        function: { name, description, parameters: schema },
      },
      provenance: {
        filepath: sourceFile.getFilePath(),
        line: fn.getStartLineNumber(),
        extractor: "generic_decorator",
      },
    });
  });

  return found;
}

export function extractGenericTools(
  sourceFile: SourceFile
): { tool: OpenAITool; provenance: ToolProvenance }[] {
  return [
    ...extractCallExpressions(sourceFile),
    ...extractDecorators(sourceFile),
  ];
}
