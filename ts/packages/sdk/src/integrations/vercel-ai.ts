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
 *   - guardBefore runs for every tool call the model emits, on both
 *     ``generateText`` (``wrapGenerate``) and ``streamText``
 *     (``wrapStream``), before the AI SDK executes the tool.
 *   - Allowed calls pass through untouched.
 *   - Blocked calls are *dropped* (so the AI SDK never executes them and
 *     ``parseToolCall`` doesn't reject the response), and a
 *     ``[Sponsio blocked: <reason>]`` note is added to the model's text.
 *     If every emitted call was blocked, the finishReason is forced to
 *     ``stop`` so the agent loop terminates instead of spinning.
 *   - Both provider result shapes are handled: ``ai`` v4
 *     (``LanguageModelV1``: ``result.toolCalls[]`` with ``args``) and
 *     ``ai`` v5 and later (``LanguageModelV2`` / ``V3``:
 *     ``result.content[]`` with ``{ type: "tool-call", input }`` parts).
 *   - The model's args arrive JSON-stringified at the language-model
 *     layer; the middleware parses defensively before handing them to the
 *     guard so the contract evaluator sees a real object.
 */

import type { Sponsio } from "../index.js";

/** A tool call as either provider generation emits it. */
interface ToolCallLike {
  toolCallId: string;
  toolName: string;
  /** v4: JSON string (or object across SDK versions). */
  args?: unknown;
  /** v5+: JSON string. */
  input?: unknown;
  type?: string;
  toolCallType?: string;
}

interface GenerateResult {
  text?: string;
  toolCalls?: ToolCallLike[];
  content?: Array<Record<string, unknown>>;
  finishReason?: string;
  [k: string]: unknown;
}

function emitBanner(toolName: string, reason: string, agentId: string) {
  if (process.env.SPONSIO_NO_BANNER) return;
  const isTty = !!(process.stderr as unknown as { isTTY?: boolean }).isTTY;
  const c = (code: string, text: string) => (isTty ? `\x1b[${code}m${text}\x1b[0m` : text);
  const sep = "━".repeat(60);
  const lines = [
    "",
    c("2;36", `  ${sep}`),
    `  ${c("1;31", "BLOCKED")}  ${c("1", `${agentId}.${toolName}`)}`,
    `  ${c("2", "rule")}    ${reason}`,
    c("2;36", `  ${sep}`),
    "",
  ];
  process.stderr.write(lines.join("\n"));
}

/**
 * The model's arguments as an object. A string that is not JSON is kept
 * under ``_sponsio_malformed_args`` so coarse regex contracts still see
 * it instead of an empty object that passes every argument rule.
 */
export function parseArgs(raw: unknown): Record<string, unknown> {
  if (raw == null) return {};
  if (typeof raw === "object" && !Array.isArray(raw)) return raw as Record<string, unknown>;
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : { input: parsed };
    } catch {
      return { _sponsio_malformed_args: raw };
    }
  }
  return { input: raw };
}

/** Compact one-line reason for the appended note; full message stays in the session log. */
function trimReason(msg: string): string {
  // Strip prefix via indexOf rather than regex — every regex form that
  // matched the pattern ``^[A-Z-]*BLOCKED:\s*[^—]+—\s*`` kept tripping
  // CodeQL js/polynomial-redos (the ``\s*`` and ``[^—]+`` overlap on
  // spaces).  indexOf is O(n) with no backtracking.
  let trimmed = msg;
  const blockedIdx = trimmed.indexOf("BLOCKED:");
  if (blockedIdx >= 0 && blockedIdx <= 64) {
    const dashIdx = trimmed.indexOf("—", blockedIdx + "BLOCKED:".length);
    if (dashIdx >= 0) {
      trimmed = trimmed.slice(dashIdx + "—".length).trimStart();
    }
  }
  const head = trimmed.toLowerCase();
  if (head.startsWith("det constraint violated:")) {
    trimmed = trimmed.slice("det constraint violated:".length).trimStart();
  } else if (head.startsWith("violated:")) {
    trimmed = trimmed.slice("violated:".length).trimStart();
  }
  return trimmed.split("\n")[0];
}

type Decision = { blocked: false } | { blocked: true; reason: string };

/** Run one emitted tool call through the guard, before anything executes it. */
function decide(guard: Sponsio, tc: ToolCallLike): Decision {
  const check = guard.guardBefore(tc.toolName, parseArgs(tc.input ?? tc.args));
  // ``stopOriginal`` covers a redirect verdict as well as a block: the
  // middleware has no substitution path, so a redirect drops the call.
  if (!check.stopOriginal) return { blocked: false };
  const reason = trimReason(check.message ?? `${tc.toolName} blocked by Sponsio`);
  emitBanner(tc.toolName, reason, guard.agentId);
  return { blocked: true, reason };
}

function blockNote(reasons: string[]): string {
  return `[Sponsio blocked: ${reasons.join("; ")}]`;
}

export function sponsioMiddleware(guard: Sponsio) {
  return {
    transformParams: async ({ params }: { params: any }): Promise<any> => params,

    wrapGenerate: async ({
      doGenerate,
    }: {
      doGenerate: () => any;
      params: any;
    }): Promise<any> => {
      const result: GenerateResult = await doGenerate();

      // ai v5+: tool calls are ``tool-call`` parts inside ``content``.
      if (Array.isArray(result.content)) {
        const content: Array<Record<string, unknown>> = [];
        const blockedReasons: string[] = [];
        let survivingCalls = 0;
        for (const part of result.content) {
          if (part?.type === "tool-call") {
            const d = decide(guard, part as unknown as ToolCallLike);
            if (d.blocked) {
              blockedReasons.push(`${String(part.toolName)}: ${d.reason}`);
              continue;
            }
            survivingCalls++;
          }
          content.push(part);
        }
        if (blockedReasons.length === 0) return result;
        content.push({ type: "text", text: blockNote(blockedReasons) });
        return {
          ...result,
          content,
          finishReason: survivingCalls === 0 ? "stop" : result.finishReason,
        };
      }

      // ai v4: tool calls are ``result.toolCalls``.
      const calls = result.toolCalls ?? [];
      if (calls.length === 0) return result;

      const surviving: ToolCallLike[] = [];
      const blockedReasons: string[] = [];
      for (const tc of calls) {
        const d = decide(guard, tc);
        if (d.blocked) {
          blockedReasons.push(`${tc.toolName}: ${d.reason}`);
        } else {
          surviving.push(tc);
        }
      }

      if (blockedReasons.length === 0) return result;

      const note = blockNote(blockedReasons);
      const newText = result.text ? `${result.text}\n\n${note}` : note;
      return {
        ...result,
        toolCalls: surviving,
        text: newText,
        finishReason: surviving.length === 0 ? "stop" : result.finishReason,
      };
    },

    wrapStream: async ({ doStream }: { doStream: () => any }): Promise<any> => {
      const streamed = await doStream();
      const source: ReadableStream<any> | undefined = streamed?.stream;
      if (!source || typeof source.pipeThrough !== "function") return streamed;

      // ``streamText`` executes a tool when the ``tool-call`` part arrives,
      // so that part is the gate. Earlier ``tool-input-*`` /
      // ``tool-call-delta`` parts only render partial arguments and pass
      // through; nothing runs on them.
      let survivingCalls = 0;
      let blockedAny = false;
      let noteId = 0;
      const gated = source.pipeThrough(
        new TransformStream<any, any>({
          transform(part, controller) {
            if (part?.type === "tool-call") {
              const d = decide(guard, part as ToolCallLike);
              if (d.blocked) {
                blockedAny = true;
                const note = blockNote([`${String(part.toolName)}: ${d.reason}`]);
                if ("input" in part) {
                  // v5+ text streams are start / delta / end triples.
                  const id = `sponsio-block-${++noteId}`;
                  controller.enqueue({ type: "text-start", id });
                  controller.enqueue({ type: "text-delta", id, delta: note });
                  controller.enqueue({ type: "text-end", id });
                } else {
                  controller.enqueue({ type: "text-delta", textDelta: note });
                }
                return;
              }
              survivingCalls++;
            }
            if (part?.type === "finish" && blockedAny && survivingCalls === 0) {
              controller.enqueue({ ...part, finishReason: "stop" });
              return;
            }
            controller.enqueue(part);
          },
        }),
      );
      return { ...streamed, stream: gated };
    },
  };
}
