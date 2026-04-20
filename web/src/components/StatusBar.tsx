export interface StatusBarProps {
  isConnected: boolean;
  stats: { label: string; value: number; color?: string }[];
  onReset?: () => void;
  autoRefresh?: boolean;
  onAutoRefreshToggle?: (enabled: boolean) => void;
}

export default function StatusBar({ isConnected, stats, onReset, autoRefresh, onAutoRefreshToggle }: StatusBarProps) {
  return (
    <div className="flex items-center gap-6 p-4 rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 mb-6">
      {/* Connection indicator */}
      <div className="flex items-center gap-2 shrink-0">
        <span className={`w-2.5 h-2.5 rounded-full ${isConnected ? 'bg-brand animate-pulse' : 'bg-surface-600'}`} />
        <span className={`text-xs font-medium ${isConnected ? 'text-emerald-400' : 'text-muted'}`}>
          {isConnected ? 'Monitoring active' : 'No agents connected'}
        </span>
      </div>

      {/* Stat pills */}
      <div className="flex items-center gap-4 flex-1 flex-wrap">
        {stats.map((s) => (
          <div key={s.label} className="flex items-center gap-1.5">
            <span className={`text-sm font-bold font-mono ${s.color ?? 'text-zinc-900 dark:text-zinc-100'}`}>{s.value}</span>
            <span className="text-xs text-muted">{s.label}</span>
          </div>
        ))}
      </div>

      {/* Auto-refresh toggle */}
      {onAutoRefreshToggle && (
        <button
          onClick={() => onAutoRefreshToggle(!autoRefresh)}
          className={`px-3 py-1 text-xs rounded-lg transition-colors ${
            autoRefresh ? 'bg-emerald-500/10 text-emerald-400' : 'bg-surface-100 dark:bg-surface-800 text-muted'
          }`}
        >
          Auto-refresh {autoRefresh ? 'on' : 'off'}
        </button>
      )}

      {/* Reset button */}
      {onReset && (
        <button
          onClick={onReset}
          className="px-3 py-1 text-xs border border-surface-200 dark:border-surface-800 text-muted rounded-lg hover:bg-surface-100 dark:hover:bg-surface-800 hover:text-stone-900 dark:hover:text-white transition-colors"
        >
          Reset
        </button>
      )}
    </div>
  );
}
