import { useState, useEffect } from 'react';
import type { SpanNode } from '../types';

interface SpanTreeProps {
  span: SpanNode;
  defaultLevel?: 1 | 2 | 3;
  compact?: boolean;
}

function statusIcon(status: string): { icon: string; colorClass: string } {
  if (status === 'ok') return { icon: '\u2713', colorClass: 'text-emerald-400' };
  if (status === 'violated') return { icon: '\u2717', colorClass: 'text-red-400' };
  return { icon: '!', colorClass: 'text-amber-400' };
}

function borderColor(status: string): string {
  if (status === 'ok') return 'border-l-emerald-500';
  if (status === 'violated') return 'border-l-red-500';
  return 'border-l-amber-500';
}

function enforcementColor(action: string): string {
  if (action === 'blocked') return 'text-red-400 font-bold';
  if (action === 'escalated') return 'text-amber-400 font-bold';
  if (action === 'retrying') return 'text-sky-400 font-bold';
  if (action === 'redirected') return 'text-violet-400 font-bold';
  return 'text-muted';
}

function deriveFix(span: SpanNode): string {
  if (!span.children) return '';
  for (const child of span.children) {
    if (child.span_type === 'sponsio.contract_check' && child.children) {
      for (const sub of child.children) {
        if (sub.span_type === 'sponsio.enforcement') {
          if (sub.strategy === 'HardBlock') return 'Action blocked by hard constraint';
          if (sub.strategy === 'EscalateToHuman') return 'Requires human approval';
          return sub.result_action ?? '';
        }
      }
    }
  }
  return '';
}

function findViolatedContract(span: SpanNode): string {
  if (!span.children) return '';
  for (const child of span.children) {
    if (child.span_type === 'sponsio.contract_check' && child.status === 'violated') {
      return child.contract_name ?? '';
    }
  }
  return '';
}

function getContractChecks(span: SpanNode): SpanNode[] {
  if (!span.children) return [];
  return span.children.filter(c => c.span_type === 'sponsio.contract_check');
}

function getSoftChecks(span: SpanNode): SpanNode[] {
  if (!span.children) return [];
  const softContainer = span.children.find(c => c.span_type === 'sponsio.soft_check');
  if (!softContainer?.children) return [];
  return softContainer.children.filter(c => c.span_type === 'sponsio.soft_eval');
}

function hasChildViolation(span: SpanNode): boolean {
  if (span.blocked) return true;
  if (span.status === 'violated') return true;
  for (const c of span.children ?? []) {
    if (c.span_type === 'sponsio.contract_check' && c.status === 'violated') return true;
  }
  return false;
}

/* Level 1: Verdict card */
function VerdictCard({ span, onExpand }: { span: SpanNode; onExpand: () => void }) {
  const isBlocked = span.blocked === true;
  const isViolated = hasChildViolation(span);
  const effectiveStatus = isViolated ? 'violated' : span.status;
  const { icon, colorClass } = statusIcon(effectiveStatus);

  const label = isBlocked ? 'BLOCKED' : isViolated ? 'VIOLATED' : 'PASSED';
  const contractCount = (span.children ?? []).filter(c => c.span_type === 'sponsio.contract_check').length;
  const rule = isViolated ? findViolatedContract(span) : `${contractCount} contract${contractCount !== 1 ? 's' : ''} checked`;
  const fix = isViolated ? deriveFix(span) : '';

  return (
    <div className={`rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 border-l-[3px] ${borderColor(effectiveStatus)}`}>
      <div className="px-4 py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className={`text-lg ${colorClass}`}>{icon}</span>
            <span className={`text-sm font-semibold ${colorClass}`}>{label}</span>
          </div>
          {span.duration_ms != null && (
            <span className="text-xs text-muted font-mono">{span.duration_ms.toFixed(0)}ms</span>
          )}
        </div>
        {rule && <p className="text-sm text-zinc-600 dark:text-zinc-300 mt-1">{rule}</p>}
        {fix && <p className="text-xs text-muted mt-0.5">{fix}</p>}
        <button
          onClick={onExpand}
          className="mt-2 text-xs text-muted hover:text-stone-900 dark:hover:text-zinc-100 transition-colors"
        >
          Details
        </button>
      </div>
    </div>
  );
}

/* Level 2: Per-contract summary */
function ContractSummary({ span, expandedContracts, toggleContract }: {
  span: SpanNode;
  expandedContracts: Set<number>;
  toggleContract: (i: number) => void;
}) {
  const contractChecks = getContractChecks(span);
  const softChecks = getSoftChecks(span);

  return (
    <div className={`rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 border-l-[3px] ${borderColor(span.status)}`}>
      <div className="px-4 py-3 border-b border-surface-100 dark:border-surface-800 flex items-center justify-between">
        <span className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
          Contract Evaluations ({contractChecks.length} contract{contractChecks.length !== 1 ? 's' : ''})
        </span>
        {span.duration_ms != null && (
          <span className="text-xs text-muted font-mono">{span.duration_ms.toFixed(0)}ms</span>
        )}
      </div>

      {contractChecks.map((cc, i) => {
        const { icon, colorClass } = statusIcon(cc.status);
        const isExpanded = expandedContracts.has(i);

        return (
          <div key={i}>
            <button
              onClick={() => toggleContract(i)}
              className="w-full px-4 py-3 border-b border-surface-100 dark:border-surface-800 hover:bg-surface-50 dark:hover:bg-surface-800/50 cursor-pointer text-left"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className={colorClass}>{icon}</span>
                  <span className="text-sm text-zinc-700 dark:text-zinc-300">{cc.contract_name || '(unnamed)'}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className={`text-xs font-mono px-1.5 py-0.5 rounded ${
                    cc.pipeline === 'hard'
                      ? 'bg-red-500/10 text-red-400'
                      : 'bg-violet-500/10 text-violet-400'
                  }`}>{cc.pipeline}</span>
                  <span className={`text-xs ${colorClass}`}>{cc.status === 'ok' ? 'PASSED' : 'VIOLATED'}</span>
                  <span className="text-zinc-600 dark:text-zinc-300 text-xs">{isExpanded ? '\u25BC' : '\u25B6'}</span>
                </div>
              </div>
            </button>

            {isExpanded && cc.children && (
              <EvaluationChain children={cc.children} />
            )}
          </div>
        );
      })}

      {softChecks.length > 0 && (
        <>
          <div className="px-4 py-2 border-b border-surface-100 dark:border-surface-800">
            <span className="text-xs text-muted uppercase tracking-wider">Soft Constraints</span>
          </div>
          {softChecks.map((se, i) => {
            const { icon, colorClass } = statusIcon(se.passed ? 'ok' : 'violated');
            return (
              <div key={`soft-${i}`} className="px-4 py-3 border-b border-surface-100 dark:border-surface-800">
                <div className="flex items-center gap-2">
                  <span className={colorClass}>{icon}</span>
                  <span className="text-sm text-zinc-700 dark:text-zinc-300">{se.constraint_name}</span>
                  <span className="text-xs text-muted ml-auto">
                    score={se.score?.toFixed(2)}, threshold={se.threshold?.toFixed(2)}
                  </span>
                </div>
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}

/* Level 3: Full evaluation chain */
function EvaluationChain({ children }: { children: SpanNode[] }) {
  return (
    <div className="border-l-2 border-surface-200 dark:border-surface-800 ml-4 pl-3 py-2 mb-2">
      {children.map((child, i) => {
        if (child.span_type === 'sponsio.precondition') {
          const { icon, colorClass } = statusIcon(child.result === false ? 'violated' : 'ok');
          return (
            <div key={i} className="py-1.5 text-sm flex items-start gap-2">
              <span className={colorClass}>{icon}</span>
              <div>
                <span className="text-muted">Precondition: </span>
                <span className="text-zinc-700 dark:text-zinc-300">{child.formula_desc || '(none)'}</span>
                <span className={`ml-2 ${colorClass}`}>{child.result === false ? 'VIOLATED' : 'OK'}</span>
              </div>
            </div>
          );
        }
        if (child.span_type === 'sponsio.guarantee') {
          const { icon, colorClass } = statusIcon(child.result === false ? 'violated' : 'ok');
          return (
            <div key={i} className="py-1.5 text-sm flex items-start gap-2">
              <span className={colorClass}>{icon}</span>
              <div>
                <span className="text-muted">Guarantee: </span>
                <span className="text-zinc-700 dark:text-zinc-300">{child.formula_desc || '(unnamed)'}</span>
                <span className={`ml-2 ${colorClass}`}>{child.result === false ? 'VIOLATED' : 'SATISFIED'}</span>
              </div>
            </div>
          );
        }
        if (child.span_type === 'sponsio.violation') {
          return (
            <div key={i} className="py-1.5 text-sm">
              <div className="flex items-center gap-2">
                <span className="text-red-400">{'\u2717'}</span>
                <span className="text-muted">Violation: </span>
                <span className="text-red-400">{child.kind}</span>
                {child.severity && <span className="text-xs text-muted">severity={child.severity}</span>}
              </div>
              {child.evidence && (
                <div className="text-xs text-muted font-mono bg-surface-100 dark:bg-surface-800 rounded px-2 py-1 mt-1 ml-6">
                  {child.evidence}
                </div>
              )}
            </div>
          );
        }
        if (child.span_type === 'sponsio.enforcement') {
          return (
            <div key={i} className="py-1.5 text-sm flex items-center gap-2">
              <span className="text-muted">-&gt;</span>
              <span className="text-muted">Enforcement: </span>
              <span className="text-zinc-600 dark:text-zinc-300">{child.strategy}</span>
              <span className="text-muted">-&gt;</span>
              <span className={enforcementColor(child.result_action ?? '')}>{child.result_action}</span>
            </div>
          );
        }
        return null;
      })}
    </div>
  );
}

export default function SpanTree({ span, defaultLevel = 1, compact = false }: SpanTreeProps) {
  const [expandedLevel, setExpandedLevel] = useState<1 | 2 | 3>(defaultLevel);
  const [expandedContracts, setExpandedContracts] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (compact) return;
    if (hasChildViolation(span)) {
      setExpandedLevel(2);
      const contracts = getContractChecks(span);
      if (contracts.length === 1) {
        setExpandedContracts(new Set([0]));
        setExpandedLevel(2);
      }
    }
  }, [span, compact]);

  const toggleContract = (i: number) => {
    setExpandedContracts(prev => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  };

  if (expandedLevel === 1) {
    return <VerdictCard span={span} onExpand={() => setExpandedLevel(2)} />;
  }

  return (
    <div>
      <ContractSummary span={span} expandedContracts={expandedContracts} toggleContract={toggleContract} />
      <button
        onClick={() => { setExpandedLevel(1); setExpandedContracts(new Set()); }}
        className="mt-1 text-xs text-muted hover:text-stone-900 dark:hover:text-zinc-100 transition-colors"
      >
        Collapse
      </button>
    </div>
  );
}
