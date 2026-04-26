/**
 * sponsio-openclaw-shield
 * ----------------------
 *
 * The OpenClaw counterpart to plugins/sponsio-shield (the Claude Code
 * plugin). Same architecture, different transport:
 *
 *   Claude Code shield (Mode A)            OpenClaw shield (this file)
 *   ──────────────────────────────         ─────────────────────────────
 *   PreToolUse hook fires        →         api.registerHook('before_tool_call', …)
 *   shell command sponsio guard  →         child_process spawn() of same
 *   stdin: PreToolUse JSON       →         same payload, manually built
 *   stdout: deny JSON / silent   →         parsed → {block, reason} | undefined
 *
 * Why subprocess instead of pure-TS evaluation: the Sponsio config
 * loader, contract-pack include resolution, tool_rename / workspace
 * substitution, override merging, and the runtime monitor are all
 * Python today. Spawning the existing CLI gets us 100% logic reuse
 * with ~80ms per-call cost — same as the Claude Code shield. A pure-
 * TS evaluation path is possible (the ts-sdk has the det engine) but
 * requires porting the YAML config loader; that's a separate effort.
 *
 * Routing: this plugin doesn't override sponsio's per-plugin
 * routing. Every tool call goes through the same
 * derive_plugin_id(tool_name) function in
 * `sponsio/guard_stdin.py` and lands in
 * `~/.sponsio/plugins/<id>/sponsio.yaml`. Libraries authored for
 * Claude Code work here verbatim and vice versa.
 */

import { spawn } from "node:child_process";

// ----------------------------------------------------------------------------
// OpenClaw plugin SDK shape — minimal subset we actually consume.
// Mirrors the docs at https://docs.openclaw.ai/plugins/sdk-overview ;
// kept intentionally narrow so we don't pretend to know fields we
// haven't verified end-to-end.
// ----------------------------------------------------------------------------

export interface BeforeToolCallEvent {
  /** The tool the agent is about to invoke. */
  tool: { name: string };
  /** Tool arguments dictionary (shape varies by tool). */
  args: Record<string, unknown>;
}

export type BeforeToolCallReturn =
  | { block: true; reason?: string }
  | { args: Record<string, unknown> }
  | undefined;

export interface OpenClawPluginApi {
  registerHook(
    event: "before_tool_call",
    handler: (e: BeforeToolCallEvent) => Promise<BeforeToolCallReturn>,
  ): void;
  logger?: {
    debug(msg: string, ...rest: unknown[]): void;
    info(msg: string, ...rest: unknown[]): void;
    warn(msg: string, ...rest: unknown[]): void;
    error(msg: string, ...rest: unknown[]): void;
  };
}

// ----------------------------------------------------------------------------
// Public entry point — what OpenClaw calls to load the plugin.
// ----------------------------------------------------------------------------

/**
 * OpenClaw plugin entry point.
 *
 * Registers a `before_tool_call` hook that delegates to
 * `sponsio shield guard --stdin`. Returns `{ block: true, reason }`
 * when Sponsio denies; returns undefined to let the call proceed.
 *
 * Exported as default so `main: dist/index.js` resolves to a
 * loadable function regardless of import style.
 */
export default function register(api: OpenClawPluginApi): void {
  api.registerHook("before_tool_call", async (event) => {
    let reply: ShieldGuardReply | null;
    try {
      reply = await callShieldGuard({
        // Reuse the Claude Code event shape so the same backend,
        // routing, and library files work for both transports.
        // ``hook_event_name`` is informational; the routing key is
        // derived from ``tool_name``.
        hook_event_name: "PreToolUse",
        tool_name: event?.tool?.name ?? "",
        tool_input: event?.args ?? {},
      });
    } catch (err) {
      // Never wedge a tool call on a Sponsio bug. Same fail-open
      // policy as the Python entry point in
      // ``sponsio.guard_stdin.run_stdin``.
      api.logger?.error?.(
        "[sponsio-openclaw-shield] guard call failed; allowing through",
        err,
      );
      return undefined;
    }

    if (reply?.permissionDecision === "deny") {
      return {
        block: true,
        reason:
          reply.permissionDecisionReason ?? "blocked by Sponsio contract",
      };
    }
    return undefined;
  });
}

// ----------------------------------------------------------------------------
// Subprocess transport
// ----------------------------------------------------------------------------

interface ShieldGuardReply {
  permissionDecision?: "allow" | "deny";
  permissionDecisionReason?: string;
}

/**
 * Spawn ``sponsio shield guard --stdin``, pipe ``payload`` as JSON,
 * parse the reply.
 *
 * Returns the decoded ``hookSpecificOutput`` block (or null if the
 * shield emitted nothing — meaning ``allow``). Errors propagate so
 * the caller can decide whether to fail-open or fail-closed; the
 * default ``register`` above fails open.
 *
 * Override the binary with ``$SPONSIO_GUARD_BIN`` if your install
 * keeps it elsewhere (eg. a venv-local path inside an IDE).
 */
async function callShieldGuard(
  payload: object,
): Promise<ShieldGuardReply | null> {
  const bin = process.env.SPONSIO_GUARD_BIN ?? "sponsio";
  const args = ["shield", "guard", "--stdin"];

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
        // Empty stdout = allow. Match the Claude Code shield's
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
// Re-exports for tests + downstream consumers
// ----------------------------------------------------------------------------

/** Internal — exported for tests and advanced wrappers only. */
export const __internal = { callShieldGuard };
