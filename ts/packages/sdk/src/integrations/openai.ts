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
        let parseFailed = false;
        try {
          tcArgs = JSON.parse(tc.function.arguments || "{}");
        } catch {
          parseFailed = true;
        }

        // Malformed arguments bypass any arg-based contract
        // (``arg_blacklist``, ``arg_length_limit``, ``scope_limit``,
        // ``dangerous_sql_verbs``, ``arg_value_range``) if we treat
        // them as empty. Feed a sentinel so the guard still sees
        // *something* distinctive, and block-by-default so a
        // malformed payload doesn't silently slip through.
        if (parseFailed) {
          guard.guardBefore(tc.function.name, {
            _sponsio_malformed_args: tc.function.arguments ?? "",
          });
          blocked.push(
            `[BLOCKED] ${tc.function.name}: malformed JSON arguments — ` +
              `refusing to forward to tool loop (set ` +
              `SPONSIO_OPENAI_STRICT_TOOL_ARGS=0 to downgrade once the ` +
              `parity flag ships in TS)`,
          );
          continue;
        }

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
