/**
 * Shared helpers for the framework-specific extractors.
 */

import {
  CallExpression,
  Node,
  ObjectLiteralExpression,
  PropertyAssignment,
} from "ts-morph";
import { objectLiteralToJsonSchema, zodExprToJsonSchema } from "../zod";
import type { JSONSchema, OpenAITool } from "../types";

/**
 * Given a call/new-expression's first argument (which should be an
 * object literal like ``{ name, description, parameters }``), pull
 * out the fields we need and build an OpenAITool, or return
 * ``undefined`` when the shape doesn't look like a tool definition.
 */
export function objectArgToTool(
  obj: ObjectLiteralExpression,
  inferredName: string | undefined,
  opts: {
    nameKey?: string;
    descriptionKey?: string;
    /** Fields to try for the parameter schema, in priority order. */
    schemaKeys: string[];
  }
): OpenAITool | undefined {
  const nameKey = opts.nameKey ?? "name";
  const descKey = opts.descriptionKey ?? "description";

  const nameNode = findProperty(obj, nameKey);
  const descNode = findProperty(obj, descKey);

  let name: string | undefined = nameNode
    ? getStringInit(nameNode)
    : inferredName;
  if (!name) return undefined;

  const description = descNode ? getStringInit(descNode) ?? "" : "";

  let schema: JSONSchema = { type: "object", properties: {} };
  for (const key of opts.schemaKeys) {
    const prop = findProperty(obj, key);
    if (!prop) continue;
    const init = prop.getInitializer();
    if (!init) continue;
    // ``parameters: z.object({...})`` → full schema.  We call the
    // object-literal converter directly so we pick up the
    // ``required`` list, which ``zodExprToJsonSchema`` drops because
    // its return type is a *property* (no ``required`` field).
    if (Node.isCallExpression(init)) {
      const firstArg = init.getArguments()[0];
      if (firstArg && Node.isObjectLiteralExpression(firstArg)) {
        schema = objectLiteralToJsonSchema(firstArg);
        break;
      }
      // Fall back to the property-level converter; we still get
      // ``properties`` when the schema happens to be an object.
      const derived = zodExprToJsonSchema(init);
      if (derived.type === "object" && derived.properties) {
        schema = { type: "object", properties: derived.properties };
        break;
      }
    }
    // ``parameters: { a: z.string() }`` — plain object literal
    if (Node.isObjectLiteralExpression(init)) {
      schema = objectLiteralToJsonSchema(init);
      break;
    }
  }

  return {
    type: "function",
    function: { name, description, parameters: schema },
  };
}

export function findProperty(
  obj: ObjectLiteralExpression,
  key: string
): PropertyAssignment | undefined {
  for (const p of obj.getProperties()) {
    if (!Node.isPropertyAssignment(p)) continue;
    const n = p.getName().replace(/^['"`]|['"`]$/g, "");
    if (n === key) return p;
  }
  return undefined;
}

export function getStringInit(prop: PropertyAssignment): string | undefined {
  const init = prop.getInitializer();
  if (!init) return undefined;
  if (Node.isStringLiteral(init) || Node.isNoSubstitutionTemplateLiteral(init)) {
    return init.getLiteralText();
  }
  return undefined;
}

/**
 * Walk upwards from a call/new expression to find the variable name
 * it's being assigned to — e.g. for
 *     const lookupCustomer = tool({ ... })
 * return ``"lookupCustomer"``.
 */
export function inferNameFromAssignment(
  call: CallExpression | Node
): string | undefined {
  let cur: Node | undefined = call.getParent();
  while (cur) {
    if (Node.isVariableDeclaration(cur)) {
      const id = cur.getNameNode();
      if (Node.isIdentifier(id)) return id.getText();
      return undefined;
    }
    if (Node.isPropertyAssignment(cur)) {
      return cur.getName().replace(/^['"`]|['"`]$/g, "");
    }
    cur = cur.getParent();
  }
  return undefined;
}
