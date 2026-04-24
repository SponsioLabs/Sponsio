/**
 * OpenAI SDK integration — native TypeScript.
 *
 * Usage:
 *   import { Sponsio } from "@sponsio/sdk"
 *   import { wrapOpenAI, patchOpenAI } from "@sponsio/sdk/openai"
 *
 *   const guard = new Sponsio({ contracts: [...] })
 *   const client = wrapOpenAI(new OpenAI(), guard)
 *
 * ``patchOpenAI`` is an alias matching Python's ``patch_openai``
 * factory (same monkey-patch semantics — no wrapped return value
 * required; the client passed in is mutated in place).
 */

import type { Sponsio } from "../index.js";

export function wrapOpenAI(client: unknown, guard: Sponsio): unknown {
  const c = client as {
    chat: {
      completions: {
        create: (...args: unknown[]) => Promise<{
          choices?: Array<{
            message: {
              tool_calls?: Array<{
                function: { name: string; arguments: string };
              }> | null;
              content?: string | null;
            };
          }>;
        }>;
      };
    };
  };

  const original = c.chat.completions.create.bind(c.chat.completions);

  c.chat.completions.create = async function (...args: unknown[]) {
    const response = await original(...args);

    for (const choice of response.choices ?? []) {
      const msg = choice.message;
      if (!msg?.tool_calls) continue;

      const kept: typeof msg.tool_calls = [];
      const blocked: string[] = [];

      for (const tc of msg.tool_calls) {
        let tcArgs: Record<string, unknown> = {};
        try {
          tcArgs = JSON.parse(tc.function.arguments || "{}");
        } catch { /* empty */ }

        const result = guard.guardBefore(tc.function.name, tcArgs);
        if (result.blocked) {
          blocked.push(`[BLOCKED] ${tc.function.name}: ${result.message}`);
        } else {
          kept.push(tc);
        }
      }

      msg.tool_calls = kept.length > 0 ? kept : null;
      if (blocked.length > 0 && !msg.tool_calls) {
        // Match Python's ``_filter_blocked_calls``: join with a newline
        // separator but no leading newline before the first message.
        msg.content = (msg.content ?? "") + blocked.join("\n");
      }
    }

    return response;
  } as typeof original;

  return client;
}

/**
 * Alias for ``wrapOpenAI`` — matches Python's
 * ``from sponsio.openai import patch_openai`` naming. Both mutate
 * the passed client in place and return it; use whichever name
 * reads better alongside the Python snippet you're porting.
 */
export const patchOpenAI = wrapOpenAI;
