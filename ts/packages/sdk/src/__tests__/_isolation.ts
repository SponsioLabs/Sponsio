/**
 * Loaded into every test process before any test module
 * (``node --import ./dist/__tests__/_isolation.js --test dist/``).
 *
 * The cloud client falls back to ``~/.sponsio/credentials`` when
 * ``SPONSIO_API_KEY`` is unset, and its default base URL is production.
 * A test that deleted the env var to check the no-key path therefore read
 * the developer's real key and uploaded a run to app.sponsio.dev. So every
 * test process gets an empty HOME of its own, no cloud variables, and a
 * fetch that refuses anything but loopback. A test that needs a cloud
 * stubs ``globalThis.fetch`` itself, as ``evidence.test.ts`` does.
 */

import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const home = mkdtempSync(join(tmpdir(), "sponsio-ts-test-home-"));
process.env.HOME = home;
process.env.USERPROFILE = home;
for (const name of ["SPONSIO_API_KEY", "SPONSIO_API_URL", "SPONSIO_PROJECT"]) {
  delete process.env[name];
}

const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

const realFetch = globalThis.fetch;
globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
  const url = new URL(
    typeof input === "string" ? input : input instanceof URL ? input.href : input.url,
  );
  if (!LOOPBACK.has(url.hostname)) {
    throw new TypeError(`network disabled in tests: ${url.hostname}`);
  }
  return realFetch(input, init);
}) as typeof fetch;
