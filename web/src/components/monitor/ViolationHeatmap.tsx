/**
 * Agent × hour-of-day violation heatmap. Surfaces temporal patterns
 * ("Friday afternoons are bad", "3am spike in refund denials", etc.).
 */

import { useMemo } from 'react';
import type { MonitorEvent } from '../../types';
import { buildHeatmap } from '../../utils/aggregations';

interface Props { events: MonitorEvent[]; }

function intensityClass(count: number, max: number): string {
  if (count === 0) return 'bg-surface-100 dark:bg-surface-800';
  const r = count / Math.max(1, max);
  if (r < 0.2) return 'bg-amber-500/20';
  if (r < 0.4) return 'bg-amber-500/40';
  if (r < 0.6) return 'bg-amber-500/60';
  if (r < 0.8) return 'bg-red-500/60';
  return 'bg-red-500/80';
}

export default function ViolationHeatmap({ events }: Props) {
  const { agents, max, cellByKey } = useMemo(() => {
    const cells = buildHeatmap(events);
    const agentSet = new Set(cells.map(c => c.agentId));
    const m = cells.reduce((a, c) => Math.max(a, c.count), 0);
    const map = new Map<string, number>();
    cells.forEach(c => map.set(`${c.agentId}:${c.hour}`, c.count));
    return { agents: Array.from(agentSet).sort(), max: m, cellByKey: map };
  }, [events]);

  if (agents.length === 0 || max === 0) {
    return (
      <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
        <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium mb-3">
          Violation Heatmap
        </p>
        <p className="text-sm text-muted py-4 text-center">Not enough data to show a heatmap.</p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
      <div className="flex items-center justify-between mb-3">
        <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium">
          Violation Heatmap <span className="text-muted normal-case font-normal ml-2">agent × hour of day</span>
        </p>
        <div className="flex items-center gap-1.5 text-[9px] text-muted">
          <span>less</span>
          <span className="w-3 h-3 rounded-sm bg-surface-100 dark:bg-surface-800" />
          <span className="w-3 h-3 rounded-sm bg-amber-500/40" />
          <span className="w-3 h-3 rounded-sm bg-amber-500/60" />
          <span className="w-3 h-3 rounded-sm bg-red-500/60" />
          <span className="w-3 h-3 rounded-sm bg-red-500/80" />
          <span>more</span>
        </div>
      </div>

      <div className="overflow-x-auto">
        <div className="inline-block min-w-full">
          {/* Hour labels */}
          <div className="flex items-center mb-1">
            <div className="w-28 shrink-0" />
            <div className="flex gap-0.5">
              {Array.from({ length: 24 }, (_, h) => (
                <div key={h} className="w-4 text-[8px] text-muted font-mono text-center">
                  {h % 3 === 0 ? h : ''}
                </div>
              ))}
            </div>
          </div>
          {/* Rows */}
          {agents.map(a => (
            <div key={a} className="flex items-center mb-0.5">
              <div className="w-28 shrink-0 text-[11px] font-mono text-stone-700 dark:text-zinc-300 truncate pr-2" title={a}>
                {a}
              </div>
              <div className="flex gap-0.5">
                {Array.from({ length: 24 }, (_, h) => {
                  const count = cellByKey.get(`${a}:${h}`) ?? 0;
                  return (
                    <div
                      key={h}
                      className={`w-4 h-4 rounded-sm ${intensityClass(count, max)} transition-colors cursor-default`}
                      title={`${a} · ${h}:00 — ${count} violation${count !== 1 ? 's' : ''}`}
                    />
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
