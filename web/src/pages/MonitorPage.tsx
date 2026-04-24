import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  getMonitorLog, getMonitorStatus, getTrace, getSpans, resetMonitor,
  listTraces, importTrace, reVerifyContract, monitorStreamUrl, listContracts,
  getAnalytics,
} from '../api/client';
import type {
  MonitorEvent, MonitorStatus, TraceEvent, SpanNode, TraceSummary, ReVerifyResponse,
  AnalyticsData,
} from '../types';
import {
  MOCK_TRACE_EVENTS, MOCK_MONITOR_EVENTS_ENRICHED, MOCK_STATUS, MOCK_SPANS_ENRICHED,
  MOCK_TRACE_SUMMARIES, MOCK_ANALYTICS,
} from '../data/mockMonitorData';
import Spinner from '../components/Spinner';
import SpanTree from '../components/SpanTree';
import FileUpload from '../components/FileUpload';
import PageError from '../components/PageError';
import PipelineNav from '../components/PipelineNav';
import DemoBadge from '../components/DemoBadge';
import TraceWaterfall from '../components/monitor/TraceWaterfall';
import MetricCard from '../components/monitor/MetricCard';
import ViolationGroups from '../components/monitor/ViolationGroups';
import SloBoard from '../components/monitor/SloBoard';
import RegressionPanel from '../components/monitor/RegressionPanel';
import ViolationHeatmap from '../components/monitor/ViolationHeatmap';
import IoDiffPanel from '../components/monitor/IoDiffPanel';
import DataLineage from '../components/monitor/DataLineage';
import {
  aggregateLlmMetrics, extractLlmMetrics, extractSessionId, formatCost, formatTokens,
} from '../utils/llmMetrics';
import {
  latencyPercentiles, bucketCounts, isEnforcementCheck, normalizePipeline,
  loadSuppressions, filterSuppressed,
} from '../utils/aggregations';
import { useSearchable } from '../utils/useSearchable';

// ═══════════════════════════════════════════════════════════════════════════════
//  Shared helpers
// ═══════════════════════════════════════════════════════════════════════════════

function fmtTs(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function formatDuration(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatLatencyMs(ms: number): string {
  if (ms < 1) return '<1ms';
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function ViolationBadge({ hard, soft }: { hard: number; soft: number }) {
  if (hard === 0 && soft === 0) {
    return <span className="text-emerald-400 bg-emerald-500/10 px-1.5 py-0.5 rounded text-[10px] font-mono">clean</span>;
  }
  return (
    <span className="flex items-center gap-1">
      {hard > 0 && <span className="text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded text-[10px] font-mono">{hard} hard</span>}
      {soft > 0 && <span className="text-amber-400 bg-amber-500/10 px-1.5 py-0.5 rounded text-[10px] font-mono">{soft} soft</span>}
    </span>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
//  Shared data hook
// ═══════════════════════════════════════════════════════════════════════════════

interface MonitorData {
  status: MonitorStatus;
  violations: MonitorEvent[];       // ← already suppression-filtered
  rawViolations: MonitorEvent[];    // ← full log, unfiltered (for debugging / admin)
  events: TraceEvent[];
  spans: SpanNode[];
  traces: TraceSummary[];
  analytics: AnalyticsData | null;
  loading: boolean;
  error: string;
  /**
   * True when at least one endpoint fell back to mock data. Surfaces to the
   * user via a DEMO badge at the top of the page (fix P11).
   */
  isMockMode: boolean;
  /**
   * Per-endpoint flags — diagnostic only; drives the DemoBadge hint.
   */
  mockedEndpoints: string[];
  /**
   * When analytics period changes, re-fetch just the analytics slice.
   */
  setAnalyticsPeriod: (p: '7d' | '30d' | '90d') => void;
  analyticsPeriod: '7d' | '30d' | '90d';
}

function useMonitorData(autoRefresh: boolean): MonitorData & {
  reload: (showSpinner?: boolean) => Promise<void>;
  reset: () => Promise<void>;
} {
  const [status, setStatus] = useState<MonitorStatus>(MOCK_STATUS);
  const [rawViolations, setRawViolations] = useState<MonitorEvent[]>([]);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [spans, setSpans] = useState<SpanNode[]>([]);
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [analytics, setAnalytics] = useState<AnalyticsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [mockedEndpoints, setMockedEndpoints] = useState<string[]>([]);
  const [analyticsPeriod, setAnalyticsPeriod] = useState<'7d' | '30d' | '90d'>('30d');

  // Suppressions live in localStorage; re-read on every reload so global
  // filtering reflects user actions in ViolationGroups.
  const [suppressionVersion, setSuppressionVersion] = useState(0);
  useEffect(() => {
    // Re-read suppressions whenever storage changes in another tab.
    const onStorage = (e: StorageEvent) => {
      if (e.key === 'sponsio.suppressions.v1') setSuppressionVersion(v => v + 1);
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);
  // Also re-read periodically (2s) so same-tab ViolationGroups writes flow back.
  useEffect(() => {
    const id = setInterval(() => setSuppressionVersion(v => v + 1), 2000);
    return () => clearInterval(id);
  }, []);

  const violations = useMemo(() => {
    // `suppressionVersion` is consumed to force recompute when localStorage
    // suppressions change. (Reading localStorage inside useMemo is OK because
    // we bust it via state bump.)
    void suppressionVersion;
    return filterSuppressed(rawViolations, loadSuppressions());
  }, [rawViolations, suppressionVersion]);

  const reload = useCallback(async (showSpinner = false) => {
    if (showSpinner) setLoading(true);
    setError('');
    const mocked: string[] = [];
    const mark = (name: string) => mocked.push(name);
    try {
      const [st, log, tr, sp, trList, an] = await Promise.all([
        getMonitorStatus().catch(() => { mark('status'); return MOCK_STATUS; }),
        getMonitorLog().catch(() => { mark('log'); return MOCK_MONITOR_EVENTS_ENRICHED; }),
        getTrace().catch(() => { mark('trace'); return { events: MOCK_TRACE_EVENTS }; }),
        getSpans().catch(() => { mark('spans'); return MOCK_SPANS_ENRICHED; }),
        listTraces().catch(() => { mark('traces'); return MOCK_TRACE_SUMMARIES; }),
        getAnalytics(analyticsPeriod).catch(() => { mark('analytics'); return MOCK_ANALYTICS; }),
      ]);
      setStatus(st);
      if (log.length > 0) {
        setRawViolations(log);
      } else {
        setRawViolations(MOCK_MONITOR_EVENTS_ENRICHED);
        if (!mocked.includes('log')) mark('log');
      }
      if (tr.events.length > 0) {
        setEvents(tr.events);
      } else {
        setEvents(MOCK_TRACE_EVENTS);
        if (!mocked.includes('trace')) mark('trace');
      }
      if (sp.length > 0) {
        setSpans(sp);
      } else {
        setSpans(MOCK_SPANS_ENRICHED);
        if (!mocked.includes('spans')) mark('spans');
      }
      if (trList.length > 0) {
        setTraces(trList);
      } else {
        setTraces(MOCK_TRACE_SUMMARIES);
        if (!mocked.includes('traces')) mark('traces');
      }
      setAnalytics(an);
      setMockedEndpoints(mocked);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to load monitor data');
    } finally {
      if (showSpinner) setLoading(false);
    }
  }, [analyticsPeriod]);

  const reset = useCallback(async () => {
    try {
      await resetMonitor();
      await reload(true);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Reset failed');
    }
  }, [reload]);

  useEffect(() => { reload(true); }, [reload]);

  useEffect(() => {
    if (!autoRefresh) return;
    let pollId: ReturnType<typeof setInterval> | null = null;
    let es: EventSource | null = null;
    const clearPoll = () => {
      if (pollId !== null) { clearInterval(pollId); pollId = null; }
    };
    const startPoll = () => {
      if (pollId !== null) return;
      pollId = setInterval(() => reload(false), 2000);
    };
    try {
      es = new EventSource(monitorStreamUrl());
      // B6 fix: when SSE (re)connects, kill the polling fallback so we don't
      // double-fire reloads.
      es.onopen = () => clearPoll();
      es.onmessage = () => reload(false);
      es.onerror = () => startPoll();
    } catch {
      startPoll();
    }
    return () => {
      if (es) es.close();
      clearPoll();
    };
  }, [autoRefresh, reload]);

  return {
    status, violations, rawViolations, events, spans, traces, analytics,
    loading, error, isMockMode: mockedEndpoints.length > 0, mockedEndpoints,
    setAnalyticsPeriod, analyticsPeriod,
    reload, reset,
  };
}

// ═══════════════════════════════════════════════════════════════════════════════
//  OVERVIEW TAB
// ═══════════════════════════════════════════════════════════════════════════════

function OverviewTab({ data }: { data: MonitorData }) {
  const { status, violations, events, spans } = data;

  // P1 fix: only count events that were actually "triggered" checks toward
  // the pass rate. Pre-trigger "pass" reports dominate and make the metric
  // meaningless if included.
  const enforcementChecks = useMemo(() => violations.filter(isEnforcementCheck), [violations]);
  const hardCount = enforcementChecks.filter(v => normalizePipeline(v.pipeline) === 'hard' && v.result_action !== 'pass').length;
  const softCount = enforcementChecks.filter(v => normalizePipeline(v.pipeline) === 'soft' && v.result_action !== 'pass').length;
  const totalChecks = enforcementChecks.length;
  const passCount = enforcementChecks.filter(v => v.result_action === 'pass').length;
  const passRate = totalChecks > 0 ? Math.round((passCount / totalChecks) * 100) : 100;

  // P3 fix: restrict LLM aggregation to the last hour so the "Cost (1h)"
  // label is honest. If no spans fall in the window, the card shows "—".
  const nowSec = Date.now() / 1000;
  const recentSpans = useMemo(
    () => spans.filter(s => nowSec - s.start_time <= 3600),
    // nowSec intentionally captured at render time; we want it to move as
    // reload() replaces `spans`, not on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [spans],
  );

  const percentiles = useMemo(() => latencyPercentiles(spans), [spans]);
  const llmTotals = useMemo(() => aggregateLlmMetrics(recentSpans), [recentSpans]);
  const errorCount = useMemo(() => spans.filter(s => s.status === 'error').length, [spans]);
  const errorRate = spans.length > 0 ? (errorCount / spans.length) * 100 : 0;

  const violationSpark = useMemo(() => bucketCounts(enforcementChecks, 30, 60 * 60 * 1000), [enforcementChecks]);

  // P2 fix: bucket cost by real time-of-span, not array index. Produces a
  // faithful per-bucket distribution over the last hour.
  const costSpark = useMemo(() => {
    const buckets = new Array(30).fill(0);
    const windowSec = 3600;
    for (const s of spans) {
      const age = nowSec - s.start_time;
      if (age < 0 || age > windowSec) continue;
      const llm = extractLlmMetrics(s);
      if (!llm?.costUsd) continue;
      // Map age (seconds ago) → bucket index. Oldest = 0, newest = 29.
      const idx = Math.min(29, Math.max(0, 29 - Math.floor((age / windowSec) * 30)));
      buckets[idx] += llm.costUsd;
    }
    return buckets;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spans]);

  // Top risky agents (only over actually-triggered checks, same denominator
  // logic as Pass Rate so numbers are consistent across the dashboard).
  const riskyAgents = useMemo(() => {
    const map = new Map<string, { agentId: string; total: number; violations: number }>();
    for (const v of enforcementChecks) {
      const existing = map.get(v.agent_id) ?? { agentId: v.agent_id, total: 0, violations: 0 };
      existing.total++;
      if (v.result_action !== 'pass') existing.violations++;
      map.set(v.agent_id, existing);
    }
    return Array.from(map.values())
      .map(a => ({ ...a, rate: a.total > 0 ? a.violations / a.total : 0 }))
      .filter(a => a.violations > 0)
      .sort((a, b) => b.rate - a.rate)
      .slice(0, 3);
  }, [enforcementChecks]);

  const uniqueAgents = new Set(events.map(e => e.agent)).size;

  return (
    <div className="space-y-4">
      {/* Status banner */}
      <div className="flex items-center justify-between px-4 py-2.5 rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900">
        <div className="flex items-center gap-2">
          <span className={`w-2.5 h-2.5 rounded-full ${status.total_events > 0 ? 'bg-brand animate-pulse' : 'bg-surface-600'}`} />
          <span className={`text-xs font-medium ${status.total_events > 0 ? 'text-emerald-400' : 'text-muted'}`}>
            {status.total_events > 0 ? 'Monitoring active' : 'No agents connected'}
          </span>
          <span className="text-xs text-muted ml-2">
            · {uniqueAgents} agent{uniqueAgents !== 1 ? 's' : ''} · {events.length} events · {totalChecks} checks
          </span>
        </div>
      </div>

      {/* Metric cards: 6 cards in 3-up grid (2 rows × 3 cols at lg). The
          rows below use `lg:grid-cols-3`, so this keeps card edges
          vertically aligned throughout the page — top 3 metrics
          (violation-centric) sit above the next 3 (operational) which
          sit above the 3 main panels (Risky Agents / Contract Health /
          Enforcement). */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <MetricCard
          label="Pass Rate"
          value={`${passRate}%`}
          accent={passRate >= 80 ? 'border-l-emerald-500' : passRate >= 50 ? 'border-l-amber-500' : 'border-l-red-500'}
          sub={`${passCount}/${totalChecks} checks`}
          trend={violationSpark.map(v => -v)}
          trendColor="fill-emerald-400/50"
        />
        <MetricCard
          label="Det Blocks"
          value={hardCount}
          accent="border-l-red-500"
          sub="Hard violations"
          trend={bucketCounts(violations.filter(v => v.pipeline === 'hard'), 30, 60 * 60 * 1000)}
          trendColor="fill-red-500/60"
        />
        <MetricCard
          label="Sto Retries"
          value={softCount}
          accent="border-l-amber-500"
          sub="Soft catches"
          trend={bucketCounts(violations.filter(v => v.pipeline === 'soft'), 30, 60 * 60 * 1000)}
          trendColor="fill-amber-500/60"
        />
        <MetricCard
          label="Cost (last 1h)"
          value={llmTotals.costUsd !== undefined && llmTotals.costUsd > 0 ? formatCost(llmTotals.costUsd) : '—'}
          accent="border-l-violet-500"
          sub={llmTotals.totalTokens !== undefined && llmTotals.totalTokens > 0
            ? `${formatTokens(llmTotals.totalTokens)} tokens`
            : 'no LLM data in last 1h'}
          trend={costSpark.some(v => v > 0) ? costSpark : undefined}
          trendColor="fill-violet-400/60"
        />
        <MetricCard
          label="Latency p95"
          value={percentiles.count > 0 ? formatLatencyMs(percentiles.p95) : '—'}
          accent="border-l-sky-500"
          sub={percentiles.count > 0 ? `p50 ${formatLatencyMs(percentiles.p50)} · p99 ${formatLatencyMs(percentiles.p99)}` : 'no spans'}
        />
        <MetricCard
          label="Error Rate"
          value={`${errorRate.toFixed(1)}%`}
          accent={errorCount === 0 ? 'border-l-emerald-500' : errorCount < 3 ? 'border-l-amber-500' : 'border-l-orange-500'}
          sub={`${errorCount} exception${errorCount !== 1 ? 's' : ''}`}
        />
      </div>

      {events.length === 0 ? (
        <EmptyState />
      ) : (
        <>
          {/* Waterfall of all recent spans */}
          <TraceWaterfall spans={spans} />

          {/* Top Risky Agents + Contract Health + Enforcement */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <TopRiskyAgents agents={riskyAgents} />
            <ContractHealthPanel violations={violations} />
            <EnforcementSummary violations={violations} />
          </div>
        </>
      )}
    </div>
  );
}

function TopRiskyAgents({ agents }: { agents: { agentId: string; rate: number; violations: number; total: number }[] }) {
  if (agents.length === 0) {
    return (
      <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
        <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium mb-4">Top Risky Agents</p>
        <p className="text-sm text-muted py-2">No agents with violations.</p>
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
      <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium mb-4">Top Risky Agents</p>
      <div className="space-y-3">
        {agents.map(a => (
          <div key={a.agentId}>
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-mono text-stone-900 dark:text-zinc-100">{a.agentId}</span>
              <span className={`text-[10px] font-mono font-bold ${a.rate > 0.3 ? 'text-red-400' : 'text-amber-400'}`}>
                {(a.rate * 100).toFixed(0)}%
              </span>
            </div>
            <div className="h-1.5 rounded-full bg-surface-200 dark:bg-surface-700 overflow-hidden">
              <div
                className={`h-full rounded-full ${a.rate > 0.3 ? 'bg-red-500' : 'bg-amber-500'}`}
                style={{ width: `${Math.min(100, a.rate * 100)}%` }}
              />
            </div>
            <p className="text-[9px] text-muted mt-0.5 font-mono">
              {a.violations}/{a.total} events violated
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

function ContractHealthPanel({ violations }: { violations: MonitorEvent[] }) {
  const stats = useMemo(() => {
    const map = new Map<string, { name: string; pipeline: 'hard' | 'soft'; total: number; passed: number; violations: number }>();
    // Same denominator convention as Pass Rate: ignore events where the
    // constraint wasn't actually triggered.
    for (const v of violations) {
      if (!isEnforcementCheck(v)) continue;
      const key = v.constraint_name || v.action;
      if (!key) continue;
      const existing = map.get(key) ?? { name: key, pipeline: normalizePipeline(v.pipeline), total: 0, passed: 0, violations: 0 };
      existing.total++;
      if (v.result_action === 'pass') existing.passed++;
      else existing.violations++;
      map.set(key, existing);
    }
    return Array.from(map.values()).sort((a, b) => b.violations - a.violations).slice(0, 6);
  }, [violations]);

  if (stats.length === 0) {
    return (
      <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
        <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium mb-4">Contract Health</p>
        <p className="text-sm text-muted py-2">No contracts triggered yet.</p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
      <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium mb-4">
        Contract Health <span className="ml-1 text-muted font-mono">({stats.length})</span>
      </p>
      <div className="space-y-3">
        {stats.map(stat => {
          const passRate = stat.total > 0 ? Math.round((stat.passed / stat.total) * 100) : 100;
          const barColor = passRate >= 80 ? 'bg-emerald-500' : passRate >= 50 ? 'bg-amber-500' : 'bg-red-500';
          return (
            <div key={stat.name}>
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-1.5 min-w-0 flex-1">
                  <span className={`text-[9px] font-medium uppercase px-1 py-0.5 rounded ${
                    stat.pipeline === 'hard' ? 'text-red-400 bg-red-500/10' : 'text-violet-400 bg-violet-500/10'
                  }`}>{stat.pipeline === 'hard' ? 'DET' : 'STO'}</span>
                  <span className="text-xs text-stone-900 dark:text-zinc-100 truncate">{stat.name}</span>
                </div>
                <span className={`text-[10px] font-mono font-bold ${
                  passRate >= 80 ? 'text-emerald-400' : passRate >= 50 ? 'text-amber-400' : 'text-red-400'
                }`}>{passRate}%</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="flex-1 h-1.5 rounded-full bg-surface-200 dark:bg-surface-700 overflow-hidden">
                  <div
                    className={`h-full rounded-full ${barColor} transition-all duration-500`}
                    style={{ width: `${passRate}%` }}
                  />
                </div>
                <span className="text-[9px] text-muted font-mono w-12 text-right">{stat.passed}/{stat.total}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EnforcementSummary({ violations }: { violations: MonitorEvent[] }) {
  const counts = useMemo(() => {
    const c = { blocked: 0, retrying: 0, escalated: 0, redirected: 0, passed: 0 };
    for (const v of violations) {
      const a = (v.result_action ?? '').toLowerCase();
      if (a === 'blocked' || a === 'block') c.blocked++;
      else if (a === 'retrying' || a === 'retry') c.retrying++;
      else if (a === 'escalated' || a === 'escalate') c.escalated++;
      else if (a === 'redirected') c.redirected++;
      else if (a === 'pass') c.passed++;
    }
    return c;
  }, [violations]);

  const total = Object.values(counts).reduce((s, n) => s + n, 0);
  if (total === 0) return <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5"><p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium mb-4">Enforcement Outcomes</p><p className="text-sm text-muted">No enforcement yet.</p></div>;

  const items = [
    { label: 'Blocked', count: counts.blocked, color: 'bg-red-500', textColor: 'text-red-400' },
    { label: 'Retried', count: counts.retrying, color: 'bg-amber-500', textColor: 'text-amber-400' },
    { label: 'Escalated', count: counts.escalated, color: 'bg-violet-500', textColor: 'text-violet-400' },
    { label: 'Passed', count: counts.passed, color: 'bg-emerald-500', textColor: 'text-emerald-400' },
  ].filter(x => x.count > 0);

  return (
    <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
      <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium mb-4">Enforcement Outcomes</p>
      <div className="flex h-3 rounded-full overflow-hidden mb-3">
        {items.map(item => (
          <div key={item.label} className={`${item.color} transition-all duration-500`} style={{ width: `${(item.count / total) * 100}%` }} title={`${item.label}: ${item.count}`} />
        ))}
      </div>
      <div className="grid grid-cols-2 gap-2">
        {items.map(item => (
          <div key={item.label} className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${item.color} shrink-0`} />
            <span className="text-xs text-muted flex-1">{item.label}</span>
            <span className={`text-sm font-bold font-mono ${item.textColor}`}>{item.count}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function EmptyState() {
  const navigate = useNavigate();
  return (
    <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 px-6 py-10 text-center">
      <div className="text-4xl mb-3 opacity-20">⚡</div>
      <p className="text-sm text-zinc-600 dark:text-zinc-300 mb-2">
        No agents connected yet. Complete the integration step to start monitoring.
      </p>
      <button onClick={() => navigate('/integrate')} className="text-xs text-brand hover:text-brand/80 font-medium transition-colors">
        Go to Integrate →
      </button>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
//  TRACES TAB (master-detail, merges Live + History)
// ═══════════════════════════════════════════════════════════════════════════════

type TracesFilter = 'all' | 'violated' | 'passed' | 'error';
type TimeWindow = '15m' | '1h' | '6h' | '24h' | 'all';

/**
 * Unix-seconds lower bound for a time-window label. ``all`` → 0.
 * Returns a stable number for a given ``now`` so downstream
 * ``useMemo`` keys don't thrash on every render tick.
 */
function windowCutoff(w: TimeWindow, now: number): number {
  if (w === 'all') return 0;
  const secs = w === '15m' ? 900 : w === '1h' ? 3600 : w === '6h' ? 21_600 : 86_400;
  return now - secs;
}

interface TraceRowData {
  id: string;
  source: 'span' | 'history';
  label: string;
  agentId: string;
  startTime: number;      // unix seconds
  duration: number;       // ms
  hardViolations: number;
  softViolations: number;
  hasError: boolean;
  costUsd?: number;
  tokens?: number;
  sessionId?: string;
  span?: SpanNode;        // present for live spans, absent for history summaries
  historyTrace?: TraceSummary;
  traceId?: string;       // used for dedup between span and history rows (P8)
}

function spanTraceId(s: SpanNode): string | undefined {
  return (s.attributes?.['trace.id'] as string | undefined) ?? s.trace_id;
}

function traceRowsFromSpans(spans: SpanNode[]): TraceRowData[] {
  return spans
    .filter(s => s.span_type === 'sponsio.agent_turn')
    .map(s => {
      const llm = extractLlmMetrics(s);
      const hardViol = s.blocked ? 1 : 0;
      const softViol = s.sto_violations ?? 0;
      // B3 fix: stable key — no array index. Falls back to a hash of
      // agent + start_time + action if trace_id is missing.
      const stableKey = spanTraceId(s)
        ?? `${s.agent_id ?? 'unknown'}-${s.start_time}-${s.action ?? s.span_type}`;
      return {
        id: `span-${stableKey}`,
        source: 'span' as const,
        label: s.action ?? s.span_type,
        agentId: s.agent_id ?? 'unknown',
        startTime: s.start_time,
        duration: s.duration_ms ?? 0,
        hardViolations: hardViol + (s.det_violations ?? 0),
        softViolations: softViol,
        hasError: s.status === 'error',
        costUsd: llm?.costUsd,
        tokens: llm?.totalTokens,
        sessionId: extractSessionId(s),
        span: s,
        traceId: spanTraceId(s),
      };
    })
    .sort((a, b) => b.startTime - a.startTime);
}

function traceRowsFromHistory(traces: TraceSummary[]): TraceRowData[] {
  return traces.map(t => ({
    id: `hist-${t.traceId}`,
    source: 'history' as const,
    label: t.traceId.slice(0, 12),
    agentId: t.agentId,
    startTime: new Date(t.startTime).getTime() / 1000,
    duration: t.duration,
    hardViolations: t.hardViolations,
    softViolations: t.softViolations,
    hasError: false,
    historyTrace: t,
    traceId: t.traceId,
  }));
}

interface TracesTabProps {
  data: MonitorData;
  onReload: () => void;
  /** Controlled search string — lets other tabs pre-fill it (B1). */
  query: string;
  setQuery: (q: string) => void;
}

function TracesTab({ data, onReload, query, setQuery }: TracesTabProps) {
  const { spans, traces } = data;
  const [filter, setFilter] = useState<TracesFilter>('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showImport, setShowImport] = useState(false);
  // Default ON — long-running agents produce thousands of traces
  // across a handful of sessions. Flat-list view gets overwhelming
  // fast; session grouping is the "sane" default even when you only
  // have two sessions.
  const [groupBySession, setGroupBySession] = useState(true);
  // Default 1h — the sweet spot for "am I debugging something that
  // just happened?". 15m is tight for most agent runs; 6h+ is
  // usually too broad once production traffic kicks in.
  const [timeWindow, setTimeWindow] = useState<TimeWindow>('1h');

  // P8 fix: dedupe trace rows. When a live span and a history summary point
  // to the same trace_id, prefer the span row (richer detail panel).
  const allRows = useMemo(() => {
    const spanRows = traceRowsFromSpans(spans);
    const covered = new Set(spanRows.map(r => r.traceId).filter(Boolean));
    const histRows = traceRowsFromHistory(traces).filter(r => !(r.traceId && covered.has(r.traceId)));
    return [...spanRows, ...histRows];
  }, [spans, traces]);

  // Time-window filter first (cheapest reduction), then status filter.
  // The cutoff is memoised on a 30s tick to avoid re-running the filter
  // chain on every React render while ``now`` silently advances.
  const [nowSec, setNowSec] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = setInterval(() => setNowSec(Date.now() / 1000), 30_000);
    return () => clearInterval(id);
  }, []);
  const cutoff = windowCutoff(timeWindow, nowSec);

  const filtered = allRows.filter(r => {
    if (cutoff > 0 && r.startTime < cutoff) return false;
    if (filter === 'violated' && r.hardViolations === 0 && r.softViolations === 0) return false;
    if (filter === 'passed' && (r.hardViolations > 0 || r.softViolations > 0 || r.hasError)) return false;
    if (filter === 'error' && !r.hasError) return false;
    return true;
  });

  // Count of rows dropped by the time-window filter — surfaced in the
  // empty-state so the user isn't confused by an empty list when a
  // wider window would have traces to show.
  const droppedByWindow = useMemo(() => {
    if (timeWindow === 'all') return 0;
    return allRows.filter(r => r.startTime < cutoff).length;
  }, [allRows, cutoff, timeWindow]);

  // B4 fix: extend _all with synthetic tokens (blocked, pass, error, retry, det, sto)
  // so the placeholder's "-pass" example actually works.
  const getFields = useCallback((r: TraceRowData) => {
    const statusTokens: string[] = [];
    if (r.hardViolations > 0) statusTokens.push('blocked', 'violated', 'det', 'hard');
    if (r.softViolations > 0) statusTokens.push('retry', 'sto', 'soft');
    if (r.hasError) statusTokens.push('error');
    if (r.hardViolations === 0 && r.softViolations === 0 && !r.hasError) statusTokens.push('pass', 'clean');
    return {
      _all: `${r.agentId} ${r.label} ${r.sessionId ?? ''} ${r.traceId ?? ''} ${statusTokens.join(' ')}`,
      agent: r.agentId,
      action: r.label,
      session: r.sessionId ?? '',
      trace: r.traceId ?? '',
    };
  }, []);
  const searched = useSearchable(filtered, getFields, query);

  // Session grouping
  const grouped = useMemo(() => {
    if (!groupBySession) return null;
    const map = new Map<string, TraceRowData[]>();
    for (const r of searched) {
      const key = r.sessionId ?? `(no-session) ${r.agentId}`;
      const list = map.get(key) ?? [];
      list.push(r);
      map.set(key, list);
    }
    return Array.from(map.entries()).map(([sessionId, rows]) => ({
      sessionId,
      rows: rows.sort((a, b) => b.startTime - a.startTime),
    }));
  }, [searched, groupBySession]);

  // Derive the effective selected row: user's explicit pick, or fall back to
  // the most-recent trace. No useEffect needed — computed on each render.
  const selected = useMemo(() => {
    const explicit = selectedId ? searched.find(r => r.id === selectedId) : null;
    return explicit ?? searched[0] ?? null;
  }, [searched, selectedId]);
  const effectiveSelectedId = selected?.id ?? null;

  return (
    <div className="space-y-3">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-56">
          <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted pointer-events-none" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-4.35-4.35M17 11A6 6 0 1 1 5 11a6 6 0 0 1 12 0z" />
          </svg>
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder='Search: "agent:customer_bot", "-pass", "blocked", "session:sess_xx"…'
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-white dark:bg-surface-900 border border-surface-200 dark:border-surface-800 rounded-lg text-stone-900 dark:text-zinc-100 placeholder:text-muted focus:outline-none focus:border-brand/40 transition-colors"
          />
        </div>
        {/* Time window — primary filter for long-running agents. Sits
            before the status filter because "when" is the more common
            question than "what outcome". */}
        <div className="flex items-center gap-0.5 bg-surface-100 dark:bg-surface-800 rounded-lg p-0.5">
          {(['15m', '1h', '6h', '24h', 'all'] as TimeWindow[]).map(w => (
            <button
              key={w}
              onClick={() => setTimeWindow(w)}
              className={`px-2 py-1 text-[11px] rounded-md font-medium transition-colors ${
                timeWindow === w
                  ? 'bg-white dark:bg-surface-900 text-stone-900 dark:text-white shadow-sm'
                  : 'text-muted hover:text-stone-700 dark:hover:text-stone-200'
              }`}
              title={w === 'all' ? 'No time-window filter' : `Last ${w}`}
            >
              {w}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-0.5 bg-surface-100 dark:bg-surface-800 rounded-lg p-0.5">
          {(['all', 'violated', 'passed', 'error'] as TracesFilter[]).map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-2.5 py-1 text-[11px] rounded-md font-medium capitalize transition-colors ${
                filter === f
                  ? f === 'violated' ? 'bg-red-500/10 text-red-400'
                  : f === 'passed' ? 'bg-emerald-500/10 text-emerald-400'
                  : f === 'error' ? 'bg-orange-500/10 text-orange-400'
                  : 'bg-white dark:bg-surface-900 text-stone-900 dark:text-white shadow-sm'
                  : 'text-muted hover:text-stone-700 dark:hover:text-stone-200'
              }`}
            >
              {f}
            </button>
          ))}
        </div>
        <button
          onClick={() => setGroupBySession(g => !g)}
          className={`text-[11px] px-2.5 py-1.5 rounded-lg transition-colors ${
            groupBySession ? 'bg-brand/10 text-brand' : 'bg-surface-100 dark:bg-surface-800 text-muted'
          }`}
          title="Group traces by session.id attribute"
        >
          {groupBySession ? '✓ ' : ''}Group by session
        </button>
        <button
          onClick={() => setShowImport(v => !v)}
          className="text-[11px] bg-brand text-white px-2.5 py-1.5 rounded-lg hover:bg-brand/90 transition-colors"
        >
          {showImport ? 'Close Import' : 'Import Trace'}
        </button>
      </div>

      {showImport && <InlineImport onImported={onReload} onClose={() => setShowImport(false)} />}

      {searched.length === 0 ? (
        <div className="rounded-xl border border-dashed border-surface-300 dark:border-surface-700 bg-white dark:bg-surface-900 p-10 text-center">
          <p className="text-sm text-muted">No traces match the current filter.</p>
          {droppedByWindow > 0 && (
            <p className="text-xs text-muted mt-1">
              {droppedByWindow} older trace{droppedByWindow !== 1 ? 's' : ''} hidden by the <code className="font-mono text-[10px] bg-surface-100 dark:bg-surface-800 px-1 rounded">{timeWindow}</code> window.
            </p>
          )}
          {(query || filter !== 'all' || timeWindow !== 'all') && (
            <button
              onClick={() => { setQuery(''); setFilter('all'); setTimeWindow('all'); }}
              className="mt-2 text-xs text-brand hover:text-brand/80"
            >
              Clear filters
            </button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-3">
          {/* Left: trace list */}
          <div className="lg:col-span-2 rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 overflow-hidden flex flex-col">
            <div className="px-4 py-2.5 border-b border-surface-100 dark:border-surface-800 flex items-center justify-between">
              <span className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium">
                Traces <span className="text-muted ml-1 font-mono">({searched.length})</span>
              </span>
            </div>
            <div className="overflow-y-auto max-h-[720px] flex-1">
              {grouped
                ? grouped.map(g => (
                    <div key={g.sessionId}>
                      <div className="px-4 py-1.5 bg-surface-50 dark:bg-surface-800/60 border-b border-surface-100 dark:border-surface-800">
                        <span className="text-[10px] text-muted uppercase tracking-wider font-mono">
                          Session · {g.sessionId}
                        </span>
                        <span className="text-[10px] text-muted font-mono ml-2">{g.rows.length} trace{g.rows.length !== 1 ? 's' : ''}</span>
                      </div>
                      {g.rows.map(r => <TraceListRow key={r.id} row={r} selected={effectiveSelectedId === r.id} onSelect={() => setSelectedId(r.id)} />)}
                    </div>
                  ))
                : searched.map(r => <TraceListRow key={r.id} row={r} selected={effectiveSelectedId === r.id} onSelect={() => setSelectedId(r.id)} />)
              }
            </div>
          </div>

          {/* Right: detail */}
          <div className="lg:col-span-3">
            {selected ? (
              <TraceDetail row={selected} />
            ) : (
              <div className="rounded-xl border border-dashed border-surface-300 dark:border-surface-700 bg-white dark:bg-surface-900 p-10 text-center h-full flex items-center justify-center">
                <p className="text-sm text-muted">Select a trace on the left to inspect.</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function TraceListRow({ row, selected, onSelect }: { row: TraceRowData; selected: boolean; onSelect: () => void }) {
  const hasViol = row.hardViolations > 0 || row.softViolations > 0;
  return (
    <button
      onClick={onSelect}
      className={`w-full text-left px-4 py-2.5 border-b border-surface-100 dark:border-surface-800/60 transition-colors ${
        selected ? 'bg-brand/5 dark:bg-brand/10 border-l-2 border-l-brand' :
        hasViol ? 'hover:bg-red-500/5 border-l-2 border-l-red-500/30' :
        row.hasError ? 'hover:bg-orange-500/5 border-l-2 border-l-orange-500/30' :
        'hover:bg-surface-50 dark:hover:bg-surface-800/40 border-l-2 border-l-transparent'
      }`}
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        <span className="text-xs font-mono font-medium text-stone-900 dark:text-zinc-100 truncate flex-1">
          {row.label}
        </span>
        <ViolationBadge hard={row.hardViolations} soft={row.softViolations} />
      </div>
      <div className="flex items-center gap-2 text-[10px] text-muted font-mono">
        <span>{row.agentId}</span>
        <span>·</span>
        <span>{formatDuration(row.duration)}</span>
        {row.costUsd !== undefined && <><span>·</span><span className="text-violet-500">{formatCost(row.costUsd)}</span></>}
        {row.tokens !== undefined && <><span>·</span><span>{formatTokens(row.tokens)} tok</span></>}
        {/* B17 fix: single ml-auto element — error badge and timestamp can't
            both compete for the right edge. Error badge shows BEFORE the
            timestamp if present. */}
        <span className="ml-auto flex items-center gap-2">
          {row.hasError && <span className="text-orange-500 font-bold">error</span>}
          <span>{fmtTs(row.startTime)}</span>
        </span>
      </div>
    </button>
  );
}

function TraceDetail({ row }: { row: TraceRowData }) {
  const [showReVerify, setShowReVerify] = useState(false);

  if (row.source === 'history' && row.historyTrace) {
    return <HistoryTraceDetail trace={row.historyTrace} showReVerify={showReVerify} onToggleReVerify={() => setShowReVerify(v => !v)} />;
  }

  if (!row.span) {
    return <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-6"><p className="text-sm text-muted">No span data.</p></div>;
  }

  const span = row.span;
  const llm = extractLlmMetrics(span);

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-4">
        <div className="flex items-start justify-between gap-3 mb-2">
          <div className="min-w-0 flex-1">
            <h3 className="text-lg font-mono font-semibold text-stone-900 dark:text-white truncate">
              {span.action ?? span.span_type}
            </h3>
            <p className="text-[11px] text-muted font-mono">
              {span.agent_id} · {fmtTs(span.start_time)}
              {row.sessionId && <> · <span className="text-violet-500">session: {row.sessionId}</span></>}
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => setShowReVerify(v => !v)}
              className="text-[11px] px-2.5 py-1.5 rounded-lg bg-brand/10 text-brand hover:bg-brand/20 transition-colors"
            >
              {showReVerify ? 'Hide Re-verify' : 'Re-verify'}
            </button>
          </div>
        </div>
        {llm && (
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 mt-2 pt-2 border-t border-surface-100 dark:border-surface-800">
            {llm.model && <InlineStat label="Model" value={llm.model} />}
            {llm.totalTokens !== undefined && <InlineStat label="Tokens" value={formatTokens(llm.totalTokens)} />}
            {llm.costUsd !== undefined && <InlineStat label="Cost" value={formatCost(llm.costUsd)} />}
            {llm.ttftMs !== undefined && <InlineStat label="TTFT" value={`${llm.ttftMs}ms`} />}
            {span.duration_ms != null && <InlineStat label="Duration" value={formatLatencyMs(span.duration_ms)} />}
          </div>
        )}
        {showReVerify && <ReVerifyPanel onClose={() => setShowReVerify(false)} />}
      </div>

      {/* Waterfall — just this trace */}
      <TraceWaterfall spans={[span]} />

      {/* Contract evaluations (legacy SpanTree) */}
      <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-4">
        <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium mb-3">Contract Evaluations</p>
        <SpanTree span={span} defaultLevel={2} />
      </div>

      {/* I/O diff */}
      <IoDiffPanel span={span} />
    </div>
  );
}

function InlineStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[9px] text-muted uppercase tracking-wider">{label}</p>
      <p className="text-xs font-mono text-stone-900 dark:text-zinc-100 truncate">{value}</p>
    </div>
  );
}

function HistoryTraceDetail({ trace, showReVerify, onToggleReVerify }: { trace: TraceSummary; showReVerify: boolean; onToggleReVerify: () => void }) {
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    // B5 fix: cancel stale responses when user clicks through history rows
    // quickly. Without this, the detail panel can show events from a
    // previously-selected trace because of out-of-order resolution.
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) setLoading(true);
    });
    getTrace()
      .then(d => { if (!cancelled) setEvents(d.events); })
      .catch(() => { if (!cancelled) setEvents(MOCK_TRACE_EVENTS.filter(e => e.agent === trace.agentId)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [trace.agentId, trace.traceId]);

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-4">
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="min-w-0 flex-1">
            <h3 className="text-lg font-mono font-semibold text-stone-900 dark:text-white truncate">
              {trace.traceId}
            </h3>
            <p className="text-[11px] text-muted font-mono">{trace.agentId} · {formatTime(trace.startTime)} · {formatDuration(trace.duration)}</p>
          </div>
          <button
            onClick={onToggleReVerify}
            className="text-[11px] px-2.5 py-1.5 rounded-lg bg-brand/10 text-brand hover:bg-brand/20 transition-colors shrink-0"
          >
            {showReVerify ? 'Hide Re-verify' : 'Re-verify'}
          </button>
        </div>
        <div className="grid grid-cols-4 gap-2 pt-2 border-t border-surface-100 dark:border-surface-800">
          <InlineStat label="Events" value={String(trace.eventCount)} />
          <InlineStat label="Hard" value={String(trace.hardViolations)} />
          <InlineStat label="Soft" value={String(trace.softViolations)} />
          <InlineStat label="Duration" value={formatDuration(trace.duration)} />
        </div>
        {showReVerify && <ReVerifyPanel onClose={onToggleReVerify} />}
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-10"><Spinner size="sm" /></div>
      ) : (
        <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-4">
          <div className="flex items-center justify-between mb-3">
            <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium">Events</p>
            {/*
              P7: the backend today doesn't expose per-trace event history —
              /monitor/trace always returns the *current* monitor contents.
              We flag this in the UI so demos don't mislead, and so users
              know to check BACKEND_FIELDS.md §6 for the roadmap.
            */}
            <span
              title="Backend /monitor/trace currently returns the live monitor state, not the event stream of this historical trace. Per-trace event replay is on the backend roadmap."
              className="text-[9px] font-mono text-amber-500 bg-amber-500/10 px-1.5 py-0.5 rounded cursor-help"
            >
              live monitor · replay pending
            </span>
          </div>
          <div className="divide-y divide-surface-100 dark:divide-surface-800">
            {events.slice(0, 20).map((ev, i) => (
              <div key={i} className="flex items-center gap-2 py-2 text-[11px]">
                <span className="text-muted font-mono w-6 shrink-0">{i + 1}</span>
                <span className={`font-mono px-1.5 py-0.5 rounded shrink-0 text-[9px] ${
                  ev.event_type === 'tool_call' ? 'bg-sky-500/10 text-sky-400' :
                  ev.event_type === 'data_read' ? 'bg-green-500/10 text-green-400' :
                  'bg-sky-500/10 text-sky-400'
                }`}>{ev.event_type}</span>
                <span className="font-mono text-stone-700 dark:text-zinc-300 truncate flex-1">
                  {ev.tool ?? ev.key ?? ev.event_type}
                </span>
                {(ev.source || ev.target) && <DataLineage source={ev.source} target={ev.target} />}
                <span className="text-muted shrink-0">{ev.agent}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
//  VIOLATIONS TAB
// ═══════════════════════════════════════════════════════════════════════════════

interface ViolationsTabProps {
  data: MonitorData;
  onPromoteSlo: (c: string, a: string) => void;
  /** B1 fix: clicking "View traces" on a group switches to Traces tab with
      a pre-filled search. */
  onViewTraces: (c: string, a: string) => void;
}

function ViolationsTab({ data, onPromoteSlo, onViewTraces }: ViolationsTabProps) {
  const [query, setQuery] = useState('');
  const [pipelineFilter, setPipelineFilter] = useState<'all' | 'hard' | 'soft'>('all');

  // P4 fix: use normalizePipeline so backend "det"/"sto" also match.
  const filtered = useMemo(() => {
    if (pipelineFilter === 'all') return data.violations;
    return data.violations.filter(v => normalizePipeline(v.pipeline) === pipelineFilter);
  }, [data.violations, pipelineFilter]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="relative flex-1 min-w-56">
          <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted pointer-events-none" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-4.35-4.35M17 11A6 6 0 1 1 5 11a6 6 0 0 1 12 0z" />
          </svg>
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Filter constraint name / agent…"
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-white dark:bg-surface-900 border border-surface-200 dark:border-surface-800 rounded-lg text-stone-900 dark:text-zinc-100 placeholder:text-muted focus:outline-none focus:border-brand/40"
          />
        </div>
        <div className="flex items-center gap-0.5 bg-surface-100 dark:bg-surface-800 rounded-lg p-0.5">
          {(['all', 'hard', 'soft'] as const).map(f => (
            <button
              key={f}
              onClick={() => setPipelineFilter(f)}
              className={`px-2.5 py-1 text-[11px] rounded-md font-medium capitalize transition-colors ${
                pipelineFilter === f
                  ? f === 'hard' ? 'bg-red-500/10 text-red-400'
                  : f === 'soft' ? 'bg-violet-500/10 text-violet-400'
                  : 'bg-white dark:bg-surface-900 text-stone-900 dark:text-white shadow-sm'
                  : 'text-muted hover:text-stone-700 dark:hover:text-stone-200'
              }`}
            >
              {f === 'hard' ? 'Det' : f === 'soft' ? 'Sto' : 'All'}
            </button>
          ))}
        </div>
      </div>
      <ViolationGroups
        events={filtered}
        query={query}
        onAddSlo={onPromoteSlo}
        onViewTraces={onViewTraces}
      />
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
//  INSIGHTS TAB
// ═══════════════════════════════════════════════════════════════════════════════

function InsightsTab({ data, sloSuggestion, onSloSuggestionConsumed }: {
  data: MonitorData;
  sloSuggestion: { constraintName: string; agentId?: string } | null;
  onSloSuggestionConsumed: () => void;
}) {
  const { analytics, violations, analyticsPeriod, setAnalyticsPeriod } = data;

  return (
    <div className="space-y-4">
      {/* B16 fix: period selector for the analytics charts. Re-fetches via
          the shared data hook when changed. */}
      <div className="flex items-center justify-end gap-2">
        <span className="text-[10px] uppercase tracking-wider text-muted">Analytics window:</span>
        <div className="flex items-center gap-0.5 bg-surface-100 dark:bg-surface-800 rounded-lg p-0.5">
          {(['7d', '30d', '90d'] as const).map(p => (
            <button
              key={p}
              onClick={() => setAnalyticsPeriod(p)}
              className={`px-2.5 py-1 text-[11px] rounded-md font-medium transition-colors ${
                analyticsPeriod === p
                  ? 'bg-white dark:bg-surface-900 text-stone-900 dark:text-white shadow-sm'
                  : 'text-muted hover:text-stone-700 dark:hover:text-stone-200'
              }`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      {/* Row 1: SLO Board (full width) */}
      <SloBoard
        events={violations}
        suggestion={sloSuggestion}
        onSuggestionConsumed={onSloSuggestionConsumed}
      />

      {/* Row 2: Regressions + Heatmap */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <RegressionPanel events={violations} />
        <ViolationHeatmap events={violations} />
      </div>

      {/* Row 3: Analytics charts (legacy Analytics page content) */}
      {analytics && <AnalyticsCharts analytics={analytics} />}
    </div>
  );
}

function AnalyticsCharts({ analytics }: { analytics: AnalyticsData }) {
  const sortedViolations = [...analytics.violationsByPattern].sort((a, b) => b.count - a.count);
  const maxViolationCount = sortedViolations[0]?.count ?? 1;
  const BAR_COLORS = ['bg-sky-500', 'bg-blue-500', 'bg-amber-400', 'bg-red-500', 'bg-violet-500'];
  const topContracts = [...analytics.topViolatedContracts].sort((a, b) => b.count - a.count).slice(0, 5);
  const improvement = analytics.scoreHistory.length >= 2
    ? Math.round(analytics.scoreHistory[analytics.scoreHistory.length - 1].score - analytics.scoreHistory[0].score)
    : 0;

  return (
    <>
      {/* Score Over Time */}
      {analytics.scoreHistory.length > 0 && (
        <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
          <p className="text-[10px] text-muted uppercase tracking-widest font-medium mb-4">Safety Score Over Time</p>
          <div className="h-40 flex items-end gap-0.5 overflow-hidden">
            {analytics.scoreHistory.map((entry, i) => (
              <div
                key={i}
                title={`${entry.date}: ${entry.score}`}
                className="flex-1 min-w-0 bg-sky-500 rounded-t-sm transition-all"
                style={{ height: `${Math.max(2, entry.score)}%` }}
              />
            ))}
          </div>
          <p className="text-xs text-muted mt-3">
            {improvement > 0 ? <span>Score improved by <span className="text-emerald-400 font-semibold">+{improvement}%</span> this period</span>
             : improvement < 0 ? <span>Score declined by <span className="text-red-400 font-semibold">{improvement}%</span> this period</span>
             : 'No change this period'}
          </p>
        </div>
      )}

      {/* Violation Breakdown + Top Contracts + Agent Reliability in a grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {sortedViolations.length > 0 && (
          <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
            <p className="text-[10px] text-muted uppercase tracking-widest font-medium mb-4">Violation Breakdown</p>
            <div className="space-y-2.5">
              {sortedViolations.slice(0, 6).map((item, i) => (
                <div key={item.pattern} className="flex items-center gap-3">
                  <span className="text-[11px] font-mono text-muted w-32 shrink-0 truncate" title={item.pattern}>
                    {item.pattern}
                  </span>
                  <div className="flex-1 h-4 bg-surface-100 dark:bg-surface-800 rounded overflow-hidden">
                    <div
                      className={`h-full rounded ${BAR_COLORS[i] ?? 'bg-zinc-500'}`}
                      style={{ width: `${Math.max(2, (item.count / maxViolationCount) * 100)}%` }}
                    />
                  </div>
                  <span className="text-[11px] font-mono w-8 text-right shrink-0">{item.count}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {analytics.agentReliability.length > 0 && (
          <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
            <p className="text-[10px] text-muted uppercase tracking-widest font-medium mb-4">Agent Reliability</p>
            <div className="space-y-3">
              {analytics.agentReliability.map(a => {
                const color = a.reliability >= 95 ? 'bg-emerald-500' : a.reliability >= 80 ? 'bg-amber-400' : 'bg-red-500';
                const tcolor = a.reliability >= 95 ? 'text-emerald-400' : a.reliability >= 80 ? 'text-amber-400' : 'text-red-500';
                return (
                  <div key={a.agentId}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-mono text-stone-900 dark:text-zinc-100">{a.agentId}</span>
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] text-muted">{a.totalEvents} events</span>
                        <span className={`text-xs font-semibold ${tcolor}`}>{a.reliability.toFixed(1)}%</span>
                      </div>
                    </div>
                    <div className="h-2 bg-surface-100 dark:bg-surface-800 rounded-full overflow-hidden">
                      <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.min(100, a.reliability)}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* Top Violated Contracts */}
      {topContracts.length > 0 && (
        <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
          <p className="text-[10px] text-muted uppercase tracking-widest font-medium mb-4">Top Violated Contracts</p>
          <div className="space-y-2">
            {topContracts.map((c, i) => (
              <div key={i} className="flex items-start gap-3 px-3 py-2.5 rounded-lg bg-surface-50 dark:bg-surface-800">
                <span className="text-xs font-bold text-muted w-6 shrink-0 pt-0.5">#{i + 1}</span>
                <p className="text-sm font-mono text-stone-900 dark:text-zinc-100 flex-1 leading-relaxed min-w-0 break-words">
                  {c.nlText}
                </p>
                <div className="flex flex-col items-end gap-1 shrink-0">
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-red-500/10 text-red-400 font-semibold">{c.count}×</span>
                  <span className="text-[10px] text-muted whitespace-nowrap">{c.lastViolated}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
//  RE-VERIFY PANEL + INLINE IMPORT (reused from old implementation)
// ═══════════════════════════════════════════════════════════════════════════════

function ReVerifyPanel({ onClose }: { onClose: () => void }) {
  const [nlText, setNlText] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ReVerifyResponse | null>(null);
  const [err, setErr] = useState('');

  const handleVerify = async () => {
    if (!nlText.trim()) return;
    setLoading(true);
    setErr('');
    setResult(null);
    try {
      setResult(await reVerifyContract(nlText.trim()));
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Verification failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mt-3 rounded-lg border border-surface-200 dark:border-surface-800 bg-surface-50 dark:bg-surface-800 p-3">
      <div className="flex items-center justify-between mb-2">
        <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium">Re-verify Contract</p>
        <button onClick={onClose} className="text-muted hover:text-stone-900 dark:hover:text-white text-xs">✕</button>
      </div>
      <textarea
        value={nlText}
        onChange={e => setNlText(e.target.value)}
        placeholder='e.g. tool `check_policy` must precede `issue_refund`'
        rows={2}
        className="w-full bg-white dark:bg-surface-900 border border-surface-200 dark:border-surface-700 rounded-lg px-3 py-2 text-sm font-mono text-stone-900 dark:text-zinc-100 placeholder:text-muted focus:outline-none focus:border-brand/40 resize-none mb-2"
      />
      <button
        onClick={handleVerify}
        disabled={loading || !nlText.trim()}
        className="text-xs bg-brand text-white px-3 py-1.5 rounded-lg hover:bg-brand/90 disabled:opacity-40 transition-colors flex items-center gap-1.5"
      >
        {loading && <Spinner size="sm" />}
        Verify
      </button>
      {err && <p className="text-xs text-red-400 mt-2">{err}</p>}
      {result && (
        <div className="mt-3 space-y-2">
          <div className="flex items-center gap-2">
            <span className={`text-xs font-bold ${result.overall_passed ? 'text-emerald-400' : 'text-red-400'}`}>
              {result.overall_passed ? 'PASSED' : 'FAILED'}
            </span>
            <span className="text-xs text-muted font-mono">{result.pattern_name}</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {result.results.map(step => (
              <div key={step.timestep} title={`Step ${step.timestep}: ${step.event_summary}`}
                className={`w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold ${
                  step.passed ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'
                }`}>
                {step.timestep}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function InlineImport({ onImported, onClose }: { onImported: () => void; onClose: () => void }) {
  const [jsonText, setJsonText] = useState('');
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  // B10 fix: track the active FileReader and abort it if the component
  // unmounts mid-read so we don't call setJsonText on an unmounted tree.
  const readerRef = useRef<FileReader | null>(null);
  const mountedRef = useRef(true);
  useEffect(() => {
    return () => {
      mountedRef.current = false;
      if (readerRef.current && readerRef.current.readyState === 1 /* LOADING */) {
        readerRef.current.abort();
      }
    };
  }, []);

  const handleFile = useCallback((file: File) => {
    // Abort any in-flight read before starting a new one.
    if (readerRef.current && readerRef.current.readyState === 1) {
      readerRef.current.abort();
    }
    const reader = new FileReader();
    readerRef.current = reader;
    reader.onload = e => {
      if (!mountedRef.current) return;
      setJsonText((e.target?.result as string) ?? '');
    };
    reader.onerror = () => {
      if (!mountedRef.current) return;
      setErr('Failed to read file.');
    };
    reader.readAsText(file);
  }, []);

  const handleImport = async () => {
    setErr('');
    let parsed: unknown;
    try { parsed = JSON.parse(jsonText); }
    catch { setErr('Invalid JSON'); return; }
    const asObj = parsed as Record<string, unknown>;
    const events: Record<string, unknown>[] = Array.isArray(parsed)
      ? (parsed as Record<string, unknown>[])
      : Array.isArray(asObj.events) ? (asObj.events as Record<string, unknown>[]) : [];
    if (events.length === 0) { setErr('No events found.'); return; }
    const metadata = !Array.isArray(parsed) && asObj.metadata ? (asObj.metadata as Record<string, unknown>) : undefined;
    setLoading(true);
    try {
      await importTrace({ events, ...(metadata ? { metadata } : {}) });
      onImported();
      onClose();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Import failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-4">
      <div className="flex items-center justify-between mb-3">
        <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium">Import Trace</p>
        <button onClick={onClose} className="text-muted hover:text-stone-900 dark:hover:text-white text-xs">✕</button>
      </div>
      <FileUpload
        accept=".json"
        onFile={handleFile}
        label="Drop a .json trace file here or click to browse"
        sublabel="Accepts .json files with an events array"
      />
      <div className="my-3 flex items-center gap-2">
        <div className="flex-1 border-t border-surface-200 dark:border-surface-700" />
        <span className="text-[10px] text-muted uppercase tracking-wider">or paste JSON</span>
        <div className="flex-1 border-t border-surface-200 dark:border-surface-700" />
      </div>
      <textarea
        value={jsonText}
        onChange={e => setJsonText(e.target.value)}
        placeholder='{"events": [...]}'
        rows={4}
        className="w-full bg-surface-50 dark:bg-surface-800 border border-surface-200 dark:border-surface-700 rounded-lg px-3 py-2 text-xs font-mono text-stone-900 dark:text-zinc-100 placeholder:text-muted focus:outline-none focus:border-brand/40 resize-none"
      />
      {err && <p className="text-xs text-red-400 mt-2">{err}</p>}
      <div className="flex justify-end gap-2 mt-3">
        <button onClick={onClose} className="text-xs text-muted hover:text-stone-900 dark:hover:text-white border border-surface-200 dark:border-surface-700 px-4 py-2 rounded-lg">Cancel</button>
        <button onClick={handleImport} disabled={loading || !jsonText.trim()} className="text-xs bg-brand text-white px-4 py-2 rounded-lg hover:bg-brand/90 disabled:opacity-40 flex items-center gap-1.5">
          {loading && <Spinner size="sm" />}
          Import
        </button>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
//  MAIN PAGE
// ═══════════════════════════════════════════════════════════════════════════════

type Tab = 'overview' | 'traces' | 'violations' | 'insights';

export default function MonitorPage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>('overview');
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [sloSuggestion, setSloSuggestion] = useState<{ constraintName: string; agentId?: string } | null>(null);
  // B1 fix: Traces query lives on the parent so other tabs (notably
  // Violations → "View traces →") can pre-fill it before switching tabs.
  const [tracesQuery, setTracesQuery] = useState('');

  const data = useMonitorData(autoRefresh);

  const handleRefineConstraints = useCallback(async () => {
    try {
      const contracts = await listContracts();
      if (contracts.length === 0) { navigate('/rulebook'); return; }
      const byAgent = new Map<string, string[]>();
      for (const c of contracts) {
        const existing = byAgent.get(c.agent_id) ?? [];
        for (const g of c.guarantees) existing.push(g.desc);
        byAgent.set(c.agent_id, existing);
      }
      // B12 fix: reduce() on an empty array with no init value throws. Even
      // after the contracts.length > 0 check above, byAgent may still be
      // empty if every contract's guarantees list is empty.
      const entries = Array.from(byAgent.entries());
      if (entries.length === 0) { navigate('/rulebook'); return; }
      const [chosenAgent, chosenLines] = entries.reduce(
        (acc, cur) => (cur[1].length > acc[1].length ? cur : acc),
      );
      navigate('/rulebook', { state: { nlText: chosenLines.join('\n'), agentId: chosenAgent } });
    } catch {
      navigate('/rulebook');
    }
  }, [navigate]);

  const handlePromoteSlo = useCallback((constraintName: string, agentId: string) => {
    setSloSuggestion({ constraintName, agentId });
    setTab('insights');
  }, []);

  // B1 fix: wire "View traces" buttons in Violations tab to Traces tab with
  // the right filter pre-applied (via the search query).
  const handleViewTraces = useCallback((_constraintName: string, agentId: string) => {
    setTracesQuery(`agent:${agentId}`);
    setTab('traces');
  }, []);

  // P5/B8 fix: confirm before destroying the monitor log.
  const handleReset = useCallback(() => {
    const ok = window.confirm(
      'Reset the monitor? This permanently deletes all recorded events, spans, and traces. This cannot be undone.',
    );
    if (ok) data.reset();
  }, [data]);

  if (data.error) return <PageError message={data.error} onRetry={() => data.reload(true)} />;
  if (data.loading) return <div className="flex items-center justify-center h-64"><Spinner /></div>;

  const tabs: { key: Tab; label: string; icon: string; count?: number }[] = [
    { key: 'overview',   label: 'Overview',   icon: '●' },
    { key: 'traces',     label: 'Traces',     icon: '≡' },
    { key: 'violations', label: 'Violations', icon: '!',
      // Only count actual violations (not pre-trigger "pass" noise), and
      // only unsuppressed ones (data.violations is already suppression-filtered).
      count: data.violations.filter(v => isEnforcementCheck(v) && v.result_action !== 'pass').length },
    { key: 'insights',   label: 'Insights',   icon: '◎' },
  ];

  return (
    <div className="max-w-7xl mx-auto">
      {/* header */}
      <div className="flex items-start justify-between mb-1">
        <div>
          <h1 className="text-3xl font-display text-stone-900 dark:text-white">Monitor</h1>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            className={`px-3 py-1 text-xs rounded-lg transition-colors ${
              autoRefresh ? 'bg-emerald-500/10 text-emerald-400' : 'bg-surface-100 dark:bg-surface-800 text-muted'
            }`}
          >
            Auto-refresh {autoRefresh ? 'on' : 'off'}
          </button>
          <button
            onClick={handleReset}
            className="px-3 py-1 text-xs border border-surface-200 dark:border-surface-800 text-muted rounded-lg hover:bg-surface-100 dark:hover:bg-surface-800 hover:text-stone-900 dark:hover:text-white transition-colors"
          >
            Reset
          </button>
        </div>
      </div>
      <p className="text-muted text-sm mb-6 max-w-2xl">
        Real-time observability for contract enforcement. Streams agent actions, waterfalls, violations,
        and rolls them up into analytics and SLOs.
      </p>

      {/* P11 fix: demo mode banner — surfaces mock-data fallback so users
          don't mistake simulated data for live production traffic. */}
      {data.isMockMode && (
        <DemoBadge
          className="mb-4"
          label="DEMO MODE"
          hint={`Some Monitor endpoints aren't wired up — showing simulated data for: ${data.mockedEndpoints.join(', ')}. Connect your agent via Integrate to see live traces.`}
        />
      )}

      {/* tab bar */}
      <div className="flex items-center gap-1 bg-surface-100 dark:bg-surface-800 rounded-xl p-1 mb-6 w-fit">
        {tabs.map(({ key, label, icon, count }) => {
          // P10 fix: the pulse icon on Overview is meant to signal "live
          // streaming". Tie it to autoRefresh, not to tab selection.
          const isLive = key === 'overview' && autoRefresh;
          return (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`px-4 py-1.5 text-sm font-medium rounded-lg transition-colors flex items-center gap-2 ${
                tab === key
                  ? 'bg-white dark:bg-surface-900 text-zinc-900 dark:text-white shadow-sm'
                  : 'text-muted hover:text-stone-700 dark:hover:text-stone-200'
              }`}
            >
              <span className={`text-[10px] ${isLive ? 'text-emerald-400 animate-pulse' : 'opacity-50'}`}>{icon}</span>
              {label}
              {count !== undefined && count > 0 && (
                <span className={`text-[9px] font-mono px-1.5 py-0.5 rounded ${
                  tab === key ? 'bg-red-500/10 text-red-400' : 'bg-surface-200 dark:bg-surface-700 text-muted'
                }`}>
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* tab content */}
      {tab === 'overview'   && <OverviewTab   data={data} />}
      {tab === 'traces'     && <TracesTab     data={data} onReload={() => data.reload(true)} query={tracesQuery} setQuery={setTracesQuery} />}
      {tab === 'violations' && <ViolationsTab data={data} onPromoteSlo={handlePromoteSlo} onViewTraces={handleViewTraces} />}
      {tab === 'insights'   && <InsightsTab   data={data} sloSuggestion={sloSuggestion} onSloSuggestionConsumed={() => setSloSuggestion(null)} />}

      {/* bottom nav */}
      <div className="mt-8">
        <PipelineNav
          prev={{ label: 'Back to Integrate', path: '/integrate' }}
          next={{ label: 'Refine Constraints', path: '/rulebook', onClick: handleRefineConstraints }}
        />
      </div>
    </div>
  );
}
