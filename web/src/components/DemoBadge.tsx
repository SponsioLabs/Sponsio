interface DemoBadgeProps {
  label?: string;
  hint?: string;
  className?: string;
}

export default function DemoBadge({
  label = 'DEMO DATA',
  hint = 'This tab is showing simulated data. Backend endpoint not yet implemented.',
  className = '',
}: DemoBadgeProps) {
  return (
    <div
      role="note"
      className={`flex items-start gap-2 rounded-lg border border-amber-400/40 bg-amber-400/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300 ${className}`}
    >
      <span className="font-mono font-semibold tracking-wide shrink-0">{label}</span>
      <span className="text-amber-700/80 dark:text-amber-300/80">{hint}</span>
    </div>
  );
}
