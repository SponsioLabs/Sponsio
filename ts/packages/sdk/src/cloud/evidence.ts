/**
 * The evidence lane, from TypeScript.
 *
 * Rules check what an agent *does*; evidence checks what it *says*. A
 * model that reports "the three line items total $100" is making a claim
 * that is either true of the numbers it was given or is not, and no
 * amount of prompting settles it. This sends the claim to
 * `/v1/evidence/verify`, which resolves it against the declared inputs
 * and answers PASS, MISMATCH, or a verdict meaning it could not tell.
 *
 * Parity with `sponsio/cloud/evidence.py`: same endpoint, same wire
 * shape, same verdict vocabulary. One service, one meter — the server
 * records usage per tenant and cannot see which SDK called it.
 *
 * The judgement is entirely server-side. This file normalises inputs,
 * makes one request, and classifies the answer.
 *
 * @module @sponsio/sdk/cloud/evidence
 */

/** A verdict the server can return. Anything else is treated as unknown. */
export type EvidenceVerdict =
  | "PASS"
  | "MISMATCH"
  | "NO_EVIDENCE"
  | "SOURCE_UNAVAILABLE"
  | "UNDERDETERMINED";

/** What the server says to do about a verdict. */
export type EvidenceAction = "release" | "block" | "clarify";

/**
 * One input, tagged with where its value came from.
 *
 * The tag is not decoration. Each predicate declares which sources it
 * will accept, and the server refuses a value that arrived from anywhere
 * else: a total the model made up must not be checked against itself.
 * `GET /v1/evidence/predicates` lists the allowlist per predicate.
 */
export interface TaggedInput {
  value: unknown;
  source: string;
}

/** Ergonomic forms accepted per input: a tagged object or a `[value, source]` pair. */
export type InputSpec = TaggedInput | [unknown, string];

export interface EvidenceResult {
  predicate: string;
  verdict: EvidenceVerdict | string;
  action: EvidenceAction | string;
  /** The resolved values the verdict was reached from. */
  values: unknown[];
  evidenceTs?: number;
  source?: string;
  tableVersion?: string;
  /** The right value, when the server knows it. Never invented. */
  correction?: unknown;
  /** What to ask about, on a clarify action. */
  clarifyOn: unknown[];
  /** Server-side record of this check, for an audit trail. */
  attestationId?: string;
}

/** The check did not reach a verdict. The caller decides open or closed. */
export class EvidenceError extends Error {
  readonly status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = "EvidenceError";
    this.status = status;
  }
}

/** True when this verdict must stop the response. */
export function isBlocking(result: EvidenceResult): boolean {
  return result.action === "block";
}

/** True when this verdict asks the agent to clarify rather than stop. */
export function needsClarification(result: EvidenceResult): boolean {
  return result.action === "clarify";
}

/**
 * Ergonomic input forms to the wire's tagged shape.
 *
 * An untagged input is rejected here rather than sent. The server would
 * refuse it anyway under the taint rule, and failing locally names the
 * input that is missing its tag.
 *
 * The pair form needs a string source. Without that check a two-element
 * array of data read as a pair: `{ items: [40, 60] }` became
 * `value: 40, source: "60"`, and the server then refused source `"60"`
 * as untrusted, an error naming nothing the caller had written. A list
 * value takes the explicit form, `[[40, 60], "submitted_output"]`.
 */
export function normalizeInputs(
  inputs?: Record<string, InputSpec>,
): Record<string, TaggedInput> {
  const wire: Record<string, TaggedInput> = {};
  for (const [name, spec] of Object.entries(inputs ?? {})) {
    if (Array.isArray(spec) && spec.length === 2 && typeof spec[1] === "string") {
      wire[name] = { value: spec[0], source: spec[1] };
    } else if (spec && typeof spec === "object" && "source" in spec) {
      wire[name] = {
        value: (spec as TaggedInput).value,
        source: String((spec as TaggedInput).source),
      };
    } else {
      throw new Error(
        `input '${name}' needs a source tag: pass [value, source] or ` +
          `{ value, source } — the server rejects untagged inputs (taint rule)`,
      );
    }
  }
  return wire;
}

function parseResult(payload: Record<string, unknown>): EvidenceResult {
  const evidence = (payload.evidence ?? {}) as Record<string, unknown>;
  const rawValues = evidence.values;
  const clarify = payload.clarify_on;
  return {
    predicate: String(payload.predicate ?? ""),
    verdict: String(payload.verdict ?? ""),
    action: String(payload.action ?? ""),
    values: Array.isArray(rawValues) ? rawValues : [],
    ...(typeof evidence.ts === "number" ? { evidenceTs: evidence.ts } : {}),
    ...(typeof evidence.source === "string" ? { source: evidence.source } : {}),
    ...(typeof evidence.table_version === "string"
      ? { tableVersion: evidence.table_version }
      : {}),
    ...(payload.correction !== null && payload.correction !== undefined
      ? { correction: payload.correction }
      : {}),
    clarifyOn: Array.isArray(clarify) ? clarify : [],
    ...(typeof payload.attestation_id === "string"
      ? { attestationId: payload.attestation_id }
      : {}),
  };
}

export interface EvidenceClientOptions {
  /** Defaults to `SPONSIO_API_KEY`. */
  apiKey?: string;
  /** Defaults to `SPONSIO_API_URL`, then `https://app.sponsio.dev`. */
  url?: string;
  /** Per-request timeout in milliseconds. */
  timeoutMs?: number;
}

export class EvidenceClient {
  readonly url: string;
  private readonly apiKey: string | undefined;
  private readonly timeoutMs: number;

  constructor(opts: EvidenceClientOptions = {}) {
    this.apiKey = opts.apiKey ?? process.env.SPONSIO_API_KEY ?? undefined;
    this.url = (
      opts.url ??
      process.env.SPONSIO_API_URL ??
      "https://app.sponsio.dev"
    ).replace(/\/+$/, "");
    this.timeoutMs = opts.timeoutMs ?? 10_000;
  }

  get configured(): boolean {
    return Boolean(this.apiKey);
  }

  private async post(
    path: string,
    body: unknown,
  ): Promise<Record<string, unknown>> {
    if (!this.apiKey) {
      throw new EvidenceError(
        "no API key: set SPONSIO_API_KEY or pass apiKey to EvidenceClient",
      );
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    let response: Response;
    try {
      response = await fetch(`${this.url}${path}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${this.apiKey}`,
          "Content-Type": "application/json",
          "User-Agent": "sponsio-sdk-ts",
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (err) {
      throw new EvidenceError(
        `cannot reach ${this.url}: ${err instanceof Error ? err.message : err}`,
      );
    } finally {
      clearTimeout(timer);
    }

    const text = await response.text();
    let payload: unknown;
    try {
      payload = text ? JSON.parse(text) : {};
    } catch {
      throw new EvidenceError(
        `${this.url} answered ${response.status} with non-JSON`,
        response.status,
      );
    }
    if (!response.ok) {
      const detail = (payload as Record<string, unknown>)?.detail;
      throw new EvidenceError(
        typeof detail === "string" ? detail : `verify failed (${response.status})`,
        response.status,
      );
    }
    return payload as Record<string, unknown>;
  }

  /**
   * Verify one claim against fresh evidence, server-side.
   *
   * Throws `EvidenceError` when the check did not run to a verdict.
   * Decide open or closed at the call site; closed means treating the
   * throw as a block.
   */
  async verify(
    predicate: string,
    opts: {
      value?: unknown;
      text?: string;
      inputs?: Record<string, InputSpec>;
      sessionId?: string;
    } = {},
  ): Promise<EvidenceResult> {
    const body: Record<string, unknown> = {
      predicate,
      claim: { value: opts.value ?? null, text: opts.text ?? null },
      inputs: normalizeInputs(opts.inputs),
    };
    if (opts.sessionId) body.session_id = opts.sessionId;
    return parseResult(await this.post("/v1/evidence/verify", body));
  }

  /** The predicate catalog this key can reach. */
  async predicates(): Promise<Record<string, unknown>[]> {
    if (!this.apiKey) {
      throw new EvidenceError(
        "no API key: set SPONSIO_API_KEY or pass apiKey to EvidenceClient",
      );
    }
    const response = await fetch(`${this.url}/v1/evidence/predicates`, {
      headers: {
        Authorization: `Bearer ${this.apiKey}`,
        "User-Agent": "sponsio-sdk-ts",
      },
    });
    if (!response.ok) {
      throw new EvidenceError(
        `predicates failed (${response.status})`,
        response.status,
      );
    }
    return (await response.json()) as Record<string, unknown>[];
  }
}
