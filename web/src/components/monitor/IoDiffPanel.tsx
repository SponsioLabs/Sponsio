/**
 * Input/Output diff panel: shows the LLM prompt and completion for a span,
 * plus — uniquely for Sponsio — the completion AFTER a soft-retry, so users
 * can see exactly what the constraint feedback changed.
 *
 * Visual: side-by-side cards for the before/after cases, with a subtle
 * highlight on differing regions. We deliberately avoid a full diff algorithm
 * (deps-free) — a line-level comparison is plenty for demo value.
 */

import type { SpanNode } from '../../types';
import { extractLlmMetrics, formatCost, formatTokens } from '../../utils/llmMetrics';

interface Props { span: SpanNode; }

// Very lightweight line-level "diff highlight": just flags lines that differ
// between before/after. Good enough for demo; we're not promising a real diff.
function highlightLines(a: string, b: string): { a: string[]; b: string[]; diffA: Set<number>; diffB: Set<number> } {
  const aLines = a.split(/\n/);
  const bLines = b.split(/\n/);
  const aSet = new Set(aLines);
  const bSet = new Set(bLines);
  const diffA = new Set<number>();
  const diffB = new Set<number>();
  aLines.forEach((line, i) => { if (!bSet.has(line)) diffA.add(i); });
  bLines.forEach((line, i) => { if (!aSet.has(line)) diffB.add(i); });
  return { a: aLines, b: bLines, diffA, diffB };
}

export default function IoDiffPanel({ span }: Props) {
  const llm = extractLlmMetrics(span);
  if (!llm || (!llm.prompt && !llm.completion)) {
    return (
      <div className="rounded-xl border border-dashed border-surface-300 dark:border-surface-700 bg-surface-50 dark:bg-surface-800/40 p-5 text-center">
        <p className="text-xs text-muted">
          No LLM input/output recorded for this span.
          <br />
          <span className="text-[10px]">
            Populate <span className="font-mono">llm.prompt</span> / <span className="font-mono">llm.completion</span> in span attributes.
          </span>
        </p>
      </div>
    );
  }

  const hasRetry = !!llm.completionAfterRetry;
  const diff = hasRetry
    ? highlightLines(llm.completion ?? '', llm.completionAfterRetry ?? '')
    : null;

  return (
    <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-100 dark:border-surface-800">
        <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium">
          LLM Input / Output
        </p>
        <div className="flex items-center gap-3 text-[10px] font-mono text-muted">
          {llm.model && <span>{llm.model}</span>}
          {llm.temperature !== undefined && <span>temp={llm.temperature}</span>}
          {llm.totalTokens !== undefined && <span>{formatTokens(llm.totalTokens)} tok</span>}
          {llm.costUsd !== undefined && <span>{formatCost(llm.costUsd)}</span>}
          {llm.ttftMs !== undefined && <span>TTFT {llm.ttftMs}ms</span>}
        </div>
      </div>

      {/* Prompt */}
      {llm.prompt && (
        <div className="px-4 py-3 border-b border-surface-100 dark:border-surface-800">
          <p className="text-[9px] text-muted uppercase tracking-wider mb-1">Prompt</p>
          <pre className="text-[11px] font-mono text-stone-700 dark:text-zinc-300 bg-surface-50 dark:bg-surface-800 rounded-md p-2 whitespace-pre-wrap max-h-40 overflow-y-auto">
            {llm.prompt}
          </pre>
        </div>
      )}

      {/* Completion(s) — side by side if retry */}
      {hasRetry && diff ? (
        <div className="grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-surface-100 dark:divide-surface-800">
          <div className="px-4 py-3">
            <div className="flex items-center justify-between mb-1">
              <p className="text-[9px] text-muted uppercase tracking-wider">Before (violated)</p>
              <span className="text-[9px] font-bold text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded">sto violation</span>
            </div>
            <pre className="text-[11px] font-mono bg-surface-50 dark:bg-surface-800 rounded-md p-2 whitespace-pre-wrap max-h-48 overflow-y-auto">
              {diff.a.map((line, i) => (
                <div
                  key={i}
                  className={diff.diffA.has(i) ? 'bg-red-500/10 -mx-2 px-2 text-red-600 dark:text-red-300' : ''}
                >
                  {line || '\u00A0'}
                </div>
              ))}
            </pre>
          </div>
          <div className="px-4 py-3">
            <div className="flex items-center justify-between mb-1">
              <p className="text-[9px] text-muted uppercase tracking-wider">After retry (passed)</p>
              <span className="text-[9px] font-bold text-emerald-400 bg-emerald-500/10 px-1.5 py-0.5 rounded">sto pass</span>
            </div>
            <pre className="text-[11px] font-mono bg-surface-50 dark:bg-surface-800 rounded-md p-2 whitespace-pre-wrap max-h-48 overflow-y-auto">
              {diff.b.map((line, i) => (
                <div
                  key={i}
                  className={diff.diffB.has(i) ? 'bg-emerald-500/10 -mx-2 px-2 text-emerald-600 dark:text-emerald-300' : ''}
                >
                  {line || '\u00A0'}
                </div>
              ))}
            </pre>
          </div>
        </div>
      ) : llm.completion ? (
        <div className="px-4 py-3">
          <p className="text-[9px] text-muted uppercase tracking-wider mb-1">Completion</p>
          <pre className="text-[11px] font-mono text-stone-700 dark:text-zinc-300 bg-surface-50 dark:bg-surface-800 rounded-md p-2 whitespace-pre-wrap max-h-48 overflow-y-auto">
            {llm.completion}
          </pre>
        </div>
      ) : null}
    </div>
  );
}
