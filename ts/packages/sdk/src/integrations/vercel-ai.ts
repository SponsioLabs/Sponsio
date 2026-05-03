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
 *
 * Behaviour:
 *   - guardBefore is called for every tool call the model emits.
 *   - Allowed calls pass through untouched.
 *   - Blocked calls are *dropped* from ``result.toolCalls`` (so the AI
 *     SDK's parseToolCall doesn't reject the response with NoSuchToolError),
 *     and a `[Sponsio blocked: <reason>]` line is appended to the
 *     model's text output. If every emitted call was blocked, the
 *     finishReason is forced to ``stop`` so the agent loop terminates
 *     instead of spinning on an empty turn.
 *   - The model's args are JSON-stringified at the language-model layer;
 *     the middleware parses defensively before handing them to the
 *     guard so the contract evaluator sees a real object.
 */

import type { Sponsio } from "../index.js";

interface ToolCallV1 {
  toolCallType: "function";
  toolCallId: string;
  toolName: string;
  args: unknown; // string | Record<string, unknown> across SDK versions; parsed defensively
}

interface GenerateResult {
  text?: string;
  toolCalls?: ToolCallV1[];
  finishReason?: string;
  [k: string]: unknown;
}

function parseArgs(raw: unknown): Record<string, unknown> {
  if (raw == null) return {};
  if (typeof raw === "object" && !Array.isArray(raw)) return raw as Record<string, unknown>;
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : {};
    } catch {
      return {};
    }
  }
  return {};
}

export function sponsioMiddleware(guard: Sponsio) {
  return {
    transformParams: async ({ params }: { params: unknown }) => params,

    wrapGenerate: async ({
      doGenerate,
    }: {
      doGenerate: () => Promise<GenerateResult>;
      params: unknown;
    }): Promise<GenerateResult> => {
      const result = await doGenerate();
      const calls = result.toolCalls ?? [];
      if (calls.length === 0) return result;

      const surviving: ToolCallV1[] = [];
      const blockedReasons: string[] = [];
      for (const tc of calls) {
        const check = guard.guardBefore(tc.toolName, parseArgs(tc.args));
        if (check.blocked) {
          // Strip the leading "BLOCKED: agent.tool — det constraint violated: "
          // prefix and any "det constraint violated:" / "violated:" boilerplate
          // so the appended text is concise; full message stays in the session log.
          const msg = check.message ?? `${tc.toolName} blocked by Sponsio`;
          const trimmed = msg
            .replace(/^[A-Z-]*BLOCKED:\s*[^—]+—\s*/, "")
            .replace(/^(?:det\s+constraint\s+)?violated:\s*/i, "");
          blockedReasons.push(`${tc.toolName}: ${trimmed.split("\n")[0]}`);
        } else {
          surviving.push(tc);
        }
      }

      if (blockedReasons.length === 0) return result;

      const blockNote = `[Sponsio blocked: ${blockedReasons.join("; ")}]`;
      const newText = result.text ? `${result.text}\n\n${blockNote}` : blockNote;
      return {
        ...result,
        toolCalls: surviving,
        text: newText,
        finishReason: surviving.length === 0 ? "stop" : result.finishReason,
      };
    },

    wrapStream: async ({ doStream }: { doStream: () => Promise<unknown> }) => {
      // Stream support is intentionally pass-through for now; the
      // stream parts API is more involved and the demo / OSS hot
      // path uses generateText. We can add full stream filtering in
      // a follow-up without changing the wrapGenerate contract.
      return doStream();
    },
  };
}
