// Fixture: Vercel AI SDK-style tool definitions.  Not exercised at
// runtime by the tests — ``ts-morph`` parses the source directly.

import { tool } from "ai";
import { z } from "zod";

export const lookupCustomer = tool({
  description: "Fetch a customer record by their ID",
  parameters: z.object({
    user_id: z.string(),
    include_archived: z.boolean().optional(),
  }),
  execute: async ({ user_id }: { user_id: string }) => ({ user_id }),
});

export const deleteAccount = tool({
  description: "Permanently delete a user account — IRREVERSIBLE",
  parameters: z.object({
    user_id: z.string(),
    confirm: z.literal(true),
  }),
  execute: async () => "deleted",
});

// Tool with nested/advanced Zod usage — degrades to string but still appears.
export const listOrders = tool({
  description: "List orders matching a filter",
  parameters: z.object({
    status: z.enum(["pending", "shipped", "delivered"]),
    limit: z.number().int(),
  }),
  execute: async () => [],
});
