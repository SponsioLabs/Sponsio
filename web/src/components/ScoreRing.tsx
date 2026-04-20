interface ScoreRingProps {
  score: number;
  size?: 'sm' | 'md' | 'lg';
  grade: string;
}

const sizeMap = { sm: 64, md: 96, lg: 128 };
const strokeMap = { sm: 4, md: 6, lg: 8 };
const textMap = { sm: 'text-lg', md: 'text-2xl', lg: 'text-4xl' };
const gradeMap = { sm: 'text-[8px]', md: 'text-[10px]', lg: 'text-xs' };

function gradeColor(grade: string): string {
  if (grade === 'A+' || grade === 'A') return '#34d399'; // emerald-400
  if (grade === 'B') return '#60a5fa'; // blue-400
  if (grade === 'C') return '#fbbf24'; // amber-400
  if (grade === 'D') return '#fb923c'; // orange-400
  return '#f87171'; // red-400
}

export default function ScoreRing({ score, size = 'md', grade }: ScoreRingProps) {
  const px = sizeMap[size];
  const sw = strokeMap[size];
  const r = (px - sw) / 2;
  const circ = 2 * Math.PI * r;
  const offset = circ * (1 - Math.min(score, 100) / 100);
  const color = gradeColor(grade);

  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: px, height: px }}>
      <svg width={px} height={px} className="-rotate-90">
        <circle cx={px / 2} cy={px / 2} r={r} fill="none" stroke="currentColor" strokeWidth={sw}
          className="text-surface-200 dark:text-surface-800" />
        <circle cx={px / 2} cy={px / 2} r={r} fill="none" stroke={color} strokeWidth={sw}
          strokeDasharray={circ} strokeDashoffset={offset} strokeLinecap="round"
          style={{ transition: 'stroke-dashoffset 0.6s ease' }} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className={`font-bold font-mono text-zinc-900 dark:text-zinc-100 ${textMap[size]}`}>{score}</span>
        <span className={`font-bold ${gradeMap[size]}`} style={{ color }}>{grade}</span>
      </div>
    </div>
  );
}
