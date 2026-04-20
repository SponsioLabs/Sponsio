/**
 * Unified metric card shared across Overview / Insights.
 * Supports an optional sparkline below the value for trend-at-a-glance.
 */

import type { ReactNode } from 'react';
import Sparkline from './Sparkline';

interface MetricCardProps {
  label: string;
  value: number | string;
  icon?: ReactNode;
  accent?: string;
  sub?: string;
  trend?: number[];        // optional sparkline series
  trendColor?: string;     // Tailwind fill class
  deltaPct?: number;       // optional ±% vs prior period
}

function DeltaBadge({ pct }: { pct: number }) {
  if (!Number.isFinite(pct) || pct === 0) return null;
  const up = pct > 0;
  return (
    <span className={`text-[9px] font-mono font-bold ${up ? 'text-red-400' : 'text-emerald-400'}`}>
      {up ? '▲' : '▼'} {Math.abs(pct).toFixed(0)}%
    </span>
  );
}

export default function MetricCard({ label, value, icon, accent = 'border-l-brand', sub, trend, trendColor, deltaPct }: MetricCardProps) {
  return (
    <div className={`rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-4 border-l-[3px] ${accent}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium mb-1 flex items-center gap-1.5">
            {label}
            {deltaPct !== undefined && <DeltaBadge pct={deltaPct} />}
          </p>
          <p className="text-2xl font-bold font-mono text-stone-900 dark:text-white truncate">{value}</p>
          {sub && <p className="text-[10px] text-muted mt-0.5 truncate">{sub}</p>}
        </div>
        {icon && <div className="text-muted opacity-60 mt-0.5 shrink-0">{icon}</div>}
      </div>
      {trend && trend.length > 0 && (
        <div className="mt-2">
          <Sparkline values={trend} width={120} height={16} colorClass={trendColor ?? 'fill-brand/60'} />
        </div>
      )}
    </div>
  );
}
