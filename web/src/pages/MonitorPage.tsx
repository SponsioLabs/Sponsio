/**
 * Monitor — three-pane viewer over local session-log JSONL files.
 *
 *   ┌──────────┬──────────┬─────────────────────────────────────┐
 *   │ Agents   │ Traces   │ Events table (+ optional live tail) │
 *   └──────────┴──────────┴─────────────────────────────────────┘
 *
 * Agents and traces come from the four read-only endpoints; the
 * "Live" toggle opens the WS /api/live socket and prepends new events
 * as they arrive. Live frames are never persisted — refresh and the
 * accumulated tail is gone (this is by design; persistence is Cloud).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  getTrace,
  listSessions,
  listTraces,
  openLiveSocket,
} from '../api/client';
import MetricCard from '../components/monitor/MetricCard';
import Spinner from '../components/Spinner';
import { useApp, useFeature } from '../context/AppContext';
import type {
  AgentSummary,
  SessionEvent,
  TraceMeta,
} from '../types';

// ---------------------------------------------------------------------------
// Helpers.
// ---------------------------------------------------------------------------

function formatTs(ts: number): string {
  if (!Number.isFinite(ts) || ts <= 0) return '—';
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${String(d.getMilliseconds()).padStart(3, '0')}`;
}

function relativeTime(ts: number): string {
  if (!Number.isFinite(ts) || ts <= 0) return 'never';
  const delta = Date.now() / 1000 - ts;
  if (delta < 60) return `${Math.floor(delta)}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

function verdictTone(action?: string): string {
  switch (action) {
    case 'allow':
      return 'text-emerald-500';
    case 'block':
    case 'redirect':
      return 'text-red-500';
    case 'retry':
    case 'feedback':
      return 'text-amber-500';
    default:
      return 'text-muted';
  }
}

// ---------------------------------------------------------------------------
// Sub-views.
// ---------------------------------------------------------------------------

function AgentList({
  agents,
  selected,
  onSelect,
  loading,
}: {
  agents: AgentSummary[];
  selected: string | null;
  onSelect: (id: string) => void;
  loading: boolean;
}) {
  return (
    <div className="flex flex-col h-full border-r border-surface-200 dark:border-surface-800">
      <div className="px-4 py-3 border-b border-surface-200 dark:border-surface-800">
        <div className="text-[10px] uppercase tracking-widest text-muted">Agents</div>
        <div className="text-sm font-medium mt-0.5">{agents.length} found</div>
      </div>
      <div className="flex-1 overflow-y-auto">
        {loading && (
          <div className="flex items-center justify-center py-10">
            <Spinner />
          </div>
        )}
        {!loading && agents.length === 0 && (
          <div className="px-4 py-6 text-xs text-muted">
            No session logs yet. Run an agent with Sponsio installed and traces
            will land in <code>~/.sponsio/sessions/</code>.
          </div>
        )}
        <ul>
          {agents.map((a) => (
            <li key={a.agent_id}>
              <button
                onClick={() => onSelect(a.agent_id)}
                className={`w-full text-left px-4 py-2 border-l-2 hover:bg-surface-50 dark:hover:bg-surface-900 transition-colors ${
                  a.agent_id === selected
                    ? 'bg-brand/5 border-brand text-stone-900 dark:text-white'
                    : 'border-transparent text-muted'
                }`}
              >
                <div className="font-mono text-sm truncate">{a.agent_id}</div>
                <div className="text-[10px] text-muted mt-0.5">
                  {a.trace_count} trace{a.trace_count === 1 ? '' : 's'} · last{' '}
                  {relativeTime(a.latest_mtime)}
                </div>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function TraceList({
  agentId,
  traces,
  selected,
  onSelect,
  loading,
}: {
  agentId: string | null;
  traces: TraceMeta[];
  selected: string | null;
  onSelect: (id: string) => void;
  loading: boolean;
}) {
  return (
    <div className="flex flex-col h-full border-r border-surface-200 dark:border-surface-800">
      <div className="px-4 py-3 border-b border-surface-200 dark:border-surface-800">
        <div className="text-[10px] uppercase tracking-widest text-muted">Traces</div>
        <div className="text-sm font-medium mt-0.5 truncate">
          {agentId ?? 'select an agent'}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto">
        {loading && (
          <div className="flex items-center justify-center py-10">
            <Spinner />
          </div>
        )}
        {!loading && agentId && traces.length === 0 && (
          <div className="px-4 py-6 text-xs text-muted">No traces for this agent.</div>
        )}
        <ul>
          {traces.map((t) => (
            <li key={t.trace_id}>
              <button
                onClick={() => onSelect(t.trace_id)}
                className={`w-full text-left px-4 py-2 border-l-2 hover:bg-surface-50 dark:hover:bg-surface-900 transition-colors ${
                  t.trace_id === selected
                    ? 'bg-brand/5 border-brand text-stone-900 dark:text-white'
                    : 'border-transparent text-muted'
                }`}
              >
                <div className="font-mono text-xs truncate">{t.trace_id}</div>
                <div className="text-[10px] text-muted mt-0.5">
                  {(t.size_bytes / 1024).toFixed(1)} KB · {relativeTime(t.mtime)}
                </div>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function EventRow({ event }: { event: SessionEvent }) {
  return (
    <tr className="border-b border-surface-100 dark:border-surface-900 hover:bg-surface-50 dark:hover:bg-surface-900/50">
      <td className="px-3 py-1.5 font-mono text-[11px] text-muted whitespace-nowrap">
        {formatTs(event.ts)}
      </td>
      <td className="px-3 py-1.5">
        <span
          className={`inline-block text-[9px] font-mono uppercase px-1.5 py-0.5 rounded ${
            event.pipeline === 'sto'
              ? 'bg-purple-500/10 text-purple-500'
              : 'bg-blue-500/10 text-blue-500'
          }`}
        >
          {event.pipeline}
        </span>
      </td>
      <td className="px-3 py-1.5 font-mono text-xs text-stone-900 dark:text-white truncate max-w-[18rem]">
        {event.action}
      </td>
      <td className="px-3 py-1.5 font-mono text-xs text-muted truncate max-w-[14rem]">
        {event.constraint ?? '—'}
      </td>
      <td className={`px-3 py-1.5 font-mono text-xs ${verdictTone(event.result?.action)}`}>
        {event.result?.action ?? '—'}
      </td>
      <td className="px-3 py-1.5 text-xs text-muted truncate max-w-[24rem]">
        {event.result?.message ?? ''}
      </td>
    </tr>
  );
}

function EventTable({
  events,
  loading,
  emptyHint,
}: {
  events: SessionEvent[];
  loading: boolean;
  emptyHint: string;
}) {
  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Spinner size="lg" />
      </div>
    );
  }
  if (events.length === 0) {
    return <div className="px-6 py-10 text-sm text-muted">{emptyHint}</div>;
  }
  return (
    <table className="w-full text-left">
      <thead className="bg-surface-50 dark:bg-surface-900 sticky top-0">
        <tr>
          <th className="px-3 py-2 text-[10px] uppercase tracking-widest text-muted font-medium">
            Time
          </th>
          <th className="px-3 py-2 text-[10px] uppercase tracking-widest text-muted font-medium">
            Pipe
          </th>
          <th className="px-3 py-2 text-[10px] uppercase tracking-widest text-muted font-medium">
            Action
          </th>
          <th className="px-3 py-2 text-[10px] uppercase tracking-widest text-muted font-medium">
            Constraint
          </th>
          <th className="px-3 py-2 text-[10px] uppercase tracking-widest text-muted font-medium">
            Verdict
          </th>
          <th className="px-3 py-2 text-[10px] uppercase tracking-widest text-muted font-medium">
            Message
          </th>
        </tr>
      </thead>
      <tbody>
        {events.map((e, i) => (
          <EventRow key={`${e.ts}-${i}`} event={e} />
        ))}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------------------
// Page.
// ---------------------------------------------------------------------------

export default function MonitorPage() {
  const { capabilities } = useApp();
  const liveSupported = useFeature('live_trace');

  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [agentsLoading, setAgentsLoading] = useState(true);
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);

  const [traces, setTraces] = useState<TraceMeta[]>([]);
  const [tracesLoading, setTracesLoading] = useState(false);
  const [selectedTrace, setSelectedTrace] = useState<string | null>(null);

  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [eventsError, setEventsError] = useState<string | null>(null);

  // Live tail — separate event buffer that prepends to whatever's loaded.
  const [liveOn, setLiveOn] = useState(false);
  const [liveEvents, setLiveEvents] = useState<SessionEvent[]>([]);
  const closeRef = useRef<(() => void) | null>(null);

  // Load agents on mount + when capabilities arrive.
  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- spinner toggle for the in-flight fetch
    setAgentsLoading(true);
    listSessions()
      .then((r) => {
        if (cancelled) return;
        setAgents(r.agents);
        if (r.agents.length > 0 && !selectedAgent) {
          setSelectedAgent(r.agents[0].agent_id);
        }
      })
      .catch(() => {
        // Surface noisy errors via the capabilities banner instead.
      })
      .finally(() => !cancelled && setAgentsLoading(false));
    return () => {
      cancelled = true;
    };
    // capabilities changes are the trigger — selectedAgent is intentionally
    // omitted so we don't refetch when user clicks an agent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [capabilities]);

  // Load traces when an agent is picked.
  useEffect(() => {
    if (!selectedAgent) return;
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset list on selection change before refetch
    setTracesLoading(true);
    setTraces([]);
    setSelectedTrace(null);
    listTraces(selectedAgent)
      .then((r) => {
        if (cancelled) return;
        setTraces(r.traces);
        if (r.traces.length > 0) {
          // Default to most recent.
          const newest = [...r.traces].sort((a, b) => b.mtime - a.mtime)[0];
          setSelectedTrace(newest.trace_id);
        }
      })
      .finally(() => !cancelled && setTracesLoading(false));
    return () => {
      cancelled = true;
    };
  }, [selectedAgent]);

  // Load events when a trace is picked.
  useEffect(() => {
    if (!selectedAgent || !selectedTrace) return;
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset table on selection change before refetch
    setEventsLoading(true);
    setEventsError(null);
    setEvents([]);
    getTrace(selectedAgent, selectedTrace)
      .then((r) => !cancelled && setEvents(r.events))
      .catch((err: Error) => !cancelled && setEventsError(err.message))
      .finally(() => !cancelled && setEventsLoading(false));
    return () => {
      cancelled = true;
    };
  }, [selectedAgent, selectedTrace]);

  // WS live tail lifecycle.
  const toggleLive = useCallback(() => {
    if (liveOn) {
      closeRef.current?.();
      closeRef.current = null;
      setLiveOn(false);
      return;
    }
    setLiveEvents([]);
    closeRef.current = openLiveSocket((frame) => {
      if (frame.type === 'event') {
        setLiveEvents((prev) => [frame.data, ...prev].slice(0, 200));
      }
    });
    setLiveOn(true);
  }, [liveOn]);

  // Always close the socket on unmount.
  useEffect(() => () => closeRef.current?.(), []);

  const totalEvents = events.length;
  const detEvents = useMemo(
    () => events.filter((e) => e.pipeline === 'det').length,
    [events],
  );
  const blocked = useMemo(
    () => events.filter((e) => e.result?.action === 'block').length,
    [events],
  );

  const visibleEvents = liveOn ? liveEvents : events;
  const emptyHint = liveOn
    ? 'Live tail is on. New events from any session log file will appear here.'
    : selectedTrace
    ? 'No events in this trace.'
    : 'Pick a trace on the left to view its events.';

  return (
    <div className="h-screen flex flex-col">
      {/* Header */}
      <header className="border-b border-surface-200 dark:border-surface-800 px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-display">Monitor</h1>
          <p className="text-xs text-muted mt-0.5 font-mono">
            {capabilities?.sessions_dir ?? '…'}
          </p>
        </div>
        {liveSupported && (
          <button
            onClick={toggleLive}
            className={`text-xs px-3 py-1.5 rounded border transition-colors ${
              liveOn
                ? 'bg-brand text-surface-950 border-brand'
                : 'border-surface-200 dark:border-surface-700 text-muted hover:text-stone-900 dark:hover:text-white'
            }`}
          >
            {liveOn ? '● Live' : '○ Live tail'}
          </button>
        )}
      </header>

      {/* Metrics strip */}
      <div className="px-6 py-3 grid grid-cols-3 gap-3 border-b border-surface-200 dark:border-surface-800">
        <MetricCard label="Events in trace" value={totalEvents} sub={selectedTrace ?? '—'} />
        <MetricCard
          label="Det events"
          value={detEvents}
          sub={`${totalEvents - detEvents} sto / other`}
          accent="border-l-blue-500"
        />
        <MetricCard
          label="Blocked"
          value={blocked}
          sub={`${blocked === 0 ? 'all clear' : 'see verdict column'}`}
          accent="border-l-red-500"
        />
      </div>

      {/* 3-pane body */}
      <div className="flex-1 grid grid-cols-[14rem_18rem_1fr] min-h-0">
        <AgentList
          agents={agents}
          selected={selectedAgent}
          onSelect={setSelectedAgent}
          loading={agentsLoading}
        />
        <TraceList
          agentId={selectedAgent}
          traces={traces}
          selected={selectedTrace}
          onSelect={setSelectedTrace}
          loading={tracesLoading}
        />
        <div className="overflow-auto">
          {eventsError ? (
            <div className="px-6 py-6 text-sm text-red-500">{eventsError}</div>
          ) : (
            <EventTable
              events={visibleEvents}
              loading={eventsLoading && !liveOn}
              emptyHint={emptyHint}
            />
          )}
        </div>
      </div>
    </div>
  );
}
