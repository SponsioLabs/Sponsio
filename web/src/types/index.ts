/**
 * Type contracts for the Sponsio OSS local dashboard.
 *
 * All shapes mirror what `sponsio.serve.app` returns. Cloud installs
 * extend the `Capabilities.features` map with extra flags but the OSS
 * payloads stay forwards-compatible — unknown features are simply not
 * rendered.
 */

export interface Capabilities {
  tier: 'oss' | 'cloud';
  version: string;
  sessions_dir: string;
  features: Record<string, boolean>;
}

export interface AgentSummary {
  agent_id: string;
  trace_count: number;
  latest_mtime: number;
}

export interface SessionsResponse {
  sessions_dir: string;
  agents: AgentSummary[];
}

export interface TraceMeta {
  trace_id: string;
  filename: string;
  size_bytes: number;
  mtime: number;
}

export interface TracesResponse {
  agent_id: string;
  traces: TraceMeta[];
}

/**
 * One MonitorEvent row from a session-log JSONL file. Fields that may
 * be absent (sto, retry_prompt) are surfaced as optional rather than
 * null so the UI can render conditionally.
 */
export interface SessionEvent {
  ts: number;
  agent_id: string;
  action: string;
  pipeline: 'det' | 'sto' | string;
  constraint?: string;
  result?: {
    action: string;
    message?: string;
    retry_prompt?: string;
  };
  sto?: {
    score?: number;
    evidence?: unknown;
  };
  // WS live-tail synthesises these for events streamed in.
  _agent_id?: string;
  _trace_id?: string;
  // Bucket-events synthesise this.
  _conv?: string;
}

export interface TraceEventsResponse {
  agent_id: string;
  trace_id: string;
  events: SessionEvent[];
}

export interface PatternDef {
  name: string;
  params: string[];
  summary: string;
  kind: 'det' | 'sto';
}

export interface YamlContractFile {
  path: string;
  contracts: Record<string, unknown>[];
  error?: string;
}

export interface ContractsResponse {
  patterns: PatternDef[];
  yaml: YamlContractFile | null;
}

export interface HostBucketSummary {
  name: string;
  conv_count: number;
  latest_mtime: number;
  has_yaml: boolean;
}

export interface HostBucketsResponse {
  plugins_dir: string;
  buckets: HostBucketSummary[];
}

export interface BucketEventsResponse {
  bucket: string;
  events: SessionEvent[];
}

export type LiveFrame =
  | { type: 'ready'; sessions_dir: string }
  | { type: 'event'; data: SessionEvent };
