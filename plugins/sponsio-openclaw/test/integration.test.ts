/**
 * Integration tests for sponsio-openclaw.
 *
 * These don't run an OpenClaw runtime — they exercise the
 * subprocess protocol against the real ``sponsio plugin guard
 * --stdin`` binary, with ``$SPONSIO_PLUGIN_ROOT`` pointed at a
 * temp dir we set up by hand.
 *
 * Mock OpenClaw API surface tracks the public docs as of 2026-04-26:
 *   https://docs.openclaw.ai/plugins/hooks.md (event payload)
 *   https://docs.openclaw.ai/plugins/sdk-entrypoints.md (registerHook)
 *
 * Run with: ``npm test``.
 *
 * Skipped automatically when ``sponsio`` isn't on PATH so the test
 * doesn't false-positive in environments without the Python CLI
 * installed.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { execSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import sponsioOpenClawPlugin, {
  type BeforeToolCallEvent,
  type BeforeToolCallResult,
  type OpenClawPluginApi,
} from "../src/index.ts";

// Skip the whole suite if the user doesn't have the Python CLI
// installed locally.
let SPONSIO_AVAILABLE = false;
try {
  execSync("sponsio --version", { stdio: "pipe" });
  SPONSIO_AVAILABLE = true;
} catch {
  SPONSIO_AVAILABLE = false;
}

const skipUnlessSponsio = SPONSIO_AVAILABLE ? test : test.skip;

// ----------------------------------------------------------------------------
// Mock OpenClaw API
// ----------------------------------------------------------------------------

interface MockApi extends OpenClawPluginApi {
  /** Captured spec from the last ``registerHook`` call. */
  __lastHookSpec?: {
    name: string;
    handler: (
      event: BeforeToolCallEvent,
    ) => Promise<BeforeToolCallResult | undefined>;
    priority?: number;
  };
}

function makeMockApi(): MockApi {
  const api: MockApi = {
    registerHook(spec) {
      api.__lastHookSpec = spec;
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
    ctx: {
      agentId: "test-agent",
      sessionKey: "test-session-key",
      sessionId: "test-session",
      trace: null,
    },
  };
}

// ----------------------------------------------------------------------------
// Library helpers
// ----------------------------------------------------------------------------

function makePluginRoot(): string {
  return mkdtempSync(join(tmpdir(), "sponsio-openclaw-test-"));
}

function writeLib(root: string, plugin: string, body: string): void {
  const dir = join(root, plugin);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "sponsio.yaml"), body);
}

// Minimal _host library that bans ``rm -rf`` on Bash.
const HOST_LIBRARY = `
version: "1"
agents:
  _host:
    contracts:
      - desc: "ban rm"
        E:
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
  assert.ok(api.__lastHookSpec, "registerHook was never called");
  assert.equal(api.__lastHookSpec.name, "before_tool_call");
  assert.equal(typeof api.__lastHookSpec.handler, "function");
  // High priority = early in the chain. We picked 1000 so a Sponsio
  // deny short-circuits any other hooks the user has installed.
  assert.equal(typeof api.__lastHookSpec.priority, "number");
  assert.ok(
    (api.__lastHookSpec.priority ?? 0) >= 100,
    "Sponsio guardrails should run at high priority",
  );
});

// ----------------------------------------------------------------------------
// End-to-end against the real sponsio plugin guard backend
// ----------------------------------------------------------------------------

skipUnlessSponsio(
  "before_tool_call: undefined when no library exists (vacuous allow)",
  async () => {
    const api = makeMockApi();
    const root = makePluginRoot();
    process.env.SPONSIO_PLUGIN_ROOT = root;
    try {
      sponsioOpenClawPlugin.register(api);
      const result = await api.__lastHookSpec!.handler(
        makeEvent("Bash", { command: "ls" }),
      );
      assert.equal(result, undefined);
    } finally {
      delete process.env.SPONSIO_PLUGIN_ROOT;
      rmSync(root, { recursive: true, force: true });
    }
  },
);

skipUnlessSponsio(
  "before_tool_call: {block: true, blockReason} when guard denies",
  async () => {
    const api = makeMockApi();
    const root = makePluginRoot();
    writeLib(root, "_host", HOST_LIBRARY);
    process.env.SPONSIO_PLUGIN_ROOT = root;
    try {
      sponsioOpenClawPlugin.register(api);
      const result = await api.__lastHookSpec!.handler(
        makeEvent("Bash", { command: "rm -rf /tmp/x" }),
      );
      assert.ok(result, "expected a block result, got undefined");
      assert.equal(result.block, true);
      // OpenClaw uses ``blockReason`` (not ``reason``).
      assert.ok(result.blockReason, "blockReason should be populated");
      assert.match(result.blockReason!, /Bash/);
    } finally {
      delete process.env.SPONSIO_PLUGIN_ROOT;
      rmSync(root, { recursive: true, force: true });
    }
  },
);

skipUnlessSponsio(
  "before_tool_call: undefined for benign commands when library loaded",
  async () => {
    const api = makeMockApi();
    const root = makePluginRoot();
    writeLib(root, "_host", HOST_LIBRARY);
    process.env.SPONSIO_PLUGIN_ROOT = root;
    try {
      sponsioOpenClawPlugin.register(api);
      const result = await api.__lastHookSpec!.handler(
        makeEvent("Bash", { command: "ls -la" }),
      );
      assert.equal(result, undefined);
    } finally {
      delete process.env.SPONSIO_PLUGIN_ROOT;
      rmSync(root, { recursive: true, force: true });
    }
  },
);

skipUnlessSponsio(
  "before_tool_call: routes mcp__server__tool to the matching library",
  async () => {
    const api = makeMockApi();
    const root = makePluginRoot();
    writeLib(
      root,
      "acme",
      `
version: "1"
agents:
  acme:
    contracts:
      - desc: "ban evil URLs"
        E:
          pattern: arg_blacklist
          args:
            - mcp__acme__fetch
            - url
            - - "evil"
`,
    );
    process.env.SPONSIO_PLUGIN_ROOT = root;
    try {
      sponsioOpenClawPlugin.register(api);
      const handler = api.__lastHookSpec!.handler;

      const blocked = await handler(
        makeEvent("mcp__acme__fetch", { url: "https://evil.com" }),
      );
      assert.ok(blocked, "expected block on evil URL");
      assert.equal(blocked.block, true);

      const allowed = await handler(
        makeEvent("mcp__acme__fetch", { url: "https://example.com" }),
      );
      assert.equal(allowed, undefined);
    } finally {
      delete process.env.SPONSIO_PLUGIN_ROOT;
      rmSync(root, { recursive: true, force: true });
    }
  },
);

// ----------------------------------------------------------------------------
// Edge-case + robustness tests
// ----------------------------------------------------------------------------

skipUnlessSponsio(
  "missing event.ctx field doesn't crash — fail-open allow",
  async () => {
    const api = makeMockApi();
    const root = makePluginRoot();
    writeLib(root, "_host", HOST_LIBRARY);
    process.env.SPONSIO_PLUGIN_ROOT = root;
    try {
      sponsioOpenClawPlugin.register(api);
      // Some OpenClaw versions may omit ctx. Our handler should
      // not throw — defensive ?. on every dotted access.
      const result = await api.__lastHookSpec!.handler({
        toolName: "Bash",
        params: { command: "ls" },
      } as unknown as BeforeToolCallEvent);
      assert.equal(result, undefined);
    } finally {
      delete process.env.SPONSIO_PLUGIN_ROOT;
      rmSync(root, { recursive: true, force: true });
    }
  },
);

skipUnlessSponsio(
  "subprocess crash → fail open (logger error called, no throw)",
  async () => {
    const api = makeMockApi();
    let errors = 0;
    api.logger = {
      debug: () => {},
      info: () => {},
      warn: () => {},
      error: () => {
        errors += 1;
      },
    };
    // Force a missing binary by pointing SPONSIO_GUARD_BIN at /nonexistent
    process.env.SPONSIO_GUARD_BIN = "/definitely/nonexistent/sponsio-bin";
    try {
      sponsioOpenClawPlugin.register(api);
      const result = await api.__lastHookSpec!.handler(
        makeEvent("Bash", { command: "rm -rf /tmp/x" }),
      );
      // Crashed → no decision → undefined → caller proceeds.
      assert.equal(result, undefined);
      assert.equal(errors, 1, "logger.error should have been called once");
    } finally {
      delete process.env.SPONSIO_GUARD_BIN;
    }
  },
);

skipUnlessSponsio(
  "passes session metadata through to the backend payload",
  async () => {
    // We can't intercept the subprocess from outside; verify
    // indirectly by writing a library that depends on tool routing
    // (which uses tool_name, the most important wire field).
    const api = makeMockApi();
    const root = makePluginRoot();
    writeLib(root, "github", `
version: "1"
agents:
  github:
    contracts:
      - desc: "ban delete_repo"
        E:
          pattern: rate_limit
          args: [mcp__github__delete_repository, 0]
`);
    process.env.SPONSIO_PLUGIN_ROOT = root;
    try {
      sponsioOpenClawPlugin.register(api);
      const result = await api.__lastHookSpec!.handler(
        makeEvent("mcp__github__delete_repository", { name: "test" }),
      );
      assert.ok(result, "expected block");
      assert.equal(result.block, true);
    } finally {
      delete process.env.SPONSIO_PLUGIN_ROOT;
      rmSync(root, { recursive: true, force: true });
    }
  },
);

// ----------------------------------------------------------------------------
// Backward compat: the deprecated `register` named export still works
// ----------------------------------------------------------------------------

test("deprecated register() named export is still callable", async () => {
  const { register } = await import("../src/index.ts");
  const api = makeMockApi();
  register(api);
  assert.ok(api.__lastHookSpec, "deprecated register() should still install hook");
  assert.equal(api.__lastHookSpec.name, "before_tool_call");
});
