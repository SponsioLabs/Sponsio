/**
 * Integration tests for sponsio-openclaw-shield.
 *
 * These don't run an OpenClaw runtime — they exercise the
 * subprocess protocol against the real ``sponsio shield guard
 * --stdin`` binary, with ``$SPONSIO_PLUGIN_ROOT`` pointed at a
 * temp dir we set up by hand.
 *
 * Run with: ``npm test`` (which calls ``tsc && node --test test/``).
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

// Import directly from source — tests run with Node's
// ``--experimental-strip-types`` so we don't need a compile step.
import register, { type OpenClawPluginApi } from "../src/index.ts";

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
// Helpers
// ----------------------------------------------------------------------------

interface MockApi extends OpenClawPluginApi {
  __handlers: Map<string, (e: unknown) => Promise<unknown>>;
}

function makeMockApi(): MockApi {
  const handlers = new Map<string, (e: unknown) => Promise<unknown>>();
  return {
    __handlers: handlers,
    registerHook(event, handler) {
      handlers.set(event, handler as (e: unknown) => Promise<unknown>);
    },
  };
}

function makeShieldRoot(): string {
  const root = mkdtempSync(join(tmpdir(), "sponsio-openclaw-test-"));
  return root;
}

function writeLib(root: string, plugin: string, body: string): void {
  const dir = join(root, plugin);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "sponsio.yaml"), body);
}

// Minimal _host library that bans ``rm -rf`` on Bash. Enough to
// validate the deny path end-to-end.
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
// Tests
// ----------------------------------------------------------------------------

skipUnlessSponsio(
  "register: hook is installed for before_tool_call",
  () => {
    const api = makeMockApi();
    register(api);
    assert.equal(api.__handlers.size, 1);
    assert.ok(api.__handlers.has("before_tool_call"));
  },
);

skipUnlessSponsio(
  "before_tool_call returns undefined when no library exists",
  async () => {
    const api = makeMockApi();
    const root = makeShieldRoot();
    process.env.SPONSIO_PLUGIN_ROOT = root;
    try {
      register(api);
      const handler = api.__handlers.get("before_tool_call")!;
      const result = await handler({
        tool: { name: "Bash" },
        args: { command: "ls" },
      });
      assert.equal(
        result,
        undefined,
        "no library → should be allowed (undefined)",
      );
    } finally {
      delete process.env.SPONSIO_PLUGIN_ROOT;
      rmSync(root, { recursive: true, force: true });
    }
  },
);

skipUnlessSponsio(
  "before_tool_call returns {block: true} when shield denies",
  async () => {
    const api = makeMockApi();
    const root = makeShieldRoot();
    writeLib(root, "_host", HOST_LIBRARY);
    process.env.SPONSIO_PLUGIN_ROOT = root;
    try {
      register(api);
      const handler = api.__handlers.get("before_tool_call")!;
      const result = (await handler({
        tool: { name: "Bash" },
        args: { command: "rm -rf /tmp/x" },
      })) as { block: true; reason: string } | undefined;

      assert.ok(result, "expected a block result, got undefined");
      assert.equal(result.block, true);
      assert.match(
        result.reason,
        /Bash/,
        "reason should mention the rule",
      );
    } finally {
      delete process.env.SPONSIO_PLUGIN_ROOT;
      rmSync(root, { recursive: true, force: true });
    }
  },
);

skipUnlessSponsio(
  "before_tool_call allows benign commands",
  async () => {
    const api = makeMockApi();
    const root = makeShieldRoot();
    writeLib(root, "_host", HOST_LIBRARY);
    process.env.SPONSIO_PLUGIN_ROOT = root;
    try {
      register(api);
      const handler = api.__handlers.get("before_tool_call")!;
      const result = await handler({
        tool: { name: "Bash" },
        args: { command: "ls -la" },
      });
      assert.equal(result, undefined);
    } finally {
      delete process.env.SPONSIO_PLUGIN_ROOT;
      rmSync(root, { recursive: true, force: true });
    }
  },
);

skipUnlessSponsio(
  "before_tool_call routes mcp__server__tool to the right library",
  async () => {
    const api = makeMockApi();
    const root = makeShieldRoot();
    // Library for the "acme" plugin id, blocks any URL with "evil"
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
      register(api);
      const handler = api.__handlers.get("before_tool_call")!;
      const blocked = (await handler({
        tool: { name: "mcp__acme__fetch" },
        args: { url: "https://evil.com" },
      })) as { block: true; reason: string } | undefined;
      assert.ok(blocked, "expected block on evil URL");
      assert.equal(blocked.block, true);

      const allowed = await handler({
        tool: { name: "mcp__acme__fetch" },
        args: { url: "https://example.com" },
      });
      assert.equal(allowed, undefined);
    } finally {
      delete process.env.SPONSIO_PLUGIN_ROOT;
      rmSync(root, { recursive: true, force: true });
    }
  },
);
