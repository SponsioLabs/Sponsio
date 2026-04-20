import { useNavigate } from 'react-router-dom';

interface NavButton {
  label: string;
  path: string;
  /**
   * Optional click override. When provided, the button runs this handler
   * instead of plain `navigate(path)` — use it to carry handoff state like
   * "ScanAgent → Rulebook with current suggestions".
   */
  onClick?: () => void;
}

interface PipelineNavProps {
  prev?: NavButton;
  next?: NavButton;
}

export default function PipelineNav({ prev, next }: PipelineNavProps) {
  const navigate = useNavigate();

  return (
    <div className="mt-12 pt-6 border-t border-surface-200 dark:border-surface-800 flex items-center justify-between">
      {prev ? (
        <button
          onClick={() => (prev.onClick ? prev.onClick() : navigate(prev.path))}
          className="text-sm text-zinc-600 dark:text-zinc-300 hover:text-stone-900 dark:hover:text-zinc-100 transition-colors flex items-center gap-1"
        >
          &larr; {prev.label}
        </button>
      ) : <div />}
      {next && (
        <button
          onClick={() => (next.onClick ? next.onClick() : navigate(next.path))}
          className="ml-auto px-5 py-2 bg-brand hover:bg-brand-400 text-black text-sm font-semibold rounded-lg transition-colors flex items-center gap-2"
        >
          {next.label} &rarr;
        </button>
      )}
    </div>
  );
}
