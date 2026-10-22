/**
 * sponsio-openclaw
 * ----------------
 *
 * The OpenClaw counterpart to plugins/sponsio-claude-code (the Claude
 * Code plugin). Same architecture, different transport:
 *
 *   Claude Code / Cursor hook              sponsio-openclaw (this file)
 *   ──────────────────────────────         ─────────────────────────────
 *   PreToolUse hook fires        →         api.on("before_tool_call", h, …)
 *   shim → daemon guard.check    →         Unix socket → daemon guard.check
 *   deny JSON / silent           →         {allowed, reason} → BeforeToolCallResult
 *
 * The decision is made by the Sponsio daemon (Python): the config
 * loader, contract-pack includes, and the runtime monitor all live
 * there, and the daemon keeps each session's history in memory. This
 * plugin speaks the daemon's wire protocol directly (a 4-byte
 * big-endian length, then a JSON body) over its Unix socket, so a tool
 * call costs one round trip instead of a Python process start.
 *
 * Where the socket is, which libraries a daemon it starts serves, and
 * the mode come from ``sponsio-config.json`` next to this file, which
 * ``sponsio host install openclaw`` writes. When no daemon answers the
 * plugin starts one, waits briefly, retries once, and otherwise blocks
 * the call: a guard that cannot be reached is not a pass.
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
import { readFileSync } from "node:fs";
import { createConnection } from "node:net";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
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
const DEFAULT_DEADLINE_MS = 2000;
const START_WAIT_MS = 1500;
/** Read ``sponsio-config.json`` next to this module, if present. */
export function loadInstalledOptions() {
    try {
        const here = dirname(fileURLToPath(import.meta.url));
        // The config sits next to the extension root (dist/..).
        for (const dir of [join(here, ".."), here]) {
            try {
                const raw = readFileSync(join(dir, "sponsio-config.json"), "utf8");
                const parsed = JSON.parse(raw);
                if (parsed && typeof parsed === "object")
                    return parsed;
            }
            catch {
                // try the next location
            }
        }
    }
    catch {
        // import.meta.url unavailable: fall through to defaults
    }
    return {};
}
/** Build the plugin object for a given daemon configuration. */
export function createSponsioOpenClawPlugin(options = {}) {
    const opts = resolveOptions(options);
    return {
        id: "sponsio-openclaw",
        name: "Sponsio for OpenClaw",
        description: "Runtime contract guardrails for every tool call — decided by the " +
            "Sponsio daemon against per-plugin libraries under ~/.sponsio/plugins/.",
        register(api) {
            api.on("before_tool_call", async (event, ctx) => {
                // The Claude Code event shape on the wire, so the same
                // routing and library files serve both hosts. ``ctx`` is the
                // second handler arg in the OpenClaw runtime, not a field on
                // event.
                let verdict;
                try {
                    const payload = {
                        hook_event_name: "PreToolUse",
                        tool_name: event?.toolName ?? "",
                        tool_input: event?.params ?? {},
                        // Routes the fallback to _host_openclaw.
                        host: "openclaw",
                        // The daemon keys session history on this.
                        session_id: ctx?.sessionId,
                        agent_id: ctx?.agentId,
                        tool_use_id: event?.toolCallId ?? ctx?.toolCallId,
                    };
                    verdict = await decide(opts, payload);
                }
                catch (err) {
                    // No verdict: the daemon answered with an error, or this
                    // handler failed. Blocked, the same as an unreachable daemon
                    // (see ``decide``), unless the operator chose failOpen.
                    const cause = err instanceof Error ? err.message : String(err);
                    if (opts.failOpen) {
                        api.logger?.warn?.(`[sponsio-openclaw] no verdict (${cause}); failOpen is set, ` +
                            "so this tool call runs unchecked");
                        return undefined;
                    }
                    api.logger?.error?.(`[sponsio-openclaw] no verdict (${cause}); blocking the call`);
                    return {
                        block: true,
                        blockReason: `Sponsio could not decide this call (${cause}), so it is ` +
                            "blocked. Check `sponsio doctor` from your own terminal.",
                    };
                }
                if (!verdict.allowed) {
                    return {
                        block: true,
                        blockReason: verdict.reason || "blocked by Sponsio contract",
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
}
/** The plugin object the OpenClaw runtime expects, configured from the
 * installed ``sponsio-config.json``.
 *
 * Wrap with ``definePluginEntry(...)`` from
 * ``openclaw/plugin-sdk/plugin-entry`` if you want the official
 * helper's type-checking + future-proofing. The raw shape is
 * exported as default for environments that resolve it directly.
 */
export const sponsioOpenClawPlugin = createSponsioOpenClawPlugin(loadInstalledOptions());
export default sponsioOpenClawPlugin;
function resolveOptions(o) {
    return {
        socketPath: o.socketPath || join(homedir(), ".sponsio", "sponsio.sock"),
        pluginRoot: o.pluginRoot,
        stateDir: o.stateDir,
        sponsio: Array.isArray(o.sponsio) && o.sponsio.length ? o.sponsio : ["sponsio"],
        mode: o.mode === "observe" || o.mode === "enforce" ? o.mode : undefined,
        deadlineMs: typeof o.deadlineMs === "number" && o.deadlineMs > 0
            ? o.deadlineMs
            : DEFAULT_DEADLINE_MS,
        failOpen: o.failOpen === true,
    };
}
/** No daemon answered: missing socket, refused, or out of time. */
class Unreachable extends Error {
}
/** One RPC over the daemon socket. Rejects with ``Unreachable`` when no
 * daemon answers, with a plain ``Error`` when one answers with an error. */
export function callDaemon(socketPath, method, params, timeoutMs) {
    return new Promise((resolve, reject) => {
        if (timeoutMs <= 0) {
            reject(new Unreachable("deadline passed"));
            return;
        }
        const body = Buffer.from(JSON.stringify({ method, params }), "utf8");
        const header = Buffer.alloc(4);
        header.writeUInt32BE(body.length, 0);
        let buf = Buffer.alloc(0);
        let settled = false;
        const sock = createConnection({ path: socketPath });
        const finish = (fn) => {
            if (settled)
                return;
            settled = true;
            clearTimeout(timer);
            sock.destroy();
            fn();
        };
        const timer = setTimeout(() => finish(() => reject(new Unreachable("timed out"))), timeoutMs);
        sock.on("connect", () => sock.write(Buffer.concat([header, body])));
        sock.on("data", (chunk) => {
            buf = Buffer.concat([buf, chunk]);
            if (buf.length < 4)
                return;
            const len = buf.readUInt32BE(0);
            if (buf.length < 4 + len)
                return;
            finish(() => {
                try {
                    const reply = JSON.parse(buf.subarray(4, 4 + len).toString("utf8"));
                    if (!reply || reply.ok !== true || typeof reply.result !== "object") {
                        reject(new Error(`daemon error: ${reply?.error ?? "malformed reply"}`));
                    }
                    else {
                        resolve(reply.result);
                    }
                }
                catch (err) {
                    reject(err);
                }
            });
        });
        sock.on("error", (err) => finish(() => reject(new Unreachable(err.code === "ENOENT" ? `no socket at ${socketPath}` : String(err.code ?? err)))));
        sock.on("close", () => finish(() => reject(new Unreachable("daemon closed the connection"))));
    });
}
const ENV_PASSTHROUGH = [
    "HOME",
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "USER",
    "LOGNAME",
    "SHELL",
    "TZ",
    "OPENCLAW_HOME",
];
/** Spawn ``sponsio daemon run --detach`` for our socket, detached, with
 * locale and path basics only (no SPONSIO_* from the host's process). */
function startDaemon(o) {
    const [bin, ...prefix] = o.sponsio;
    const args = [...prefix, "daemon", "run", "--detach", "--socket", o.socketPath];
    if (o.pluginRoot)
        args.push("--plugin-root", o.pluginRoot);
    if (o.stateDir)
        args.push("--state-dir", o.stateDir);
    const env = {};
    for (const k of ENV_PASSTHROUGH) {
        const v = process.env[k];
        if (v !== undefined)
            env[k] = v;
    }
    try {
        const child = spawn(bin, args, { detached: true, stdio: "ignore", env });
        child.on("error", () => { });
        child.unref();
    }
    catch {
        // reported as "did not come up" below
    }
}
function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
}
async function waitForSocket(o, until) {
    while (Date.now() < until) {
        try {
            await callDaemon(o.socketPath, "ping", {}, Math.min(200, until - Date.now()));
            return true;
        }
        catch {
            await sleep(50);
        }
    }
    return false;
}
function recoveryReason(cause, o) {
    return (`Sponsio: the enforcement daemon is not reachable (${cause}). ` +
        `This tool call is blocked, and every tool call will be blocked until ` +
        `this is fixed; the agent cannot repair it itself. From your own ` +
        `terminal: (1) run \`sponsio daemon run --detach --socket ${o.socketPath}\` ` +
        `and then \`sponsio doctor\`; (2) if that does not help, remove the ` +
        `plugin with \`sponsio host uninstall openclaw\`.`);
}
/** Ask the daemon for a verdict, starting it once if needed. An
 * unreachable daemon is a block. */
async function decide(o, payload) {
    const deadline = Date.now() + o.deadlineMs;
    const params = { host: "openclaw", payload };
    if (o.mode)
        params.mode = o.mode;
    const ask = async () => {
        const r = await callDaemon(o.socketPath, "guard.check", params, deadline - Date.now());
        return {
            allowed: r.allowed === true,
            reason: typeof r.reason === "string" ? r.reason : "",
        };
    };
    try {
        return await ask();
    }
    catch (first) {
        if (!(first instanceof Unreachable))
            throw first;
        startDaemon(o);
        const up = await waitForSocket(o, Math.min(deadline, Date.now() + START_WAIT_MS));
        if (!up) {
            return {
                allowed: false,
                reason: recoveryReason(`${first.message}; the daemon did not come up`, o),
            };
        }
        try {
            return await ask();
        }
        catch (second) {
            if (!(second instanceof Unreachable))
                throw second;
            return {
                allowed: false,
                reason: recoveryReason(`${first.message}; after restart: ${second.message}`, o),
            };
        }
    }
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
export const __internal = { callDaemon, decide, resolveOptions };
