export default function PageError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center h-64 gap-3">
      <div className="w-10 h-10 rounded-full bg-red-500/10 border border-red-500/30 flex items-center justify-center">
        <svg className="w-5 h-5 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      </div>
      <p className="text-sm text-red-400">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="text-xs text-muted hover:text-stone-900 dark:hover:text-zinc-100 border border-surface-200 dark:border-surface-800 dark:border-surface-800 px-3 py-1.5 rounded-lg transition-colors"
        >
          Retry
        </button>
      )}
    </div>
  );
}
