/**
 * Four Sponsio-specific verdict cards on the Monitor page.
 *
 * These render the same data shapes documented in
 * `docs/observability.md` (the Sponsio Semantic Conventions doc) so
 * what the user sees in our dashboard is byte-for-byte the same data
 * any external OTLP consumer would render off the schema.
 *
 *   A. Today's blocks       — reverse-chronological list of denies
 *   B. Rule fire heatmap    — rule × time-bucket matrix, colored by frequency
 *   C. Sto judge spend      — per-pipeline call counts (cost rollup)
 *   D. Policy source-of-truth audit — group by policy paragraph reference
 *
 * Source: the `violations` array on `MonitorData` (already fetched
 * from `/monitor/log`). Aggregations happen client-side; nothing here
 * hits the network. When the user has the in-tree `OtlpHttpExporter`
 * pushing to a custom backend, the *same* attributes drive these four
 * cards on a hosted dashboard — that's the design payoff of the
 * shared schema.
 */

import { useMemo } from 'react';
import type { MonitorEvent } from '../../types';

interface Props {
  violations: MonitorEvent[];
}

// ---------------------------------------------------------------------------
// Shared
// ---------------------------------------------------------------------------

function formatTime(tsSec: number): string {
  return new Date(tsSec * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  });
}

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + '…';
}

// Uniform card frame — matches the existing MetricCard look so the
// monitor page reads as one consistent surface.
function Card({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-4 flex flex-col">
      <div className="mb-2">
        <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium">
          {title}
        </p>
        {subtitle && (
          <p className="text-[10px] text-muted mt-0.5">{subtitle}</p>
        )}
      </div>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// A. Today's blocks — denied turns, newest first
// ---------------------------------------------------------------------------

function TodaysBlocksCard({ violations }: Props) {
  const blocks = useMemo(() => {
    return violations
      .filter(
        v =>
          v.result_action === 'blocked' ||
          v.result_action === 'observed' ||
          v.result_action === 'escalated'
      )
      .sort((a, b) => (b.ts ?? 0) - (a.ts ?? 0))
      .slice(0, 12);
  }, [violations]);

  if (blocks.length === 0) {
    return (
      <Card
        title="Today's blocks"
        subtitle="Denials in the current window — none yet"
      >
        <p className="text-xs text-muted py-4 text-center">
          Nothing blocked. Either everything is policy-compliant or
          you're still in observe-mode warmup.
        </p>
      </Card>
    );
  }

  return (
    <Card
      title="Today's blocks"
      subtitle={`${blocks.length} most recent · sorted newest first`}
    >
      <div className="overflow-y-auto max-h-72 -mx-1">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-muted text-[10px] uppercase tracking-wider">
              <th className="text-left px-1 py-1 font-medium">Time</th>
              <th className="text-left px-1 py-1 font-medium">Tool</th>
              <th className="text-left px-1 py-1 font-medium">Rule</th>
              <th className="text-left px-1 py-1 font-medium">Action</th>
            </tr>
          </thead>
          <tbody>
            {blocks.map((v, i) => (
              <tr
                key={`${v.ts ?? 0}-${i}`}
                className="border-t border-surface-200/40 dark:border-surface-800/40"
              >
                <td className="px-1 py-1.5 font-mono text-muted">
                  {v.ts ? formatTime(v.ts) : '—'}
                </td>
                <td className="px-1 py-1.5 font-mono text-stone-700 dark:text-stone-200">
                  {truncate(v.action, 14)}
                </td>
                <td
                  className="px-1 py-1.5 text-stone-600 dark:text-stone-300"
                  title={v.constraint_name}
                >
                  {truncate(v.constraint_name, 38)}
                </td>
                <td className="px-1 py-1.5">
                  <ActionBadge action={v.result_action} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function ActionBadge({ action }: { action: string }) {
  const map: Record<string, string> = {
    blocked: 'bg-red-500/10 text-red-400',
    escalated: 'bg-amber-500/10 text-amber-400',
    observed: 'bg-blue-500/10 text-blue-400',
    retrying: 'bg-purple-500/10 text-purple-400',
    redirected: 'bg-cyan-500/10 text-cyan-400',
  };
  const cls = map[action] ?? 'bg-surface-200/40 text-stone-500';
  return (
    <span className={`px-1.5 py-0.5 rounded text-[9px] font-mono ${cls}`}>
      {action}
    </span>
  );
}

// ---------------------------------------------------------------------------
// B. Rule fire heatmap — rule × time-bucket
// ---------------------------------------------------------------------------

const HEATMAP_BUCKETS = 12;

function RuleFireHeatmapCard({ violations }: Props) {
  const matrix = useMemo(() => {
    const blocked = violations.filter(v => v.result_action !== 'pass');
    if (blocked.length === 0) {
      return { rules: [], cells: [], maxCount: 0 };
    }
    // Snapshot ``now`` at the moment the data changed. ``Date.now()``
    // inside useMemo trips ``react-hooks/purity`` because the memo's
    // value depends on the wall clock — but here that's the intent:
    // re-bucket whenever ``violations`` updates, treating that update
    // as the freshest "now". A subscription to live time would
    // re-render this card every second for no UX gain.
    // eslint-disable-next-line react-hooks/purity
    const now = Date.now() / 1000;
    const windowSec = 24 * 3600;
    const bucketSec = windowSec / HEATMAP_BUCKETS;

    const ruleAgg = new Map<string, number[]>();
    for (const v of blocked) {
      const ts = v.ts ?? now;
      const age = now - ts;
      if (age < 0 || age > windowSec) continue;
      const idx = Math.min(HEATMAP_BUCKETS - 1, Math.floor(age / bucketSec));
      // Reverse so 0 = oldest, HEATMAP_BUCKETS-1 = newest, i.e. left → right is past → present
      const col = HEATMAP_BUCKETS - 1 - idx;
      const row = ruleAgg.get(v.constraint_name) ?? new Array(HEATMAP_BUCKETS).fill(0);
      row[col] += 1;
      ruleAgg.set(v.constraint_name, row);
    }

    // Sort rules by total fires (desc), keep top 6 rows so the heatmap fits.
    const sortedRules = Array.from(ruleAgg.entries())
      .map(([rule, counts]) => ({
        rule,
        counts,
        total: counts.reduce((a, b) => a + b, 0),
      }))
      .sort((a, b) => b.total - a.total)
      .slice(0, 6);

    const maxCount = Math.max(1, ...sortedRules.flatMap(r => r.counts));

    return {
      rules: sortedRules.map(r => r.rule),
      cells: sortedRules.map(r => r.counts),
      maxCount,
    };
  }, [violations]);

  if (matrix.rules.length === 0) {
    return (
      <Card
        title="Rule fire heatmap"
        subtitle="Per-rule fire frequency over the last 24h"
      >
        <p className="text-xs text-muted py-4 text-center">
          No rule has fired in the window. Either nothing's tripping
          contracts (good) or you're brand new (also good — observe a
          while to populate this view).
        </p>
      </Card>
    );
  }

  return (
    <Card
      title="Rule fire heatmap"
      subtitle="Per-rule fire frequency · last 24h, 2h buckets"
    >
      <div className="space-y-1 mt-1">
        {matrix.rules.map((rule, i) => (
          <div key={rule} className="flex items-center gap-2">
            <span
              className="text-[10px] text-stone-700 dark:text-stone-300 w-44 truncate shrink-0"
              title={rule}
            >
              {truncate(rule, 32)}
            </span>
            <div className="flex gap-px flex-1">
              {matrix.cells[i].map((count, j) => (
                <HeatCell
                  key={j}
                  count={count}
                  maxCount={matrix.maxCount}
                />
              ))}
            </div>
            <span className="text-[10px] font-mono text-muted w-8 text-right shrink-0">
              {matrix.cells[i].reduce((a, b) => a + b, 0)}
            </span>
          </div>
        ))}
      </div>
      <p className="text-[9px] text-muted mt-2 text-center">
        ← 24h ago · now →
      </p>
    </Card>
  );
}

function HeatCell({ count, maxCount }: { count: number; maxCount: number }) {
  const intensity = count === 0 ? 0 : Math.max(0.15, count / maxCount);
  const bg =
    count === 0
      ? 'bg-surface-200/30 dark:bg-surface-800/40'
      : ''; // override via inline style for opacity-graded red
  return (
    <div
      className={`flex-1 h-4 rounded-[2px] ${bg}`}
      style={
        count > 0
          ? { backgroundColor: `rgba(239, 68, 68, ${intensity})` }
          : undefined
      }
      title={count > 0 ? `${count} fires` : 'no fires'}
    />
  );
}

// ---------------------------------------------------------------------------
// C. Sto judge spend — per-pipeline call counts
// ---------------------------------------------------------------------------

function StoJudgeSpendCard({ violations }: Props) {
  const rollup = useMemo(() => {
    // Group by `pipeline` (det / sto) + count by constraint_name. The
    // session log doesn't carry per-judge cost yet (that's a span-tree
    // attribute via `sponsio.judge.model` / `sponsio.judge.latency_ms`),
    // so we surface call count and sto-share for now. Cost-per-call can
    // be wired in once the user-supplied price table lands.
    const byKind = { det: 0, sto: 0 };
    const stoByConstraint = new Map<string, number>();
    for (const v of violations) {
      const pipeline = v.pipeline === 'hard' ? 'det' : v.pipeline;
      if (pipeline === 'det') byKind.det += 1;
      if (pipeline === 'sto' || pipeline === 'soft') {
        byKind.sto += 1;
        stoByConstraint.set(
          v.constraint_name,
          (stoByConstraint.get(v.constraint_name) ?? 0) + 1
        );
      }
    }
    const stoTop = Array.from(stoByConstraint.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 4);
    return { byKind, stoTop };
  }, [violations]);

  return (
    <Card
      title="Sto judge spend"
      subtitle="LLM judge invocations · cost-per-call requires price table"
    >
      <div className="grid grid-cols-2 gap-2 mb-3">
        <div className="rounded-lg bg-surface-100 dark:bg-surface-800/60 p-2">
          <p className="text-[9px] text-muted uppercase tracking-wider">
            Det checks
          </p>
          <p className="text-xl font-bold font-mono text-stone-900 dark:text-white">
            {rollup.byKind.det.toLocaleString()}
          </p>
          <p className="text-[9px] text-muted">no LLM calls</p>
        </div>
        <div className="rounded-lg bg-surface-100 dark:bg-surface-800/60 p-2">
          <p className="text-[9px] text-muted uppercase tracking-wider">
            Sto checks
          </p>
          <p className="text-xl font-bold font-mono text-amber-400">
            {rollup.byKind.sto.toLocaleString()}
          </p>
          <p className="text-[9px] text-muted">judge per call</p>
        </div>
      </div>

      {rollup.stoTop.length === 0 ? (
        <p className="text-[10px] text-muted text-center py-2">
          No stochastic checks fired in the window.
        </p>
      ) : (
        <div className="space-y-1">
          <p className="text-[9px] text-muted uppercase tracking-wider mb-1">
            Top sto atoms
          </p>
          {rollup.stoTop.map(([name, n]) => (
            <div
              key={name}
              className="flex items-center justify-between text-[10px]"
            >
              <span
                className="truncate text-stone-700 dark:text-stone-300 font-mono"
                title={name}
              >
                {truncate(name, 30)}
              </span>
              <span className="font-mono text-amber-400 ml-2 shrink-0">
                {n.toLocaleString()}
              </span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// D. Policy source-of-truth audit — by paragraph ref
// ---------------------------------------------------------------------------

function PolicySourceAuditCard({ violations }: Props) {
  // policy_ref is a forward-looking attribute (sponsio.violation.policy_ref
  // in the OTLP schema) that the agent populates when it derives a rule
  // from a specific user-policy paragraph. The session-log MonitorEvent
  // doesn't expose it directly today; we infer ref tags from
  // constraint_name patterns like "...(policy.md ¶1)..." until the
  // backend fills the attribute through.
  const audit = useMemo(() => {
    const refRegex = /\b((?:policy|handbook|policy\.md|playbook)[^)]*¶\s*\d+)/i;
    const byRef = new Map<string, { count: number; rules: Set<string> }>();
    for (const v of violations) {
      if (v.result_action === 'pass') continue;
      const m = v.constraint_name.match(refRegex);
      const ref = m ? m[1].trim() : 'untagged rules';
      const entry = byRef.get(ref) ?? { count: 0, rules: new Set<string>() };
      entry.count += 1;
      entry.rules.add(v.constraint_name);
      byRef.set(ref, entry);
    }
    return Array.from(byRef.entries())
      .map(([ref, data]) => ({
        ref,
        count: data.count,
        rules: data.rules.size,
      }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 6);
  }, [violations]);

  if (audit.length === 0) {
    return (
      <Card
        title="Policy source-of-truth audit"
        subtitle="Rule fires grouped by source-document paragraph"
      >
        <p className="text-xs text-muted py-4 text-center">
          No policy-tagged rules have fired. Tag rule descriptions with
          ``(policy.md ¶N)`` to enable this view.
        </p>
      </Card>
    );
  }

  return (
    <Card
      title="Policy source-of-truth audit"
      subtitle="Compliance trail · which paragraph drove which fires"
    >
      <div className="space-y-1.5 mt-1">
        {audit.map(({ ref, count, rules }) => (
          <div
            key={ref}
            className="flex items-center justify-between text-[10px] gap-2"
          >
            <span
              className="truncate text-stone-700 dark:text-stone-300 font-mono"
              title={ref}
            >
              {truncate(ref, 28)}
            </span>
            <div className="flex items-center gap-2 shrink-0">
              <span className="text-muted">{rules} rule{rules > 1 ? 's' : ''}</span>
              <span className="font-mono text-red-400 w-10 text-right">
                {count}
              </span>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Container — 2×2 grid of the four cards
// ---------------------------------------------------------------------------

export default function SponsioVerdictCards({ violations }: Props) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-[11px] uppercase tracking-widest font-medium text-zinc-700 dark:text-zinc-300">
          Sponsio verdicts
        </h3>
        <span className="text-[9px] text-muted">
          schema {SCHEMA_VERSION} · derived from session log
        </span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <TodaysBlocksCard violations={violations} />
        <RuleFireHeatmapCard violations={violations} />
        <StoJudgeSpendCard violations={violations} />
        <PolicySourceAuditCard violations={violations} />
      </div>
    </div>
  );
}

// Mirrors `sponsio.tracer.semconv.SCHEMA_VERSION`. Bump in lockstep
// when the backend schema bumps so users see when their dashboard is
// ahead/behind the runtime.
const SCHEMA_VERSION = '1.0.0';
