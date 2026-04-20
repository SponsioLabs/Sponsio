import type {
  Agent,
  AnalyticsData,
  Contract,
  ContractParseResult,
  DemoInfo,
  DemoScenario,
  LeaderboardEntry,
  LeaderboardStats,
  MonitorEvent,
  MonitorStatus,
  PatternDef,
  PlaygroundResult,
  ReVerifyResponse,
  ScoreResponse,
  SpanNode,
  SuggestedContract,
  SystemInfo,
  TraceEvent,
  TraceFilters,
  TraceSummary,
} from '../types';

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api';

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) {
    const contentType = res.headers.get('content-type') ?? '';
    const body = contentType.includes('application/json')
      ? await res.json().catch(() => ({ detail: res.statusText }))
      : { detail: res.statusText };
    throw new Error((body as { detail?: string }).detail ?? res.statusText);
  }
  return res.json() as Promise<T>;
}

// System
export const getSystem = () => json<SystemInfo>('/system');

// Agents
export const listAgents = () => json<Agent[]>('/agents');
export const createAgent = (data: { id: string; tools: string[]; permissions: string[] }) =>
  json<Agent>('/agents', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
export const deleteAgent = (id: string) =>
  json<{ deleted: string }>(`/agents/${id}`, { method: 'DELETE' });

// Contracts
export const parseContracts = (nl_text: string) =>
  json<ContractParseResult>('/contracts/parse', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ nl_text }),
  });
export const commitContracts = (agent_id: string, nl_text: string) =>
  json<{ agent_id: string; guarantees_count: number }>('/contracts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent_id, nl_text }),
  });
export const listContracts = () => json<Contract[]>('/contracts');

// Playground
export const simulateAction = (data: { agent_id: string; action: string; event_type?: string }) =>
  json<PlaygroundResult>('/playground/action', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
export const resetPlayground = () => json<{ status: string }>('/playground/reset', { method: 'POST' });

// Monitor
export const getMonitorLog = () => json<MonitorEvent[]>('/monitor/log');
export const getMonitorStatus = () => json<MonitorStatus>('/monitor/status');
export const getTrace = () => json<{ events: TraceEvent[] }>('/monitor/trace');

// Monitor — new endpoints
export const pushEvent = (event: { agent: string; type: string; tool?: string; content?: string }) =>
  json<{ status: string; event_index: number }>('/monitor/push', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(event),
  });

export const importTrace = (data: { events: Record<string, unknown>[]; metadata?: Record<string, unknown> }) =>
  json<{ status: string; event_count: number }>('/monitor/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });

export const reVerifyContract = (nl_text: string) =>
  json<ReVerifyResponse>('/monitor/re-verify', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ nl_text }),
  });

export const resetMonitor = () =>
  json<{ status: string }>('/monitor/reset', { method: 'POST' });

export const getSpans = () =>
  json<SpanNode[]>('/monitor/spans');

export const getReport = () =>
  json<Record<string, unknown>>('/monitor/report');

export const addContract = (nl_text: string) =>
  json<{ status: string; contract_desc: string }>('/monitor/add-contract', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ nl_text }),
  });

// Patterns
export const getPatternLibrary = () =>
  json<PatternDef[]>('/patterns/library');

// Demo
export const listDemos = () => json<DemoInfo[]>('/demo/list');
export const seedDemo = (demoId: string = 'customer_service') =>
  json<{ status: string }>('/demo/seed', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ demo_id: demoId }),
  });
export const getDemoScenarios = () => json<DemoScenario[]>('/demo/scenarios');
export const getDemoContracts = () =>
  json<{ hard: Record<string, unknown>[]; soft: Record<string, unknown>[] }>('/demo/contracts');
export const getLiveStatus = () =>
  json<{ api_key_set: boolean; dependencies_installed: boolean; ready: boolean; model: string }>('/demo/live-status');
export const runLiveDemo = (demoId: string) =>
  json<{ status: string; demo_id: string }>('/demo/run-live', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ demo_id: demoId }),
  });

// Leaderboard
export const getLeaderboard = (limit = 50, offset = 0) =>
  json<{ entries: LeaderboardEntry[]; count: number }>(`/leaderboard?limit=${limit}&offset=${offset}`);
export const getLeaderboardStats = () => json<LeaderboardStats>('/leaderboard/stats');

// Scoring
export const scoreTools = (data: { agent_name: string; tools: { name: string; description: string; parameters: Record<string, string> }[]; display_name?: string; description?: string; is_public?: boolean }) =>
  json<ScoreResponse>('/score', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });

// Scan upload — accepts .py / .zip / .yaml, runs backend CodeAnalyzer + scorer.
// Sends source=upload so CLI tab polling doesn't pick it up.
export async function uploadScan(file: File): Promise<ScoreResponse> {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${BASE}/scan/upload?source=upload`, {
    method: 'POST',
    body: form,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error((body as { detail?: string }).detail ?? res.statusText);
  }
  return res.json() as Promise<ScoreResponse>;
}

export interface ScanHistoryItem {
  id: number;
  agent_name: string;
  score: number;
  grade: string;
  timestamp: string;
  description: string;
  badge_url: string;
}

export type ScanSource = 'upload' | 'cli';

export const getScanHistory = (limit = 10, source?: ScanSource) => {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (source) qs.set('source', source);
  return json<{ items: ScanHistoryItem[]; count: number }>(`/scan/history?${qs}`);
};

export const clearScanHistory = (source?: ScanSource) => {
  const qs = source ? `?source=${source}` : '';
  return json<{ deleted: number }>(`/scan/history${qs}`, { method: 'DELETE' });
};

export interface ScanDetail {
  id: number;
  agent_name: string;
  score: number;
  grade: string;
  timestamp: string;
  description: string;
  yaml_content: string;
  source_filename: string;
  deductions: {
    check_id: string;
    points_lost: number;
    description: string;
    affected_tools: string[];
    suggested_contract: string;
  }[];
  suggested_contracts: string[];
}

export const getScanDetail = (id: number) =>
  json<ScanDetail>(`/scan/${id}`);

export const getLatestScan = (source?: ScanSource) => {
  const qs = source ? `?source=${source}` : '';
  return json<ScanDetail | null>(`/scan/latest${qs}`);
};

export const scanYamlUrl = (id: number) => `${BASE}/scan/${id}/yaml`;

// Contracts (new aliases & operations)
export const getContracts = listContracts;

export const deleteContract = (agentId: string) =>
  json<{ deleted_agent_id: string; removed_count: number }>(
    `/contracts/${encodeURIComponent(agentId)}`,
    { method: 'DELETE' },
  );

export const deleteGuarantee = (agentId: string, index: number) =>
  json<{ deleted_agent_id: string; deleted_index: number; deleted_desc: string }>(
    `/contracts/${encodeURIComponent(agentId)}/${index}`,
    { method: 'DELETE' },
  );

export async function getDiscoverySuggestions(agentId?: string): Promise<SuggestedContract[]> {
  const qs = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : '';
  const resp = await json<{ suggestions: SuggestedContract[] }>(`/discovery/suggestions${qs}`);
  return resp.suggestions;
}

export const getAnalytics = (period: string) =>
  json<AnalyticsData>(`/analytics?period=${encodeURIComponent(period)}`);

interface OtelTraceSummary {
  trace_id: string;
  root_name: string;
  root_scope: string;
  span_count: number;
  duration_ms: number;
  has_sponsio_spans: boolean;
  has_violations: boolean;
  contracts_checked: number;
  violations_found: number;
  timestamp_ms: number;
}

export async function listTraces(_filters?: TraceFilters): Promise<TraceSummary[]> {
  const summaries = await json<OtelTraceSummary[]>('/otel/traces');
  return summaries.map(s => ({
    traceId: s.trace_id,
    agentId: s.root_scope || s.root_name || 'unknown',
    startTime: s.timestamp_ms
      ? new Date(s.timestamp_ms).toISOString()
      : new Date().toISOString(),
    duration: s.duration_ms,
    eventCount: s.span_count,
    violationCount: s.violations_found,
    hardViolations: s.violations_found,
    softViolations: 0,
  }));
}

// Server-Sent Events stream URL for monitor updates.
export const monitorStreamUrl = () => `${BASE}/monitor/stream`;
