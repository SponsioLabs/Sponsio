/**
 * LangSmith-style gantt waterfall for trace spans.
 *
 * Design notes (per design review with SponsioLabs):
 *   - Single-color neutral bar (grey), 8px status-color left border.
 *     Colors are reserved for the Sponsio signal (violated / blocked / retry).
 *   - Time axis at top. Each row is one span; nested spans indent to show tree.
 *   - Hover shows duration / tokens / cost / violations.
 *   - Click emits onSelectSpan(span) so downstream panels can sync.
 *
 * Expects a flat list of top-level spans OR a single root. The component
 * flattens the tree with indentation metadata; it does NOT re-use SpanTree
 * (different visual metaphor — tree view vs time axis).
 */

import { useMemo, useState } from 'react';
import type { SpanNode } from '../../types';
import { extractLlmMetrics, formatCost, formatTokens } from '../../utils/llmMetrics';

interface WaterfallProps {
  spans: SpanNode[];
  onSelectSpan?: (span: SpanNode) => void;
  selectedSpan?: SpanNode | null;
  maxRows?: number;
}

interface FlatRow {
  span: SpanNode;
  depth: number;
  path: number[];        // indices into the tree — unique per row
  isLeaf: boolean;
}

function flattenTree(spans: SpanNode[]): FlatRow[] {
  const out: FlatRow[] = [];
  const walk = (list: SpanNode[], depth: number, path: number[]) => {
    list.forEach((s, i) => {
      const myPath = [...path, i];
      const children = s.children ?? [];
      out.push({ span: s, depth, path: myPath, isLeaf: children.length === 0 });
      if (children.length > 0) walk(children, depth + 1, myPath);
    });
  };
  walk(spans, 0, []);
  return out;
}

/**
 * One entry in the rendered list — either a single row or a folded
 * run of ≥3 consecutive identical rows (e.g. ``poll_status × 47``).
 * A run is *foldable* only when every span is clean (no block, no
 * violation, no error) — we never want to hide the interesting one.
 */
type RowGroup =
  | { kind: 'single'; row: FlatRow }
  | { kind: 'fold'; key: string; rows: FlatRow[] };

function isFoldable(r: FlatRow): boolean {
  return !r.span.blocked && r.span.status !== 'violated' && r.span.status !== 'error' && !hasChildViolation(r.span);
}

function groupRows(rows: FlatRow[]): RowGroup[] {
  const out: RowGroup[] = [];
  let i = 0;
  while (i < rows.length) {
    const head = rows[i];
    const headLabel = spanLabel(head.span);
    let j = i + 1;
    while (
      j < rows.length &&
      isFoldable(head) &&
      isFoldable(rows[j]) &&
      rows[j].depth === head.depth &&
      spanLabel(rows[j].span) === headLabel
    ) {
      j++;
    }
    const runLen = j - i;
    if (runLen >= 3) {
      // Fold key = first row's path + count; stable across rerenders
      // unless the underlying group itself changes.
      out.push({
        kind: 'fold',
        key: `fold:${head.path.join('.')}:${runLen}`,
        rows: rows.slice(i, j),
      });
    } else {
      for (let k = i; k < j; k++) out.push({ kind: 'single', row: rows[k] });
    }
    i = j;
  }
  return out;
}

function hasChildViolation(span: SpanNode): boolean {
  if (span.blocked) return true;
  if (span.status === 'violated') return true;
  for (const c of span.children ?? []) {
    if (c.status === 'violated') return true;
  }
  return false;
}

function statusBorderClass(span: SpanNode): string {
  if (span.blocked) return 'border-l-red-500';
  if (span.status === 'error') return 'border-l-orange-500';
  if (hasChildViolation(span)) return 'border-l-red-500';
  if (span.status === 'violated') return 'border-l-red-500';
  return 'border-l-emerald-500/60';
}

function rowBgClass(span: SpanNode, selected: boolean): string {
  if (selected) return 'bg-brand/5 dark:bg-brand/10';
  if (span.blocked || hasChildViolation(span)) return 'hover:bg-red-500/5';
  if (span.status === 'error') return 'hover:bg-orange-500/5';
  return 'hover:bg-surface-50 dark:hover:bg-surface-800/40';
}

function spanLabel(s: SpanNode): string {
  if (s.action) return s.action;
  if (s.contract_name) return s.contract_name;
  if (s.formula_desc) return s.formula_desc;
  if (s.constraint_name) return s.constraint_name;
  if (s.strategy) return `enforce: ${s.strategy}`;
  return s.span_type.replace('sponsio.', '');
}

function spanTypeBadge(s: SpanNode): { label: string; cls: string } | null {
  const t = s.span_type.replace('sponsio.', '');
  switch (t) {
    case 'agent_turn':      return { label: 'turn',     cls: 'text-sky-500 bg-sky-500/10' };
    case 'contract_check':  return { label: 'contract', cls: 'text-violet-500 bg-violet-500/10' };
    case 'precondition':    return { label: 'pre',      cls: 'text-stone-500 bg-stone-500/10' };
    case 'guarantee':       return { label: 'guar',     cls: 'text-stone-500 bg-stone-500/10' };
    case 'violation':       return { label: 'violation', cls: 'text-red-500 bg-red-500/10' };
    case 'enforcement':     return { label: s.result_action ?? 'enforce', cls: 'text-amber-500 bg-amber-500/10' };
    case 'soft_eval':       return { label: 'soft',     cls: 'text-violet-500 bg-violet-500/10' };
    case 'soft_check':      return { label: 'soft',     cls: 'text-violet-500 bg-violet-500/10' };
    default:                return { label: t,          cls: 'text-stone-400 bg-stone-500/10' };
  }
}

function fmtDuration(ms: number | null | undefined): string {
  if (ms == null) return '—';
  // Sub-ms: render in µs so short internal spans (contract_check,
  // guarantee) don't all flatten to "<1ms".
  if (ms < 1) return `${Math.max(1, Math.round(ms * 1000))}µs`;
  // Low-ms: keep one decimal so 1.2ms / 3.7ms are distinguishable.
  if (ms < 10) return `${ms.toFixed(1)}ms`;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return s === 0 ? `${m}m` : `${m}m${s}s`;
}

export default function TraceWaterfall({ spans, onSelectSpan, selectedSpan, maxRows = 120 }: WaterfallProps) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  // Folded cycle runs (``poll_status × 47``). Keyed by ``RowGroup.key``
  // so the expand state survives reorders without unfolding
  // everything on each render.
  const [unfolded, setUnfolded] = useState<Set<string>>(new Set());

  const { rows, globalStart, globalEnd, hiddenCount } = useMemo(() => {
    if (spans.length === 0) return { rows: [] as FlatRow[], globalStart: 0, globalEnd: 1, hiddenCount: 0 };
    const allFlat = flattenTree(spans);

    // Time bounds
    let start = Infinity;
    let end = -Infinity;
    for (const { span } of allFlat) {
      if (span.start_time != null && span.start_time < start) start = span.start_time;
      const spanEnd = span.end_time ?? (span.start_time + (span.duration_ms ?? 0) / 1000);
      if (spanEnd > end) end = spanEnd;
    }
    if (!Number.isFinite(start)) start = 0;
    if (!Number.isFinite(end) || end <= start) end = start + 1;

    // Apply collapsed filter: hide a row if any ancestor path is in `collapsed`.
    const isHidden = (r: FlatRow): boolean => {
      for (let len = 1; len < r.path.length; len++) {
        const ancestor = r.path.slice(0, len).join('.');
        if (collapsed.has(ancestor)) return true;
      }
      return false;
    };
    const uncollapsed = allFlat.filter(r => !isHidden(r));
    const visible = uncollapsed.slice(0, maxRows);
    return { rows: visible, globalStart: start, globalEnd: end, hiddenCount: uncollapsed.length - visible.length };
  }, [spans, collapsed, maxRows]);

  const totalMs = Math.max(1, (globalEnd - globalStart) * 1000);

  if (rows.length === 0) {
    return (
      <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-6 text-center">
        <p className="text-sm text-muted">No spans to display.</p>
      </div>
    );
  }

  const toggleCollapse = (path: number[]) => {
    const key = path.join('.');
    setCollapsed(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  // Vertical guide positions only — the axis *labels* that used to
  // show offsets like "0ms / 87ms / 175ms / 262ms / 350ms" were
  // confusing: same unit and similar formatting as each row's
  // individual duration label, but describing a different thing
  // (offset-from-trace-start vs this-span's-length). Rows already
  // communicate relative position via the bar's `leftPct`; dropping
  // the tick *labels* (not the guide lines) removes the visual
  // double-up without losing the grid rhythm.
  const tickCount = 5;
  const ticks = Array.from({ length: tickCount + 1 }, (_, i) => ({
    frac: i / tickCount,
  }));

  return (
    <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-100 dark:border-surface-800">
        <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium">
          Waterfall
          <span className="ml-2 text-muted font-mono">{rows.length} spans · {fmtDuration(totalMs)}</span>
        </p>
        {collapsed.size > 0 && (
          <button
            onClick={() => setCollapsed(new Set())}
            className="text-[10px] text-muted hover:text-stone-900 dark:hover:text-white transition-colors"
          >
            Expand all
          </button>
        )}
      </div>

      {/* Axis strip kept as empty border so guide lines below have a
          natural top cap. See the note on the tick block above for
          why the labels themselves were removed. */}
      <div className="h-1 border-b border-surface-100 dark:border-surface-800" />

      {/* Rows */}
      <div className="overflow-y-auto max-h-[540px]">
        {(() => {
          const groups = groupRows(rows);

          // Per-row renderer — reused for both single rows and
          // expanded fold groups. Keyed by ``rk`` so React can
          // distinguish the same FlatRow across different group
          // contexts (folded vs expanded).
          const renderRow = (row: FlatRow, rk: string) => {
            const { span, depth, path } = row;
            const spanStart = ((span.start_time - globalStart) * 1000);
            const spanDur = span.duration_ms ?? Math.max(1, (span.end_time ?? span.start_time) - span.start_time) * 1000;
            const leftPct = Math.max(0, (spanStart / totalMs) * 100);
            const widthPct = Math.max(0.4, (spanDur / totalMs) * 100);
            const label = spanLabel(span);
            const badge = spanTypeBadge(span);
            const hasKids = (span.children?.length ?? 0) > 0;
            const key = path.join('.');
            const isCollapsed = collapsed.has(key);
            const isSelected = selectedSpan === span;
            const llm = extractLlmMetrics(span);
            const errorMsg = span.status === 'error'
              ? (span.attributes?.['error.message'] as string | undefined)
              : undefined;

            return (
              <div
                key={rk}
                className={`flex items-stretch border-b border-surface-100 dark:border-surface-800/60 border-l-[3px] ${statusBorderClass(span)} ${rowBgClass(span, isSelected)} transition-colors cursor-pointer group`}
                onClick={() => onSelectSpan?.(span)}
                style={{ minHeight: '30px' }}
              >
                {/* Left: label column (45%) */}
                <div
                  className="flex items-center gap-1.5 min-w-0 py-1 pr-2"
                  style={{ width: '45%', paddingLeft: `${8 + depth * 14}px` }}
                >
                  {hasKids && (
                    <button
                      onClick={(e) => { e.stopPropagation(); toggleCollapse(path); }}
                      className="text-[10px] text-muted w-3 shrink-0 hover:text-stone-900 dark:hover:text-white"
                      aria-label={isCollapsed ? 'expand' : 'collapse'}
                    >
                      {isCollapsed ? '▸' : '▾'}
                    </button>
                  )}
                  {!hasKids && <span className="w-3 shrink-0" />}
                  {badge && (
                    <span className={`text-[9px] font-mono px-1 py-0.5 rounded shrink-0 ${badge.cls}`}>
                      {badge.label}
                    </span>
                  )}
                  <span className="text-xs font-mono text-stone-900 dark:text-zinc-100 truncate" title={label}>
                    {label}
                  </span>
                  {span.agent_id && (
                    <span className="text-[10px] text-muted shrink-0 ml-auto font-mono">{span.agent_id}</span>
                  )}
                </div>

                {/* Right: bar column (55%) */}
                <div className="flex-1 relative py-1">
                  {/* Axis guide lines */}
                  {ticks.slice(1, -1).map(t => (
                    <span
                      key={t.frac}
                      className="absolute top-0 bottom-0 border-l border-dashed border-surface-100 dark:border-surface-800 pointer-events-none"
                      style={{ left: `${t.frac * 100}%` }}
                    />
                  ))}
                  <div
                    className={`absolute h-[18px] rounded-sm top-1/2 -translate-y-1/2 ${
                      span.blocked ? 'bg-red-500/70' :
                      span.status === 'violated' ? 'bg-red-500/50' :
                      span.status === 'error' ? 'bg-orange-500/50' :
                      'bg-stone-400/60 dark:bg-zinc-500/60'
                    } group-hover:opacity-100 opacity-90 transition-opacity`}
                    style={{ left: `${leftPct}%`, width: `${widthPct}%`, minWidth: '3px' }}
                  />
                  <div
                    className="absolute top-1/2 -translate-y-1/2 text-[10px] font-mono text-muted whitespace-nowrap pointer-events-none"
                    style={{ left: `calc(${Math.min(leftPct + widthPct, 90)}% + 4px)` }}
                  >
                    {fmtDuration(span.duration_ms)}
                    {llm?.totalTokens !== undefined && (
                      <span className="text-muted/80 ml-1.5">· {formatTokens(llm.totalTokens)} tok</span>
                    )}
                    {llm?.costUsd !== undefined && llm.costUsd > 0 && (
                      <span className="text-muted/80 ml-1">· {formatCost(llm.costUsd)}</span>
                    )}
                    {errorMsg && (
                      <span className="text-orange-500 ml-1.5">· {errorMsg.slice(0, 32)}{errorMsg.length > 32 ? '…' : ''}</span>
                    )}
                  </div>
                </div>
              </div>
            );
          };

          // Render a compact "× N" pill for a fold group. Click
          // expands the whole run inline. Shows the group's
          // aggregate duration so the user knows how much time the
          // cycle cost without having to open it.
          const renderFold = (group: Extract<RowGroup, { kind: 'fold' }>) => {
            const first = group.rows[0];
            const last = group.rows[group.rows.length - 1];
            const runStart = first.span.start_time;
            const runEnd = (last.span.end_time ?? (last.span.start_time + (last.span.duration_ms ?? 0) / 1000));
            const runMs = Math.max(0, (runEnd - runStart) * 1000);
            const leftPct = Math.max(0, ((runStart - globalStart) * 1000 / totalMs) * 100);
            const widthPct = Math.max(0.4, (runMs / totalMs) * 100);
            const label = spanLabel(first.span);
            const badge = spanTypeBadge(first.span);
            return (
              <div
                key={group.key}
                className="flex items-stretch border-b border-dashed border-surface-200 dark:border-surface-700/70 border-l-[3px] border-l-stone-400/60 dark:border-l-zinc-600/60 hover:bg-surface-50 dark:hover:bg-surface-800/40 cursor-pointer group"
                onClick={() => setUnfolded(prev => { const n = new Set(prev); n.add(group.key); return n; })}
                style={{ minHeight: '30px' }}
                title={`Expand ${group.rows.length} identical calls`}
              >
                <div
                  className="flex items-center gap-1.5 min-w-0 py-1 pr-2"
                  style={{ width: '45%', paddingLeft: `${8 + first.depth * 14}px` }}
                >
                  <span className="w-3 shrink-0 text-[10px] text-muted">▸</span>
                  {badge && (
                    <span className={`text-[9px] font-mono px-1 py-0.5 rounded shrink-0 ${badge.cls}`}>
                      {badge.label}
                    </span>
                  )}
                  <span className="text-xs font-mono text-stone-900 dark:text-zinc-100 truncate" title={label}>
                    {label}
                  </span>
                  <span className="text-[10px] font-mono text-brand bg-brand/10 px-1.5 rounded shrink-0 ml-1">
                    × {group.rows.length}
                  </span>
                </div>
                <div className="flex-1 relative py-1">
                  {ticks.slice(1, -1).map(t => (
                    <span
                      key={t.frac}
                      className="absolute top-0 bottom-0 border-l border-dashed border-surface-100 dark:border-surface-800 pointer-events-none"
                      style={{ left: `${t.frac * 100}%` }}
                    />
                  ))}
                  <div
                    className="absolute h-[18px] rounded-sm top-1/2 -translate-y-1/2 bg-stone-400/40 dark:bg-zinc-500/40 border border-dashed border-stone-400/70 dark:border-zinc-500/70"
                    style={{ left: `${leftPct}%`, width: `${widthPct}%`, minWidth: '3px' }}
                  />
                  <div
                    className="absolute top-1/2 -translate-y-1/2 text-[10px] font-mono text-muted whitespace-nowrap pointer-events-none"
                    style={{ left: `calc(${Math.min(leftPct + widthPct, 90)}% + 4px)` }}
                  >
                    {fmtDuration(runMs)} total
                  </div>
                </div>
              </div>
            );
          };

          return groups.map((g, gi) => {
            if (g.kind === 'single') {
              return renderRow(g.row, `s${gi}`);
            }
            // Fold — show compact pill unless the user clicked expand.
            if (!unfolded.has(g.key)) return renderFold(g);
            return (
              <div key={g.key}>
                {g.rows.map((r, ri) => renderRow(r, `f${gi}-${ri}`))}
              </div>
            );
          });
        })()}
      </div>

      {/* Truncation notice */}
      {hiddenCount > 0 && (
        <div className="px-4 py-2 border-t border-amber-500/20 bg-amber-500/5 text-[10px] text-amber-600 dark:text-amber-400 text-center font-mono">
          {hiddenCount} more row{hiddenCount !== 1 ? 's' : ''} not shown · collapse branches or increase maxRows to see more
        </div>
      )}

      {/* Legend */}
      <div className="flex items-center gap-3 px-4 py-2 border-t border-surface-100 dark:border-surface-800 text-[10px] text-muted">
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-stone-400/60 dark:bg-zinc-500/60" /> ok</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-red-500/60" /> violated / blocked</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-orange-500/60" /> error</span>
        <span className="ml-auto font-mono">click a row to inspect</span>
      </div>
    </div>
  );
}
