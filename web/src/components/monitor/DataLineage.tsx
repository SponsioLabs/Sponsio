/**
 * Inline data-lineage arrow: "Google Drive ─► #marketing".
 *
 * Used wherever a violation event has a source/target pair (see
 * MonitorEvent.source / MonitorEvent.target in types/index.ts).
 * Intentionally tiny — lives inline inside violation feed rows.
 */

interface DataLineageProps {
  source?: string | null;
  target?: string | null;
  emphasize?: boolean;   // highlight red for sensitive flows
}

export default function DataLineage({ source, target, emphasize }: DataLineageProps) {
  if (!source && !target) return null;
  const color = emphasize ? 'text-red-500 dark:text-red-400' : 'text-stone-500 dark:text-zinc-400';
  return (
    <div className="flex items-center gap-1.5 text-[11px] font-mono leading-tight">
      <span className={`px-1.5 py-0.5 rounded ${source ? 'bg-surface-100 dark:bg-surface-800 text-stone-700 dark:text-zinc-300' : 'opacity-40'}`}>
        {source ?? '—'}
      </span>
      <svg className={`w-3 h-3 shrink-0 ${color}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M5 12h14" />
        <path d="M13 6l6 6-6 6" />
      </svg>
      <span className={`px-1.5 py-0.5 rounded ${target ? (emphasize ? 'bg-red-500/10 text-red-500 dark:text-red-400' : 'bg-surface-100 dark:bg-surface-800 text-stone-700 dark:text-zinc-300') : 'opacity-40'}`}>
        {target ?? '—'}
      </span>
    </div>
  );
}
