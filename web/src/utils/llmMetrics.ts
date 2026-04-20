/**
 * Extract LLM metrics from a SpanNode's attributes (llm.* namespace).
 *
 * The backend contract for these keys lives in:
 *   claude_cowork/BACKEND_FIELDS.md §1
 *
 * Walks the span and all children. Aggregates tokens/cost across nested spans.
 * Returns null if no llm.* attributes are found anywhere in the tree.
 *
 * Everything is defensive: any field may be missing and the rest still work.
 */

import type { LlmMetrics, SpanNode, SpanError } from '../types';

type Attrs = Record<string, unknown>;

function num(v: unknown): number | undefined {
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  return undefined;
}

function str(v: unknown): string | undefined {
  if (typeof v === 'string') return v;
  return undefined;
}

function bool(v: unknown): boolean | undefined {
  if (typeof v === 'boolean') return v;
  return undefined;
}

function readSpanLlm(attrs: Attrs | undefined): Partial<LlmMetrics> {
  if (!attrs) return {};
  return {
    model: str(attrs['llm.model']),
    provider: str(attrs['llm.provider']),
    promptTokens: num(attrs['llm.prompt_tokens']),
    completionTokens: num(attrs['llm.completion_tokens']),
    totalTokens: num(attrs['llm.total_tokens']),
    costUsd: num(attrs['llm.cost_usd']),
    ttftMs: num(attrs['llm.ttft_ms']),
    temperature: num(attrs['llm.temperature']),
    cached: bool(attrs['llm.cached']),
    prompt: str(attrs['llm.prompt']),
    completion: str(attrs['llm.completion']),
    completionAfterRetry: str(attrs['llm.completion_after_retry']),
  };
}

function hasAnyLlm(m: Partial<LlmMetrics>): boolean {
  return (
    m.model !== undefined ||
    m.promptTokens !== undefined ||
    m.completionTokens !== undefined ||
    m.totalTokens !== undefined ||
    m.costUsd !== undefined
  );
}

export function extractLlmMetrics(span: SpanNode): LlmMetrics | null {
  const own = readSpanLlm(span.attributes);
  const childAggregates: LlmMetrics[] = [];
  for (const c of span.children ?? []) {
    const child = extractLlmMetrics(c);
    if (child) childAggregates.push(child);
  }

  if (!hasAnyLlm(own) && childAggregates.length === 0) return null;

  // Sum numeric fields across self + children.
  const sumField = (k: keyof LlmMetrics): number | undefined => {
    let total = 0;
    let any = false;
    const ownVal = own[k];
    if (typeof ownVal === 'number') {
      total += ownVal;
      any = true;
    }
    for (const c of childAggregates) {
      const v = c[k];
      if (typeof v === 'number') {
        total += v;
        any = true;
      }
    }
    return any ? total : undefined;
  };

  // For non-aggregated fields (model, prompt text) prefer self, fall back to first child.
  const pickString = (k: keyof LlmMetrics): string | undefined => {
    const o = own[k];
    if (typeof o === 'string') return o;
    for (const c of childAggregates) {
      const v = c[k];
      if (typeof v === 'string') return v;
    }
    return undefined;
  };

  const pickBool = (k: keyof LlmMetrics): boolean | undefined => {
    const o = own[k];
    if (typeof o === 'boolean') return o;
    for (const c of childAggregates) {
      const v = c[k];
      if (typeof v === 'boolean') return v;
    }
    return undefined;
  };

  return {
    model: pickString('model'),
    provider: pickString('provider'),
    promptTokens: sumField('promptTokens'),
    completionTokens: sumField('completionTokens'),
    totalTokens: sumField('totalTokens') ?? ((sumField('promptTokens') ?? 0) + (sumField('completionTokens') ?? 0) || undefined),
    costUsd: sumField('costUsd'),
    ttftMs: num(own.ttftMs) ?? childAggregates.find(c => c.ttftMs !== undefined)?.ttftMs,
    temperature: num(own.temperature) ?? childAggregates.find(c => c.temperature !== undefined)?.temperature,
    cached: pickBool('cached'),
    prompt: pickString('prompt'),
    completion: pickString('completion'),
    completionAfterRetry: pickString('completionAfterRetry'),
  };
}

/** Sum cost/tokens across a list of top-level spans. */
export function aggregateLlmMetrics(spans: SpanNode[]): LlmMetrics {
  const acc: LlmMetrics = {
    promptTokens: 0,
    completionTokens: 0,
    totalTokens: 0,
    costUsd: 0,
  };
  let anyData = false;
  for (const s of spans) {
    const m = extractLlmMetrics(s);
    if (!m) continue;
    anyData = true;
    acc.promptTokens = (acc.promptTokens ?? 0) + (m.promptTokens ?? 0);
    acc.completionTokens = (acc.completionTokens ?? 0) + (m.completionTokens ?? 0);
    acc.totalTokens = (acc.totalTokens ?? 0) + (m.totalTokens ?? 0);
    acc.costUsd = (acc.costUsd ?? 0) + (m.costUsd ?? 0);
  }
  if (!anyData) return {};
  // Round cost for display.
  if (acc.costUsd !== undefined) acc.costUsd = Number(acc.costUsd.toFixed(4));
  return acc;
}

export function extractSpanError(span: SpanNode): SpanError | null {
  if (span.status !== 'error') return null;
  const attrs = span.attributes ?? {};
  const t = str(attrs['error.type']);
  const m = str(attrs['error.message']);
  if (!t && !m) return { type: 'UnknownError' };
  return { type: t, message: m };
}

export function extractSessionId(span: SpanNode): string | undefined {
  const own = str(span.attributes?.['session.id']);
  if (own) return own;
  for (const c of span.children ?? []) {
    const sub = extractSessionId(c);
    if (sub) return sub;
  }
  return undefined;
}

export function formatCost(usd: number | undefined): string {
  if (usd === undefined || usd === null) return '—';
  if (usd < 0.01) return `$${(usd * 100).toFixed(2)}¢`.replace('$', '');
  if (usd < 1) return `$${usd.toFixed(3)}`;
  return `$${usd.toFixed(2)}`;
}

export function formatTokens(n: number | undefined): string {
  if (n === undefined || n === null) return '—';
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}
