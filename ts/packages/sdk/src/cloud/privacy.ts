/**
 * What may leave this machine.
 *
 * Port of ``sponsio/bridge/privacy.py``. Enforcement already happened
 * here — the det checks are pure and local — so nothing in this file can
 * change a verdict. It only narrows what the console is shown, which is
 * what makes it cheap: the run's shape is all the cloud needs to render
 * a dashboard, count a finding, or meter usage.
 */

import { createHash } from "node:crypto";

export const LEVELS = ["full", "shape", "hashed", "metadata", "tool_calls"] as const;
export type PrivacyLevel = (typeof LEVELS)[number];

const DEFAULT: PrivacyLevel = "full";
const STRICTEST: PrivacyLevel = "tool_calls";

function isLevel(v: string): v is PrivacyLevel {
  return (LEVELS as readonly string[]).includes(v);
}

/**
 * The level in force. The environment beats the argument, deliberately:
 * the person who decides what may leave a machine is the operator of
 * that machine, not the author of the agent running on it.
 */
export function resolve(explicit?: string): PrivacyLevel {
  const env = (process.env.SPONSIO_PRIVACY ?? "").trim().toLowerCase();
  if (isLevel(env)) return env;
  if (env) {
    // An unrecognised value is far more likely to be a typo in a
    // deployment that meant to tighten than a request to loosen, so it
    // fails closed, to the strictest level there is.
    return STRICTEST;
  }
  if (explicit && isLevel(explicit)) return explicit;
  return DEFAULT;
}

function typeOf(value: unknown): string {
  if (value === null || value === undefined) return "<null>";
  if (typeof value === "boolean") return "<bool>";
  if (typeof value === "number") {
    // The one place the two runtimes cannot agree. JavaScript has a
    // single number type, so a value Python reports as ``<float>``
    // because it was written 250.0 arrives here indistinguishable from
    // 250 and is reported ``<int>``. Reporting every number as a float
    // to match would misdescribe every integer, which is worse: a shape
    // is read to answer "what did the agent pass", and the answer for
    // this runtime is that it passed a number JavaScript calls integral.
    return Number.isInteger(value) ? "<int>" : "<float>";
  }
  if (typeof value === "string") return `<str:${value.length}>`;
  if (Array.isArray(value)) return `<list:${value.length}>`;
  if (typeof value === "object") {
    const parts = Object.entries(value as Record<string, unknown>).map(
      ([k, v]) => `${k}: ${typeOf(v)}`,
    );
    return `{${parts.join(", ")}}`;
  }
  return `<${typeof value}>`;
}

/**
 * The structure of an argument set, with every value replaced.
 *
 * Lengths survive for strings and lists because "the model passed a
 * 40 000-character path" is a debugging fact and not content, and it is
 * the one an oversize-argument rule fires on.
 */
export function shape(args: unknown): string {
  if (args === null || args === undefined) return "";
  if (typeof args === "object" && !Array.isArray(args)) {
    const parts = Object.entries(args as Record<string, unknown>).map(
      ([k, v]) => `"${k}": ${typeOf(v)}`,
    );
    return `{${parts.join(", ")}}`;
  }
  return typeOf(args);
}

/**
 * ``json.dumps`` as Python writes it, which is what the other runtime
 * hashes and displays.
 *
 * ``JSON.stringify`` omits the spaces Python puts after ``,`` and
 * ``:``. That is invisible in a console and decisive in a digest: the
 * same arguments hashed to different values on the two sides, so
 * duplicate and loop detection disagreed about whether two calls were
 * the same call. ``sortKeys`` additionally makes two calls that differ
 * only in key order agree, which is what the digest is for.
 */
export function pyJson(value: unknown, sortKeys = false): string {
  const seen = new WeakSet<object>();
  const walk = (v: unknown): string => {
    if (v === null || v === undefined) return "null";
    if (typeof v === "string") return JSON.stringify(v);
    if (typeof v === "number" || typeof v === "boolean") return JSON.stringify(v);
    if (typeof v !== "object") return JSON.stringify(String(v));
    if (seen.has(v as object)) return JSON.stringify("[circular]");
    seen.add(v as object);
    if (Array.isArray(v)) return "[" + v.map(walk).join(", ") + "]";
    const o = v as Record<string, unknown>;
    const keys = sortKeys ? Object.keys(o).sort() : Object.keys(o);
    return "{" + keys.map((k) => `${JSON.stringify(k)}: ${walk(o[k])}`).join(", ") + "}";
  };
  try {
    return walk(value);
  } catch {
    return String(value);
  }
}

/**
 * A stable short hash of an argument set. An equality token, not a
 * signature, which is why twelve hex characters is enough: it is what
 * lets duplicate and loop detection keep working with no content.
 */
export function digest(args: unknown): string {
  if (args === null || args === undefined) return "";
  return (
    "sha256:" +
    createHash("sha256").update(pyJson(args, true)).digest("hex").slice(0, 12)
  );
}

/** What ``argsPreview`` carries at this level. */
export function preview(
  args: unknown,
  level: PrivacyLevel,
  fullPreview: string,
): string {
  if (level === "full") return fullPreview;
  if (level === "shape") return shape(args);
  if (level === "hashed") return digest(args);
  return "";
}

/**
 * What a model turn may quote of itself. A response is the most
 * content-bearing field in the payload, so it goes one level earlier
 * than arguments do.
 */
export function say(text: string, level: PrivacyLevel): string {
  return level === "metadata" || level === "tool_calls" ? "" : text;
}

/**
 * Whether a step of this type is recorded at all.
 *
 * Only ``tool_calls`` drops whole steps. Every other level keeps the
 * shape of the run and empties fields; this one keeps the action lane
 * alone, because that is what the customer asked for by name.
 */
export function keepsStep(stepType: string, level: PrivacyLevel): boolean {
  return level === "tool_calls" ? stepType === "tool_call" : true;
}

/**
 * A stamp for the run, so a reader knows what they are not seeing.
 *
 * A dashboard showing empty arguments has to be able to say whether the
 * agent passed none or the deployment declined to send them.
 */
export function describe(level: PrivacyLevel): Record<string, unknown> {
  return {
    level,
    sendsArguments: level === "full",
    sendsArgumentShapes: level === "full" || level === "shape",
    sendsOutputText: level === "full" || level === "shape" || level === "hashed",
    sendsClaimValues: level === "full" || level === "shape" || level === "hashed",
    sendsOutputLane: level !== "tool_calls",
  };
}
