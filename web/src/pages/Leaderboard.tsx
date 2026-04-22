import { useEffect, useState, useCallback } from 'react';
import { getLeaderboard, getLeaderboardStats } from '../api/client';
import type { LeaderboardEntry, LeaderboardStats } from '../types';
import PageError from '../components/PageError';
import Spinner from '../components/Spinner';
import { MOCK_LEADERBOARD_ENTRIES, MOCK_LEADERBOARD_STATS } from '../data/mockMonitorData';

const gradeColors: Record<string, string> = {
  'A+': 'text-emerald-400 bg-emerald-500/10',
  A: 'text-emerald-400 bg-emerald-500/10',
  B: 'text-blue-400 bg-blue-500/10',
  C: 'text-amber-400 bg-amber-500/10',
  D: 'text-orange-400 bg-orange-500/10',
  F: 'text-red-400 bg-red-500/10',
};

type SortField = 'rank' | 'score' | 'timestamp';
type Period = 'all' | 'week' | 'today';

export default function Leaderboard() {
  const [entries, setEntries] = useState<LeaderboardEntry[]>([]);
  const [stats, setStats] = useState<LeaderboardStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [sortBy, setSortBy] = useState<SortField>('rank');
  const [sortAsc, setSortAsc] = useState(true);
  const [frameworkFilter, setFrameworkFilter] = useState('');
  const [period, setPeriod] = useState<Period>('all');
  const [showBadge, setShowBadge] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError('');
    Promise.all([getLeaderboard(), getLeaderboardStats()])
      .then(([lb, st]) => { setEntries(lb.entries); setStats(st); })
      .catch(() => {
        // API offline — fall back to mock leaderboard for demo mode
        setEntries(MOCK_LEADERBOARD_ENTRIES);
        setStats(MOCK_LEADERBOARD_STATS);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  if (error) return <PageError message={error} onRetry={load} />;
  if (loading) return <div className="flex items-center justify-center h-64"><Spinner /></div>;

  // Filtering
  const frameworks = [...new Set(entries.map(e => e.framework).filter(Boolean))] as string[];
  let filtered = frameworkFilter
    ? entries.filter(e => e.framework === frameworkFilter)
    : entries;

  if (period === 'today') {
    const today = new Date().toDateString();
    filtered = filtered.filter(e => new Date(e.timestamp).toDateString() === today);
  } else if (period === 'week') {
    // Rolling 7d window from "now" (intentionally wall-clock relative).
    // eslint-disable-next-line react-hooks/purity -- time-relative filter, not render-idempotent
    const weekAgo = Date.now() - 7 * 86400000;
    filtered = filtered.filter(e => new Date(e.timestamp).getTime() > weekAgo);
  }

  // Sorting
  const sorted = [...filtered].sort((a, b) => {
    let cmp = 0;
    if (sortBy === 'rank') cmp = a.rank - b.rank;
    else if (sortBy === 'score') cmp = b.score - a.score;
    else if (sortBy === 'timestamp') cmp = new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime();
    return sortAsc ? cmp : -cmp;
  });

  const handleSort = (field: SortField) => {
    if (sortBy === field) setSortAsc(!sortAsc);
    else { setSortBy(field); setSortAsc(true); }
  };

  const sortIcon = (field: SortField) => sortBy === field ? (sortAsc ? ' \u25B2' : ' \u25BC') : '';

  return (
    <div>
      <h1 className="text-3xl font-display text-stone-900 dark:text-white mb-1">Safety Leaderboard</h1>
      <p className="text-muted text-sm mb-6">Public ranking of agent safety scores</p>

      {/* Stats row */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-4">
            <p className="text-xs text-muted uppercase tracking-wider mb-1">Submissions</p>
            <p className="text-2xl font-bold text-stone-900 dark:text-white">{stats.total_submissions}</p>
          </div>
          <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-4">
            <p className="text-xs text-muted uppercase tracking-wider mb-1">Avg Score</p>
            <p className="text-2xl font-bold text-stone-900 dark:text-white">{stats.average_score.toFixed(1)}</p>
          </div>
          <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-4">
            <p className="text-xs text-muted uppercase tracking-wider mb-1">Public Entries</p>
            <p className="text-2xl font-bold text-stone-900 dark:text-white">{stats.public_entries}</p>
          </div>
          {stats.top_agent && (
            <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-4">
              <p className="text-xs text-muted uppercase tracking-wider mb-1">Top Agent</p>
              <p className="text-sm font-bold text-stone-900 dark:text-white truncate">{stats.top_agent.display_name}</p>
              <p className="text-xs text-muted">{stats.top_agent.score} pts ({stats.top_agent.grade})</p>
            </div>
          )}
        </div>
      )}

      {/* Score Distribution Histogram */}
      {entries.length > 0 && (
        <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5 mb-6">
          <p className="text-[10px] text-muted uppercase tracking-widest font-medium mb-3">Score Distribution</p>
          <div className="flex items-end gap-2 h-24">
            {(() => {
              const buckets = [
                { label: '0-20', min: 0, max: 20, color: 'bg-zinc-600' },
                { label: '21-40', min: 21, max: 40, color: 'bg-zinc-600' },
                { label: '41-60', min: 41, max: 60, color: 'bg-zinc-600' },
                { label: '61-80', min: 61, max: 80, color: 'bg-zinc-600' },
                { label: '81-100', min: 81, max: 100, color: 'bg-brand' },
              ];
              const counts = buckets.map(b => entries.filter(e => e.score >= b.min && e.score <= b.max).length);
              const maxCount = Math.max(...counts, 1);
              return buckets.map((b, i) => (
                <div key={b.label} className="flex-1 flex flex-col items-center gap-1">
                  <div className="w-full flex items-end justify-center" style={{ height: '80px' }}>
                    <div
                      className={`w-full max-w-[40px] rounded-t ${b.color} transition-all duration-300`}
                      style={{ height: `${(counts[i] / maxCount) * 100}%`, minHeight: counts[i] > 0 ? '4px' : '0' }}
                    />
                  </div>
                  <span className="text-[10px] text-muted font-mono">{b.label}</span>
                  <span className="text-[10px] text-muted">{counts[i]}</span>
                </div>
              ));
            })()}
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <div className="flex items-center gap-1 bg-surface-100 dark:bg-surface-800 rounded-lg p-0.5">
          {(['all', 'week', 'today'] as Period[]).map(p => (
            <button key={p} onClick={() => setPeriod(p)}
              className={`px-3 py-1 text-xs font-medium rounded-md transition-colors capitalize ${
                period === p ? 'bg-white dark:bg-surface-900 text-stone-900 dark:text-white shadow-sm' : 'text-muted hover:text-stone-900 dark:hover:text-white'
              }`}>
              {p === 'all' ? 'All Time' : p}
            </button>
          ))}
        </div>
        <div className="flex gap-1 flex-wrap">
          {['', 'LangGraph', 'OpenAI', 'CrewAI', 'MCP', 'Agents SDK', ...frameworks.filter(f => !['LangGraph', 'OpenAI', 'CrewAI', 'MCP', 'Agents SDK'].includes(f))].map(f => (
            <button key={f} onClick={() => setFrameworkFilter(f)}
              className={`px-2.5 py-1 text-xs rounded-md transition-colors ${frameworkFilter === f ? 'bg-surface-200 dark:bg-surface-800 text-stone-900 dark:text-white' : 'text-muted hover:text-stone-900 dark:hover:text-white'}`}>
              {f || 'All'}
            </button>
          ))}
        </div>
      </div>

      {/* Badge embed modal */}
      {showBadge && (
        <div className="mb-4 rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-4">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-semibold text-emerald-400">Embed Safety Badge</h3>
            <button onClick={() => setShowBadge(null)} className="text-xs text-muted hover:text-stone-900 dark:hover:text-white">Close</button>
          </div>
          <p className="text-xs text-muted mb-2">Add this to your README.md:</p>
          <div className="bg-surface-50 dark:bg-surface-900 rounded-lg px-3 py-2 font-mono text-xs text-stone-700 dark:text-zinc-300 select-all">
            {`![Sponsio Safety Score](${showBadge})`}
          </div>
          <div className="mt-2">
            <img src={showBadge} alt="Safety badge" className="h-5" />
          </div>
        </div>
      )}

      {/* Table */}
      {sorted.length === 0 ? (
        <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-8 text-center">
          <p className="text-muted text-sm mb-2">No entries yet.</p>
          <p className="text-zinc-600 dark:text-zinc-300 text-xs">Score an agent on the <a href="/scan" className="text-zinc-600 dark:text-zinc-300 hover:text-stone-900 dark:hover:text-zinc-100">Scan</a> page to appear here.</p>
        </div>
      ) : (
        <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-surface-200 dark:border-surface-800">
                <th onClick={() => handleSort('rank')} className="text-left px-4 py-3 text-xs text-muted uppercase tracking-wider font-medium cursor-pointer hover:text-stone-900 dark:hover:text-zinc-100">#{sortIcon('rank')}</th>
                <th className="text-left px-4 py-3 text-xs text-muted uppercase tracking-wider font-medium">Agent</th>
                <th onClick={() => handleSort('score')} className="text-left px-4 py-3 text-xs text-muted uppercase tracking-wider font-medium cursor-pointer hover:text-stone-900 dark:hover:text-zinc-100">Score{sortIcon('score')}</th>
                <th className="text-left px-4 py-3 text-xs text-muted uppercase tracking-wider font-medium">Grade</th>
                <th className="text-left px-4 py-3 text-xs text-muted uppercase tracking-wider font-medium">Framework</th>
                <th onClick={() => handleSort('timestamp')} className="text-left px-4 py-3 text-xs text-muted uppercase tracking-wider font-medium cursor-pointer hover:text-stone-900 dark:hover:text-zinc-100">Date{sortIcon('timestamp')}</th>
                <th className="text-left px-4 py-3 text-xs text-muted uppercase tracking-wider font-medium">Badge</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((e) => (
                <tr key={e.rank} className="border-b border-surface-100 dark:border-surface-800 last:border-0 hover:bg-surface-50 dark:hover:bg-surface-800">
                  <td className="px-4 py-3 text-muted font-mono">{e.rank}</td>
                  <td className="px-4 py-3">
                    <p className="font-medium text-stone-900 dark:text-white">{e.display_name}</p>
                    {e.description && <p className="text-xs text-muted truncate max-w-xs">{e.description}</p>}
                  </td>
                  <td className="px-4 py-3 font-mono font-bold text-stone-900 dark:text-white">{e.score}</td>
                  <td className="px-4 py-3">
                    <span className={`inline-block px-2 py-0.5 rounded text-xs font-bold ${gradeColors[e.grade] ?? 'text-muted bg-surface-100 dark:bg-surface-800'}`}>{e.grade}</span>
                  </td>
                  <td className="px-4 py-3 text-muted">{e.framework ?? '-'}</td>
                  <td className="px-4 py-3 text-muted text-xs">{new Date(e.timestamp).toLocaleDateString()}</td>
                  <td className="px-4 py-3">
                    <button onClick={() => setShowBadge(e.badge_url)}
                      className="text-[10px] text-zinc-600 dark:text-zinc-300 hover:text-stone-900 dark:hover:text-zinc-100 transition-colors font-medium">
                      Embed
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
