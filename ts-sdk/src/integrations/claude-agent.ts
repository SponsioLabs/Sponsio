/**
 * Claude Agent SDK integration — native TypeScript.
 *
 * Usage:
 *   import { Sponsio } from "@sponsio/sdk"
 *   import { sponsioHooks } from "@sponsio/sdk/claude-agent"
 *
 *   const guard = new Sponsio({ contracts: [...] })
 *   const options = { hooks: sponsioHooks(guard) }
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

export function sponsioHooks(guard: Sponsio) {
  async function preToolUse(
    input: Record<string, unknown>,
    _toolUseId: string | null,
    _context: unknown,
  ): Promise<HookResult | Record<string, never>> {
    const toolName = (input.tool_name as string) ?? "";
    const toolInput = (input.tool_input as Record<string, unknown>) ?? {};

    const result = guard.guardBefore(toolName, toolInput);

    if (result.blocked) {
      return {
        systemMessage:
          `[Sponsio] Tool \`${toolName}\` was blocked: ${result.message}. ` +
          `Please adjust your approach.`,
        hookSpecificOutput: {
          hookEventName: "PreToolUse",
          permissionDecision: "deny",
          permissionDecisionReason: `Sponsio: ${result.message}`,
        },
      };
    }

    return {};
  }

  async function postToolUse(
    input: Record<string, unknown>,
    _toolUseId: string | null,
    _context: unknown,
  ): Promise<Record<string, never>> {
    const toolName = (input.tool_name as string) ?? "";
    const toolResult = (input.tool_result as string) ?? "";
    guard.guardAfter(toolName, String(toolResult));
    return {};
  }

  return {
    PreToolUse: [{ hooks: [preToolUse] }],
    PostToolUse: [{ hooks: [postToolUse] }],
  };
}
