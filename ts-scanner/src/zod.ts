/**
 * Static Zod-expression → JSON-Schema converter.
 *
 * We never execute the user's code — we read the TypeScript AST and
 * translate common Zod call patterns:
 *
 *   z.object({ user_id: z.string(), count: z.number().int() })
 *
 * becomes:
 *
 *   { type: "object",
 *     properties: {
 *       user_id: { type: "string" },
 *       count:   { type: "integer" }
 *     },
 *     required: ["user_id", "count"] }
 *
 * Unknown Zod calls degrade to ``{ type: "string" }`` so the tool
 * still shows up in the inventory — downstream heuristics care more
 * about *which* param names exist than their exact types.
 */

import {
  CallExpression,
  Expression,
  Node,
  ObjectLiteralExpression,
  PropertyAssignment,
  SyntaxKind,
} from "ts-morph";
import type { JSONSchema, JSONSchemaProperty, JSONSchemaType } from "./types";

const PRIMITIVE_MAP: Record<string, JSONSchemaType> = {
  string: "string",
  number: "number",
  bigint: "integer",
  boolean: "boolean",
  date: "string",
  null: "null",
  any: "string",
  unknown: "string",
  never: "string",
  void: "null",
};

/**
 * Convert a Zod expression (``z.object({ ... })``, ``z.string()``, etc.)
 * into a JSON Schema property.  Returns ``{ type: "string" }`` for
 * anything we can't statically decode — the param *name* carries most
 * of the signal for the downstream Sponsio heuristics.
 */
export function zodExprToJsonSchema(expr: Expression): JSONSchemaProperty {
  const call = unwrapChain(expr);
  if (!call) return { type: "string" };

  const head = getHeadMethod(call);
  if (!head) return { type: "string" };

  // ── z.object({...}) — the only compound shape we emit as ``object``.
  if (head === "object") {
    const firstArg = call.getArguments()[0];
    if (firstArg && Node.isObjectLiteralExpression(firstArg)) {
      const nested = objectLiteralToJsonSchema(firstArg);
      return {
        type: "object",
        properties: nested.properties,
      };
    }
    return { type: "object", properties: {} };
  }

  // ── z.array(<inner>) → { type: "array", items: ... }
  if (head === "array") {
    const inner = call.getArguments()[0];
    if (inner && Node.isExpression(inner)) {
      return { type: "array", items: zodExprToJsonSchema(inner) };
    }
    return { type: "array", items: { type: "string" } };
  }

  // ── z.enum([...]) → { type: "string", enum: [...] }
  if (head === "enum") {
    const arr = call.getArguments()[0];
    if (arr && Node.isArrayLiteralExpression(arr)) {
      const values = arr
        .getElements()
        .map((e) => literalValue(e))
        .filter((v): v is string | number | boolean => v !== undefined);
      return { type: "string", enum: values };
    }
    return { type: "string" };
  }

  // ── z.literal(x) → narrow type + enum
  if (head === "literal") {
    const arg = call.getArguments()[0];
    if (arg && Node.isExpression(arg)) {
      const lit = literalValue(arg);
      if (typeof lit === "string") return { type: "string", enum: [lit] };
      if (typeof lit === "number") return { type: "number", enum: [lit] };
      if (typeof lit === "boolean") return { type: "boolean", enum: [lit] };
    }
    return { type: "string" };
  }

  // ── Primitive: z.string(), z.number(), z.boolean(), ...
  const prim = PRIMITIVE_MAP[head];
  if (prim) {
    const base: JSONSchemaProperty = { type: prim };
    // Walk the method chain beyond the primitive to catch ``.int()``
    // and similar refinements.
    if (head === "number" && chainContains(expr, "int")) {
      base.type = "integer";
    }
    return base;
  }

  // Unknown method — degrade gracefully.
  return { type: "string" };
}

/**
 * Translate a ``z.object({...})`` inner literal into a top-level
 * JSON Schema.
 */
export function objectLiteralToJsonSchema(
  literal: ObjectLiteralExpression
): JSONSchema {
  const properties: Record<string, JSONSchemaProperty> = {};
  const required: string[] = [];

  for (const prop of literal.getProperties()) {
    if (!Node.isPropertyAssignment(prop)) continue;
    const key = (prop as PropertyAssignment).getName().replace(/^['"`]|['"`]$/g, "");
    const init = (prop as PropertyAssignment).getInitializer();
    if (!init) continue;
    const schema = zodExprToJsonSchema(init);
    properties[key] = schema;
    if (!chainContains(init, "optional") && !chainContains(init, "nullish")) {
      required.push(key);
    }
  }

  const out: JSONSchema = { type: "object", properties };
  if (required.length) out.required = required;
  return out;
}

// ---------------------------------------------------------------------------
// AST walk helpers
// ---------------------------------------------------------------------------

/** Return the *deepest* CallExpression in a ``a.b().c().d()`` chain. */
function unwrapChain(expr: Expression): CallExpression | undefined {
  let cur: Node | undefined = expr;
  while (cur) {
    if (Node.isCallExpression(cur)) return cur;
    if (Node.isPropertyAccessExpression(cur)) {
      cur = cur.getExpression();
      continue;
    }
    return undefined;
  }
  return undefined;
}

/**
 * Return the *head* method name of a Zod chain —
 * ``z.string().min(3).optional()`` → ``"string"``.  This is the
 * identifier immediately following the root ``z.`` / variable.
 */
function getHeadMethod(call: CallExpression): string | undefined {
  let cur: Node = call;
  let last: string | undefined;
  while (cur) {
    if (Node.isCallExpression(cur)) {
      const callee = cur.getExpression();
      if (Node.isPropertyAccessExpression(callee)) {
        last = callee.getName();
        cur = callee.getExpression();
        continue;
      }
      if (Node.isIdentifier(callee)) {
        return callee.getText();
      }
      return last;
    }
    if (Node.isPropertyAccessExpression(cur)) {
      last = cur.getName();
      cur = cur.getExpression();
      continue;
    }
    return last;
  }
  return last;
}

/**
 * Does any node in the method chain (at any depth) call a method
 * whose name matches ``target``?  Used to detect ``.optional()`` and
 * ``.int()`` refiners.
 */
function chainContains(expr: Expression, target: string): boolean {
  let found = false;
  expr.forEachDescendant((node, traversal) => {
    if (
      Node.isCallExpression(node) &&
      Node.isPropertyAccessExpression(node.getExpression()) &&
      (node.getExpression() as any).getName() === target
    ) {
      found = true;
      traversal.stop();
    }
  });
  if (found) return true;
  // Also check the immediate expression itself
  if (Node.isCallExpression(expr)) {
    const callee = expr.getExpression();
    if (
      Node.isPropertyAccessExpression(callee) &&
      callee.getName() === target
    ) {
      return true;
    }
  }
  return false;
}

/** Extract a literal JS value from an AST node, or ``undefined``. */
function literalValue(
  node: Node
): string | number | boolean | undefined {
  if (Node.isStringLiteral(node) || Node.isNoSubstitutionTemplateLiteral(node)) {
    return node.getLiteralText();
  }
  if (Node.isNumericLiteral(node)) {
    return Number(node.getLiteralText());
  }
  const k = node.getKind();
  if (k === SyntaxKind.TrueKeyword) return true;
  if (k === SyntaxKind.FalseKeyword) return false;
  return undefined;
}
