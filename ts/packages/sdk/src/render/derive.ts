/**
 * Tool-name → service label inference. Mirrors Python's
 * ``sponsio/render/derive.py``. Used by the session-view renderer
 * to colorise the service column on each event row (mcp / postgres
 * / fs / shell / etc.).
 *
 * Ordered most-specific-first; the table is checked top-down and
 * the first matching prefix wins. Falls through to a keyword search
 * for SQL verbs buried in args, then to ``"unknown"``.
 */

const TOOL_PREFIX_TO_SERVICE: [string, string][] = [
  ["execute_sql", "postgres"],
  ["query_sql", "postgres"],
  ["connect_db", "postgres"],
  ["db_query", "postgres"],
  ["db_execute", "postgres"],
  ["postgres.", "postgres"],
  ["psql.", "postgres"],
  ["mysql.", "mysql"],
  ["mongo.", "mongodb"],
  ["redis.", "redis"],
  ["github.", "github"],
  ["gh.", "github"],
  ["gitlab.", "gitlab"],
  ["slack.", "slack"],
  ["gmail.", "gmail"],
  ["aws.", "aws"],
  ["s3.", "aws"],
  ["ec2.", "aws"],
  ["gcp.", "gcp"],
  ["azure.", "azure"],
  ["openai.", "openai"],
  ["anthropic.", "anthropic"],
  ["gemini.", "gemini"],
  ["mistral.", "mistral"],
  ["read_file", "fs"],
  ["write_file", "fs"],
  ["edit_file", "fs"],
  ["list_dir", "fs"],
  ["file.", "fs"],
  ["fs.", "fs"],
  ["bash", "shell"],
  ["shell.", "shell"],
  ["run_tests", "shell"],
  ["execute_command", "shell"],
  ["user_instruction", "mcp"],
  ["user_message", "mcp"],
  ["mcp.", "mcp"],
  ["mcp__", "mcp"],
  ["http.", "http"],
  ["fetch", "http"],
  ["web_fetch", "http"],
  ["web_search", "http"],
];

export function serviceForTool(tool: string | undefined): string {
  if (!tool) return "unknown";
  const lowered = tool.toLowerCase();
  for (const [prefix, service] of TOOL_PREFIX_TO_SERVICE) {
    if (lowered.startsWith(prefix)) return service;
  }
  if (lowered.includes("sql")) return "postgres";
  return "unknown";
}

const SERVICE_COLORS: Record<string, string> = {
  postgres: "34", // blue
  mysql: "34",
  mongodb: "32",
  redis: "31",
  github: "35", // magenta
  gitlab: "35",
  slack: "35",
  gmail: "35",
  aws: "33", // yellow
  gcp: "33",
  azure: "36",
  openai: "37",
  anthropic: "37",
  gemini: "37",
  mistral: "37",
  fs: "36", // cyan
  shell: "33",
  mcp: "35",
  http: "37",
  unknown: "2", // dim
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
