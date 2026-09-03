/**
 * The evidence client's own contract. No network: `fetch` is replaced,
 * so CI exercises the request we build and the answer we parse rather
 * than the service's availability.
 *
 * The wire shapes here were captured from `app.sponsio.dev`, and the
 * verdicts match what `sponsio/cloud/evidence.py` returns for the same
 * calls.
 */

import { test, afterEach } from "node:test";
import assert from "node:assert/strict";

import {
  EvidenceClient,
  EvidenceError,
  isBlocking,
  needsClarification,
  normalizeInputs,
} from "../cloud/evidence.js";

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

/** Capture the outgoing request and answer with a canned payload. */
function stubFetch(
  payload: unknown,
  status = 200,
): { seen: () => { url: string; body: Record<string, unknown> } } {
  let url = "";
  let body: Record<string, unknown> = {};
  globalThis.fetch = (async (input: string, init: RequestInit) => {
    url = String(input);
    body = JSON.parse(String(init.body));
    return {
      ok: status >= 200 && status < 300,
      status,
      text: async () => JSON.stringify(payload),
      json: async () => payload,
    } as Response;
  }) as typeof fetch;
  return { seen: () => ({ url, body }) };
}

const PASS = {
  predicate: "items_sum_to_total",
  verdict: "PASS",
  action: "release",
  evidence: { values: [{ items: [40, 60] }], ts: 1788451084.63, source: "submitted_output", table_version: null },
  correction: null,
  clarify_on: null,
  attestation_id: "7afe536c-4470-4fde-ab2b-ad9c867e8d0b",
};

const MISMATCH = { ...PASS, verdict: "MISMATCH", action: "block" };

test("a claim is sent in the wire shape the server accepts", async () => {
  const stub = stubFetch(PASS);
  const client = new EvidenceClient({ apiKey: "sk_test", url: "https://api.test" });
  await client.verify("items_sum_to_total", {
    value: 100,
    inputs: { items: [[40, 60], "submitted_output"], total: [100, "submitted_output"] },
  });

  const { url, body } = stub.seen();
  assert.equal(url, "https://api.test/v1/evidence/verify");
  assert.equal(body.predicate, "items_sum_to_total");
  // `claim` is a nested object, not a bare value: sending the value flat
  // is a 422 from the server, which is how this shape was established.
  assert.deepEqual(body.claim, { value: 100, text: null });
  assert.deepEqual(body.inputs, {
    items: { value: [40, 60], source: "submitted_output" },
    total: { value: 100, source: "submitted_output" },
  });
});

test("a PASS releases and a MISMATCH blocks", async () => {
  const client = new EvidenceClient({ apiKey: "sk_test", url: "https://api.test" });

  stubFetch(PASS);
  const pass = await client.verify("items_sum_to_total", { value: 100 });
  assert.equal(pass.verdict, "PASS");
  assert.equal(isBlocking(pass), false);
  assert.equal(pass.attestationId, "7afe536c-4470-4fde-ab2b-ad9c867e8d0b");
  assert.deepEqual(pass.values, [{ items: [40, 60] }]);

  stubFetch(MISMATCH);
  const bad = await client.verify("items_sum_to_total", { value: 999 });
  assert.equal(isBlocking(bad), true);
  assert.equal(needsClarification(bad), false);
});

test("a clarify verdict is not a block", async () => {
  stubFetch({ ...PASS, verdict: "UNDERDETERMINED", action: "clarify", clarify_on: ["total"] });
  const client = new EvidenceClient({ apiKey: "sk_test", url: "https://api.test" });
  const r = await client.verify("items_sum_to_total", { value: 100 });
  assert.equal(needsClarification(r), true);
  assert.equal(isBlocking(r), false);
  assert.deepEqual(r.clarifyOn, ["total"]);
});

test("an untagged input fails locally, naming itself", () => {
  // The server refuses it under the taint rule anyway. Failing here says
  // which input is missing its tag instead of returning a 400 about one.
  assert.throws(
    () => normalizeInputs({ items: [40, 60] as never }),
    /input 'items' needs a source tag/,
  );
  assert.deepEqual(normalizeInputs({ a: [1, "claim"] }), {
    a: { value: 1, source: "claim" },
  });
  assert.deepEqual(normalizeInputs({ a: { value: 1, source: "claim" } }), {
    a: { value: 1, source: "claim" },
  });
});

test("a server refusal surfaces its own detail, not a generic failure", async () => {
  stubFetch(
    { detail: "input 'items' came from source 'user', which the predicate's taint allowlist does not permit" },
    400,
  );
  const client = new EvidenceClient({ apiKey: "sk_test", url: "https://api.test" });
  await assert.rejects(
    () => client.verify("items_sum_to_total", { value: 100 }),
    (err: EvidenceError) => {
      assert.equal(err.status, 400);
      assert.match(err.message, /taint allowlist/);
      return true;
    },
  );
});

test("no key is an error before any request goes out", async () => {
  let called = false;
  globalThis.fetch = (async () => {
    called = true;
    return {} as Response;
  }) as typeof fetch;
  const client = new EvidenceClient({ apiKey: "", url: "https://api.test" });
  assert.equal(client.configured, false);
  await assert.rejects(() => client.verify("x"), /SPONSIO_API_KEY/);
  assert.equal(called, false, "a keyless client must not reach the network");
});

test("a base url with many trailing slashes does not stall", () => {
  // CodeQL caught `/\/+$/` here: polynomial backtracking on a string of
  // slashes, and the url comes from an env var, so its shape is not ours
  // to assume. Stripping is a linear scan now.
  const started = Date.now();
  const client = new EvidenceClient({
    apiKey: "k",
    url: "https://a.test" + "/".repeat(200_000),
  });
  assert.equal(client.url, "https://a.test");
  assert.ok(Date.now() - started < 1000, "trailing-slash stripping must stay linear");
});
