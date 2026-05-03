/**
 * Tool-name → transport label inference. Mirrors Python's
 * ``sponsio/render/derive.py``. The session-view's ``service``
 * column shows the **transport** the tool uses to actually run —
 * one of ``function`` / ``mcp`` / ``shell`` / ``http``. Other axes
 * (resource being acted on, business domain) belong in additional
 * columns; folding them into ``service`` makes the label semantics
 * inconsistent across scenarios.
 *
 * Default for anything not on the table is ``function`` — the
 * overwhelmingly common case in modern agent SDKs (Vercel AI SDK,
 * Anthropic, OpenAI, Claude Agent SDK, LangChain) is in-process
 * function-call dispatch from a structured ``tool_use`` block.
 */

const TOOL_PREFIX_TO_TRANSPORT: [string, string][] = [
  // Shell exec — the runtime spawns a subprocess.
  ["bash", "shell"],
  ["shell.", "shell"],
  ["run_tests", "shell"],
  ["execute_command", "shell"],
  // Model Context Protocol — the runtime speaks JSON-RPC to a
  // separate MCP server (stdio or HTTP+SSE).
  ["user_instruction", "mcp"],
  ["user_message", "mcp"],
  ["mcp.", "mcp"],
  ["mcp__", "mcp"],
  // Raw HTTP fetch — a thin wrapper around the network stack rather
  // than a typed function-call handler.
  ["http.", "http"],
  ["fetch", "http"],
  ["web_fetch", "http"],
  ["web_search", "http"],
];

export function serviceForTool(tool: string | undefined): string {
  if (!tool) return "unknown";
  const lowered = tool.toLowerCase();
  for (const [prefix, transport] of TOOL_PREFIX_TO_TRANSPORT) {
    if (lowered.startsWith(prefix)) return transport;
  }
  return "func";
}

// Transport color palette — only labels ``serviceForTool`` returns
// ever appear here. Keep this minimal: ``function`` is the
// background-noise default (dim), distinguished transports get a
// distinct hue so they pop in the trace.
const SERVICE_COLORS: Record<string, string> = {
  func: "2", // dim — the unmarked common case (in-process function call)
  shell: "33", // yellow
  mcp: "35", // magenta
  http: "37", // white
  unknown: "2",
};

export function serviceColor(service: string): string {
  return SERVICE_COLORS[service] ?? SERVICE_COLORS.unknown;
}

/**
 * Truncate args to a short summary the trace-row can show inline.
 * Mirrors Python's ``args_summary`` — keep first key=value pair,
 * truncate at ~30 chars.
 */
export function argsSummary(args: Record<string, unknown> | undefined, maxLen = 30): string {
  if (!args || typeof args !== "object") return "";
  const keys = Object.keys(args);
  if (keys.length === 0) return "";
  const parts: string[] = [];
  let len = 0;
  for (const k of keys) {
    const v = args[k];
    let vs: string;
    if (typeof v === "string") vs = JSON.stringify(v);
    else if (v == null) vs = "null";
    else vs = String(v);
    if (vs.length > 20) vs = vs.slice(0, 19) + "…";
    const part = `${k}=${vs}`;
    if (len + part.length + 2 > maxLen && parts.length > 0) {
      parts.push("…");
      break;
    }
    parts.push(part);
    len += part.length + 2;
  }
  return parts.join(", ");
}
