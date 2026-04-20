import { useState, useEffect, useMemo } from 'react';
import { getPatternLibrary } from '../api/client';
import type { PatternDef } from '../types';

interface PatternPickerProps {
  onAdd: (exampleNl: string) => void;
}

export default function PatternPicker({ onAdd }: PatternPickerProps) {
  const [patterns, setPatterns] = useState<PatternDef[]>([]);
  const [search, setSearch] = useState('');
  const [collapsed, setCollapsed] = useState(true);
  const [collapsedCategories, setCollapsedCategories] = useState<Set<string>>(new Set());

  useEffect(() => {
    getPatternLibrary().then(setPatterns).catch(() => {});
  }, []);

  const filtered = useMemo(() => {
    if (!search.trim()) return patterns;
    const q = search.toLowerCase();
    return patterns.filter(
      p => p.name.toLowerCase().includes(q) || p.description.toLowerCase().includes(q)
    );
  }, [patterns, search]);

  const grouped = useMemo(() => {
    const map = new Map<string, PatternDef[]>();
    for (const p of filtered) {
      const list = map.get(p.category) ?? [];
      list.push(p);
      map.set(p.category, list);
    }
    return map;
  }, [filtered]);

  const toggleCategory = (cat: string) => {
    setCollapsedCategories(prev => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  };

  if (collapsed) {
    return (
      <button
        onClick={() => setCollapsed(false)}
        className="text-xs text-muted hover:text-stone-900 dark:hover:text-zinc-100 transition-colors mt-2"
      >
        + Browse pattern library ({patterns.length} patterns)
      </button>
    );
  }

  return (
    <div className="mt-3 rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 overflow-hidden">
      <div className="px-3 py-2 border-b border-surface-100 dark:border-surface-800 flex items-center justify-between">
        <span className="text-xs text-muted">Or pick from library:</span>
        <button
          onClick={() => setCollapsed(true)}
          className="text-xs text-muted hover:text-stone-900 dark:hover:text-white"
        >
          Hide
        </button>
      </div>

      <div className="px-3 py-2 border-b border-surface-100 dark:border-surface-800">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search patterns..."
          className="w-full text-sm bg-transparent text-zinc-700 dark:text-zinc-300 placeholder-muted focus:outline-none"
        />
      </div>

      <div className="max-h-64 overflow-y-auto">
        {Array.from(grouped.entries()).map(([category, pats]) => (
          <div key={category}>
            <button
              onClick={() => toggleCategory(category)}
              className="w-full px-3 py-1.5 text-left flex items-center justify-between bg-surface-50 dark:bg-surface-800/50"
            >
              <span className="text-xs text-muted uppercase tracking-wider font-medium">{category}</span>
              <span className="text-xs text-zinc-600 dark:text-zinc-300">{collapsedCategories.has(category) ? '\u25B6' : '\u25BC'}</span>
            </button>
            {!collapsedCategories.has(category) && pats.map((p) => (
              <div
                key={p.name}
                className="flex items-center justify-between px-3 py-2 hover:bg-surface-50 dark:hover:bg-surface-800"
              >
                <div className="min-w-0 flex-1">
                  <span className="text-sm font-mono text-violet-400">{p.name}</span>
                  <span className="text-xs text-muted ml-2">{p.description}</span>
                </div>
                <button
                  onClick={() => onAdd(p.example_nl)}
                  className="text-xs text-emerald-400 bg-emerald-500/10 hover:bg-emerald-500/20 border border-surface-200 dark:border-surface-800 rounded px-2 py-0.5 ml-2 shrink-0 transition-colors"
                >
                  + Add
                </button>
              </div>
            ))}
          </div>
        ))}
        {grouped.size === 0 && (
          <p className="px-3 py-4 text-sm text-muted text-center">No patterns match "{search}"</p>
        )}
      </div>
    </div>
  );
}
