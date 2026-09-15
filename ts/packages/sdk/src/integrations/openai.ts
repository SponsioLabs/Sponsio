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
 * Covers ``chat.completions.create`` / ``parse`` and, when the client has
 * it, ``responses.create`` / ``parse``. Every tool call the model emits
 * is checked before the caller's tool loop can run it; refused calls are
 * removed from the response and a ``[BLOCKED] <tool>: <reason>`` line is
 * added to the assistant text so the model learns why.
 *
 * ``stream: true`` throws at call time on every wrapped method: tool
 * calls arrive as deltas and could only be checked after the caller has
 * already assembled and run them. Call without ``stream``, or run your
 * own executor through ``guard.guardBefore()`` before each tool call.
 *
 * ``patchOpenAI`` is an alias matching Python's ``patch_openai``
 * factory (same monkey-patch semantics — no wrapped return value
 * required; the client passed in is mutated in place).
 */

import type { Sponsio } from "../index.js";

const STREAM_UNSUPPORTED =
  "sponsio: the OpenAI guard does not support stream: true. Tool calls " +
  "arrive as deltas and could only be checked after the caller has already " +
  "assembled and run them. Call without stream, or run your own executor " +
  "through guard.guardBefore() before each tool call.";

interface ChatToolCall {
  function: { name: string; arguments: string };
  [k: string]: unknown;
}

interface ChatResponse {
  choices?: Array<{
    message: {
      tool_calls?: ChatToolCall[] | null;
      content?: string | null;
      [k: string]: unknown;
    };
  }>;
}

interface ResponsesOutputItem {
  type?: string;
  name?: string;
  arguments?: string;
  call_id?: string;
  content?: Array<{ type?: string; text?: string; [k: string]: unknown }>;
  [k: string]: unknown;
}

interface ResponsesResponse {
  output?: ResponsesOutputItem[];
  [k: string]: unknown;
}

export function wrapOpenAI(client: unknown, guard: Sponsio): unknown {
  const c = client as {
    chat?: { completions?: Record<string, unknown> };
    responses?: Record<string, unknown>;
  };
  const completions = c?.chat?.completions;
  if (!completions || typeof completions.create !== "function") {
    throw new TypeError(
      "wrapOpenAI: expected an OpenAI client with chat.completions.create",
    );
  }
  patchMethod(completions, "create", guard, "chat");
  patchMethod(completions, "parse", guard, "chat");
  if (c.responses) {
    patchMethod(c.responses, "create", guard, "responses");
    patchMethod(c.responses, "parse", guard, "responses");
  }
  return client;
}

function patchMethod(
  target: Record<string, unknown>,
  name: string,
  guard: Sponsio,
  kind: "chat" | "responses",
): void {
  const original = target[name];
  if (typeof original !== "function") return;
  const bound = (original as (...args: unknown[]) => unknown).bind(target);
  target[name] = async function (...args: unknown[]) {
    const params = args[0];
    if (params && typeof params === "object" && (params as { stream?: unknown }).stream) {
      throw new Error(STREAM_UNSUPPORTED);
    }
    const response = await bound(...args);
    return kind === "chat"
      ? guardChatResponse(response as ChatResponse, guard)
      : guardResponsesOutput(response as ResponsesResponse, guard);
  };
}

/**
 * Decide one emitted call. Malformed JSON arguments are refused in
 * enforce mode without touching the trace (a call that never runs must
 * not count as a call); in observe mode they pass through and are
 * recorded with a sentinel so the session log shows what arrived.
 */
function decide(
  guard: Sponsio,
  toolName: string,
  rawArgs: string | undefined,
): { blocked: false } | { blocked: true; note: string } {
  let args: Record<string, unknown>;
  try {
    const parsed = JSON.parse(rawArgs || "{}");
    args =
      parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : { input: parsed };
  } catch {
    if (guard.mode === "enforce") {
      return {
        blocked: true,
        note: `[BLOCKED] ${toolName}: malformed JSON arguments — refusing to forward to the tool loop`,
      };
    }
    args = { _sponsio_malformed_args: rawArgs ?? "" };
  }
  const result = guard.guardBefore(toolName, args);
  // ``stopOriginal`` covers a redirect verdict as well as a block: the
  // wrapper has no substitution path, so a redirect strips the call.
  if (result.stopOriginal) {
    return { blocked: true, note: `[BLOCKED] ${toolName}: ${result.message}` };
  }
  return { blocked: false };
}

function guardChatResponse(response: ChatResponse, guard: Sponsio): ChatResponse {
  for (const choice of response?.choices ?? []) {
    const msg = choice.message;
    if (!msg?.tool_calls) continue;

    const kept: ChatToolCall[] = [];
    const blocked: string[] = [];
    for (const tc of msg.tool_calls) {
      const d = decide(guard, tc.function.name, tc.function.arguments);
      if (d.blocked) blocked.push(d.note);
      else kept.push(tc);
    }

    msg.tool_calls = kept.length > 0 ? kept : null;
    if (blocked.length > 0 && !msg.tool_calls) {
      // Match Python's ``_filter_blocked_calls``: join with a newline
      // separator but no leading newline before the first message.
      msg.content = (msg.content ?? "") + blocked.join("\n");
    }
  }
  return response;
}

function guardResponsesOutput(
  response: ResponsesResponse,
  guard: Sponsio,
): ResponsesResponse {
  const output = response?.output;
  if (!Array.isArray(output)) return response;

  const kept: ResponsesOutputItem[] = [];
  const blocked: string[] = [];
  for (const item of output) {
    if (item?.type === "function_call") {
      const d = decide(guard, item.name ?? "", item.arguments);
      if (d.blocked) {
        blocked.push(d.note);
        continue;
      }
    }
    kept.push(item);
  }
  if (blocked.length === 0) return response;

  response.output = kept;
  const note = blocked.join("\n");
  const lastMessage = [...kept].reverse().find((i) => i?.type === "message");
  const textPart = lastMessage?.content
    ? [...lastMessage.content].reverse().find((p) => typeof p?.text === "string")
    : undefined;
  if (textPart) {
    textPart.text = `${textPart.text}\n${note}`;
  } else {
    kept.push({
      type: "message",
      role: "assistant",
      status: "completed",
      content: [{ type: "output_text", text: note, annotations: [] }],
    });
  }
  return response;
}

/**
 * Alias for ``wrapOpenAI`` — matches Python's
 * ``from sponsio.openai import patch_openai`` naming. Both mutate
 * the passed client in place and return it; use whichever name
 * reads better alongside the Python snippet you're porting.
 */
export const patchOpenAI = wrapOpenAI;
