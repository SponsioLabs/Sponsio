import { useState } from 'react';

export interface ContractCardProps {
  nlText: string;
  patternName?: string;
  status?: 'verified' | 'proposed' | 'rejected';
  violationCount?: number;
  lastChecked?: string;
  onEdit?: () => void;
  onDelete?: () => void;
  onAccept?: () => void;
  onReject?: () => void;
  expandable?: boolean;
  formulaText?: string;
}

const statusStyles: Record<string, string> = {
  verified: 'text-emerald-400 bg-emerald-500/10',
  proposed: 'text-amber-400 bg-amber-500/10',
  rejected: 'text-red-400 bg-red-500/10',
};

export default function ContractCard({
  nlText, patternName, status, violationCount, lastChecked,
  onEdit, onDelete, onAccept, onReject, expandable, formulaText,
}: ContractCardProps) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-lg border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 px-4 py-3">
      <div className="flex items-start gap-3">
        {/* Status dot */}
        {status && (
          <span className={`mt-1 shrink-0 inline-block px-1.5 py-0.5 rounded text-[10px] font-medium capitalize ${statusStyles[status] ?? 'text-muted bg-surface-100 dark:bg-surface-800'}`}>
            {status}
          </span>
        )}

        {/* Content */}
        <div className="flex-1 min-w-0 overflow-hidden">
          <p
            className="text-sm font-mono text-zinc-700 dark:text-zinc-300 leading-relaxed whitespace-pre-wrap"
            style={{ overflowWrap: 'anywhere', wordBreak: 'break-word' }}
          >
            {nlText}
          </p>
          <div className="flex items-center gap-3 mt-1.5">
            {patternName && (
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-violet-500/10 text-violet-400">{patternName}</span>
            )}
            {violationCount !== undefined && violationCount > 0 && (
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-red-500/10 text-red-400">
                {violationCount} violation{violationCount !== 1 ? 's' : ''}
              </span>
            )}
            {lastChecked && (
              <span className="text-[10px] text-muted">
                Last checked {new Date(lastChecked).toLocaleTimeString()}
              </span>
            )}
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1 shrink-0">
          {onAccept && (
            <button onClick={onAccept} className="px-2 py-1 text-[10px] text-emerald-400 hover:bg-emerald-500/10 rounded transition-colors">Accept</button>
          )}
          {onReject && (
            <button onClick={onReject} className="px-2 py-1 text-[10px] text-red-400 hover:bg-red-500/10 rounded transition-colors">Reject</button>
          )}
          {onEdit && (
            <button onClick={onEdit} className="px-2 py-1 text-[10px] text-muted hover:text-stone-900 dark:hover:text-white rounded transition-colors">Edit</button>
          )}
          {onDelete && (
            <button onClick={onDelete} className="px-2 py-1 text-[10px] text-red-400 hover:bg-red-500/10 rounded transition-colors">Delete</button>
          )}
          {expandable && (
            <button onClick={() => setExpanded(!expanded)} className="px-2 py-1 text-[10px] text-muted hover:text-stone-900 dark:hover:text-white rounded transition-colors">
              {expanded ? 'Collapse' : 'Details'}
            </button>
          )}
        </div>
      </div>

      {/* Expanded details */}
      {expanded && formulaText && (
        <div className="mt-3 pt-3 border-t border-surface-200 dark:border-surface-800">
          <p className="text-[10px] text-muted uppercase tracking-wider mb-1">Compiled Formula</p>
          <pre className="text-xs font-mono text-zinc-600 dark:text-zinc-300 bg-surface-50 dark:bg-surface-800 rounded-lg px-3 py-2 overflow-x-auto">
            {formulaText}
          </pre>
        </div>
      )}
    </div>
  );
}
