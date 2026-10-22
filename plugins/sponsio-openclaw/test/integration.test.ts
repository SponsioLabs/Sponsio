/**
 * Integration tests for sponsio-openclaw.
 *
 * These don't run an OpenClaw runtime. They drive the plugin's hook
 * handler against a real Sponsio daemon, which the plugin itself starts
 * on a socket under a temp dir, with HOME pointed at that temp dir so
 * nothing here touches the real home directory. Each test stops the
 * daemon it caused by the pid in its own lock file.
 *
 * Mock OpenClaw API surface tracks the runtime observed in
 * ghcr.io/openclaw/openclaw:2026.4.14:
 *
 *   - api.on(hookName, handler, opts) — three positional args.
 *   - handler(event, ctx) — ctx is the SECOND arg, not event.ctx.
 *   - opts.name is the hook's unique identifier (NOT the event).
 *
 * Run with: ``npm test``. The end-to-end cases need a Python with
 * sponsio importable: ``$SPONSIO_TEST_PYTHON`` (default ``python3``).
 * They are skipped when it cannot import sponsio.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

import sponsioOpenClawPlugin, {
  createSponsioOpenClawPlugin,
  type BeforeToolCallEvent,
  type BeforeToolCallContext,
  type BeforeToolCallResult,
  type OpenClawPluginApi,
  type RegisterHookOptions,
  type SponsioOpenClawOptions,
} from "../src/index.ts";

const PYTHON = process.env.SPONSIO_TEST_PYTHON ?? "python3";
let SPONSIO_AVAILABLE = false;
try {
  execFileSync(PYTHON, ["-c", "import sponsio"], { stdio: "pipe" });
  SPONSIO_AVAILABLE = true;
} catch {
  SPONSIO_AVAILABLE = false;
}

const skipUnlessSponsio = SPONSIO_AVAILABLE ? test : test.skip;

// ----------------------------------------------------------------------------
// Mock OpenClaw API
// ----------------------------------------------------------------------------

type CapturedRegistration = {
  hookName: string;
  handler: (
    event: BeforeToolCallEvent,
    ctx: BeforeToolCallContext,
  ) => Promise<BeforeToolCallResult | undefined>;
  opts: RegisterHookOptions | undefined;
};

interface MockApi extends OpenClawPluginApi {
  /** Captured args from the last ``api.on`` call. */
  __lastRegistration?: CapturedRegistration;
}

function makeMockApi(): MockApi {
  const api: MockApi = {
    on(hookName, handler, opts) {
      api.__lastRegistration = {
        hookName: String(hookName),
        // The plugin uses the BeforeToolCall variant; cast for the
        // mock since OpenClawPluginApi.on is typed generically.
        handler: handler as CapturedRegistration["handler"],
        opts,
      };
    },
  };
  return api;
}

function makeEvent(
  toolName: string,
  params: Record<string, unknown>,
): BeforeToolCallEvent {
  return {
    toolName,
    params,
    runId: "run-test",
    toolCallId: "toolu-test",
  };
}

function makeCtx(toolName: string): BeforeToolCallContext {
  return {
    toolName,
    agentId: "test-agent",
    sessionKey: "test-session-key",
    sessionId: "test-session",
    runId: "run-test",
    toolCallId: "toolu-test",
  };
}

// ----------------------------------------------------------------------------
// Sandbox: a temp HOME with a plugin root, a socket path, a state dir
// ----------------------------------------------------------------------------

interface Sandbox {
  dir: string;
  options: SponsioOpenClawOptions;
  close(): Promise<void>;
}

function alive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function makeSandbox(libs: Record<string, string> = {}): Sandbox {
  // AF_UNIX paths are capped near 104 bytes on macOS: keep it short.
  const dir = mkdtempSync("/tmp/spoc-");
  const root = join(dir, "plugins");
  for (const [bucket, body] of Object.entries(libs)) {
    mkdirSync(join(root, bucket), { recursive: true });
    writeFileSync(join(root, bucket, "sponsio.yaml"), body);
  }
  const savedHome = process.env.HOME;
  process.env.HOME = dir;
  const options: SponsioOpenClawOptions = {
    socketPath: join(dir, "sock"),
    pluginRoot: root,
    stateDir: join(dir, "state"),
    sponsio: [PYTHON, "-m", "sponsio.cli"],
    deadlineMs: 5000,
  };
  return {
    dir,
    options,
    async close() {
      // Stop the daemon this test caused, by the pid in its own lock
      // file, and wait for it to exit: it writes its stop record into
      // the state dir on the way out.
      const lock = join(dir, "state", "daemon.lock");
      if (existsSync(lock)) {
        const pid = Number(readFileSync(lock, "utf8").trim());
        if (pid > 0 && alive(pid)) {
          process.kill(pid, "SIGTERM");
          const until = Date.now() + 5000;
          while (alive(pid) && Date.now() < until) {
            await new Promise((r) => setTimeout(r, 50));
          }
        }
      }
      process.env.HOME = savedHome;
      rmSync(dir, { recursive: true, force: true });
    },
  };
}

function lib(agent: string, contracts: string): string {
  return `
version: "1"
agents:
  ${agent}:
    contracts:
${contracts}`;
}

// ``rm -rf`` on Bash is refused.
const NO_RM = `      - desc: "ban rm"
        G:
          pattern: arg_blacklist
          args:
            - Bash
            - command
            - - "rm\\\\s+-rf"
`;

// ----------------------------------------------------------------------------
// Plugin shape — verifies the exported object matches definePluginEntry's
// expected schema (id / name / description / register).
// ----------------------------------------------------------------------------

test("plugin object exposes definePluginEntry-compatible shape", () => {
  assert.equal(sponsioOpenClawPlugin.id, "sponsio-openclaw");
  assert.ok(sponsioOpenClawPlugin.name);
  assert.ok(sponsioOpenClawPlugin.description);
  assert.equal(typeof sponsioOpenClawPlugin.register, "function");
});

test("register installs a single before_tool_call hook with high priority", () => {
  const api = makeMockApi();
  sponsioOpenClawPlugin.register(api);
  assert.ok(api.__lastRegistration, "api.on was never called");
  // The plugin must use ``api.on(hookName, handler, opts)`` for the
  // typed-hook surface.  ``api.registerHook`` writes to a different
  // registry (``registry.hooks`` / ``triggerInternalHook``) that the
  // tool-call path doesn't consult — verified against
  // pi-tools.before-tool-call's ``hookRunner.runBeforeToolCall``,
  // which reads ``registry.typedHooks``.
  assert.equal(api.__lastRegistration.hookName, "before_tool_call");
  assert.equal(typeof api.__lastRegistration.handler, "function");
  assert.ok(
    api.__lastRegistration.opts?.name &&
      api.__lastRegistration.opts.name.length > 0,
    "hook needs a unique name in opts",
  );
  // High priority = early in the chain. We picked 1000 so a Sponsio
  // deny short-circuits any other hooks the user has installed.
  assert.equal(typeof api.__lastRegistration.opts?.priority, "number");
  assert.ok(
    (api.__lastRegistration.opts?.priority ?? 0) >= 100,
    "Sponsio guardrails should run at high priority",
  );
});

// ----------------------------------------------------------------------------
// End to end against a real daemon
// ----------------------------------------------------------------------------

async function run(
  sb: Sandbox,
  tool: string,
  params: Record<string, unknown>,
  ctx: BeforeToolCallContext | undefined = makeCtx(tool),
): Promise<BeforeToolCallResult | undefined> {
  const api = makeMockApi();
  createSponsioOpenClawPlugin(sb.options).register(api);
  return api.__lastRegistration!.handler(makeEvent(tool, params), ctx as BeforeToolCallContext);
}

skipUnlessSponsio("no library: allowed, and the plugin started the daemon", async () => {
  const sb = makeSandbox();
  try {
    assert.equal(await run(sb, "Bash", { command: "ls" }), undefined);
    assert.ok(existsSync(join(sb.dir, "state", "daemon.lock")), "no daemon came up");
  } finally {
    await sb.close();
  }
});

skipUnlessSponsio("a violating call is blocked with the daemon's reason", async () => {
  const sb = makeSandbox({ _host_openclaw: lib("_host_openclaw", NO_RM) });
  try {
    const blocked = await run(sb, "Bash", { command: "rm -rf /tmp/x" });
    assert.ok(blocked, "expected a block result");
    assert.equal(blocked.block, true);
    assert.match(blocked.blockReason!, /Bash/);
    assert.equal(await run(sb, "Bash", { command: "ls -la" }), undefined);
  } finally {
    await sb.close();
  }
});

skipUnlessSponsio("observe mode logs and lets the call through", async () => {
  const sb = makeSandbox({ _host_openclaw: lib("_host_openclaw", NO_RM) });
  sb.options.mode = "observe";
  try {
    assert.equal(await run(sb, "Bash", { command: "rm -rf /tmp/x" }), undefined);
  } finally {
    await sb.close();
  }
});

skipUnlessSponsio("routes server__tool to the matching MCP library", async () => {
  const sb = makeSandbox({
    acme: lib(
      "acme",
      `      - desc: "ban evil URLs"
        G:
          pattern: arg_blacklist
          args:
            - mcp__acme__fetch
            - url
            - - "evil"
`,
    ),
  });
  try {
    const blocked = await run(sb, "acme__fetch", { url: "https://evil.com" });
    assert.equal(blocked?.block, true);
    assert.equal(await run(sb, "acme__fetch", { url: "https://example.com" }), undefined);
  } finally {
    await sb.close();
  }
});

skipUnlessSponsio("a missing ctx argument does not crash", async () => {
  const sb = makeSandbox({ _host_openclaw: lib("_host_openclaw", NO_RM) });
  try {
    assert.equal(await run(sb, "Bash", { command: "ls" }, undefined), undefined);
  } finally {
    await sb.close();
  }
});

test("no daemon and none can start: the call is blocked with a recovery hint", async () => {
  const dir = mkdtempSync("/tmp/spoc-");
  try {
    const api = makeMockApi();
    createSponsioOpenClawPlugin({
      socketPath: join(dir, "sock"),
      sponsio: ["/nonexistent/sponsio"],
      deadlineMs: 500,
    }).register(api);
    const t0 = Date.now();
    const result = await api.__lastRegistration!.handler(
      makeEvent("Bash", { command: "ls" }),
      makeCtx("Bash"),
    );
    assert.ok(Date.now() - t0 < 2000, "took longer than the deadline allows");
    assert.equal(result?.block, true);
    assert.match(result!.blockReason!, /not reachable/);
    assert.match(result!.blockReason!, /sponsio host uninstall openclaw/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
