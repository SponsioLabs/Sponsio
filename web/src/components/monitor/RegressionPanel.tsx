/**
 * Regression detector: flags agents whose recent pass rate is significantly
 * below their baseline. Computed client-side via two-sample proportion z-test
 * (see utils/aggregations.ts detectRegressions).
 */

import { useMemo } from 'react';
import type { MonitorEvent } from '../../types';
import { detectRegressions } from '../../utils/aggregations';

interface Props { events: MonitorEvent[]; }

export default function RegressionPanel({ events }: Props) {
  const findings = useMemo(() => detectRegressions(events), [events]);

  return (
    <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
      <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium mb-3">
        Regressions
        {findings.length > 0 && <span className="ml-2 text-red-400 font-mono">({findings.length})</span>}
      </p>

      {findings.length === 0 ? (
        <p className="text-sm text-muted py-4 text-center">
          No significant pass-rate drops detected vs baseline.
        </p>
      ) : (
        <div className="space-y-2">
          {findings.map(f => {
            const delta = f.currentPassRate - f.baselinePassRate;
            return (
              <div key={f.agentId} className="rounded-lg border border-red-500/20 bg-red-500/5 px-3 py-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-mono text-stone-900 dark:text-zinc-100">{f.agentId}</span>
                  <span className="text-xs font-mono font-bold text-red-400">
                    {(f.currentPassRate * 100).toFixed(1)}%
                    <span className="text-muted font-normal mx-1">vs</span>
                    {(f.baselinePassRate * 100).toFixed(1)}%
                  </span>
                </div>
                <p className="text-[11px] text-muted mt-1">
                  Pass rate dropped by <span className="text-red-400 font-mono font-medium">{(Math.abs(delta) * 100).toFixed(1)}pp</span>
                  {' '}in the recent window{' '}
                  (<span className="font-mono">z = {f.zScore.toFixed(2)}</span>, n = {f.sampleSize})
                </p>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
