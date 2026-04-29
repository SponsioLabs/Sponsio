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
/** The plugin object the OpenClaw runtime expects.
 *
 * Wrap with ``definePluginEntry(...)`` from
 * ``openclaw/plugin-sdk/plugin-entry`` if you want the official
 * helper's type-checking + future-proofing. The raw shape is
 * exported as default for environments that resolve it directly.
 */
export declare const sponsioOpenClawPlugin: SponsioOpenClawPlugin;
export default sponsioOpenClawPlugin;
interface GuardReply {
    permissionDecision?: "allow" | "deny";
    permissionDecisionReason?: string;
}
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
declare function callGuard(payload: object): Promise<GuardReply | null>;
/**
 * @deprecated Pass ``sponsioOpenClawPlugin`` (or the default export)
 * to ``definePluginEntry`` instead. Kept for the early-prototype consumer
 * who imported the register fn as the default export.
 */
export declare function register(api: OpenClawPluginApi): void;
/** Internal — exported for tests + advanced wrappers only. */
export declare const __internal: {
    callGuard: typeof callGuard;
};
