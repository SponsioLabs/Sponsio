import { useState, useEffect } from 'react';
import { getAnalytics } from '../api/client';
import type { AnalyticsData } from '../types';
import Spinner from '../components/Spinner';
import PageError from '../components/PageError';
import { useNavigate } from 'react-router-dom';
import { MOCK_ANALYTICS } from '../data/mockMonitorData';

const BAR_COLORS = [
  'bg-sky-500',
  'bg-blue-500',
  'bg-amber-400',
  'bg-red-500',
];

function reliabilityColor(r: number): string {
  if (r >= 95) return 'bg-emerald-500';
  if (r >= 80) return 'bg-amber-400';
  return 'bg-red-500';
}

function reliabilityTextColor(r: number): string {
  if (r >= 95) return 'text-emerald-400';
  if (r >= 80) return 'text-amber-400';
  return 'text-red-500';
}

function scoreImprovement(history: { date: string; score: number }[]): number {
  if (history.length < 2) return 0;
  return Math.round(history[history.length - 1].score - history[0].score);
}

function isEmpty(data: AnalyticsData): boolean {
  return (
    data.scoreHistory.length === 0 &&
    data.violationsByPattern.length === 0 &&
    data.topViolatedContracts.length === 0 &&
    data.agentReliability.length === 0
  );
}

export default function Analytics() {
  const [data, setData] = useState<AnalyticsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [period, setPeriod] = useState<'7d' | '30d' | '90d'>('30d');
  const navigate = useNavigate();

  useEffect(() => {
    queueMicrotask(() => {
      setLoading(true);
      setError('');
    });
    getAnalytics(period)
      .then((d: AnalyticsData) => setData(d))
      .catch(() => {
        // API offline — fall back to mock analytics for demo mode
        setData(MOCK_ANALYTICS);
      })
      .finally(() => setLoading(false));
  }, [period]);

  if (error) return <PageError message={error} onRetry={() => { setError(''); setLoading(true); getAnalytics(period).then(setData).catch(() => setData(MOCK_ANALYTICS)).finally(() => setLoading(false)); }} />;
  if (loading) return <div className="flex items-center justify-center h-64"><Spinner /></div>;
  if (!data) return null;

  const empty = isEmpty(data);

  const sortedViolations = [...data.violationsByPattern].sort((a, b) => b.count - a.count);
  const maxViolationCount = sortedViolations[0]?.count ?? 1;

  const topContracts = [...data.topViolatedContracts]
    .sort((a, b) => b.count - a.count)
    .slice(0, 5);

  const improvement = scoreImprovement(data.scoreHistory);

  return (
    <div className="max-w-6xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-3xl font-display text-stone-900 dark:text-white">Analytics</h1>
        <div className="flex items-center gap-1 bg-surface-100 dark:bg-surface-800 rounded-lg p-0.5">
          {(['7d', '30d', '90d'] as const).map(p => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                period === p
                  ? 'bg-white dark:bg-surface-900 text-stone-900 dark:text-white shadow-sm'
                  : 'text-muted hover:text-stone-900 dark:hover:text-stone-900 dark:hover:text-white'
              }`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>
      <p className="text-muted text-sm mb-8 max-w-2xl">
        Historical view of your agents' safety posture. Aggregates violation events from the monitor log
        to show score trends, most-violated contracts, and per-agent reliability over the selected window.
      </p>

      {/* Empty State */}
      {empty && (
        <div className="rounded-2xl border border-dashed border-brand/30 bg-brand/5 p-10 text-center">
          <div className="w-12 h-12 rounded-full bg-brand/10 flex items-center justify-center mx-auto mb-4">
            <svg className="w-6 h-6 text-brand" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
            </svg>
          </div>
          <h2 className="text-lg font-display text-stone-900 dark:text-white mb-2">No analytics yet</h2>
          <p className="text-sm text-muted mb-6 max-w-sm mx-auto">
            Start monitoring your agents to see analytics here. Define your first contract in the Rulebook.
          </p>
          <button
            onClick={() => navigate('/rulebook')}
            className="px-5 py-2 bg-brand text-surface-950 text-sm font-semibold rounded-lg hover:bg-brand-400 transition-colors"
          >
            Go to Rulebook
          </button>
        </div>
      )}

      {!empty && (
        <div className="space-y-6">
          {/* 1. Safety Score Over Time */}
          {data.scoreHistory.length > 0 && (
            <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
              <p className="text-[10px] text-muted uppercase tracking-widest font-medium mb-4">Safety Score Over Time</p>
              <div className="h-40 flex items-end gap-0.5 overflow-hidden">
                {data.scoreHistory.map((entry, i) => (
                  <div
                    key={i}
                    title={`${entry.date}: ${entry.score}`}
                    className="flex-1 min-w-0 bg-sky-500 rounded-t-sm transition-all"
                    style={{ height: `${Math.max(2, entry.score)}%` }}
                  />
                ))}
              </div>
              <p className="text-xs text-muted mt-3">
                {improvement > 0
                  ? <span>Score improved by <span className="text-emerald-400 font-semibold">+{improvement}%</span> this month</span>
                  : improvement < 0
                    ? <span>Score declined by <span className="text-red-400 font-semibold">{improvement}%</span> this month</span>
                    : <span className="text-zinc-600 dark:text-zinc-300">No change this month</span>
                }
              </p>
            </div>
          )}

          {/* 2. Violation Breakdown */}
          {sortedViolations.length > 0 && (
            <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
              <p className="text-[10px] text-muted uppercase tracking-widest font-medium mb-4">Violation Breakdown</p>
              <div className="space-y-3">
                {sortedViolations.map((item, i) => (
                  <div key={item.pattern} className="flex items-center gap-3">
                    <span className="text-xs font-mono text-muted w-40 shrink-0 truncate" title={item.pattern}>
                      {item.pattern}
                    </span>
                    <div className="flex-1 h-5 bg-surface-100 dark:bg-surface-800 rounded overflow-hidden">
                      <div
                        className={`h-full rounded transition-all ${BAR_COLORS[i] ?? 'bg-zinc-500'}`}
                        style={{ width: `${Math.max(2, (item.count / maxViolationCount) * 100)}%` }}
                      />
                    </div>
                    <span className="text-xs font-mono text-stone-700 dark:text-zinc-300 w-8 text-right shrink-0">
                      {item.count}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 3. Top Violated Contracts */}
          {topContracts.length > 0 && (
            <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
              <div className="flex items-center justify-between mb-4">
                <p className="text-[10px] text-muted uppercase tracking-widest font-medium">Top Violated Contracts</p>
                <button
                  onClick={() => navigate('/monitor')}
                  className="text-xs text-zinc-600 dark:text-zinc-300 hover:text-stone-900 dark:hover:text-zinc-100 transition-colors"
                >
                  View in Monitor →
                </button>
              </div>
              <div className="space-y-2">
                {topContracts.map((contract, i) => (
                  <div
                    key={i}
                    className="flex items-start gap-3 px-3 py-3 rounded-lg bg-surface-50 dark:bg-surface-800"
                  >
                    <span className="text-xs font-bold text-zinc-600 dark:text-zinc-300 w-6 shrink-0 pt-0.5">#{i + 1}</span>
                    <p className="text-sm font-mono text-stone-900 dark:text-zinc-100 flex-1 leading-relaxed min-w-0 break-words">
                      {contract.nlText}
                    </p>
                    <div className="flex flex-col items-end gap-1 shrink-0">
                      <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-red-500/10 text-red-400 font-semibold">
                        {contract.count}×
                      </span>
                      <span className="text-[10px] text-zinc-600 dark:text-zinc-300 whitespace-nowrap">
                        {contract.lastViolated}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 4. Agent Reliability Score */}
          {data.agentReliability.length > 0 && (
            <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
              <p className="text-[10px] text-muted uppercase tracking-widest font-medium mb-4">Agent Reliability Score</p>
              <div className="space-y-4">
                {data.agentReliability.map(agent => (
                  <div key={agent.agentId}>
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="text-sm font-mono text-stone-900 dark:text-zinc-100">{agent.agentId}</span>
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] text-muted">{agent.totalEvents} events</span>
                        <span className={`text-sm font-semibold ${reliabilityTextColor(agent.reliability)}`}>
                          {agent.reliability.toFixed(1)}%
                        </span>
                      </div>
                    </div>
                    <div className="h-2 w-full bg-surface-100 dark:bg-surface-800 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${reliabilityColor(agent.reliability)}`}
                        style={{ width: `${Math.min(100, Math.max(0, agent.reliability))}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
