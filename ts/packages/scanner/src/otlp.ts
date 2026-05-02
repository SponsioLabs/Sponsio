/**
 * Minimal OTLP/JSON -> Sponsio event extractor.
 *
 * OTLP shape used by Sponsio (mirrors what ``trace_to_otlp`` writes):
 *
 *   {
 *     resourceSpans: [{
 *       resource: { attributes: [{ key: "service.name", value: {stringValue: ... } }] },
 *       scopeSpans: [{ scope: { name: "sponsio" }, spans: [
 *         { name: "<tool>", attributes: [{ key: "args.<k>", value: {...} }, ...] },
 *         ...
 *       ]}]
 *     }]
 *   }
 *
 * For ``check`` / ``eval`` we only need the per-span ``name`` (the
 * tool) plus any ``args.<k>`` attributes, decoded back into a plain
 * object. Spans are sorted by ``startTimeUnixNano`` so replay order
 * matches the original trace.
 */

interface OtlpAttrValue {
  stringValue?: string;
  intValue?: string;
  doubleValue?: number;
  boolValue?: boolean;
}
interface OtlpAttr {
  key: string;
  value?: OtlpAttrValue;
}
interface OtlpSpan {
  name?: string;
  startTimeUnixNano?: string;
  attributes?: OtlpAttr[];
}

export interface ExtractedEvent {
  tool: string;
  args: Record<string, unknown>;
}

function decodeAttrValue(v: OtlpAttrValue | undefined): unknown {
  if (!v) return undefined;
  if (v.stringValue !== undefined) return v.stringValue;
  if (v.intValue !== undefined) {
    const n = Number(v.intValue);
    return Number.isFinite(n) ? n : v.intValue;
  }
  if (v.doubleValue !== undefined) return v.doubleValue;
  if (v.boolValue !== undefined) return v.boolValue;
  return undefined;
}

export function isOtlpPayload(data: unknown): boolean {
  return !!data && typeof data === "object" && Array.isArray((data as { resourceSpans?: unknown }).resourceSpans);
}

export function extractOtlpEvents(data: unknown): ExtractedEvent[] {
  if (!isOtlpPayload(data)) return [];
  const out: { ts: bigint; tool: string; args: Record<string, unknown> }[] = [];
  const resourceSpans = (data as { resourceSpans: unknown[] }).resourceSpans;
  for (const rs of resourceSpans) {
    if (!rs || typeof rs !== "object") continue;
    const scopeSpans = (rs as { scopeSpans?: unknown[] }).scopeSpans ?? [];
    for (const ss of scopeSpans) {
      if (!ss || typeof ss !== "object") continue;
      const spans = ((ss as { spans?: OtlpSpan[] }).spans ?? []) as OtlpSpan[];
      for (const sp of spans) {
        if (!sp || typeof sp.name !== "string" || !sp.name) continue;
        const args: Record<string, unknown> = {};
        for (const a of sp.attributes ?? []) {
          if (!a.key.startsWith("args.")) continue;
          args[a.key.slice("args.".length)] = decodeAttrValue(a.value);
        }
        let ts = 0n;
        try {
          ts = BigInt(sp.startTimeUnixNano ?? "0");
        } catch {
          ts = 0n;
        }
        out.push({ ts, tool: sp.name, args });
      }
    }
  }
  out.sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0));
  return out.map((e) => ({ tool: e.tool, args: e.args }));
}
