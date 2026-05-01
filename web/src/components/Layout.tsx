import { useEffect, useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';

import { useApp } from '../context/AppContext';

const NAV_LINKS = [
  { to: '/monitor', label: 'Monitor', step: 1 },
  { to: '/rulebook', label: 'Contracts', step: 2 },
];

function ThemeToggle() {
  const [dark, setDark] = useState(() =>
    typeof document !== 'undefined' &&
    document.documentElement.classList.contains('dark'),
  );

  useEffect(() => {
    const saved = localStorage.getItem('sponsio.theme');
    const prefers =
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-color-scheme: dark)').matches;
    const initial = saved ? saved === 'dark' : prefers;
    document.documentElement.classList.toggle('dark', initial);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-shot mount sync from localStorage/media query
    setDark(initial);
  }, []);

  function toggle() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle('dark', next);
    localStorage.setItem('sponsio.theme', next ? 'dark' : 'light');
  }

  return (
    <button
      onClick={toggle}
      className="text-xs px-2 py-1 rounded border border-surface-200 dark:border-surface-700 text-muted hover:text-stone-900 dark:hover:text-white transition-colors"
      aria-label="Toggle theme"
    >
      {dark ? '☼' : '☾'}
    </button>
  );
}

export default function Layout() {
  const { capabilities, capabilitiesError } = useApp();
  const tier = capabilities?.tier ?? '…';
  const version = capabilities?.version ?? '';

  return (
    <div className="min-h-screen flex bg-white dark:bg-surface-950 text-stone-900 dark:text-zinc-100">
      <aside className="w-56 shrink-0 border-r border-surface-200 dark:border-surface-800 flex flex-col">
        <div className="px-5 py-5 border-b border-surface-200 dark:border-surface-800">
          <div className="font-display text-xl">Sponsio</div>
          <div className="text-[10px] uppercase tracking-widest text-muted mt-0.5">
            {tier === 'oss' ? 'Local · Single User' : tier}
          </div>
        </div>

        <nav className="px-3 py-4 flex-1">
          <div className="text-[10px] uppercase tracking-widest text-muted px-3 mb-2">
            Pipeline
          </div>
          <ul className="space-y-1">
            {NAV_LINKS.map((link) => (
              <li key={link.to}>
                <NavLink
                  to={link.to}
                  className={({ isActive }) =>
                    `flex items-center gap-3 px-3 py-2 rounded text-sm transition-colors ${
                      isActive
                        ? 'bg-brand/10 text-brand-700 dark:text-brand'
                        : 'text-muted hover:bg-surface-100 dark:hover:bg-surface-900 hover:text-stone-900 dark:hover:text-white'
                    }`
                  }
                >
                  <span className="text-[10px] font-mono opacity-50 w-3">
                    {link.step}
                  </span>
                  <span>{link.label}</span>
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        <div className="px-5 py-3 border-t border-surface-200 dark:border-surface-800 text-[10px] text-muted flex items-center justify-between">
          <span className="font-mono truncate">v{version || '—'}</span>
          <ThemeToggle />
        </div>
      </aside>

      <main className="flex-1 min-w-0 overflow-hidden">
        {capabilitiesError ? (
          <div className="p-6 text-sm text-red-500">
            Could not reach the local Sponsio backend at <code>/api</code>:{' '}
            {capabilitiesError}
          </div>
        ) : (
          <Outlet />
        )}
      </main>
    </div>
  );
}
