/**
 * sponsio-openclaw
 * ----------------
 *
 * The OpenClaw counterpart to plugins/sponsio-claude-code (the Claude
 * Code plugin). Same architecture, different transport:
 *
 *   sponsio-claude-code (Mode A)           sponsio-openclaw (this file)
 *   ──────────────────────────────         ─────────────────────────────
 *   PreToolUse hook fires        →         api.registerHook("before_tool_call", h, …)
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
 * standalone. The shapes track the real OpenClaw runtime as observed
 * in the 2026.4.14 image:
 *
 *   - registerHook signature is (events, handler, opts) — three
 *     positional args, NOT a single spec object.
 *   - The handler is invoked as handler(event, ctx) — ctx arrives as
 *     the second argument; it is NOT a field on event.
 *   - opts.name is the hook's unique identifier (NOT the event
 *     name); priority lives in opts.priority.
 *
 * If a future OpenClaw release changes these shapes, update them
 * here — the subprocess transport itself doesn't care.
 */
import { spawn } from "node:child_process";
// ----------------------------------------------------------------------------
// Public entry point — what OpenClaw loads.
// ----------------------------------------------------------------------------
/** Higher = earlier in the merge chain. 1000 is well above any
 * documented default; lower it to integrate with ordering-sensitive
 * setups. */
const PLUGIN_HOOK_PRIORITY = 1000;
/** Hook identifier — globally unique within OpenClaw's hook
 * registry. Distinct from the event name (``before_tool_call``)
 * which can have many handlers. */
const HOOK_NAME = "sponsio-openclaw-before-tool-call";
/** The plugin object the OpenClaw runtime expects.
 *
 * Wrap with ``definePluginEntry(...)`` from
 * ``openclaw/plugin-sdk/plugin-entry`` if you want the official
 * helper's type-checking + future-proofing. The raw shape is
 * exported as default for environments that resolve it directly.
 */
export const sponsioOpenClawPlugin = {
    id: "sponsio-openclaw",
    name: "Sponsio for OpenClaw",
    description: "Runtime contract guardrails for every tool call — backed by " +
        "per-plugin contract libraries under ~/.sponsio/plugins/.",
    register(api) {
        api.on("before_tool_call", async (event, ctx) => {
            let reply;
            try {
                reply = await callGuard({
                    // Reuse the Claude Code event shape on the wire so the
                    // same backend, routing, and library files work for
                    // both transports. The OpenClaw event names differ
                    // (``toolName`` / ``params``) but the JSON we send the
                    // guard is normalised to PreToolUse.
                    hook_event_name: "PreToolUse",
                    tool_name: event?.toolName ?? "",
                    tool_input: event?.params ?? {},
                    // Tells the guard which host's fallback library to use
                    // when the tool name doesn't match a namespace pattern
                    // (mcp__*, plugin:skill).  OpenClaw uses canonical names
                    // (exec / read / write / ...) so the fallback lands in
                    // _host_openclaw, not the Claude-Code-shaped _host.
                    host: "openclaw",
                    // Pass session metadata so a future daemon-mode runtime
                    // can correlate calls; today's stateless backend just
                    // ignores these fields. ``ctx`` is the second handler
                    // arg in the OpenClaw runtime — NOT a field on event.
                    session_id: ctx?.sessionId,
                    agent_id: ctx?.agentId,
                    tool_use_id: event?.toolCallId ?? ctx?.toolCallId,
                });
            }
            catch (err) {
                // Never wedge a tool call on a Sponsio bug. Same fail-open
                // policy as ``sponsio.guard_stdin.run_stdin``.
                api.logger?.error?.("[sponsio-openclaw] guard call failed; allowing through", err);
                return undefined;
            }
            if (reply?.permissionDecision === "deny") {
                return {
                    block: true,
                    blockReason: reply.permissionDecisionReason ??
                        "blocked by Sponsio contract",
                };
            }
            return undefined;
        }, {
            name: HOOK_NAME,
            description: "Sponsio runtime contract guard — denies tool calls that " +
                "violate per-plugin libraries under ~/.sponsio/plugins/.",
            priority: PLUGIN_HOOK_PRIORITY,
        });
    },
};
export default sponsioOpenClawPlugin;
/**
 * Spawn ``sponsio plugin guard --stdin``, pipe ``payload`` as JSON,
 * parse the reply.
 *
 * Returns the decoded ``hookSpecificOutput`` block (or null if the
 * guard emitted nothing — meaning ``allow``). Errors propagate so
 * the caller can decide whether to fail-open or fail-closed; the
 * default ``register`` above fails open.
 *
 * Override the binary with ``$SPONSIO_GUARD_BIN`` if your install
 * keeps it elsewhere (eg. a venv-local path inside an IDE).
 */
async function callGuard(payload) {
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
        child.stderr.on("data", () => { });
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
                }
                else {
                    resolve(null);
                }
            }
            catch (err) {
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
 * to ``definePluginEntry`` instead. Kept for early consumers who
 * imported the register fn as the default export.
 */
export function register(api) {
    sponsioOpenClawPlugin.register(api);
}
/** Internal — exported for tests + advanced wrappers only. */
export const __internal = { callGuard };
