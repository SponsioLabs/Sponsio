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
/** Event payload OpenClaw passes to a ``before_tool_call`` handler.
 *
 * Pinned to ``hookRunner.runBeforeToolCall(event, ctx)`` in the
 * runtime's hook-runner. ``runId`` / ``toolCallId`` are present
 * whenever the runtime has them; both can be absent for early /
 * synthetic calls.
 */
export interface BeforeToolCallEvent {
    toolName: string;
    params: Record<string, unknown>;
    runId?: string;
    toolCallId?: string;
}
/** Per-call context — passed as the SECOND argument to the handler.
 *
 * The runtime builds this in ``pi-tools.before-tool-call`` from the
 * agent's session state, then forwards it to every registered
 * handler. We only read ``agentId`` / ``sessionId`` (forwarded to
 * the guard so a future daemon-mode backend can demux), but the full
 * shape is documented for completeness.
 */
export interface BeforeToolCallContext {
    toolName: string;
    agentId?: string;
    sessionKey?: string;
    sessionId?: string;
    runId?: string;
    toolCallId?: string;
}
/** Decision object returned from a ``before_tool_call`` handler.
 *
 * Per the runtime's merge policy in ``runBeforeToolCall``:
 *   - ``{block: true, blockReason}`` — terminal; merger short-circuits.
 *   - ``{block: false}`` / ``undefined`` — no decision, chain continues.
 *   - ``{params: ...}`` — rewrite tool args before execution.
 *   - ``{requireApproval: ...}`` — pause and prompt the user (we
 *     don't use this today; reserved for ``must_confirm``-style
 *     contracts in a future iteration).
 *
 * Returning ``undefined`` is treated as the no-decision path.
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
        onResolution?: (decision: "allow-once" | "allow-always" | "deny" | "timeout" | "cancelled") => Promise<void> | void;
    };
}
/** Options OpenClaw accepts on ``registerHook``. */
export interface RegisterHookOptions {
    /** Unique hook identifier within the plugin. Required by the
     * runtime — registrations without a name are dropped with a
     * "hook registration missing name" diagnostic. */
    name: string;
    /** Human-readable description; surfaced in the hooks CLI. */
    description?: string;
    /** Higher = earlier in the sequential hook chain. The runtime
     * orders ``before_tool_call`` handlers by this value when merging
     * results. */
    priority?: number;
}
/** OpenClaw plugin SDK API — narrowed to what we register.
 *
 * Tracks ``createApi(...)`` in the runtime's loader.  Two surfaces
 * matter for us:
 *
 *   * ``api.on(hookName, handler, opts)`` — registers a *typed*
 *     hook that ``hookRunner.runBeforeToolCall`` actually consults.
 *     This is what we need for ``before_tool_call``.
 *
 *   * ``api.registerHook(events, handler, opts)`` — registers an
 *     *internal* hook (event types like ``command``, ``gateway:startup``,
 *     ``message:received``).  Surfaces in ``openclaw hooks list`` and
 *     gets dispatched via ``triggerInternalHook(event)``.  NOT what
 *     fires for tool calls.
 *
 * Verified against ``memory-lancedb``'s registrations (``api.on
 * ("before_agent_start", …)`` / ``api.on("agent_end", …)``) and by
 * tracing ``getHooksForName(registry, "before_tool_call")`` in the
 * 2026.4.14 image — it reads ``registry.typedHooks``, which only
 * ``api.on`` writes to.
 */
export interface OpenClawPluginApi {
    on(hookName: "before_tool_call" | string, handler: (event: BeforeToolCallEvent, ctx: BeforeToolCallContext) => Promise<BeforeToolCallResult | undefined>, opts?: RegisterHookOptions): void;
    logger?: {
        debug(msg: string, ...rest: unknown[]): void;
        info(msg: string, ...rest: unknown[]): void;
        warn(msg: string, ...rest: unknown[]): void;
        error(msg: string, ...rest: unknown[]): void;
    };
}
/** Plugin entry shape consumed by ``definePluginEntry`` (or directly by
 * the runtime — the loader accepts the raw object as long as the
 * required fields are present). */
export interface SponsioOpenClawPlugin {
    id: string;
    name: string;
    description: string;
    register(api: OpenClawPluginApi): void;
}
/** How the plugin reaches the daemon. ``sponsio host install openclaw``
 * writes these into ``sponsio-config.json`` beside the plugin; every
 * field has a default for a plugin deployed by hand. */
export interface SponsioOpenClawOptions {
    /** The daemon's agent socket. Default ``~/.sponsio/sponsio.sock``. */
    socketPath?: string;
    /** Plugin root a daemon started by this plugin serves. */
    pluginRoot?: string;
    /** State dir a daemon started by this plugin uses. */
    stateDir?: string;
    /** Command prefix that runs the Sponsio CLI, used to start the daemon
     * (``[python, "-m", "sponsio.cli"]`` from the installer). Default
     * ``["sponsio"]``. */
    sponsio?: string[];
    /** ``enforce`` (default) or ``observe``. */
    mode?: "enforce" | "observe";
    /** Budget for one decision, start to answer. Default 2000 ms. */
    deadlineMs?: number;
    /** Let a call through when the plugin cannot get a verdict (the
     * daemon answered with an error, or the plugin itself failed).
     * Default false: such a call is blocked. Every call let through this
     * way logs a warning. An unreachable daemon blocks either way. */
    failOpen?: boolean;
}
/** Read ``sponsio-config.json`` next to this module, if present. */
export declare function loadInstalledOptions(): SponsioOpenClawOptions;
/** Build the plugin object for a given daemon configuration. */
export declare function createSponsioOpenClawPlugin(options?: SponsioOpenClawOptions): SponsioOpenClawPlugin;
/** The plugin object the OpenClaw runtime expects, configured from the
 * installed ``sponsio-config.json``.
 *
 * Wrap with ``definePluginEntry(...)`` from
 * ``openclaw/plugin-sdk/plugin-entry`` if you want the official
 * helper's type-checking + future-proofing. The raw shape is
 * exported as default for environments that resolve it directly.
 */
export declare const sponsioOpenClawPlugin: SponsioOpenClawPlugin;
export default sponsioOpenClawPlugin;
interface ResolvedOptions {
    socketPath: string;
    pluginRoot?: string;
    stateDir?: string;
    sponsio: string[];
    mode?: "enforce" | "observe";
    deadlineMs: number;
    failOpen: boolean;
}
interface Verdict {
    allowed: boolean;
    reason: string;
}
declare function resolveOptions(o: SponsioOpenClawOptions): ResolvedOptions;
/** One RPC over the daemon socket. Rejects with ``Unreachable`` when no
 * daemon answers, with a plain ``Error`` when one answers with an error. */
export declare function callDaemon(socketPath: string, method: string, params: object, timeoutMs: number): Promise<Record<string, unknown>>;
/** Ask the daemon for a verdict, starting it once if needed. An
 * unreachable daemon is a block. */
declare function decide(o: ResolvedOptions, payload: object): Promise<Verdict>;
/**
 * @deprecated Pass ``sponsioOpenClawPlugin`` (or the default export)
 * to ``definePluginEntry`` instead. Kept for early consumers who
 * imported the register fn as the default export.
 */
export declare function register(api: OpenClawPluginApi): void;
/** Internal — exported for tests + advanced wrappers only. */
export declare const __internal: {
    callDaemon: typeof callDaemon;
    decide: typeof decide;
    resolveOptions: typeof resolveOptions;
};
