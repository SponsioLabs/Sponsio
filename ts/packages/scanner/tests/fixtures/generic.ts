/* eslint-disable @typescript-eslint/no-unused-vars */
// Fixture: synthetic homegrown agent toolkit + a decorator-based
// API.  Neither pattern matches the Vercel AI or LangChain.js
// extractors, so the generic fallback is the only thing that should
// pick them up.

import { z } from "zod";

// --- Pattern 1: factory call ``createTool({...})``
export const lookupOrder = createTool({
  name: "lookup_order",
  description: "Look up an order by id.",
  parameters: z.object({
    order_id: z.string().describe("the order id"),
  }),
  execute: async (_args: { order_id: string }) => "ok",
});

// Member access: ``Sponsio.tool({...})`` — should also be caught.
export const cancelOrder = Sponsio.tool({
  name: "cancel_order",
  description: "Cancel an order.",
  schema: z.object({ order_id: z.string() }),
});

// Google ADK TypeScript exposes FunctionTool-shaped tool objects.
export const searchFlights = new FunctionTool({
  name: "search_flights",
  description: "Search available flights.",
  parameters: z.object({
    origin: z.string(),
    destination: z.string(),
  }),
  execute: async (_args: { origin: string; destination: string }) => "ok",
});

// Negative case: looks like a tool factory but only carries one of
// the shape keys (``id`` is not in TOOL_SHAPE_KEYS).  Generic
// extractor must NOT emit a tool entry here, otherwise random calls
// to ``tool()`` in user code would pollute the inventory.
export const notATool = createTool({ id: 5 });

// --- Pattern 2: ``@tool`` decorator on a method.
class CustomerAgent {
  /**
   * Fetch customer profile from CRM.
   */
  @tool
  async getCustomer(customer_id: string): Promise<string> {
    return "customer record";
  }

  // Decorator with config object — config wins over JSDoc.
  /** This JSDoc should be ignored when config provides description. */
  @tool({
    description: "Issue a refund (uses config.description).",
    parameters: z.object({ amount: z.number() }),
  })
  async refund(amount: number): Promise<string> {
    return "refunded";
  }
}

declare function createTool<T>(config: T): unknown;
declare class FunctionTool<T> {
  constructor(config: T);
}
declare const Sponsio: { tool: <T>(config: T) => unknown };
declare function tool(...args: any[]): any;
