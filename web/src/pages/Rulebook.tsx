/**
 * Contracts — pattern catalog + sponsio.yaml viewer.
 *
 * Two stacked sections:
 *   1. The deterministic pattern library (introspected from
 *      sponsio.patterns.library on the backend).
 *   2. The current sponsio.yaml (CWD or SPONSIO_CONFIG override) if
 *      one is found, rendered as a JSON-ish list.
 *
 * No write operations — editing contracts in OSS is a code task. The
 * cloud variant adds an editor, validation, and per-agent assignment.
 */

import { useEffect, useMemo, useState } from 'react';

import { getContracts } from '../api/client';
import Spinner from '../components/Spinner';
import type { ContractsResponse, PatternDef } from '../types';

function PatternRow({ pattern }: { pattern: PatternDef }) {
  return (
    <div className="border-b border-surface-100 dark:border-surface-900 px-4 py-3 hover:bg-surface-50 dark:hover:bg-surface-900/50">
      <div className="flex items-baseline gap-3">
        <code className="font-mono text-sm text-stone-900 dark:text-white">
          {pattern.name}
        </code>
        <span className="text-[9px] uppercase tracking-widest text-blue-500">
          {pattern.kind}
        </span>
      </div>
      <p className="text-xs text-muted mt-1">{pattern.summary || '—'}</p>
      {pattern.params.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {pattern.params.map((p) => (
            <code
              key={p}
              className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-surface-100 dark:bg-surface-800 text-muted"
            >
              {p}
            </code>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Rulebook() {
  const [data, setData] = useState<ContractsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    let cancelled = false;
    getContracts()
      .then((r) => !cancelled && setData(r))
      .catch((err: Error) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    if (!data) return [];
    const q = filter.trim().toLowerCase();
    if (!q) return data.patterns;
    return data.patterns.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        p.summary.toLowerCase().includes(q) ||
        p.params.some((param) => param.toLowerCase().includes(q)),
    );
  }, [data, filter]);

  if (error) {
    return (
      <div className="px-6 py-6 text-sm text-red-500">
        Could not load contracts: {error}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spinner size="lg" />
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col">
      <header className="border-b border-surface-200 dark:border-surface-800 px-6 py-4">
        <h1 className="text-lg font-display">Contracts</h1>
        <p className="text-xs text-muted mt-0.5">
          {data.patterns.length} deterministic patterns available
          {data.yaml ? ` · sponsio.yaml @ ${data.yaml.path}` : ' · no sponsio.yaml in CWD'}
        </p>
      </header>

      <div className="grid grid-cols-[1fr_22rem] flex-1 min-h-0">
        {/* Pattern catalog */}
        <section className="flex flex-col min-h-0 border-r border-surface-200 dark:border-surface-800">
          <div className="px-4 py-3 border-b border-surface-200 dark:border-surface-800 flex items-center gap-3">
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter by name, params, or summary…"
              className="flex-1 bg-transparent border-b border-surface-200 dark:border-surface-700 px-2 py-1 text-sm focus:outline-none focus:border-brand placeholder:text-muted"
            />
            <span className="text-[10px] text-muted font-mono">
              {filtered.length} / {data.patterns.length}
            </span>
          </div>
          <div className="flex-1 overflow-y-auto">
            {filtered.length === 0 ? (
              <div className="px-4 py-10 text-sm text-muted">
                No patterns match "{filter}".
              </div>
            ) : (
              filtered.map((p) => <PatternRow key={p.name} pattern={p} />)
            )}
          </div>
        </section>

        {/* Loaded sponsio.yaml */}
        <aside className="flex flex-col min-h-0 overflow-y-auto">
          <div className="px-4 py-3 border-b border-surface-200 dark:border-surface-800">
            <div className="text-[10px] uppercase tracking-widest text-muted">
              sponsio.yaml
            </div>
            <div className="text-sm font-medium mt-0.5 truncate">
              {data.yaml ? data.yaml.path : 'not found'}
            </div>
          </div>
          {!data.yaml && (
            <div className="px-4 py-6 text-xs text-muted">
              Drop a <code>sponsio.yaml</code> in the working directory (or
              point <code>SPONSIO_CONFIG</code> at one) and refresh.
            </div>
          )}
          {data.yaml?.error && (
            <div className="px-4 py-3 text-xs text-red-500">
              Parse error: {data.yaml.error}
            </div>
          )}
          {data.yaml && data.yaml.contracts.length === 0 && !data.yaml.error && (
            <div className="px-4 py-6 text-xs text-muted">
              The file loaded but declares no contracts.
            </div>
          )}
          {data.yaml && data.yaml.contracts.length > 0 && (
            <ul>
              {data.yaml.contracts.map((c, i) => (
                <li
                  key={i}
                  className="border-b border-surface-100 dark:border-surface-900 px-4 py-3"
                >
                  <pre className="text-[11px] font-mono text-muted whitespace-pre-wrap break-words">
                    {JSON.stringify(c, null, 2)}
                  </pre>
                </li>
              ))}
            </ul>
          )}
        </aside>
      </div>
    </div>
  );
}
