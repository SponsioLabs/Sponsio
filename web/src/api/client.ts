/**
 * Thin fetch wrapper for the Sponsio OSS local dashboard backend.
 *
 * One function per endpoint defined in `sponsio.serve.app`. Cloud
 * installs swap the BASE prefix to a hosted endpoint; the OSS frontend
 * speaks the same protocol so a Cloud-hosted backend will Just Work
 * for shapes that overlap, and the capability flags hide what doesn't.
 */

import type {
  BucketEventsResponse,
  Capabilities,
  ContractsResponse,
  HostBucketsResponse,
  LiveFrame,
  SessionsResponse,
  TraceEventsResponse,
  TracesResponse,
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

export const getCapabilities = () => json<Capabilities>('/capabilities');

export const listSessions = () => json<SessionsResponse>('/sessions');

export const listTraces = (agentId: string) =>
  json<TracesResponse>(`/sessions/${encodeURIComponent(agentId)}/traces`);

export const getTrace = (agentId: string, traceId: string) =>
  json<TraceEventsResponse>(
    `/sessions/${encodeURIComponent(agentId)}/traces/${encodeURIComponent(traceId)}`,
  );

export const getContracts = () => json<ContractsResponse>('/contracts');

export const listHostBuckets = () => json<HostBucketsResponse>('/host/buckets');

export const getHostBucketEvents = (bucket: string, limit = 200) =>
  json<BucketEventsResponse>(
    `/host/buckets/${encodeURIComponent(bucket)}/events?limit=${limit}`,
  );

/**
 * Open the live-tail WebSocket and dispatch frames to a handler.
 *
 * Returns a `close()` callback the caller invokes on unmount. The
 * default protocol/host derive from `window.location` so a `vite dev`
 * proxy or a same-origin Cloud install both work without config.
 */
export function openLiveSocket(
  onFrame: (frame: LiveFrame) => void,
  onError?: (event: Event) => void,
): () => void {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${proto}//${window.location.host}${BASE}/live`;
  const ws = new WebSocket(url);
  ws.onmessage = (e) => {
    try {
      onFrame(JSON.parse(e.data) as LiveFrame);
    } catch {
      // Server should only send JSON; ignore garbage frames.
    }
  };
  if (onError) ws.onerror = onError;
  return () => {
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
      ws.close();
    }
  };
}
