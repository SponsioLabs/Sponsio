/**
 * Vercel AI SDK integration — native TypeScript.
 *
 * Usage:
 *   import { Sponsio } from "@sponsio/sdk"
 *   import { sponsioMiddleware } from "@sponsio/sdk/vercel-ai"
 *   import { wrapLanguageModel } from "ai"
 *
 *   const guard = new Sponsio({ contracts: [...] })
 *   const model = wrapLanguageModel({ model, middleware: sponsioMiddleware(guard) })
 */

import type { Sponsio } from "../index.js";

export function sponsioMiddleware(guard: Sponsio) {
  return {
    transformParams: async ({ params }: { params: unknown }) => params,

    wrapGenerate: async ({
      doGenerate,
    }: {
      doGenerate: () => Promise<{ toolCalls?: Array<{ toolName: string; args: Record<string, unknown> }> }>;
      params: unknown;
    }) => {
      const result = await doGenerate();
      if (result.toolCalls) {
        for (const tc of result.toolCalls) {
          const check = guard.guardBefore(tc.toolName, tc.args ?? {});
          if (check.blocked) {
            tc.toolName = `__blocked_${tc.toolName}`;
            tc.args = { _sponsio_blocked: true, _reason: check.message };
          }
        }
      }
      return result;
    },

    wrapStream: async ({ doStream }: { doStream: () => Promise<unknown> }) => {
      return doStream();
    },
  };
}
