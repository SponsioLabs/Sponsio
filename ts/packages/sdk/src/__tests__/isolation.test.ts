/**
 * The test isolation in ``_isolation.ts`` holds.
 *
 * Nothing here moves HOME or stubs fetch: these call the credential and
 * upload paths the way a careless test would, and pass only because the
 * setup module ran first. If ``npm test`` stops loading it, they fail.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { homedir, userInfo } from "node:os";

import { CloudClient, readApiKey } from "../cloud/client.js";
import { Sponsio } from "../index.js";

test("HOME is not the real home directory", () => {
  assert.notEqual(homedir(), userInfo().homedir);
});

test("no cloud variable reaches a test", () => {
  assert.equal(process.env.SPONSIO_API_KEY, undefined);
  assert.equal(process.env.SPONSIO_API_URL, undefined);
});

test("the real credentials file is not read", () => {
  assert.equal(readApiKey(), null);
  assert.equal(new CloudClient().configured, false);
});

test("a request to the cloud never leaves the machine", async () => {
  // A key is supplied, so only the network guard stands between this
  // call and app.sponsio.dev.
  const client = new CloudClient({ apiKey: "sk-test-not-a-key" });
  assert.equal(client.base, "https://app.sponsio.dev");
  await assert.rejects(client.pullRulebook("p"), /network disabled in tests/);
});

test("an upload with a key resolves false and sends nothing", async () => {
  const g = new Sponsio({ agentId: "a", contracts: [], mode: "observe" });
  const client = new CloudClient({ apiKey: "sk-test-not-a-key" });
  const quiet = process.env.SPONSIO_QUIET;
  process.env.SPONSIO_QUIET = "1";
  try {
    assert.equal(await g.uploadSession({ client }), false);
  } finally {
    if (quiet === undefined) delete process.env.SPONSIO_QUIET;
    else process.env.SPONSIO_QUIET = quiet;
  }
});
