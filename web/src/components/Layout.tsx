import { useState, useEffect } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';

const PIPELINE_PATHS = ['/scan', '/rulebook', '/integrate', '/monitor'] as const;

const sections = [
  {
    label: 'Pipeline',
    links: [
      {
        to: '/scan',
        step: 1,
        label: 'Scan',
        icon: (
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
        ),
      },
      {
        to: '/rulebook',
        step: 2,
        label: 'Contract Library',
        icon: (
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
              d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
          </svg>
        ),
      },
      {
        to: '/integrate',
        step: 3,
        label: 'Integrate',
        icon: (
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
              d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
          </svg>
        ),
      },
      {
        to: '/monitor',
        step: 4,
        label: 'Monitor',
        icon: (
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
              d="M13 10V3L4 14h7v7l9-11h-7z" />
          </svg>
        ),
      },
    ],
  },
  {
    label: 'More',
    links: [
      {
        to: '/playground',
        label: 'Playground',
        icon: (
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
              d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
              d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        ),
      },
    ],
  },
];

export default function Layout() {
  const [dark, setDark] = useState(() => {
    if (typeof window !== 'undefined') {
      const stored = localStorage.getItem('theme');
      if (stored) return stored === 'dark';
      return true;
    }
    return true;
  });

  const [collapsed, setCollapsed] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark);
    localStorage.setItem('theme', dark ? 'dark' : 'light');
  }, [dark]);

  useEffect(() => {
    queueMicrotask(() => {
      setSidebarOpen(false);
    });
  }, [location.pathname]);

  const sidebarWidth = collapsed ? 'w-16' : 'w-56';

  // Determine pipeline progress
  const currentPipelineIdx = PIPELINE_PATHS.indexOf(location.pathname as typeof PIPELINE_PATHS[number]);

  const navLinkClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] transition-all duration-150 ${
      isActive
        ? 'bg-surface-100 dark:bg-surface-800 text-stone-900 dark:text-white font-medium'
        : 'text-zinc-600 dark:text-zinc-300 hover:bg-surface-100 dark:hover:bg-surface-800 hover:text-zinc-700 dark:hover:text-zinc-600 dark:text-zinc-300'
    } ${collapsed ? 'justify-center' : ''}`;

  const sidebar = (
    <nav className={`${sidebarWidth} shrink-0 border-r border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-950 flex flex-col h-full transition-all duration-200`}>
      {/* Brand */}
      <div className="px-4 py-4 border-b border-surface-200 dark:border-surface-800">
        <div className="flex items-center">
          <img src={dark ? '/logo.svg' : '/logo-light.svg'} alt="Sponsio" className={collapsed ? 'h-6 w-auto' : 'h-8 w-auto'} />
        </div>
      </div>

      {/* Step progress indicator */}
      {!collapsed && (
        <div className="px-4 py-3 border-b border-surface-200 dark:border-surface-800">
          <div className="flex items-center gap-1">
            {PIPELINE_PATHS.map((path, i) => {
              const isActive = location.pathname === path;
              const isPast = currentPipelineIdx > i;
              return (
                <div key={path} className="flex items-center gap-1 flex-1">
                  <div className={`w-2 h-2 rounded-full shrink-0 ${
                    isActive ? 'bg-brand' : isPast ? 'bg-emerald-500' : 'bg-surface-700'
                  }`} />
                  {i < PIPELINE_PATHS.length - 1 && (
                    <div className={`flex-1 h-px ${isPast ? 'bg-emerald-500' : 'bg-surface-700'}`} />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Navigation */}
      <div className="flex-1 py-3 px-2 overflow-y-auto">
        {sections.map((section, si) => (
          <div key={si} className={si > 0 ? 'mt-3' : ''}>
            {section.label && !collapsed && (
              si === 0 ? (
                <p className="px-3 mb-1.5 text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-semibold">{section.label}</p>
              ) : (
                <div className="px-2 mb-1.5 mt-4"><div className="w-full h-px bg-surface-200 dark:bg-surface-800" /></div>
              )
            )}
            {section.label && collapsed && (
              <div className="px-2 mb-1.5"><div className="w-full h-px bg-surface-200 dark:bg-surface-800" /></div>
            )}
            <ul className="space-y-0.5">
              {section.links.map((l) => (
                <li key={l.to}>
                  <NavLink
                    to={l.to}
                    end={l.to === '/'}
                    onClick={() => setSidebarOpen(false)}
                    className={navLinkClass}
                    title={collapsed ? l.label : undefined}
                  >
                    <span className="shrink-0">{l.icon}</span>
                    {!collapsed && (
                      <>
                        {'step' in l && (
                          <span className="text-zinc-600 dark:text-zinc-300 font-mono text-[11px]">{l.step}.</span>
                        )}
                        <span className={'step' in l ? '' : 'text-zinc-600 dark:text-zinc-300'}>{l.label}</span>
                      </>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      {/* External link */}
      <div className="px-2 pb-2">
        {!collapsed && (
          <a
            href="https://sponsio.dev/"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] text-zinc-600 dark:text-zinc-300 hover:bg-surface-100 dark:hover:bg-surface-800 hover:text-zinc-600 dark:text-zinc-300 transition-all duration-150"
          >
            <svg className="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
                d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
            </svg>
            Website
          </a>
        )}
      </div>

      {/* Footer */}
      <div className="p-3 border-t border-surface-200 dark:border-surface-800 flex items-center gap-2">
        {!collapsed && (
          <span className="text-[10px] text-zinc-600 dark:text-zinc-300 font-mono flex-1">v0.1.0-alpha</span>
        )}
        <button
          onClick={() => setDark(!dark)}
          className="p-1.5 rounded-lg text-zinc-600 dark:text-zinc-300 hover:text-zinc-600 dark:text-zinc-300 hover:bg-surface-100 dark:hover:bg-surface-800 transition-colors"
          title={dark ? 'Light mode' : 'Dark mode'}
        >
          {dark ? (
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" /></svg>
          ) : (
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" /></svg>
          )}
        </button>
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="p-1.5 rounded-lg text-zinc-600 dark:text-zinc-300 hover:text-zinc-600 dark:text-zinc-300 hover:bg-surface-100 dark:hover:bg-surface-800 transition-colors hidden md:block"
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? (
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" /></svg>
          ) : (
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" /></svg>
          )}
        </button>
      </div>
    </nav>
  );

  return (
    <div className="flex h-screen bg-surface-50 dark:bg-surface-950 text-stone-900 dark:text-zinc-200 overflow-hidden">
      {/* Mobile hamburger */}
      <button
        onClick={() => setSidebarOpen(!sidebarOpen)}
        className="fixed top-4 left-4 z-50 md:hidden p-2 rounded-lg bg-white dark:bg-surface-900 text-stone-900 dark:text-white shadow-lg border border-surface-200 dark:border-surface-800"
        aria-label="Toggle navigation"
      >
        {sidebarOpen ? (
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        ) : (
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        )}
      </button>

      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/60 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <div className={`fixed inset-y-0 left-0 z-40 md:relative md:z-auto transition-transform duration-200 ${
        sidebarOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'
      }`}>
        {sidebar}
      </div>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto">
        <div className="p-4 md:p-6 lg:p-8 pt-16 md:pt-6 lg:pt-8 max-w-[1400px]">
          <Outlet key={location.pathname} />
        </div>
      </main>
    </div>
  );
}
