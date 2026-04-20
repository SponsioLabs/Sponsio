/**
 * Grouped violation view. Replaces the flat "one row per violation" feed with
 * collapsible groups keyed by (constraint, agent, pipeline). Each group shows:
 *   - severity chip
 *   - count + trend sparkline
 *   - data-lineage arrow(s)
 *   - quick actions: Suppress / Add to SLO / View traces
 */

import { useMemo, useState } from 'react';
import type { MonitorEvent } from '../../types';
import { bucketCounts, groupViolations, loadSuppressions, saveSuppressions } from '../../utils/aggregations';
import DataLineage from './DataLineage';
import Sparkline from './Sparkline';

interface Props {
  events: MonitorEvent[];
  onAddSlo?: (constraintName: string, agentId: string) => void;
  onViewTraces?: (constraintName: string, agentId: string) => void;
  query?: string;
}

const SEV_STYLES: Record<'low' | 'medium' | 'high' | 'critical', { dot: string; text: string }> = {
  low:      { dot: 'bg-stone-400',  text: 'text-stone-500' },
  medium:   { dot: 'bg-amber-500',  text: 'text-amber-500' },
  high:     { dot: 'bg-orange-500', text: 'text-orange-500' },
  critical: { dot: 'bg-red-500',    text: 'text-red-500' },
};

function formatRelative(ts?: number): string {
  if (ts === undefined) return '—';
  const sec = Date.now() / 1000 - ts;
  if (sec < 60) return `${Math.round(sec)}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
}

export default function ViolationGroups({ events, onAddSlo, onViewTraces, query = '' }: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [suppressedKeys, setSuppressedKeys] = useState<Set<string>>(() => {
    const now = Date.now();
    return new Set(loadSuppressions().filter(s => s.until > now).map(s => `${s.constraintName}::${s.agentId ?? ''}`));
  });
  // By default, suppressed groups are hidden (not just dimmed). Users opt-in
  // to seeing them back via the "Show N suppressed" toggle at the top.
  const [showSuppressed, setShowSuppressed] = useState(false);

  const allGroups = useMemo(() => {
    const base = groupViolations(events);
    if (!query.trim()) return base;
    const q = query.toLowerCase();
    return base.filter(g =>
      g.constraintName.toLowerCase().includes(q) ||
      g.agentId.toLowerCase().includes(q) ||
      g.pipeline.toLowerCase().includes(q),
    );
  }, [events, query]);

  const suppressedCount = useMemo(
    () => allGroups.filter(g => suppressedKeys.has(`${g.constraintName}::${g.agentId}`)).length,
    [allGroups, suppressedKeys],
  );

  const groups = useMemo(() => {
    if (showSuppressed) return allGroups;
    return allGroups.filter(g => !suppressedKeys.has(`${g.constraintName}::${g.agentId}`));
  }, [allGroups, suppressedKeys, showSuppressed]);

  const toggleExpand = (key: string) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const suppress = (constraintName: string, agentId: string, minutes: number) => {
    const until = Date.now() + minutes * 60_000;
    const current = loadSuppressions();
    const key = `${constraintName}::${agentId}`;
    const next = [
      ...current.filter(s => `${s.constraintName}::${s.agentId ?? ''}` !== key),
      { id: key, constraintName, agentId, until, createdAt: Date.now() },
    ];
    saveSuppressions(next);
    setSuppressedKeys(new Set([...suppressedKeys, key]));
  };

  const unsuppress = (constraintName: string, agentId: string) => {
    const key = `${constraintName}::${agentId}`;
    const next = loadSuppressions().filter(s => `${s.constraintName}::${s.agentId ?? ''}` !== key);
    saveSuppressions(next);
    const newSet = new Set(suppressedKeys);
    newSet.delete(key);
    setSuppressedKeys(newSet);
  };

  if (groups.length === 0) {
    return (
      <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-8 text-center">
        <div className="text-3xl mb-2 opacity-30">✓</div>
        <p className="text-sm text-muted">
          {query.trim()
            ? 'No violation groups match your search.'
            : suppressedCount > 0
              ? `All active violations suppressed. ${suppressedCount} hidden.`
              : 'No violations grouped. System is healthy.'}
        </p>
        {suppressedCount > 0 && !showSuppressed && (
          <button
            onClick={() => setShowSuppressed(true)}
            className="mt-3 text-[11px] text-brand hover:text-brand/80 transition-colors"
          >
            Show {suppressedCount} suppressed group{suppressedCount !== 1 ? 's' : ''}
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {suppressedCount > 0 && (
        <div className="flex items-center justify-between px-3 py-1.5 rounded-lg bg-surface-50 dark:bg-surface-800/50 border border-surface-200 dark:border-surface-800">
          <span className="text-[11px] text-muted">
            {suppressedCount} group{suppressedCount !== 1 ? 's' : ''} hidden by active suppressions
          </span>
          <button
            onClick={() => setShowSuppressed(s => !s)}
            className="text-[11px] text-brand hover:text-brand/80 font-medium transition-colors"
          >
            {showSuppressed ? 'Hide suppressed' : 'Show suppressed'}
          </button>
        </div>
      )}
      {groups.map(group => {
        const sev = SEV_STYLES[group.severity];
        const isExpanded = expanded.has(group.key);
        const suppressionKey = `${group.constraintName}::${group.agentId}`;
        const isSuppressed = suppressedKeys.has(suppressionKey);
        const spark = bucketCounts(group.events, 24, 60 * 60 * 1000);
        const emphasizeLineage = group.severity === 'critical' || group.severity === 'high';

        return (
          <div
            key={group.key}
            className={`rounded-xl border bg-white dark:bg-surface-900 overflow-hidden transition-opacity ${
              isSuppressed ? 'opacity-50 border-surface-200 dark:border-surface-800' :
              group.severity === 'critical' ? 'border-red-500/30' :
              group.severity === 'high' ? 'border-orange-500/30' :
              'border-surface-200 dark:border-surface-800'
            }`}
          >
            {/* Header row */}
            <button
              onClick={() => toggleExpand(group.key)}
              className="w-full text-left px-4 py-3 hover:bg-surface-50 dark:hover:bg-surface-800/40"
            >
              <div className="flex items-center gap-3">
                <span className={`w-2 h-2 rounded-full shrink-0 ${sev.dot}`} />
                <span className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded shrink-0 ${
                  group.pipeline === 'hard' ? 'text-red-400 bg-red-500/10' : 'text-violet-400 bg-violet-500/10'
                }`}>{group.pipeline === 'hard' ? 'DET' : 'STO'}</span>

                <span className="text-sm font-mono text-stone-900 dark:text-zinc-100 flex-1 truncate" title={group.constraintName}>
                  {group.constraintName}
                </span>

                <span className="text-[11px] text-muted shrink-0">{group.agentId}</span>

                <Sparkline values={spark} width={64} height={16} colorClass="fill-red-400/60" />

                <span className="text-[11px] font-mono shrink-0 w-20 text-right">
                  <span className={sev.text + ' font-bold'}>{group.count}×</span>
                  <span className="text-muted"> last 1h</span>
                </span>

                <span className="text-zinc-500 text-xs shrink-0">{isExpanded ? '▾' : '▸'}</span>
              </div>

              {/* Flow chips if any */}
              {group.flows.length > 0 && (
                <div className="mt-2 ml-6 flex flex-wrap gap-x-3 gap-y-1">
                  {group.flows.slice(0, 3).map((f, i) => (
                    <DataLineage key={i} source={f.source} target={f.target} emphasize={emphasizeLineage} />
                  ))}
                </div>
              )}
            </button>

            {/* Expanded body */}
            {isExpanded && (
              <div className="px-4 pb-3 border-t border-surface-100 dark:border-surface-800 pt-3 bg-surface-50 dark:bg-surface-800/30 space-y-3">
                {/* Summary stats */}
                <div className="grid grid-cols-4 gap-2 text-[11px]">
                  <div>
                    <span className="text-muted uppercase tracking-wider text-[9px]">Blocked</span>
                    <p className="font-mono font-bold text-red-400">{group.blockedCount}</p>
                  </div>
                  <div>
                    <span className="text-muted uppercase tracking-wider text-[9px]">Retried</span>
                    <p className="font-mono font-bold text-amber-400">{group.retryingCount}</p>
                  </div>
                  <div>
                    <span className="text-muted uppercase tracking-wider text-[9px]">Escalated</span>
                    <p className="font-mono font-bold text-violet-400">{group.escalatedCount}</p>
                  </div>
                  <div>
                    <span className="text-muted uppercase tracking-wider text-[9px]">Last seen</span>
                    <p className="font-mono text-stone-900 dark:text-zinc-100">{formatRelative(group.lastSeen)}</p>
                  </div>
                </div>

                {/* Individual events */}
                <div className="rounded-lg bg-white dark:bg-surface-900 border border-surface-200 dark:border-surface-800 divide-y divide-surface-100 dark:divide-surface-800 max-h-48 overflow-y-auto">
                  {group.events.slice(0, 8).map((ev, i) => (
                    <div key={i} className="px-3 py-2 text-[11px] flex items-center gap-2">
                      <span className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded shrink-0 ${
                        ev.result_action === 'pass' ? 'text-emerald-400 bg-emerald-500/10' :
                        ev.result_action === 'blocked' || ev.result_action === 'block' ? 'text-red-400 bg-red-500/10' :
                        ev.result_action === 'retrying' ? 'text-amber-400 bg-amber-500/10' :
                        'text-violet-400 bg-violet-500/10'
                      }`}>{ev.result_action}</span>
                      <span className="text-stone-500 dark:text-zinc-400 flex-1 truncate" title={ev.result_message}>
                        {ev.result_message}
                      </span>
                      <span className="text-muted font-mono shrink-0">{formatRelative(ev.ts)}</span>
                    </div>
                  ))}
                  {group.events.length > 8 && (
                    <div className="px-3 py-1.5 text-[10px] text-muted text-center">
                      …and {group.events.length - 8} more events
                    </div>
                  )}
                </div>

                {/* Actions */}
                <div className="flex items-center gap-2 flex-wrap">
                  {isSuppressed ? (
                    <button
                      onClick={(e) => { e.stopPropagation(); unsuppress(group.constraintName, group.agentId); }}
                      className="text-[11px] px-2.5 py-1 rounded-lg bg-amber-500/10 text-amber-500 hover:bg-amber-500/20 transition-colors"
                    >
                      Un-suppress
                    </button>
                  ) : (
                    <>
                      <button
                        onClick={(e) => { e.stopPropagation(); suppress(group.constraintName, group.agentId, 30); }}
                        className="text-[11px] px-2.5 py-1 rounded-lg bg-surface-100 dark:bg-surface-800 text-muted hover:bg-surface-200 dark:hover:bg-surface-700 transition-colors"
                      >
                        Suppress 30m
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); suppress(group.constraintName, group.agentId, 60 * 24); }}
                        className="text-[11px] px-2.5 py-1 rounded-lg bg-surface-100 dark:bg-surface-800 text-muted hover:bg-surface-200 dark:hover:bg-surface-700 transition-colors"
                      >
                        Suppress 24h
                      </button>
                    </>
                  )}
                  {onAddSlo && (
                    <button
                      onClick={(e) => { e.stopPropagation(); onAddSlo(group.constraintName, group.agentId); }}
                      className="text-[11px] px-2.5 py-1 rounded-lg bg-brand/10 text-brand hover:bg-brand/20 transition-colors"
                    >
                      Promote to SLO
                    </button>
                  )}
                  {onViewTraces && (
                    <button
                      onClick={(e) => { e.stopPropagation(); onViewTraces(group.constraintName, group.agentId); }}
                      className="text-[11px] px-2.5 py-1 rounded-lg text-stone-500 dark:text-zinc-300 hover:text-stone-900 dark:hover:text-white transition-colors ml-auto"
                    >
                      View traces →
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
