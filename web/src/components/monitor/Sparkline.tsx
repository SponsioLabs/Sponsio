/**
 * Tiny inline sparkline rendered as a bar chart. Pure SVG, no deps.
 * Used underneath metric cards to surface "is this getting worse / better?".
 */

interface SparklineProps {
  values: number[];
  width?: number;
  height?: number;
  colorClass?: string;   // Tailwind fill class, e.g. "fill-brand"
  ariaLabel?: string;
}

export default function Sparkline({
  values,
  width = 80,
  height = 20,
  colorClass = 'fill-stone-400 dark:fill-zinc-500',
  ariaLabel,
}: SparklineProps) {
  if (values.length === 0) {
    return <div className="h-[20px] w-[80px] bg-surface-100 dark:bg-surface-800 rounded" aria-label={ariaLabel ?? 'no data'} />;
  }
  const max = Math.max(1, ...values);
  const barWidth = width / values.length;
  const gap = Math.max(0.5, barWidth * 0.15);
  const effectiveBarWidth = barWidth - gap;
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={ariaLabel ?? `sparkline with ${values.length} values`}
      className="overflow-visible"
    >
      {values.map((v, i) => {
        const h = Math.max(1, (v / max) * height);
        return (
          <rect
            key={i}
            x={i * barWidth}
            y={height - h}
            width={effectiveBarWidth}
            height={h}
            rx={0.5}
            className={colorClass}
          />
        );
      })}
    </svg>
  );
}
