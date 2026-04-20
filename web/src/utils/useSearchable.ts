/**
 * Client-side full-text search with optional field-prefix syntax.
 *
 * Usage:
 *   const matches = useSearchable(items, item => ({
 *     agent: item.agent_id,
 *     tool: item.action,
 *     constraint: item.constraint_name,
 *     _all: [item.agent_id, item.action, item.constraint_name].join(' '),
 *   }), query);
 *
 * Query syntax:
 *   "blocked refund"            → all tokens must appear somewhere in _all
 *   "agent:customer_bot refund" → 'customer_bot' must appear in .agent,
 *                                 'refund' must appear in _all
 *   "agent:customer_bot -pass"  → '-' prefix excludes
 *
 * Case-insensitive. Whitespace-tokenized. No fuzzy, no stemming — deliberately
 * simple so the evaluation fits in a useMemo with no dependencies.
 */

import { useMemo } from 'react';

export type SearchableFields = Record<string, string | undefined | null> & { _all?: string };

interface ParsedToken {
  field: string;         // '_all' by default
  value: string;
  negate: boolean;
}

function parseQuery(query: string): ParsedToken[] {
  const out: ParsedToken[] = [];
  const parts = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  for (const p of parts) {
    const negate = p.startsWith('-');
    const raw = negate ? p.slice(1) : p;
    if (!raw) continue;
    const colon = raw.indexOf(':');
    if (colon > 0) {
      const field = raw.slice(0, colon);
      const value = raw.slice(colon + 1);
      if (value) out.push({ field, value, negate });
    } else {
      out.push({ field: '_all', value: raw, negate });
    }
  }
  return out;
}

function matches(fields: SearchableFields, tokens: ParsedToken[]): boolean {
  for (const t of tokens) {
    const haystack = (fields[t.field] ?? '').toLowerCase();
    const hit = haystack.includes(t.value);
    if (t.negate ? hit : !hit) return false;
  }
  return true;
}

export function useSearchable<T>(
  items: T[],
  getFields: (item: T) => SearchableFields,
  query: string,
): T[] {
  return useMemo(() => {
    if (!query.trim()) return items;
    const tokens = parseQuery(query);
    if (tokens.length === 0) return items;
    return items.filter(item => matches(getFields(item), tokens));
    // getFields is intentionally left out of deps; callers should pass a
    // stable callback (or accept the extra work if they don't).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, query]);
}
