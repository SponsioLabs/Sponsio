/**
 * ``sponsio export`` — convert Sponsio-native trace dumps to OTLP JSON.
 *
 * Mirrors the Python ``sponsio export`` command. Input is either a
 * single ``.json`` file (a `Trace.export()` dump) or a directory of
 * them; output is per-source-file OTLP JSON written into ``--to``,
 * filenames prefixed with ``--label`` so they're ready for
 * ``sponsio eval``.
 *
 * Event-type coverage is intentionally narrow: this command produces
 * tool_call spans plus a generic fallback for unknown event types.
 * LLM spans (``llm_request`` / ``llm_response``) are emitted as a
 * single fallback span — full OTLP gen-ai parity stays in the Python
 * exporter.
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync, readdirSync, statSync } from "node:fs";
import { resolve, join, basename } from "node:path";

const HELP =
  "sponsio export — Sponsio-native trace dumps -> OTLP JSON\n" +
  "\n" +
  "USAGE:\n" +
  "  sponsio export <source> --to <dir> [options]\n" +
  "\n" +
  "ARGUMENTS:\n" +
  "  <source>     Single .json file or directory of Sponsio native trace dumps\n" +
  "\n" +
  "OPTIONS:\n" +
  "      --to <dir>          Output directory for OTLP-JSON files (required)\n" +
  "      --label <l>         Filename prefix: safe | unsafe | none (default: safe)\n" +
  "      --agent <id>        Override service.name in the OTLP output\n" +
  "      --glob <pattern>    Only convert files matching pattern (dir mode, default: *.json)\n" +
  "  -h, --help              Show this help\n" +
  "\n" +
  "EXAMPLES:\n" +
  "  sponsio export run.json --to traces/\n" +
  "  sponsio export /var/log/sponsio/ --to traces/ --label unsafe\n";

interface SponsoEvent {
  ts?: number;
  event_type?: string;
  tool?: string;
  agent?: string;
  args?: Record<string, unknown>;
  content?: string;
  key?: string;
  contains?: string[];
  to?: string;
}

interface SponsoTrace {
  events: SponsoEvent[];
  metadata?: { agent_id?: string };
}

interface OtlpAttr {
  key: string;
  value: { stringValue?: string; intValue?: string; doubleValue?: number; boolValue?: boolean };
}
interface OtlpSpan {
  traceId: string;
  spanId: string;
  name: string;
  startTimeUnixNano: string;
  endTimeUnixNano: string;
  status: { code: number };
  attributes?: OtlpAttr[];
}

const BASE_NS = 1_700_000_000_000_000_000n; // BigInt for exact ns math
const STEP_NS = 1_000_000_000n;

function attr(key: string, value: unknown): OtlpAttr {
  if (typeof value === "boolean") return { key, value: { boolValue: value } };
  if (typeof value === "number") {
    if (Number.isInteger(value)) return { key, value: { intValue: String(value) } };
    return { key, value: { doubleValue: value } };
  }
  if (typeof value === "string") return { key, value: { stringValue: value } };
  return { key, value: { stringValue: String(value) } };
}

function spanTimeNs(ts: number): bigint {
  return BASE_NS + BigInt(ts) * STEP_NS;
}

function buildToolSpan(ev: SponsoEvent): OtlpSpan {
  const startNs = spanTimeNs(ev.ts ?? 0);
  const endNs = startNs + 500_000_000n;
  const attrs: OtlpAttr[] = [];
  for (const [k, v] of Object.entries(ev.args ?? {})) {
    attrs.push(attr(`args.${k}`, v));
  }
  if (ev.content !== undefined) attrs.push(attr("tool.output", ev.content));
  const span: OtlpSpan = {
    traceId: "0".repeat(32),
    spanId: (ev.ts ?? 0).toString(16).padStart(16, "0"),
    name: ev.tool ?? "tool_call",
    startTimeUnixNano: startNs.toString(),
    endTimeUnixNano: endNs.toString(),
    status: { code: 1 },
  };
  if (attrs.length) span.attributes = attrs;
  return span;
}

function buildFallbackSpan(ev: SponsoEvent): OtlpSpan {
  const startNs = spanTimeNs(ev.ts ?? 0);
  const endNs = startNs + 500_000_000n;
  const attrs: OtlpAttr[] = [];
  if (ev.tool !== undefined) attrs.push(attr("sponsio.tool", ev.tool));
  if (ev.key !== undefined) attrs.push(attr("sponsio.key", ev.key));
  if (ev.contains !== undefined) attrs.push(attr("sponsio.contains", ev.contains.join(",")));
  if (ev.to !== undefined) attrs.push(attr("sponsio.to", ev.to));
  if (ev.content !== undefined) attrs.push(attr("sponsio.content", ev.content));
  const span: OtlpSpan = {
    traceId: "0".repeat(32),
    spanId: (ev.ts ?? 0).toString(16).padStart(16, "0"),
    name: ev.event_type ?? "event",
    startTimeUnixNano: startNs.toString(),
    endTimeUnixNano: endNs.toString(),
    status: { code: 1 },
  };
  if (attrs.length) span.attributes = attrs;
  return span;
}

function eventToSpan(ev: SponsoEvent): OtlpSpan {
  if (ev.event_type === "tool_call" || ev.tool) return buildToolSpan(ev);
  return buildFallbackSpan(ev);
}

function traceToOtlp(trace: SponsoTrace, agentId?: string): unknown {
  const resolvedAgent =
    agentId ?? trace.metadata?.agent_id ?? trace.events[0]?.agent ?? "agent";
  const spans = (trace.events ?? []).map(eventToSpan);
  return {
    resourceSpans: [
      {
        resource: { attributes: [attr("service.name", resolvedAgent)] },
        scopeSpans: [{ scope: { name: "sponsio" }, spans }],
      },
    ],
  };
}

function listSources(source: string, glob: string): string[] {
  const abs = resolve(source);
  const st = statSync(abs);
  if (st.isFile()) return [abs];
  // Trivial glob: only support ``*.ext`` form, since OTel files are conventional.
  const m = /^\*(\.[a-z0-9]+)$/i.exec(glob);
  const ext = m ? m[1].toLowerCase() : ".json";
  return readdirSync(abs)
    .filter((f) => f.toLowerCase().endsWith(ext))
    .map((f) => join(abs, f))
    .sort();
}

export async function runExportCli(argv: string[]): Promise<void> {
  let source: string | undefined;
  let target: string | undefined;
  let label: "safe" | "unsafe" | "none" = "safe";
  let agentId: string | undefined;
  let glob = "*.json";

  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "-h" || a === "--help") {
      process.stdout.write(HELP);
      return;
    }
    if (a === "--to") {
      target = argv[++i];
      continue;
    }
    if (a === "--label") {
      const v = argv[++i];
      if (v !== "safe" && v !== "unsafe" && v !== "none") {
        process.stderr.write(`--label must be safe|unsafe|none (got ${v})\n`);
        process.exit(2);
      }
      label = v;
      continue;
    }
    if (a === "--agent") {
      agentId = argv[++i];
      continue;
    }
    if (a === "--glob") {
      glob = argv[++i];
      continue;
    }
    if (a.startsWith("-")) {
      process.stderr.write(`unknown flag: ${a}\n${HELP}`);
      process.exit(2);
    }
    if (source) {
      process.stderr.write(`extra positional argument: ${a}\n${HELP}`);
      process.exit(2);
    }
    source = a;
  }

  if (!source) {
    process.stderr.write(`Error: source required\n\n${HELP}`);
    process.exit(2);
  }
  if (!target) {
    process.stderr.write(`Error: --to required\n\n${HELP}`);
    process.exit(2);
  }
  if (!existsSync(resolve(source))) {
    process.stderr.write(`Error: ${source} does not exist\n`);
    process.exit(1);
  }
  mkdirSync(resolve(target), { recursive: true });

  const sources = listSources(source, glob);
  if (sources.length === 0) {
    process.stderr.write(`No files matched ${glob} under ${source}\n`);
    process.exit(0);
  }

  let converted = 0;
  const skipped: { src: string; reason: string }[] = [];
  for (const src of sources) {
    let raw: SponsoTrace;
    try {
      raw = JSON.parse(readFileSync(src, "utf-8"));
    } catch (e) {
      skipped.push({ src, reason: `parse: ${(e as Error).message}` });
      continue;
    }
    if (!Array.isArray(raw.events)) {
      skipped.push({ src, reason: "missing events: array" });
      continue;
    }
    const otlp = traceToOtlp(raw, agentId);
    const stem = basename(src).replace(/\.json$/i, "");
    const outName = label === "none" ? `${stem}.json` : `${label}_${stem}.json`;
    writeFileSync(join(resolve(target), outName), JSON.stringify(otlp, null, 2), "utf-8");
    converted++;
  }
  process.stdout.write(`✓ exported ${converted} of ${sources.length} traces to ${target}\n`);
  if (skipped.length) {
    process.stderr.write(`Skipped ${skipped.length}:\n`);
    for (const s of skipped.slice(0, 10)) process.stderr.write(`  ${s.src}: ${s.reason}\n`);
  }
}
