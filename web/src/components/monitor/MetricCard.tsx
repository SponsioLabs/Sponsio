/**
 * Lightweight metric tile for the OSS dashboard.
 *
 * The cloud variant adds sparklines, trend deltas, etc. — the OSS
 * variant stays minimal because we don't aggregate across runs.
 */

import type { ReactNode } from 'react';

interface MetricCardProps {
  label: string;
  value: number | string;
  icon?: ReactNode;
  accent?: string;
  sub?: string;
}

export default function MetricCard({
  label,
  value,
  icon,
  accent = 'border-l-brand',
  sub,
}: MetricCardProps) {
  return (
    <div
      className={`rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-4 border-l-[3px] ${accent}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium mb-1">
            {label}
          </p>
          <p className="text-2xl font-bold font-mono text-stone-900 dark:text-white truncate">
            {value}
          </p>
          {sub && <p className="text-[10px] text-muted mt-0.5 truncate">{sub}</p>}
        </div>
        {icon && (
          <div className="text-muted opacity-60 mt-0.5 shrink-0">{icon}</div>
        )}
      </div>
    </div>
  );
}
