// Fixture: LangChain.js-style tool definitions.

import { DynamicStructuredTool, DynamicTool } from "@langchain/core/tools";
import { z } from "zod";

export const issueRefund = new DynamicStructuredTool({
  name: "issue_refund",
  description: "Issue a refund for a given order.",
  schema: z.object({
    order_id: z.string(),
    amount: z.number(),
  }),
  func: async ({ order_id }: { order_id: string }) => `refunded ${order_id}`,
});

export const checkPolicy = new DynamicTool({
  name: "check_policy",
  description: "Check whether an order is eligible for refund.",
  func: async (input: string) => "eligible",
});

// Inferred name from assignment (no explicit ``name`` key)
export const transferFunds = new DynamicStructuredTool({
  description: "Transfer money between two accounts.",
  schema: z.object({
    from: z.string(),
    to: z.string(),
    amount: z.number(),
  }),
  func: async () => "ok",
});
