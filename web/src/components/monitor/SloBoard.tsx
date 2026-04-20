/**
 * SLO board: user-defined service-level objectives over contract pass rates.
 *
 * For P0/P1 stored in localStorage (see utils/aggregations.ts loadSlos).
 * When the backend exposes /monitor/slo (see BACKEND_FIELDS.md §6), swap the
 * source; the consumer component is already agnostic.
 *
 * The SloForm subcomponent is remounted whenever the `suggestion` identity
 * changes (via React key), so the form's initial state is simply derived from
 * props — no useEffect → setState sync pattern needed.
 */

import { useMemo, useState } from 'react';
import type { MonitorEvent, Slo } from '../../types';
import { computeSloStatus, loadSlos, saveSlos } from '../../utils/aggregations';

interface Props {
  events: MonitorEvent[];
  /** Pre-fill slot when user clicks "Promote to SLO" on a violation group. */
  suggestion?: { constraintName: string; agentId?: string } | null;
  onSuggestionConsumed?: () => void;
}

function fmtPct(p: number): string {
  return `${(p * 100).toFixed(1)}%`;
}

function BudgetBar({ remaining }: { remaining: number }) {
  const clamped = Math.max(0, Math.min(1, remaining));
  const color = remaining >= 0.5 ? 'bg-emerald-500' : remaining >= 0.2 ? 'bg-amber-500' : 'bg-red-500';
  return (
    <div className="h-1.5 w-full bg-surface-200 dark:bg-surface-700 rounded-full overflow-hidden">
      <div className={`h-full rounded-full ${color} transition-all duration-500`} style={{ width: `${clamped * 100}%` }} />
    </div>
  );
}

interface FormState {
  name: string;
  constraintName: string;
  agentId: string;
  targetPassRate: string;
  windowMinutes: string;
}

function SloForm({
  initial,
  onCreate,
  onCancel,
}: {
  initial: FormState;
  onCreate: (form: FormState) => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<FormState>(initial);

  return (
    <div className="rounded-lg border border-surface-200 dark:border-surface-800 bg-surface-50 dark:bg-surface-800/40 p-3 mb-4 space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <input
          value={form.name}
          onChange={e => setForm({ ...form, name: e.target.value })}
          placeholder="SLO name"
          className="text-xs bg-white dark:bg-surface-900 border border-surface-200 dark:border-surface-700 rounded-md px-2.5 py-1.5 text-stone-900 dark:text-zinc-100 placeholder:text-muted focus:outline-none focus:border-brand/40"
        />
        <input
          value={form.agentId}
          onChange={e => setForm({ ...form, agentId: e.target.value })}
          placeholder="Agent ID (optional — leave blank for all)"
          className="text-xs bg-white dark:bg-surface-900 border border-surface-200 dark:border-surface-700 rounded-md px-2.5 py-1.5 text-stone-900 dark:text-zinc-100 placeholder:text-muted focus:outline-none focus:border-brand/40"
        />
      </div>
      <input
        value={form.constraintName}
        onChange={e => setForm({ ...form, constraintName: e.target.value })}
        placeholder="Constraint name (must match monitor_event.constraint_name exactly)"
        className="w-full text-xs bg-white dark:bg-surface-900 border border-surface-200 dark:border-surface-700 rounded-md px-2.5 py-1.5 text-stone-900 dark:text-zinc-100 placeholder:text-muted focus:outline-none focus:border-brand/40 font-mono"
      />
      <div className="grid grid-cols-2 gap-2">
        <label className="text-[11px] text-muted flex items-center gap-2">
          Target pass rate:
          <input
            type="number"
            min="0"
            max="100"
            step="0.1"
            value={form.targetPassRate}
            onChange={e => setForm({ ...form, targetPassRate: e.target.value })}
            className="w-20 text-xs bg-white dark:bg-surface-900 border border-surface-200 dark:border-surface-700 rounded-md px-2 py-1 text-stone-900 dark:text-zinc-100 focus:outline-none focus:border-brand/40"
          />
          %
        </label>
        <label className="text-[11px] text-muted flex items-center gap-2">
          Window (minutes):
          <input
            type="number"
            min="1"
            value={form.windowMinutes}
            onChange={e => setForm({ ...form, windowMinutes: e.target.value })}
            className="w-20 text-xs bg-white dark:bg-surface-900 border border-surface-200 dark:border-surface-700 rounded-md px-2 py-1 text-stone-900 dark:text-zinc-100 focus:outline-none focus:border-brand/40"
          />
        </label>
      </div>
      <div className="flex justify-end gap-2">
        <button
          onClick={onCancel}
          className="text-[11px] px-3 py-1.5 rounded-lg text-muted hover:text-stone-900 dark:hover:text-white transition-colors"
        >
          Cancel
        </button>
        <button
          onClick={() => onCreate(form)}
          disabled={!form.constraintName}
          className="text-[11px] px-3 py-1.5 rounded-lg bg-brand text-white hover:bg-brand/90 disabled:opacity-40 transition-colors"
        >
          Create SLO
        </button>
      </div>
    </div>
  );
}

function buildInitialForm(suggestion: Props['suggestion']): FormState {
  if (!suggestion) {
    return { name: '', constraintName: '', agentId: '', targetPassRate: '99', windowMinutes: '60' };
  }
  const name = `SLO · ${suggestion.constraintName.slice(0, 30)}${suggestion.constraintName.length > 30 ? '…' : ''}`;
  return {
    name,
    constraintName: suggestion.constraintName,
    agentId: suggestion.agentId ?? '',
    targetPassRate: '99',
    windowMinutes: '60',
  };
}

export default function SloBoard({ events, suggestion, onSuggestionConsumed }: Props) {
  const [slos, setSlos] = useState<Slo[]>(() => loadSlos());
  const [manualShowForm, setManualShowForm] = useState(false);
  // Stable id for the manual form so its key doesn't change mid-edit even if
  // `suggestion` flips from non-null → null while the user is typing.
  const [manualSessionId] = useState(() => `manual-${Date.now()}`);

  // If a suggestion is present, the form is visible and pre-filled by its key.
  const formVisible = manualShowForm || suggestion !== null;
  const suggestionKey = suggestion
    ? `sug:${suggestion.constraintName}::${suggestion.agentId ?? ''}`
    : manualShowForm ? manualSessionId : 'none';

  const handleCreate = (form: FormState) => {
    const targetPassRate = Number(form.targetPassRate) / 100;
    const windowMinutes = Number(form.windowMinutes);
    if (!form.constraintName || Number.isNaN(targetPassRate) || Number.isNaN(windowMinutes)) return;
    const slo: Slo = {
      id: `slo_${Date.now()}`,
      name: form.name || form.constraintName,
      constraintName: form.constraintName,
      agentId: form.agentId || undefined,
      targetPassRate,
      windowMinutes,
      createdAt: Date.now(),
    };
    const next = [...slos, slo];
    setSlos(next);
    saveSlos(next);
    setManualShowForm(false);
    onSuggestionConsumed?.();
  };

  const handleCancel = () => {
    setManualShowForm(false);
    onSuggestionConsumed?.();
  };

  const removeSlo = (id: string) => {
    const next = slos.filter(s => s.id !== id);
    setSlos(next);
    saveSlos(next);
  };

  const statuses = useMemo(() => slos.map(s => computeSloStatus(s, events)), [slos, events]);

  return (
    <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
      <div className="flex items-center justify-between mb-3">
        <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium">
          SLO Board
          {statuses.length > 0 && <span className="ml-2 text-muted font-mono">({statuses.length})</span>}
        </p>
        {!formVisible && (
          <button
            onClick={() => setManualShowForm(true)}
            className="text-[11px] px-2.5 py-1 rounded-lg bg-brand/10 text-brand hover:bg-brand/20 transition-colors"
          >
            + New SLO
          </button>
        )}
      </div>

      {formVisible && (
        <SloForm
          key={suggestionKey}
          initial={buildInitialForm(suggestion)}
          onCreate={handleCreate}
          onCancel={handleCancel}
        />
      )}

      {statuses.length === 0 && !formVisible && (
        <p className="text-sm text-muted py-6 text-center">
          No SLOs defined yet. Click <span className="font-medium text-stone-700 dark:text-zinc-200">+ New SLO</span> to track a pass-rate target for a contract.
        </p>
      )}

      {statuses.length > 0 && (
        <div className="space-y-3">
          {statuses.map(s => (
            <div key={s.id} className="rounded-lg border border-surface-200 dark:border-surface-800 bg-surface-50 dark:bg-surface-800/40 px-3 py-2.5">
              <div className="flex items-center justify-between mb-1.5">
                <div className="min-w-0 flex-1 pr-2">
                  <p className="text-sm font-medium text-stone-900 dark:text-zinc-100 truncate">{s.name}</p>
                  <p className="text-[10px] text-muted font-mono truncate" title={s.constraintName}>
                    {s.agentId ? `${s.agentId} · ` : ''}{s.constraintName}
                  </p>
                </div>
                <span className={`text-xs font-mono font-bold shrink-0 ${s.healthy ? 'text-emerald-400' : 'text-red-400'}`}>
                  {fmtPct(s.currentPassRate)} / {fmtPct(s.targetPassRate)}
                </span>
                <button
                  onClick={() => removeSlo(s.id)}
                  className="ml-2 text-muted hover:text-red-400 text-xs transition-colors shrink-0"
                  title="Delete SLO"
                >
                  ✕
                </button>
              </div>
              <BudgetBar remaining={s.errorBudgetRemaining} />
              <p className="text-[10px] text-muted mt-1 flex items-center justify-between">
                <span>
                  Error budget:{' '}
                  <span className={`font-mono font-medium ${s.errorBudgetRemaining >= 0.5 ? 'text-emerald-400' : s.errorBudgetRemaining >= 0.2 ? 'text-amber-400' : 'text-red-400'}`}>
                    {Math.round(Math.max(0, s.errorBudgetRemaining) * 100)}%
                  </span>
                </span>
                <span className="font-mono">{s.sampleSize} events · {s.windowMinutes}m window</span>
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
