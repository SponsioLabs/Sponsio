import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

const SHORTCUTS = [
  { key: '1', label: 'Scan', path: '/scan' },
  { key: '2', label: 'Rulebook', path: '/rulebook' },
  { key: '3', label: 'Integrate', path: '/integrate' },
  { key: '4', label: 'Monitor', path: '/monitor' },
  { key: 'p', label: 'Playground', path: '/playground' },
];

export function useKeyboardShortcuts() {
  const navigate = useNavigate();

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Don't trigger when typing in inputs
      const target = e.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) return;

      // Cmd/Ctrl+K for help overlay is handled by HelpOverlay
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('sponsio:toggle-help'));
        return;
      }

      // Number shortcuts for pipeline pages
      const match = SHORTCUTS.find(s => s.key === e.key);
      if (match && !e.metaKey && !e.ctrlKey && !e.altKey) {
        navigate(match.path);
      }
    };

    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [navigate]);
}

export default function HelpOverlay() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const toggle = () => setOpen(o => !o);
    window.addEventListener('sponsio:toggle-help', toggle);

    const keyHandler = (e: KeyboardEvent) => {
      if (e.key === '?' && !(e.target as HTMLElement).tagName.match(/INPUT|TEXTAREA/)) {
        setOpen(o => !o);
      }
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', keyHandler);
    return () => {
      window.removeEventListener('sponsio:toggle-help', toggle);
      window.removeEventListener('keydown', keyHandler);
    };
  }, []);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setOpen(false)}>
      <div
        className="bg-white dark:bg-surface-900 rounded-2xl border border-surface-200 dark:border-surface-800 shadow-2xl p-6 w-[380px] max-w-[90vw]"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-stone-900 dark:text-white">Keyboard Shortcuts</h2>
          <button onClick={() => setOpen(false)} className="text-zinc-600 dark:text-zinc-300 hover:text-stone-900 dark:hover:text-white transition-colors">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="space-y-1.5">
          <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium mb-2">Navigation</p>
          {SHORTCUTS.map(s => (
            <div key={s.key} className="flex items-center justify-between py-1">
              <span className="text-xs text-zinc-600 dark:text-zinc-300">{s.label}</span>
              <kbd className="px-2 py-0.5 text-[10px] font-mono bg-surface-100 dark:bg-surface-800 border border-surface-200 dark:border-surface-700 rounded text-stone-900 dark:text-zinc-100">{s.key}</kbd>
            </div>
          ))}
          <div className="pt-2 mt-2 border-t border-surface-200 dark:border-surface-800">
            <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium mb-2">General</p>
            <div className="flex items-center justify-between py-1">
              <span className="text-xs text-zinc-600 dark:text-zinc-300">Show this help</span>
              <kbd className="px-2 py-0.5 text-[10px] font-mono bg-surface-100 dark:bg-surface-800 border border-surface-200 dark:border-surface-700 rounded text-stone-900 dark:text-zinc-100">?</kbd>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
