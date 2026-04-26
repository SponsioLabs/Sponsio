/**
 * Client-side aggregation + anomaly detection over monitor data.
 *
 * For P0/P1 the frontend computes these directly from the in-memory monitor
 * log / span list. When the backend adds dedicated endpoints (see
 * claude_cowork/BACKEND_FIELDS.md §6, §7), swap the source — the consumer
 * components don't need to change.
 */

import type {
  MonitorEvent, RegressionFinding, SpanNode, Slo, SloStatus,
} from '../types';

// ─── Pipeline normalization ──────────────────────────────────────────────────
//
// The backend may emit `pipeline` as either the Sponsio-native "det"/"sto"
// or the legacy "hard"/"soft". Always normalize at the boundary — comparisons
// downstream should use the normalized form only.
export function normalizePipeline(p: string | undefined | null): 'hard' | 'soft' {
  if (!p) return 'hard';
  const lower = p.toLowerCase();
  if (lower === 'det' || lower === 'hard') return 'hard';
  return 'soft';
}

// ─── Pass-rate denominator ──────────────────────────────────────────────────
//
// Not every MonitorEvent is a "triggered" check. `must_precede` and similar
// patterns emit `pass` events even when the precondition hasn't fired yet —
// those shouldn't inflate the Pass Rate numerator/denominator.
//
// We exclude events whose result_message looks like a "not triggered" report.
// When the backend adds a first-class `triggered: boolean` field, swap to
// `ev.triggered !== false`.
export function isEnforcementCheck(ev: MonitorEvent): boolean {
  const msg = (ev.result_message ?? '').toLowerCase();
  if (msg.includes('precondition not yet triggered')) return false;
  if (msg.includes('not triggered')) return false;
  return true;
}

// ─── Percentiles ─────────────────────────────────────────────────────────────

export function percentile(sortedAsc: number[], p: number): number {
  if (sortedAsc.length === 0) return 0;
  if (sortedAsc.length === 1) return sortedAsc[0];
  const idx = (p / 100) * (sortedAsc.length - 1);
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  if (lo === hi) return sortedAsc[lo];
  const w = idx - lo;
  return sortedAsc[lo] * (1 - w) + sortedAsc[hi] * w;
}

export function latencyPercentiles(spans: SpanNode[]): { p50: number; p95: number; p99: number; count: number } {
  const durations: number[] = [];
  const walk = (s: SpanNode) => {
    if (s.duration_ms != null && s.span_type === 'sponsio.agent_turn') {
      durations.push(s.duration_ms);
    }
    s.children?.forEach(walk);
  };
  spans.forEach(walk);
  durations.sort((a, b) => a - b);
  return {
    p50: percentile(durations, 50),
    p95: percentile(durations, 95),
    p99: percentile(durations, 99),
    count: durations.length,
  };
}

// ─── Violation grouping (P0) ────────────────────────────────────────────────

export interface ViolationGroup {
  key: string;
  constraintName: string;
  agentId: string;
  pipeline: 'hard' | 'soft';
  events: MonitorEvent[];
  count: number;       // non-pass events
  passedCount: number;
  blockedCount: number;
  retryingCount: number;
  escalatedCount: number;
  firstSeen?: number;  // unix seconds
  lastSeen?: number;
  severity: 'low' | 'medium' | 'high' | 'critical';
  // Data lineage — unique source/target pairs from this group's events.
  flows: { source?: string | null; target?: string | null }[];
}

const SEVERITY_ORDER: Record<'low' | 'medium' | 'high' | 'critical', number> = {
  low: 0, medium: 1, high: 2, critical: 3,
};

export function groupViolations(events: MonitorEvent[]): ViolationGroup[] {
  const map = new Map<string, ViolationGroup>();
  for (const ev of events) {
    if (!ev.constraint_name) continue;
    const pipeline = normalizePipeline(ev.pipeline);
    const key = `${pipeline}::${ev.constraint_name}::${ev.agent_id}`;
    const existing = map.get(key);
    const sev = ev.severity ?? 'medium';
    const flow = { source: ev.source ?? null, target: ev.target ?? null };
    if (existing) {
      existing.events.push(ev);
      if (ev.result_action === 'pass') existing.passedCount++;
      else {
        existing.count++;
        if (ev.result_action === 'blocked' || ev.result_action === 'block') existing.blockedCount++;
        else if (ev.result_action === 'retrying' || ev.result_action === 'retry') existing.retryingCount++;
        else if (ev.result_action === 'escalated') existing.escalatedCount++;
      }
      if (ev.ts !== undefined) {
        if (existing.firstSeen === undefined || ev.ts < existing.firstSeen) existing.firstSeen = ev.ts;
        if (existing.lastSeen === undefined || ev.ts > existing.lastSeen) existing.lastSeen = ev.ts;
      }
      if (SEVERITY_ORDER[sev] > SEVERITY_ORDER[existing.severity]) existing.severity = sev;
      // Dedupe flows
      if (flow.source || flow.target) {
        const already = existing.flows.some(f => f.source === flow.source && f.target === flow.target);
        if (!already) existing.flows.push(flow);
      }
    } else {
      const isPass = ev.result_action === 'pass';
      map.set(key, {
        key,
        constraintName: ev.constraint_name,
        agentId: ev.agent_id,
        pipeline,
        events: [ev],
        count: isPass ? 0 : 1,
        passedCount: isPass ? 1 : 0,
        blockedCount: (ev.result_action === 'blocked' || ev.result_action === 'block') ? 1 : 0,
        retryingCount: (ev.result_action === 'retrying' || ev.result_action === 'retry') ? 1 : 0,
        escalatedCount: ev.result_action === 'escalated' ? 1 : 0,
        firstSeen: ev.ts,
        lastSeen: ev.ts,
        severity: sev,
        flows: (flow.source || flow.target) ? [flow] : [],
      });
    }
  }
  return Array.from(map.values())
    .filter(g => g.count > 0)
    .sort((a, b) => {
      const sevDiff = SEVERITY_ORDER[b.severity] - SEVERITY_ORDER[a.severity];
      if (sevDiff !== 0) return sevDiff;
      return b.count - a.count;
    });
}

// ─── Suppression matching ────────────────────────────────────────────────────
//
// Apply the user's localStorage suppressions to a stream of monitor events.
// An event is suppressed iff a suppression matches its (constraint, agent) or
// constraint-only (agent optional on the suppression side).
export interface SuppressionRecord {
  id: string;
  constraintName: string;
  agentId?: string;
  until: number;
  createdAt: number;
}

export function eventIsSuppressed(
  ev: MonitorEvent,
  suppressions: SuppressionRecord[],
): boolean {
  const now = Date.now();
  for (const s of suppressions) {
    if (s.until <= now) continue;
    if (s.constraintName !== ev.constraint_name) continue;
    if (s.agentId && s.agentId !== ev.agent_id) continue;
    return true;
  }
  return false;
}

export function filterSuppressed(
  events: MonitorEvent[],
  suppressions: SuppressionRecord[],
): MonitorEvent[] {
  if (suppressions.length === 0) return events;
  return events.filter(e => !eventIsSuppressed(e, suppressions));
}

// ─── Regression detection (client-side z-score) ─────────────────────────────

/**
 * Split events by agent, compute rolling pass rate, flag regressions where
 * recent window's pass rate is significantly below the baseline.
 *
 * Uses a simple two-sample proportion test:
 *   z = (p_recent - p_baseline) / sqrt(p_pool * (1-p_pool) * (1/n1 + 1/n2))
 * with a threshold of |z| >= 2 (≈95% confidence) and minimum sample size.
 */
export function detectRegressions(events: MonitorEvent[], recentFraction = 0.25): RegressionFinding[] {
  if (events.length < 20) return [];

  const byAgent = new Map<string, MonitorEvent[]>();
  for (const e of events) {
    const list = byAgent.get(e.agent_id) ?? [];
    list.push(e);
    byAgent.set(e.agent_id, list);
  }

  const findings: RegressionFinding[] = [];
  for (const [agentId, list] of byAgent) {
    if (list.length < 10) continue;
    const sorted = [...list].sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0));
    const cut = Math.max(5, Math.floor(sorted.length * recentFraction));
    const baseline = sorted.slice(0, sorted.length - cut);
    const recent = sorted.slice(sorted.length - cut);

    const passRate = (arr: MonitorEvent[]) =>
      arr.length === 0 ? 1 : arr.filter(x => x.result_action === 'pass').length / arr.length;

    const pB = passRate(baseline);
    const pR = passRate(recent);
    const n1 = baseline.length;
    const n2 = recent.length;
    if (n1 < 5 || n2 < 5) continue;

    const pPool = (baseline.filter(x => x.result_action === 'pass').length +
                   recent.filter(x => x.result_action === 'pass').length) / (n1 + n2);
    const se = Math.sqrt(pPool * (1 - pPool) * (1 / n1 + 1 / n2));
    const z = se === 0 ? 0 : (pR - pB) / se;

    if (z <= -1.5 && pR < pB - 0.05) {
      findings.push({
        agentId,
        currentPassRate: pR,
        baselinePassRate: pB,
        zScore: z,
        sampleSize: n2,
        detectedAt: Date.now(),
      });
    }
  }
  findings.sort((a, b) => a.zScore - b.zScore);
  return findings;
}

// ─── Violation heatmap (Agent × hour-of-day) ────────────────────────────────

export interface HeatmapCell {
  agentId: string;
  hour: number;
  count: number;
}

export function buildHeatmap(events: MonitorEvent[]): HeatmapCell[] {
  const agg = new Map<string, number>();
  const agents = new Set<string>();
  for (const e of events) {
    if (e.result_action === 'pass' || e.ts === undefined) continue;
    const date = new Date(e.ts * 1000);
    const hour = date.getHours();
    const key = `${e.agent_id}:${hour}`;
    agg.set(key, (agg.get(key) ?? 0) + 1);
    agents.add(e.agent_id);
  }
  const cells: HeatmapCell[] = [];
  for (const agentId of agents) {
    for (let h = 0; h < 24; h++) {
      cells.push({ agentId, hour: h, count: agg.get(`${agentId}:${h}`) ?? 0 });
    }
  }
  return cells;
}

// ─── Sparkline series (count of violations per bucket) ──────────────────────

export function bucketCounts(events: MonitorEvent[], buckets = 30, lookbackMs = 60 * 60 * 1000): number[] {
  const now = Date.now();
  const start = now - lookbackMs;
  const width = lookbackMs / buckets;
  const arr = new Array(buckets).fill(0);
  for (const e of events) {
    if (e.result_action === 'pass' || e.ts === undefined) continue;
    const ms = e.ts * 1000;
    if (ms < start) continue;
    const idx = Math.min(buckets - 1, Math.max(0, Math.floor((ms - start) / width)));
    arr[idx]++;
  }
  return arr;
}

// ─── SLO math ────────────────────────────────────────────────────────────────

const SLO_STORAGE_KEY = 'sponsio.slos.v1';

function isSlo(v: unknown): v is Slo {
  if (!v || typeof v !== 'object') return false;
  const o = v as Record<string, unknown>;
  return (
    typeof o.id === 'string' &&
    typeof o.name === 'string' &&
    typeof o.constraintName === 'string' &&
    (o.agentId === undefined || typeof o.agentId === 'string') &&
    typeof o.targetPassRate === 'number' &&
    typeof o.windowMinutes === 'number' &&
    typeof o.createdAt === 'number'
  );
}

export function loadSlos(): Slo[] {
  try {
    const raw = localStorage.getItem(SLO_STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // Defensive filter: localStorage can be edited by hand or persist
    // across schema changes. Drop entries that don't match the current
    // shape rather than crash render in SLO panels downstream.
    return parsed.filter(isSlo);
  } catch {
    return [];
  }
}

export function saveSlos(slos: Slo[]): void {
  try {
    localStorage.setItem(SLO_STORAGE_KEY, JSON.stringify(slos));
  } catch {
    /* quota exceeded — ignore */
  }
}

export function computeSloStatus(slo: Slo, events: MonitorEvent[]): SloStatus {
  const now = Date.now() / 1000;
  const windowStart = now - slo.windowMinutes * 60;
  const relevant = events.filter(e => {
    if (e.constraint_name !== slo.constraintName) return false;
    if (slo.agentId && e.agent_id !== slo.agentId) return false;
    if (e.ts !== undefined && e.ts < windowStart) return false;
    return true;
  });
  const sampleSize = relevant.length;
  const passes = relevant.filter(e => e.result_action === 'pass').length;
  const currentPassRate = sampleSize === 0 ? 1 : passes / sampleSize;

  // Error budget: 1 - (observed_error_rate / allowed_error_rate)
  const observedErrorRate = 1 - currentPassRate;
  const allowedErrorRate = 1 - slo.targetPassRate;
  const errorBudgetRemaining = allowedErrorRate <= 0
    ? (observedErrorRate === 0 ? 1 : 0)
    : Math.max(-1, 1 - observedErrorRate / allowedErrorRate);

  return {
    ...slo,
    currentPassRate,
    sampleSize,
    errorBudgetRemaining,
    healthy: currentPassRate >= slo.targetPassRate,
  };
}

// ─── Violation suppressions (localStorage) ──────────────────────────────────

const SUPPR_STORAGE_KEY = 'sponsio.suppressions.v1';

export function loadSuppressions(): { id: string; constraintName: string; agentId?: string; until: number; createdAt: number }[] {
  try {
    const raw = localStorage.getItem(SUPPR_STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const now = Date.now();
    return (parsed as { id: string; constraintName: string; agentId?: string; until: number; createdAt: number }[])
      .filter(s => s.until > now);
  } catch {
    return [];
  }
}

export function saveSuppressions(suppr: { id: string; constraintName: string; agentId?: string; until: number; createdAt: number }[]): void {
  try {
    localStorage.setItem(SUPPR_STORAGE_KEY, JSON.stringify(suppr));
  } catch {
    /* quota exceeded */
  }
}
