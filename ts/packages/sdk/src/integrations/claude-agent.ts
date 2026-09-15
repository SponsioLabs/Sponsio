/**
 * Claude Agent SDK integration — native TypeScript.
 *
 * Usage:
 *   import { Sponsio } from "@sponsio/sdk"
 *   import { sponsioHooks } from "@sponsio/sdk/claude-agent"
 *
 *   const guard = new Sponsio({ contracts: [...] })
 *   const options = { hooks: sponsioHooks(guard) }
 *
 * ``PreToolUse`` denies through the SDK's permission system before the
 * tool runs. A guard that throws while evaluating denies as well: the
 * SDK's handling of a rejected hook promise is host-defined, and
 * "could not check" must not read as "allowed".
 */

import type { Sponsio } from "../index.js";

export interface HookResult {
  systemMessage?: string;
  hookSpecificOutput?: {
    hookEventName: string;
    permissionDecision?: "allow" | "deny" | "ask";
    permissionDecisionReason?: string;
    additionalContext?: string;
  };
}

function deny(toolName: string, reason: string): HookResult {
  return {
    systemMessage:
      `[Sponsio] Tool \`${toolName}\` was blocked: ${reason}. ` +
      `Please adjust your approach.`,
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: `Sponsio: ${reason}`,
    },
  };
}

export function sponsioHooks(guard: Sponsio) {
  async function preToolUse(
    input: Record<string, unknown>,
    _toolUseId: string | null,
    _context: unknown,
  ): Promise<HookResult | Record<string, never>> {
    const toolName = (input.tool_name as string) ?? "";
    const toolInput = (input.tool_input as Record<string, unknown>) ?? {};

    let result;
    try {
      result = guard.guardBefore(toolName, toolInput);
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err);
      return deny(toolName, `the contract guard failed to evaluate this call (${reason})`);
    }

    // ``stopOriginal`` covers a redirect verdict as well as a block: the
    // hook has no substitution path, so a redirect denies.
    if (result.stopOriginal) {
      return deny(toolName, result.message);
    }

    return {};
  }

  async function postToolUse(
    input: Record<string, unknown>,
    _toolUseId: string | null,
    _context: unknown,
  ): Promise<Record<string, never>> {
    const toolName = (input.tool_name as string) ?? "";
    // The SDK's PostToolUse payload carries the output as
    // ``tool_response``; ``tool_result`` is kept as a fallback for hosts
    // that used the older spelling.
    const raw = input.tool_response ?? input.tool_result ?? "";
    const toolResult = typeof raw === "string" ? raw : safeStringify(raw);
    await guard.guardAfter(toolName, toolResult);
    return {};
  }

  return {
    PreToolUse: [{ hooks: [preToolUse] }],
    PostToolUse: [{ hooks: [postToolUse] }],
  };
}

function safeStringify(v: unknown): string {
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}
