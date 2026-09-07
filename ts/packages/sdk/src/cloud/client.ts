/**
 * Talking to the cloud.
 *
 * Port of ``sponsio/cloud/client.py`` narrowed to the two calls an agent
 * makes at runtime: pull the book it should enforce, and upload what it
 * did. Everything else in the Python client belongs to the CLI.
 *
 * Until this existed the TypeScript SDK enforced correctly and uploaded
 * nothing, so a customer running a TypeScript agent showed up in the
 * console with no runs at all: rules could not be rolled out to them and
 * nothing they did could be seen.
 */

import { homedir } from "node:os";
import { join } from "node:path";
import { readFileSync } from "node:fs";

const DEFAULT_BASE_URL = "https://app.sponsio.dev";
const CREDENTIALS_PATH = join(homedir(), ".sponsio", "credentials");

export class CloudError extends Error {
  readonly status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = "CloudError";
    if (status !== undefined) this.status = status;
  }
}

/**
 * ``SPONSIO_API_KEY`` first, then ``~/.sponsio/credentials``. The
 * environment wins so CI and one-off runs can override a logged-in
 * machine without touching files.
 */
export function readApiKey(): string | null {
  const env = (process.env.SPONSIO_API_KEY ?? "").trim();
  if (env) return env;
  try {
    for (const line of readFileSync(CREDENTIALS_PATH, "utf8").split("\n")) {
      const t = line.trim();
      if (!t || t.startsWith("#")) continue;
      const eq = t.indexOf("=");
      if (eq < 0) continue;
      if (t.slice(0, eq).trim() === "api_key") {
        const v = t.slice(eq + 1).trim();
        if (v) return v;
      }
    }
  } catch {
    return null;
  }
  return null;
}

/**
 * ``SPONSIO_PROJECT``, or null.
 *
 * Which customer a run belongs to is deployment configuration, not code.
 * A platform running one agent for forty customers ships one image and
 * varies the environment; naming the customer in the call would mean an
 * image per customer.
 */
export function readProject(): string | null {
  return (process.env.SPONSIO_PROJECT ?? "").trim() || null;
}

/**
 * Strip trailing slashes without a regex.
 *
 * ``/\/+$/`` is quadratic on a string that is mostly slashes, and the
 * base URL comes from the environment on a machine we do not run.
 */
function trimSlashes(url: string): string {
  let end = url.length;
  while (end > 0 && url.charCodeAt(end - 1) === 47) end--;
  return url.slice(0, end);
}

export function baseUrl(): string {
  const explicit = (process.env.SPONSIO_API_URL ?? "").trim();
  return trimSlashes(explicit || DEFAULT_BASE_URL);
}

export interface PulledRulebook {
  yamlText: string;
  versions?: string;
  sha?: string;
  agent?: string;
  channel?: string;
}

export interface CloudClientOptions {
  apiKey?: string | null;
  baseUrl?: string;
  /** Milliseconds. A run must not wait on telemetry. */
  timeoutMs?: number;
}

export class CloudClient {
  readonly apiKey: string | null;
  readonly base: string;
  readonly timeoutMs: number;

  constructor(opts: CloudClientOptions = {}) {
    this.apiKey = opts.apiKey === undefined ? readApiKey() : opts.apiKey;
    this.base = trimSlashes(opts.baseUrl ?? baseUrl());
    this.timeoutMs = opts.timeoutMs ?? 10_000;
  }

  /** Whether there is a credential to speak with at all. */
  get configured(): boolean {
    return Boolean(this.apiKey);
  }

  private async request(
    method: string,
    path: string,
    body?: string,
    contentType?: string,
  ): Promise<{ status: number; text: string; headers: Headers }> {
    if (!this.apiKey) throw new CloudError("no API key configured");
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const headers: Record<string, string> = {
        Authorization: `Bearer ${this.apiKey}`,
      };
      if (contentType) headers["Content-Type"] = contentType;
      const res = await fetch(this.base + path, {
        method,
        headers,
        ...(body === undefined ? {} : { body }),
        signal: controller.signal,
      });
      return { status: res.status, text: await res.text(), headers: res.headers };
    } finally {
      clearTimeout(timer);
    }
  }

  private static detail(text: string, fallback: string): string {
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      const d = parsed.detail;
      if (typeof d === "string") return d;
      if (d && typeof d === "object") {
        const o = d as { message?: string; error?: string };
        return o.message ?? o.error ?? fallback;
      }
    } catch {
      /* not JSON; the fallback says what happened */
    }
    return fallback;
  }

  /**
   * Fetch a project's rulebook.
   *
   * Without ``agent`` this returns every agent merged into one document:
   * a guard binds by agent id and loads only its own section, so one
   * request serves a whole repo.
   */
  async pullRulebook(
    project?: string | null,
    opts: { agent?: string; version?: number } = {},
  ): Promise<PulledRulebook> {
    const q: string[] = [];
    if (project) q.push(`project=${encodeURIComponent(project)}`);
    if (opts.agent) q.push(`agent=${encodeURIComponent(opts.agent)}`);
    if (opts.version !== undefined) q.push(`version=${opts.version}`);
    const path = "/v1/rulebook" + (q.length ? "?" + q.join("&") : "");

    const { status, text, headers } = await this.request("GET", path);
    if (status === 404) {
      // "Nothing published yet" rather than a failure: the caller may
      // publish its local yaml instead of giving up.
      throw new CloudError(
        CloudClient.detail(text, "no rulebook published"),
        404,
      );
    }
    if (status !== 200) {
      throw new CloudError(
        CloudClient.detail(text, `pull failed (${status})`),
        status,
      );
    }
    const out: PulledRulebook = { yamlText: text };
    const v = headers.get("x-rulebook-versions") ?? headers.get("x-rulebook-version");
    if (v) out.versions = v;
    const sha = headers.get("x-rulebook-sha");
    if (sha) out.sha = sha;
    const agent = headers.get("x-rulebook-agent");
    if (agent) out.agent = agent;
    const channel = headers.get("x-rulebook-channel");
    if (channel) out.channel = channel;
    return out;
  }

  /**
   * Send a run to the cloud.
   *
   * Idempotent on the session key server-side, so a retry after a blip
   * cannot double a run. Callers on the hot path must not wait on this:
   * telemetry is outbound-only and best-effort, and losing it must never
   * change what the agent does.
   */
  async ingestSession(
    project: string | null,
    payload: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const path =
      "/v1/sessions/ingest" +
      (project ? `?project=${encodeURIComponent(project)}` : "");
    const { status, text } = await this.request(
      "POST",
      path,
      JSON.stringify(payload),
      "application/json",
    );
    if (status !== 200) {
      throw new CloudError(
        CloudClient.detail(text, `ingest failed (${status})`),
        status,
      );
    }
    try {
      return JSON.parse(text) as Record<string, unknown>;
    } catch {
      throw new CloudError("ingest returned a non-JSON body", status);
    }
  }
}
