export interface Agent {
  id: string;
  tools: string[];
  permissions: string[];
  reads_from: string[];
  writes_to: string[];
}

export interface ParsedConstraint {
  original_nl: string;
  pattern_name: string;
  formula_repr: string;
  ok: boolean;
  error: string;
}

export interface ContractParseResult {
  constraints: ParsedConstraint[];
  ok: boolean;
}

export interface ConstraintItem {
  desc: string;
  type: 'hard' | 'soft';
  pattern_name: string;
}

export interface Contract {
  agent_id: string;
  assumptions: ConstraintItem[];
  guarantees: ConstraintItem[];
}

export interface EnforcementResult {
  action: string;
  message: string;
  retry_prompt: string | null;
}

export interface PlaygroundResult {
  allowed: boolean;
  results: EnforcementResult[];
}

export interface MonitorEvent {
  agent_id: string;
  action: string;
  pipeline: string;
  constraint_name: string;
  result_action: string;
  result_message: string;
  // ── Optional fields (P1 — populated by backend when available) ──────────────
  ts?: number;                // unix seconds, when the enforcement decision happened
  trace_id?: string;          // links violation to a specific trace
  source?: string | null;     // data-lineage source (e.g. "Google Drive")
  target?: string | null;     // data-lineage target (e.g. "#marketing")
  severity?: 'low' | 'medium' | 'high' | 'critical';
  enforcement_latency_ms?: number;
  retry_count?: number;
}

export interface MonitorStatus {
  total_events: number;
  det_violations: number;
  sto_violations: number;
}

export interface TraceEvent {
  ts: number;
  agent: string;
  event_type: string;
  tool: string | null;
  key: string | null;
  to: string | null;
  content: string | null;
  source?: string | null;
  target?: string | null;
}

// Unified trace step for TraceTimeline component
export interface TraceStep {
  event_type: 'tool_call' | 'data_read' | 'data_write';
  label: string;         // tool name or action name
  source?: string;       // e.g. "Google Drive", "Order DB"
  target?: string;       // e.g. "#marketing", "Production DB"
  isViolation: boolean;  // would violate / did violate
}

export interface SystemInfo {
  name: string;
  agent_count: number;
  contract_count: number;
  violation_count: number;
}

export interface DemoStep {
  type: 'agent_thought' | 'tool_call' | 'agent_response' | 'outcome' | 'sto_eval' | 'soft_feedback' | 'soft_retry' | 'soft_pass';
  event_type?: 'tool_call' | 'data_read' | 'data_write';
  text?: string;
  agent_id?: string;
  action?: string;
  args?: Record<string, unknown>;
  tool_result?: Record<string, unknown>;
  expect_blocked?: boolean;
  source?: string;
  target?: string;
  status?: 'protected' | 'allowed';
  constraint_name?: string;
  score?: number;
  threshold?: number;
  evidence?: string;
  suggestion?: string;
  feedback?: string;
  attempt?: number;
  max_retries?: number;
}

export interface DemoScenario {
  id: string;
  title: string;
  description: string;
  customer_message: string;
  agent_id: string;
  steps: DemoStep[];
}

export interface DemoInfo {
  id: string;
  title: string;
  subtitle: string;
  agent: string;
  tools: string[];
}

// Pattern Library (from GET /patterns/library)
export interface PatternDef {
  name: string;
  category: string;
  example_nl: string;
  description: string;
  params: string[];
}

// Block categories for trace flow
export type BlockCategory = 'gather' | 'think' | 'decide' | 'act' | 'violation';

export interface EventBlock {
  id: string;
  category: BlockCategory;
  events: TraceEvent[];
  hasViolation: boolean;
  violations: MonitorEvent[];
  spans: SpanNode[];
}

// Re-verify
export interface ReVerifyStepResult {
  timestep: number;
  passed: boolean;
  event_summary: string;
}

export interface ReVerifyResponse {
  contract_desc: string;
  pattern_name: string;
  results: ReVerifyStepResult[];
  overall_passed: boolean;
}

// Span tree (matches sponsio/models/spans.py Span.to_dict() output)
export interface SpanNode {
  span_type: string;
  start_time: number;
  end_time: number | null;
  duration_ms: number | null;
  status: 'ok' | 'violated' | 'error';
  attributes?: Record<string, unknown>;
  children?: SpanNode[];
  // AgentTurnSpan fields
  agent_id?: string;
  action?: string;
  blocked?: boolean;
  total_contracts_checked?: number;
  det_violations?: number;
  sto_violations?: number;
  // ContractCheckSpan fields
  contract_name?: string;
  pipeline?: string;
  // PreconditionSpan / GuaranteeSpan fields
  formula_desc?: string;
  result?: boolean;
  // ViolationSpan fields
  kind?: string;
  severity?: string;
  evidence?: string;
  // EnforcementSpan fields
  strategy?: string;
  result_action?: string;
  // StoEvalSpan fields
  constraint_name?: string;
  score?: number;
  threshold?: number;
  passed?: boolean;
  // ── Optional attributes the frontend reads defensively (see BACKEND_FIELDS.md) ─
  // These surface via span.attributes[...] but typed here for convenience:
  trace_id?: string;
}

// LLM metrics extracted from span.attributes (llm.* namespace).
// See claude_cowork/BACKEND_FIELDS.md §1 for the attribute contract.
export interface LlmMetrics {
  model?: string;
  provider?: string;
  promptTokens?: number;
  completionTokens?: number;
  totalTokens?: number;
  costUsd?: number;
  ttftMs?: number;
  temperature?: number;
  cached?: boolean;
  prompt?: string;
  completion?: string;
  completionAfterRetry?: string;
}

// Error metadata for spans with status === 'error'.
// See BACKEND_FIELDS.md §3.
export interface SpanError {
  type?: string;
  message?: string;
}

// Session grouping metadata (from span.attributes.session.*).
// See BACKEND_FIELDS.md §2.
export interface SessionInfo {
  id: string;
  userId?: string;
  turn?: number;
}

// User-defined SLO (stored in localStorage for P0/P1, backend-persisted P2).
export interface Slo {
  id: string;
  name: string;
  constraintName: string;  // match monitor_event.constraint_name
  agentId?: string;        // optional: scope to one agent
  targetPassRate: number;  // 0-1
  windowMinutes: number;   // e.g. 60 for 1h
  createdAt: number;       // unix ms
}

export interface SloStatus extends Slo {
  currentPassRate: number;
  sampleSize: number;
  errorBudgetRemaining: number;  // 1 - (violations / (totalEvents * (1 - targetPassRate)))
  healthy: boolean;
}

// Client-side regression detection result.
export interface RegressionFinding {
  agentId: string;
  constraintName?: string;
  currentPassRate: number;
  baselinePassRate: number;
  zScore: number;
  sampleSize: number;
  detectedAt: number;
}

// Violation suppression (stored in localStorage for P0/P1).
export interface Suppression {
  id: string;
  constraintName: string;
  agentId?: string;
  until: number;  // unix ms
  createdAt: number;
}

// Leaderboard
export interface LeaderboardEntry {
  rank: number;
  display_name: string;
  description: string | null;
  score: number;
  grade: string;
  framework: string | null;
  timestamp: string;
  badge_url: string;
}

export interface LeaderboardStats {
  total_submissions: number;
  public_entries: number;
  average_score: number;
  grade_distribution: Record<string, number>;
  framework_distribution: Record<string, number>;
  top_agent: { display_name: string; score: number; grade: string } | null;
}

// Suggested contracts (from scan or discovery)
export interface SuggestedContract {
  id: string;
  nlText: string;
  patternName: string;
  confidence: number; // 0-1
  reason: string;
  accepted?: boolean;
}

// Analytics
export interface AnalyticsData {
  scoreHistory: { date: string; score: number }[];
  violationsByPattern: { pattern: string; count: number }[];
  topViolatedContracts: { nlText: string; count: number; lastViolated: string }[];
  agentReliability: { agentId: string; reliability: number; totalEvents: number }[];
}

// Trace summaries (for Trace Explorer)
export interface TraceSummary {
  traceId: string;
  agentId: string;
  startTime: string;
  duration: number;
  eventCount: number;
  violationCount: number;
  hardViolations: number;
  softViolations: number;
}

export interface TraceFilters {
  search?: string;
  period?: 'hour' | 'day' | 'week' | 'all';
  agentId?: string;
  violationType?: 'all' | 'hard' | 'soft' | 'clean';
}

// Scoring
export interface Deduction {
  check_id: string;
  points_lost: number;
  description: string;
  affected_tools: string[];
  suggested_contract: string;
}

export interface ScoreResponse {
  id: number;
  agent_name: string;
  score: number;
  grade: string;
  deductions: Deduction[];
  suggested_contracts: string[];
  badge_url: string;
}
