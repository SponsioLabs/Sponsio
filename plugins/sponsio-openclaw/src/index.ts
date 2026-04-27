/**
 * sponsio-openclaw
 * ----------------
 *
 * The OpenClaw counterpart to plugins/sponsio-claude-code (the Claude
 * Code plugin). Same architecture, different transport:
 *
 *   sponsio-claude-code (Mode A)           sponsio-openclaw (this file)
 *   ──────────────────────────────         ─────────────────────────────
 *   PreToolUse hook fires        →         api.registerHook({name: "before_tool_call", …})
 *   shell command sponsio guard  →         child_process spawn() of same
 *   stdin: PreToolUse JSON       →         same payload, manually built
 *   stdout: deny JSON / silent   →         parsed → BeforeToolCallResult
 *
 * Why subprocess instead of pure-TS evaluation: the Sponsio config
 * loader, contract-pack include resolution, tool_rename / workspace
 * substitution, override merging, and the runtime monitor are all
 * Python today. Spawning the existing CLI gets us 100% logic reuse
 * with ~80ms per-call cost — same as the Claude Code plugin. A pure-
 * TS evaluation path is possible (the ts-sdk has the det engine) but
 * requires porting the YAML config loader; that's a separate effort.
 *
 * Routing: this plugin doesn't override sponsio's per-plugin
 * routing. Every tool call goes through the same
 * derive_plugin_id(tool_name) function in
 * `sponsio/guard_stdin.py` and lands in
 * `~/.sponsio/plugins/<id>/sponsio.yaml`. Libraries authored for
 * Claude Code work here verbatim and vice versa.
 *
 * Type definitions are local — we don't depend on the ``openclaw``
 * npm package at compile time so this plugin builds and tests
 * standalone. The shapes track:
 *   - https://docs.openclaw.ai/plugins/hooks.md (event + return shapes)
 *   - https://docs.openclaw.ai/plugins/sdk-entrypoints.md (registerHook signature)
 *   - https://docs.openclaw.ai/plugins/manifest.md (manifest contract)
 *
 * Verified against docs as of 2026-04-26. If OpenClaw's runtime
 * disagrees in a real session, update these types — the subprocess
 * transport itself doesn't care.
 */

import { spawn } from "node:child_process";

// ----------------------------------------------------------------------------
// OpenClaw plugin SDK shape — narrow subset we consume.
// Mirrors the public docs at docs.openclaw.ai/plugins/* ; intentionally
// excludes the wider hook surface (channel hooks, message hooks, …) we
// don't register for.
// ----------------------------------------------------------------------------

/** Event payload OpenClaw passes to a ``before_tool_call`` handler.
 *
 * Field shape pinned to https://docs.openclaw.ai/plugins/hooks.md
 * (``before_tool_call`` section). ``runId`` / ``toolCallId`` are
 * optional in the docs but always present in practice. ``ctx``
 * carries the per-session correlation; we forward ``agentId`` /
 * ``sessionId`` so multi-session daemon mode (Stage 3) can demux.
 */
export interface BeforeToolCallEvent {
  toolName: string;
  params: Record<string, unknown>;
  runId?: string;
  toolCallId?: string;
  ctx: {
    agentId: string;
    sessionKey: string;
    sessionId: string;
    trace: unknown;
  };
}

/** Decision object returned from a ``before_tool_call`` handler.
 *
 * Per the docs:
 *   - ``{block: true, blockReason}`` — terminal; lower-priority hooks skip.
 *   - ``{block: false}`` — no decision, chain continues.
 *   - ``{params: ...}`` — rewrite tool args before execution.
 *   - ``{requireApproval: ...}`` — pause and prompt the user (we
 *     don't use this today; reserved for ``must_confirm``-style
 *     contracts in a future iteration).
 *
 * Returning ``undefined`` is treated as ``{block: false}`` — the
 * default no-decision path.
 */
export interface BeforeToolCallResult {
  params?: Record<string, unknown>;
  block?: boolean;
  blockReason?: string;
  requireApproval?: {
    title: string;
    description: string;
    severity?: "info" | "warning" | "critical";
    timeoutMs?: number;
    timeoutBehavior?: "allow" | "deny";
    pluginId?: string;
    onResolution?: (
      decision:
        | "allow-once"
        | "allow-always"
        | "deny"
        | "timeout"
        | "cancelled",
    ) => Promise<void> | void;
  };
}

/** OpenClaw plugin SDK API — narrowed to what we register. */
export interface OpenClawPluginApi {
  registerHook(spec: {
    name: "before_tool_call";
    handler: (
      event: BeforeToolCallEvent,
    ) => Promise<BeforeToolCallResult | undefined>;
    /** Higher = earlier. Sponsio runs at high priority so a deny
     * short-circuits the rest of the hook chain. */
    priority?: number;
  }): void;
  logger?: {
    debug(msg: string, ...rest: unknown[]): void;
    info(msg: string, ...rest: unknown[]): void;
    warn(msg: string, ...rest: unknown[]): void;
    error(msg: string, ...rest: unknown[]): void;
  };
}

/** Plugin entry shape consumed by ``definePluginEntry`` (or directly by
 * the runtime for plugins that don't use the helper). */
export interface SponsioOpenClawPlugin {
  id: string;
  name: string;
  description: string;
  register(api: OpenClawPluginApi): void;
}

// ----------------------------------------------------------------------------
// Public entry point — what OpenClaw loads.
// ----------------------------------------------------------------------------

/** Priority chosen so Sponsio's deny short-circuits any other hook
 * chain a user has installed. 1000 is well above any documented
 * default; lower it to integrate with ordering-sensitive setups. */
const PLUGIN_HOOK_PRIORITY = 1000;

/** The plugin object the OpenClaw runtime expects.
 *
 * Wrap with ``definePluginEntry(...)`` from
 * ``openclaw/plugin-sdk/plugin-entry`` if you want the official
 * helper's type-checking + future-proofing. The raw shape is
 * exported as default for environments that resolve it directly.
 */
export const sponsioOpenClawPlugin: SponsioOpenClawPlugin = {
  id: "sponsio-openclaw",
  name: "Sponsio for OpenClaw",
  description:
    "Runtime contract guardrails for every tool call — backed by " +
    "per-plugin contract libraries under ~/.sponsio/plugins/.",
  register(api) {
    api.registerHook({
      name: "before_tool_call",
      priority: PLUGIN_HOOK_PRIORITY,
      handler: async (event) => {
        let reply: GuardReply | null;
        try {
          reply = await callGuard({
            // Reuse the Claude Code event shape on the wire so the
            // same backend, routing, and library files work for
            // both transports. The OpenClaw event names differ
            // (``toolName`` / ``params``) but the JSON we send the
            // guard payload is normalised to PreToolUse.
            hook_event_name: "PreToolUse",
            tool_name: event?.toolName ?? "",
            tool_input: event?.params ?? {},
            // Pass session metadata so a future daemon-mode runtime
            // can correlate calls; today's stateless backend just
            // ignores these fields.
            session_id: event?.ctx?.sessionId,
            agent_id: event?.ctx?.agentId,
            tool_use_id: event?.toolCallId,
          });
        } catch (err) {
          // Never wedge a tool call on a Sponsio bug. Same fail-open
          // policy as ``sponsio.guard_stdin.run_stdin``.
          api.logger?.error?.(
            "[sponsio-openclaw] guard call failed; allowing through",
            err,
          );
          return undefined;
        }

        if (reply?.permissionDecision === "deny") {
          return {
            block: true,
            blockReason:
              reply.permissionDecisionReason ??
              "blocked by Sponsio contract",
          };
        }
        return undefined;
      },
    });
  },
};

export default sponsioOpenClawPlugin;

// ----------------------------------------------------------------------------
// Subprocess transport
// ----------------------------------------------------------------------------

interface GuardReply {
  permissionDecision?: "allow" | "deny";
  permissionDecisionReason?: string;
}

/**
 * Spawn ``sponsio plugin guard --stdin``, pipe ``payload`` as JSON,
 * parse the reply.
 *
 * Returns the decoded ``hookSpecificOutput`` block (or null if the
 * the guard emitted nothing — meaning ``allow``). Errors propagate so
 * the caller can decide whether to fail-open or fail-closed; the
 * default ``register`` above fails open.
 *
 * Override the binary with ``$SPONSIO_GUARD_BIN`` if your install
 * keeps it elsewhere (eg. a venv-local path inside an IDE).
 */
async function callGuard(
  payload: object,
): Promise<GuardReply | null> {
  const bin = process.env.SPONSIO_GUARD_BIN ?? "sponsio";
  const args = ["plugin", "guard", "--stdin"];

  return new Promise((resolve, reject) => {
    const child = spawn(bin, args, { stdio: ["pipe", "pipe", "pipe"] });

    let stdout = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    // Discard stderr — Sponsio writes diagnostic banners + load
    // logs there; not part of the deny protocol.
    child.stderr.on("data", () => {});

    child.on("error", (err) => reject(err));
    child.on("close", () => {
      const text = stdout.trim();
      if (!text) {
        // Empty stdout = allow. Match the sponsio-claude-code
        // "exit 0 silent" semantics.
        return resolve(null);
      }
      try {
        const parsed = JSON.parse(text);
        const out = parsed?.hookSpecificOutput;
        if (out && typeof out === "object") {
          resolve({
            permissionDecision: out.permissionDecision,
            permissionDecisionReason: out.permissionDecisionReason,
          });
        } else {
          resolve(null);
        }
      } catch (err) {
        reject(err);
      }
    });

    child.stdin.write(JSON.stringify(payload));
    child.stdin.end();
  });
}

// ----------------------------------------------------------------------------
// Backward-compat: the prior 0.1.0-alpha.0 export was a default ``register``
// function. Some early consumers may still call ``register(api)`` directly;
// keep that path alive while pointing them at the new plugin object.
// ----------------------------------------------------------------------------

/**
 * @deprecated Pass ``sponsioOpenClawPlugin`` (or the default export)
 * to ``definePluginEntry`` instead. Kept for the early-prototype consumer
 * who imported the register fn as the default export.
 */
export function register(api: OpenClawPluginApi): void {
  sponsioOpenClawPlugin.register(api);
}

/** Internal — exported for tests + advanced wrappers only. */
export const __internal = { callGuard };
