import { useState, useEffect, useRef } from 'react';
import type { TraceStep, SpanNode } from '../types';
import Spinner from './Spinner';
import SpanTree from './SpanTree';

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function isSpanViolated(span: SpanNode): boolean {
  if (span.blocked) return true;
  if (span.status === 'violated') return true;
  for (const c of span.children ?? []) {
    if (c.span_type === 'sponsio.contract_check' && c.status === 'violated') return true;
  }
  return false;
}

function getViolatedContract(span: SpanNode): string {
  for (const c of span.children ?? []) {
    if (c.span_type === 'sponsio.contract_check' && c.status === 'violated') {
      for (const gc of c.children ?? []) {
        if (gc.span_type === 'sponsio.guarantee' && gc.result === false)
          return gc.formula_desc ?? c.contract_name ?? '';
      }
      return c.contract_name ?? '';
    }
  }
  return '';
}

/* ------------------------------------------------------------------ */
/*  Event type badge                                                   */
/* ------------------------------------------------------------------ */

function TypeBadge({ eventType }: { eventType: string }) {
  const styles =
    eventType === 'data_write'
      ? 'bg-sky-500/10 text-sky-400'
      : eventType === 'data_read'
        ? 'bg-green-500/10 text-green-400'
        : 'bg-sky-500/10 text-sky-400';
  const text =
    eventType === 'data_write' ? 'write'
      : eventType === 'data_read' ? 'read'
        : 'call';
  return (
    <span className={`text-[10px] font-mono px-1 py-0.5 rounded shrink-0 ${styles}`}>
      {text}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Source / target flow label                                         */
/* ------------------------------------------------------------------ */

function FlowLabel({ source, target, eventType }: { source?: string; target?: string; eventType: string }) {
  if (!source && !target) return null;
  return (
    <span className="text-[10px] text-zinc-600 dark:text-zinc-300 shrink-0 truncate max-w-[140px]">
      {eventType === 'data_read' && source && <>&larr; {source}</>}
      {eventType === 'data_write' && target && <>&rarr; {target}</>}
      {eventType === 'tool_call' && source && <>&larr; {source}</>}
      {eventType === 'tool_call' && !source && target && <>&rarr; {target}</>}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  TraceTimeline — unified component                                  */
/* ------------------------------------------------------------------ */

export interface TraceTimelineProps {
  steps: TraceStep[];
  label: 'without' | 'with';
  spans?: SpanNode[];
  contractDesc?: string;
  animate?: boolean;
  title?: string;
}

export default function TraceTimeline({
  steps, label, spans, contractDesc, animate, title,
}: TraceTimelineProps) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [visibleCount, setVisibleCount] = useState(0);
  const prevStepsRef = useRef<TraceStep[]>([]);

  useEffect(() => {
    if (!animate) {
      queueMicrotask(() => {
        setVisibleCount(steps.length);
      });
    }
  }, [steps.length, animate]);

  useEffect(() => {
    if (!animate) return;
    if (visibleCount >= steps.length) return;
    const timer = setTimeout(() => setVisibleCount(c => c + 1), 350);
    return () => clearTimeout(timer);
  }, [steps.length, visibleCount, animate]);

  useEffect(() => {
    const prev = prevStepsRef.current;
    const isNewRun = steps.length === 0 ||
      (prev.length > 0 && steps.length > 0 && prev[0]?.label !== steps[0]?.label);
    if (isNewRun) {
      queueMicrotask(() => {
        setExpanded(new Set());
        setVisibleCount(animate ? 0 : steps.length);
      });
    }
    prevStepsRef.current = steps;
  }, [steps, animate]);

  const visible = steps.slice(0, visibleCount);
  const firstViolIdx = steps.findIndex(s => s.isViolation);

  const toggle = (i: number) => setExpanded(prev => {
    const n = new Set(prev);
    if (n.has(i)) n.delete(i);
    else n.add(i);
    return n;
  });

  const spanMap = new Map<number, SpanNode>();
  if (spans && spans.length > 0) {
    let si = 0;
    for (let i = 0; i < steps.length && si < spans.length; i++) {
      spanMap.set(i, spans[si]);
      si++;
    }
  }

  useEffect(() => {
    for (let i = 0; i < steps.length; i++) {
      if (steps[i].isViolation) {
        const idx = i;
        queueMicrotask(() => {
          setExpanded(prev => new Set(prev).add(idx));
        });
        break;
      }
    }
  }, [steps]);

  if (visible.length === 0 && animate) {
    return (
      <div className="flex items-center justify-center h-32 text-muted text-sm">
        <Spinner size="sm" />&nbsp;Waiting...
      </div>
    );
  }

  return (
    <div>
      {title && (
        <div className="flex items-center gap-2 mb-4">
          <div className={`w-2 h-2 rounded-full ${label === 'without' ? 'bg-red-500' : 'bg-emerald-500'}`} />
          <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">{title}</h3>
        </div>
      )}
      <div className="relative pl-8">
        <div className="absolute left-[11px] top-0 bottom-0 w-0.5 bg-surface-200 dark:bg-surface-800" />
        {visible.map((step, i) => {
          const isViol = step.isViolation;
          const isTainted = label === 'without' && !isViol && firstViolIdx !== -1 && i > firstViolIdx;
          const isExp = expanded.has(i);
          const hasSpan = spanMap.has(i);

          const dot = isViol
            ? (label === 'with' ? 'bg-red-500 border-red-400 animate-pulse' : 'bg-red-500 border-red-400')
            : isTainted ? 'bg-amber-400 border-amber-300'
              : 'bg-emerald-500 border-emerald-400';

          const row = isViol
            ? 'border border-red-500/20 bg-red-500/5'
            : isTainted ? 'border border-dashed border-amber-500/20 bg-amber-500/5'
              : hasSpan ? 'hover:bg-surface-100 dark:bg-surface-800/30' : '';

          const status = isViol
            ? (label === 'without' ? 'VIOLATION' : 'BLOCKED')
            : isTainted ? 'COMPROMISED' : 'PASS';
          const statusColor = isViol
            ? 'text-red-400'
            : isTainted ? 'text-amber-400'
              : 'text-emerald-400';

          const spanNode = spanMap.get(i);
          const violatedContract = spanNode && isSpanViolated(spanNode) ? getViolatedContract(spanNode) : '';
          const isClickable = hasSpan || (isViol && contractDesc);

          return (
            <div key={i} className="relative pb-4">
              <div className={`absolute left-[-25px] top-1.5 w-3 h-3 rounded-full border-2 ${dot}`} />

              {isClickable ? (
                <button onClick={() => toggle(i)} className={`w-full text-left rounded-lg px-3 py-2 transition-all ${row}`}>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-zinc-600 dark:text-zinc-300 w-5 text-right shrink-0">{i + 1}</span>
                    <TypeBadge eventType={step.event_type} />
                    <span className="text-sm font-mono text-zinc-700 dark:text-zinc-300 flex-1 truncate">{step.label}</span>
                    <FlowLabel source={step.source} target={step.target} eventType={step.event_type} />
                    <span className={`text-xs font-bold shrink-0 ${statusColor}`}>{status}</span>
                    <span className="text-zinc-600 dark:text-zinc-300 text-xs">{isExp ? '\u25BC' : '\u25B6'}</span>
                  </div>
                  {(violatedContract || (isViol && contractDesc)) && (
                    <p className="text-xs text-red-400/70 mt-1 ml-7 truncate">
                      {violatedContract || contractDesc}
                    </p>
                  )}
                </button>
              ) : (
                <div className={`rounded-lg px-3 py-2 ${row}`}>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-zinc-600 dark:text-zinc-300 w-5 text-right shrink-0">{i + 1}</span>
                    <TypeBadge eventType={step.event_type} />
                    <span className="text-sm font-mono text-zinc-700 dark:text-zinc-300 flex-1 truncate">{step.label}</span>
                    <FlowLabel source={step.source} target={step.target} eventType={step.event_type} />
                    <span className={`text-xs font-bold shrink-0 ${statusColor}`}>{status}</span>
                  </div>
                  {isViol && contractDesc && (
                    <p className="text-xs text-red-400/70 mt-1 ml-7 truncate">
                      {contractDesc}
                    </p>
                  )}
                </div>
              )}

              {isExp && (
                <div className="mt-2 mb-2 ml-2">
                  {spanNode ? (
                    <SpanTree span={spanNode} compact={!isViol} />
                  ) : isViol && contractDesc ? (
                    <div className="rounded-xl border border-l-[3px] border-red-500/20 border-l-red-500 bg-surface-50 dark:bg-surface-900 px-4 py-3">
                      <div className="flex items-center gap-2 mb-2">
                        <span className="text-red-400 text-lg">{'\u2717'}</span>
                        <span className="text-sm font-semibold text-red-400">
                          {label === 'with' ? 'BLOCKED' : 'VIOLATION'}
                        </span>
                      </div>
                      {contractDesc.split('\n').filter(Boolean).map((line, ci) => (
                        <div key={ci} className="flex items-start gap-2 py-1 text-sm">
                          <span className="text-red-400 shrink-0">{'\u2717'}</span>
                          <span className="text-muted shrink-0">Guarantee:</span>
                          <span className="text-zinc-600 dark:text-zinc-300">{line}</span>
                          <span className="text-red-400 ml-1">VIOLATED</span>
                        </div>
                      ))}
                      {label === 'with' && <p className="text-xs text-muted mt-2">Enforcement: HardBlock &rarr; action prevented</p>}
                      {label === 'without' && <p className="text-xs text-amber-400 mt-2">No enforcement &mdash; action executed unchecked</p>}
                    </div>
                  ) : null}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
