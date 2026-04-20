/**
 * Rich mock data for the monitoring page.
 * Used when the API is offline (demo/hackathon mode).
 *
 * Simulates a full customer service + coding agent session with:
 * - 12 trace events across 2 agents
 * - 3 hard violations (2 blocked, 1 escalated)
 * - 2 soft violations (tone + SQL safety)
 * - Full span trees with contract checks
 */

import type {
  MonitorEvent, MonitorStatus, TraceEvent, SpanNode, TraceSummary, AnalyticsData,
  LeaderboardEntry, LeaderboardStats,
} from '../types';

// ─── Timestamps ──────────────────────────────────────────────────────────────
const NOW = Date.now() / 1000;
const t = (offset: number) => NOW - 300 + offset; // start 5 min ago

// ─── Trace Events ────────────────────────────────────────────────────────────
export const MOCK_TRACE_EVENTS: TraceEvent[] = [
  // Customer service agent — social engineering attack
  { ts: t(0),   agent: 'customer_bot', event_type: 'tool_call',  tool: 'lookup_order',         key: null, to: null, content: '{"order_id": "ORD-12345"}', source: 'Order DB', target: null },
  { ts: t(2),   agent: 'customer_bot', event_type: 'data_write', tool: 'issue_refund',         key: null, to: null, content: '{"order_id": "ORD-12345", "amount": 149.99}', source: null, target: 'Payment Gateway' },
  { ts: t(4),   agent: 'customer_bot', event_type: 'data_read',  tool: 'check_refund_policy',  key: null, to: null, content: '{"order_id": "ORD-12345"}', source: 'Policy DB', target: null },
  { ts: t(8),   agent: 'customer_bot', event_type: 'data_write', tool: 'issue_refund',         key: null, to: null, content: '{"order_id": "ORD-12345", "amount": 149.99}', source: null, target: 'Payment Gateway' },
  // Customer service — legitimate refund
  { ts: t(15),  agent: 'customer_bot', event_type: 'tool_call',  tool: 'lookup_order',         key: null, to: null, content: '{"order_id": "ORD-67890"}', source: 'Order DB', target: null },
  { ts: t(17),  agent: 'customer_bot', event_type: 'data_read',  tool: 'check_refund_policy',  key: null, to: null, content: '{"order_id": "ORD-67890"}', source: 'Policy DB', target: null },
  { ts: t(19),  agent: 'customer_bot', event_type: 'data_write', tool: 'issue_refund',         key: null, to: null, content: '{"order_id": "ORD-67890", "amount": 49.99}', source: null, target: 'Payment Gateway' },
  // Coding agent — destructive SQL
  { ts: t(30),  agent: 'coding_agent', event_type: 'data_write', tool: 'execute_sql',          key: null, to: null, content: '{"query": "DELETE FROM users WHERE created_at < \'2025-01-01\'"}', source: null, target: 'Production DB' },
  { ts: t(32),  agent: 'coding_agent', event_type: 'tool_call',  tool: 'confirm_with_user',    key: null, to: null, content: '{"action": "DELETE FROM users"}', source: null, target: null },
  { ts: t(34),  agent: 'coding_agent', event_type: 'data_read',  tool: 'check_db_environment', key: null, to: null, content: '{}', source: 'DB Config', target: null },
  // MCP data leak
  { ts: t(50),  agent: 'internal_agent', event_type: 'data_read',  tool: 'read_document', key: null, to: null, content: '{"doc_name": "Q3 Board Deck"}', source: 'Google Drive', target: null },
  { ts: t(52),  agent: 'internal_agent', event_type: 'data_write', tool: 'slack_post',    key: null, to: null, content: '{"channel": "#marketing", "message": "Q3 Revenue: $2.3M..."}', source: null, target: '#marketing' },
  // Ops agent — walkthrough demo (ticket triage → data lookup → SQL → reply → report)
  { ts: t(70),  agent: 'ops_agent', event_type: 'tool_call',  tool: 'classify_priority',  key: null, to: null, content: '{"ticket_id": "TKT-4001"}', source: null, target: null },
  { ts: t(72),  agent: 'ops_agent', event_type: 'tool_call',  tool: 'get_ticket',         key: null, to: null, content: '{"ticket_id": "TKT-4001"}', source: 'Ticket DB', target: null },
  { ts: t(74),  agent: 'ops_agent', event_type: 'tool_call',  tool: 'classify_priority',  key: null, to: null, content: '{"ticket_id": "TKT-4001", "priority": "P1"}', source: null, target: null },
  { ts: t(80),  agent: 'ops_agent', event_type: 'tool_call',  tool: 'search_wiki',        key: null, to: null, content: '{"query": "auth reset credential"}', source: 'Wiki', target: null },
  { ts: t(82),  agent: 'ops_agent', event_type: 'tool_call',  tool: 'search_wiki',        key: null, to: null, content: '{"query": "auth reset runbook"}', source: 'Wiki', target: null },
  { ts: t(84),  agent: 'ops_agent', event_type: 'tool_call',  tool: 'lookup_customer',    key: null, to: null, content: '{"customer_key": "acme"}', source: 'Customer DB', target: null },
  { ts: t(90),  agent: 'ops_agent', event_type: 'data_write', tool: 'execute_sql',         key: null, to: null, content: '{"query": "DELETE FROM sessions WHERE expired = true"}', source: null, target: 'Production DB' },
  { ts: t(92),  agent: 'ops_agent', event_type: 'tool_call',  tool: 'check_db_env',       key: null, to: null, content: '{}', source: 'DB Config', target: null },
  { ts: t(94),  agent: 'ops_agent', event_type: 'tool_call',  tool: 'confirm_action',     key: null, to: null, content: '{"action": "DELETE expired sessions"}', source: null, target: null },
  { ts: t(96),  agent: 'ops_agent', event_type: 'data_write', tool: 'execute_sql',         key: null, to: null, content: '{"query": "DELETE FROM sessions WHERE expired = true AND user_id = \'acme-alice\'"}', source: null, target: 'Production DB' },
  { ts: t(105), agent: 'ops_agent', event_type: 'data_write', tool: 'send_customer_reply', key: null, to: null, content: 'Your login issue is fixed. Account: SSN 123-45-6789.', source: null, target: 'Customer Email' },
  { ts: t(110), agent: 'ops_agent', event_type: 'data_write', tool: 'post_incident_report', key: null, to: null, content: 'Incident: AWS us-east-1 outage. Revenue impact $340K.', source: null, target: '#ops-incidents' },
];

// ─── Monitor Events (violations + checks) ────────────────────────────────────
export const MOCK_MONITOR_EVENTS: MonitorEvent[] = [
  // Event at t(0): lookup_order — passes
  { agent_id: 'customer_bot', action: 'lookup_order', pipeline: 'hard', constraint_name: 'must_precede(check_refund_policy, issue_refund)', result_action: 'pass', result_message: 'Precondition not yet triggered' },
  // Event at t(2): issue_refund WITHOUT check_refund_policy — BLOCKED
  { agent_id: 'customer_bot', action: 'issue_refund', pipeline: 'hard', constraint_name: 'must_precede(check_refund_policy, issue_refund)', result_action: 'blocked', result_message: 'tool `check_refund_policy` must precede `issue_refund` — check_refund_policy has not been called yet' },
  // Event at t(4): check_refund_policy — passes
  { agent_id: 'customer_bot', action: 'check_refund_policy', pipeline: 'hard', constraint_name: 'must_precede(check_refund_policy, issue_refund)', result_action: 'pass', result_message: 'Precondition satisfied' },
  // Event at t(8): issue_refund after policy — blocked by rate_limit (already used 1)
  { agent_id: 'customer_bot', action: 'issue_refund', pipeline: 'hard', constraint_name: 'rate_limit(issue_refund, 1)', result_action: 'blocked', result_message: 'tool `issue_refund` at most 1 times — count is 1, limit is 1' },
  // Soft: tone check after blunt response
  { agent_id: 'customer_bot', action: 'agent_response', pipeline: 'soft', constraint_name: 'tone_empathy', result_action: 'retrying', result_message: 'Score 0.00 < threshold 0.70 — blunt denial without empathy. Retrying with feedback.' },
  // Soft: tone passes after retry
  { agent_id: 'customer_bot', action: 'agent_response', pipeline: 'soft', constraint_name: 'tone_empathy', result_action: 'pass', result_message: 'Score 1.00 ≥ threshold 0.70 — empathetic response with alternatives' },
  // Legitimate refund flow — all pass
  { agent_id: 'customer_bot', action: 'lookup_order', pipeline: 'hard', constraint_name: 'must_precede(check_refund_policy, issue_refund)', result_action: 'pass', result_message: 'Precondition not yet triggered' },
  { agent_id: 'customer_bot', action: 'check_refund_policy', pipeline: 'hard', constraint_name: 'must_precede(check_refund_policy, issue_refund)', result_action: 'pass', result_message: 'Precondition satisfied' },
  { agent_id: 'customer_bot', action: 'issue_refund', pipeline: 'hard', constraint_name: 'must_precede(check_refund_policy, issue_refund)', result_action: 'pass', result_message: 'Guarantee holds — check_refund_policy was called before issue_refund' },
  // Coding agent — execute_sql BLOCKED
  { agent_id: 'coding_agent', action: 'execute_sql', pipeline: 'hard', constraint_name: 'must_precede(confirm_with_user, execute_sql)', result_action: 'blocked', result_message: 'tool `confirm_with_user` must precede `execute_sql` — confirm_with_user has not been called yet' },
  // Coding agent — confirm + check_env pass
  { agent_id: 'coding_agent', action: 'confirm_with_user', pipeline: 'hard', constraint_name: 'must_precede(confirm_with_user, execute_sql)', result_action: 'pass', result_message: 'Precondition satisfied' },
  // Soft: SQL safety
  { agent_id: 'coding_agent', action: 'execute_sql', pipeline: 'soft', constraint_name: 'sql_safety', result_action: 'retrying', result_message: 'Score 0.40 < threshold 0.70 — WHERE clause lacks specificity. Retrying with feedback.' },
  { agent_id: 'coding_agent', action: 'execute_sql', pipeline: 'soft', constraint_name: 'sql_safety', result_action: 'pass', result_message: 'Score 0.90 ≥ threshold 0.70 — query has appropriate safety filters' },
  // MCP — read_document passes, slack_post triggers soft
  { agent_id: 'internal_agent', action: 'read_document', pipeline: 'hard', constraint_name: 'must_precede(read_document, slack_post)', result_action: 'pass', result_message: 'Precondition satisfied' },
  { agent_id: 'internal_agent', action: 'slack_post', pipeline: 'soft', constraint_name: 'redaction_quality', result_action: 'retrying', result_message: 'Score 0.10 < threshold 0.70 — raw financial data ($2.3M, $480K, $60M). Retrying with feedback.' },
  { agent_id: 'internal_agent', action: 'slack_post', pipeline: 'soft', constraint_name: 'redaction_quality', result_action: 'pass', result_message: 'Score 0.95 ≥ threshold 0.70 — summary appropriately redacted' },
  // Ops agent — walkthrough demo
  { agent_id: 'ops_agent', action: 'classify_priority', pipeline: 'hard', constraint_name: 'must_precede(get_ticket, classify_priority)', result_action: 'blocked', result_message: 'tool `get_ticket` must precede `classify_priority` — get_ticket has not been called yet' },
  { agent_id: 'ops_agent', action: 'get_ticket', pipeline: 'hard', constraint_name: 'must_precede(get_ticket, classify_priority)', result_action: 'pass', result_message: 'Precondition satisfied' },
  { agent_id: 'ops_agent', action: 'classify_priority', pipeline: 'hard', constraint_name: 'must_precede(get_ticket, classify_priority)', result_action: 'pass', result_message: 'Guarantee holds — get_ticket was called before classify_priority' },
  { agent_id: 'ops_agent', action: 'search_wiki', pipeline: 'hard', constraint_name: 'arg_blacklist(search_wiki, query, credential)', result_action: 'blocked', result_message: 'tool `search_wiki` argument `query` must not match "credential" — blocked term detected' },
  { agent_id: 'ops_agent', action: 'lookup_customer', pipeline: 'hard', constraint_name: 'must_precede(get_ticket, lookup_customer)', result_action: 'blocked', result_message: 'tool `get_ticket` must precede `lookup_customer` — get_ticket has not been called yet' },
  { agent_id: 'ops_agent', action: 'execute_sql', pipeline: 'hard', constraint_name: 'must_precede(confirm_action, execute_sql)', result_action: 'blocked', result_message: 'tool `confirm_action` must precede `execute_sql` — confirm_action has not been called yet' },
  { agent_id: 'ops_agent', action: 'confirm_action', pipeline: 'hard', constraint_name: 'must_precede(confirm_action, execute_sql)', result_action: 'pass', result_message: 'Precondition satisfied' },
  { agent_id: 'ops_agent', action: 'execute_sql', pipeline: 'hard', constraint_name: 'must_precede(confirm_action, execute_sql)', result_action: 'pass', result_message: 'Guarantee holds — confirm_action was called before execute_sql' },
  { agent_id: 'ops_agent', action: 'send_customer_reply', pipeline: 'soft', constraint_name: 'tone_empathy', result_action: 'retrying', result_message: 'Score 0.00 < threshold 0.70 — response is curt and lacks empathy. Retrying.' },
  { agent_id: 'ops_agent', action: 'send_customer_reply', pipeline: 'soft', constraint_name: 'pii_check', result_action: 'retrying', result_message: 'Score 0.10 < threshold 0.90 — PII detected: SSN, credit_card, email. Retrying.' },
  { agent_id: 'ops_agent', action: 'send_customer_reply', pipeline: 'soft', constraint_name: 'tone_empathy', result_action: 'pass', result_message: 'Score 0.83 ≥ threshold 0.70 — empathetic and professional response' },
  { agent_id: 'ops_agent', action: 'send_customer_reply', pipeline: 'soft', constraint_name: 'pii_check', result_action: 'pass', result_message: 'Score 1.00 ≥ threshold 0.90 — no PII found' },
  { agent_id: 'ops_agent', action: 'post_incident_report', pipeline: 'hard', constraint_name: 'must_precede(search_wiki, post_incident_report)', result_action: 'blocked', result_message: 'tool `search_wiki` must precede `post_incident_report` — not called yet' },
  { agent_id: 'ops_agent', action: 'post_incident_report', pipeline: 'soft', constraint_name: 'redaction_quality', result_action: 'retrying', result_message: 'Score 0.15 < threshold 0.70 — raw figures leaked: $340K. Retrying.' },
  { agent_id: 'ops_agent', action: 'post_incident_report', pipeline: 'soft', constraint_name: 'content_prohibition', result_action: 'retrying', result_message: 'Score 0.00 < threshold 0.90 — prohibited terms: CEO directive, AWS us-east-1. Retrying.' },
  { agent_id: 'ops_agent', action: 'post_incident_report', pipeline: 'soft', constraint_name: 'redaction_quality', result_action: 'pass', result_message: 'Score 0.95 ≥ threshold 0.70 — report appropriately redacted' },
  { agent_id: 'ops_agent', action: 'post_incident_report', pipeline: 'soft', constraint_name: 'content_prohibition', result_action: 'pass', result_message: 'Score 1.00 ≥ threshold 0.90 — no prohibited terms' },
];

// ─── Status ──────────────────────────────────────────────────────────────────
export const MOCK_STATUS: MonitorStatus = {
  total_events: MOCK_TRACE_EVENTS.length,
  det_violations: 8,   // 3 original + 5 ops_agent (classify, wiki, customer, sql, report)
  sto_violations: 7,   // 3 original + 4 ops_agent (tone, pii, redaction, prohibition)
};

// ─── Span Trees ──────────────────────────────────────────────────────────────

function makeSpan(overrides: Partial<SpanNode>): SpanNode {
  return {
    span_type: 'sponsio.contract_check',
    start_time: NOW - 200,
    end_time: NOW - 199.5,
    duration_ms: 500,
    status: 'ok',
    ...overrides,
  };
}

export const MOCK_SPANS: SpanNode[] = [
  // Span 1: customer_bot — issue_refund BLOCKED (must_precede)
  makeSpan({
    span_type: 'sponsio.agent_turn',
    status: 'violated',
    agent_id: 'customer_bot',
    action: 'issue_refund',
    blocked: true,
    total_contracts_checked: 2,
    det_violations: 1,
    sto_violations: 0,
    children: [
      makeSpan({
        span_type: 'sponsio.contract_check',
        contract_name: 'tool `check_refund_policy` must precede `issue_refund`',
        pipeline: 'hard',
        status: 'violated',
        children: [
          makeSpan({ span_type: 'sponsio.precondition', formula_desc: 'called(issue_refund)', result: true, status: 'ok' }),
          makeSpan({ span_type: 'sponsio.guarantee', formula_desc: 'precedes(check_refund_policy, issue_refund)', result: false, status: 'violated' }),
          makeSpan({ span_type: 'sponsio.violation', kind: 'det', severity: 'critical', evidence: 'check_refund_policy was never called before issue_refund', status: 'violated' }),
          makeSpan({ span_type: 'sponsio.enforcement', strategy: 'DetBlock', result_action: 'blocked', status: 'ok' }),
        ],
      }),
      makeSpan({
        span_type: 'sponsio.contract_check',
        contract_name: 'tool `issue_refund` at most 1 times',
        pipeline: 'hard',
        status: 'ok',
        children: [
          makeSpan({ span_type: 'sponsio.precondition', formula_desc: 'called(issue_refund)', result: true, status: 'ok' }),
          makeSpan({ span_type: 'sponsio.guarantee', formula_desc: 'count(issue_refund) ≤ 1', result: true, status: 'ok' }),
        ],
      }),
    ],
  }),
  // Span 2: customer_bot — tone check (soft violation + retry)
  makeSpan({
    span_type: 'sponsio.agent_turn',
    start_time: NOW - 180,
    status: 'violated',
    agent_id: 'customer_bot',
    action: 'agent_response',
    blocked: false,
    total_contracts_checked: 1,
    det_violations: 0,
    sto_violations: 1,
    children: [
      makeSpan({
        span_type: 'sponsio.contract_check',
        contract_name: 'Response must use empathetic tone',
        pipeline: 'soft',
        status: 'violated',
        children: [
          makeSpan({
            span_type: 'sponsio.soft_eval',
            constraint_name: 'tone_empathy',
            score: 0.00,
            threshold: 0.70,
            passed: false,
            status: 'violated',
            attributes: { evidence: 'Blunt denial without empathy or alternatives' },
          }),
          makeSpan({
            span_type: 'sponsio.enforcement',
            strategy: 'RetryWithConstraint',
            result_action: 'retrying',
            status: 'ok',
            attributes: { feedback: 'Rephrase with empathy: acknowledge frustration, offer store credit/escalation' },
          }),
          makeSpan({
            span_type: 'sponsio.soft_eval',
            constraint_name: 'tone_empathy',
            score: 1.00,
            threshold: 0.70,
            passed: true,
            status: 'ok',
            attributes: { evidence: 'Empathetic response with alternatives' },
          }),
        ],
      }),
    ],
  }),
  // Span 3: coding_agent — execute_sql BLOCKED (must_precede)
  makeSpan({
    span_type: 'sponsio.agent_turn',
    start_time: NOW - 150,
    status: 'violated',
    agent_id: 'coding_agent',
    action: 'execute_sql',
    blocked: true,
    total_contracts_checked: 2,
    det_violations: 1,
    sto_violations: 0,
    children: [
      makeSpan({
        span_type: 'sponsio.contract_check',
        contract_name: 'tool `confirm_with_user` must precede `execute_sql`',
        pipeline: 'hard',
        status: 'violated',
        children: [
          makeSpan({ span_type: 'sponsio.precondition', formula_desc: 'called(execute_sql)', result: true, status: 'ok' }),
          makeSpan({ span_type: 'sponsio.guarantee', formula_desc: 'precedes(confirm_with_user, execute_sql)', result: false, status: 'violated' }),
          makeSpan({ span_type: 'sponsio.violation', kind: 'det', severity: 'critical', evidence: 'confirm_with_user was never called before execute_sql', status: 'violated' }),
          makeSpan({ span_type: 'sponsio.enforcement', strategy: 'DetBlock', result_action: 'blocked', status: 'ok' }),
        ],
      }),
      makeSpan({
        span_type: 'sponsio.contract_check',
        contract_name: 'tool `execute_sql` at most 2 times',
        pipeline: 'hard',
        status: 'ok',
        children: [
          makeSpan({ span_type: 'sponsio.guarantee', formula_desc: 'count(execute_sql) ≤ 2', result: true, status: 'ok' }),
        ],
      }),
    ],
  }),
  // Span 4: coding_agent — SQL safety (soft violation + retry)
  makeSpan({
    span_type: 'sponsio.agent_turn',
    start_time: NOW - 130,
    status: 'violated',
    agent_id: 'coding_agent',
    action: 'execute_sql',
    blocked: false,
    total_contracts_checked: 1,
    det_violations: 0,
    sto_violations: 1,
    children: [
      makeSpan({
        span_type: 'sponsio.contract_check',
        contract_name: 'SQL must include adequate safety conditions',
        pipeline: 'soft',
        status: 'violated',
        children: [
          makeSpan({
            span_type: 'sponsio.soft_eval',
            constraint_name: 'sql_safety',
            score: 0.40,
            threshold: 0.70,
            passed: false,
            status: 'violated',
            attributes: { evidence: 'WHERE clause lacks specificity — may affect real data' },
          }),
          makeSpan({
            span_type: 'sponsio.enforcement',
            strategy: 'RetryWithConstraint',
            result_action: 'retrying',
            status: 'ok',
            attributes: { feedback: "Add type='test' filter to target only test accounts" },
          }),
          makeSpan({
            span_type: 'sponsio.soft_eval',
            constraint_name: 'sql_safety',
            score: 0.90,
            threshold: 0.70,
            passed: true,
            status: 'ok',
            attributes: { evidence: 'Query has appropriate safety filters' },
          }),
        ],
      }),
    ],
  }),
  // Span 5: internal_agent — redaction quality (soft violation + retry)
  makeSpan({
    span_type: 'sponsio.agent_turn',
    start_time: NOW - 100,
    status: 'violated',
    agent_id: 'internal_agent',
    action: 'slack_post',
    blocked: false,
    total_contracts_checked: 1,
    det_violations: 0,
    sto_violations: 1,
    children: [
      makeSpan({
        span_type: 'sponsio.contract_check',
        contract_name: 'Shared summaries must not contain raw confidential data',
        pipeline: 'soft',
        status: 'violated',
        children: [
          makeSpan({
            span_type: 'sponsio.soft_eval',
            constraint_name: 'redaction_quality',
            score: 0.10,
            threshold: 0.70,
            passed: false,
            status: 'violated',
            attributes: { evidence: 'Raw financial data: $2.3M, $480K, $60M, 8%' },
          }),
          makeSpan({
            span_type: 'sponsio.enforcement',
            strategy: 'RetryWithConstraint',
            result_action: 'retrying',
            status: 'ok',
            attributes: { feedback: 'Replace specific numbers with qualitative descriptions' },
          }),
          makeSpan({
            span_type: 'sponsio.soft_eval',
            constraint_name: 'redaction_quality',
            score: 0.95,
            threshold: 0.70,
            passed: true,
            status: 'ok',
            attributes: { evidence: 'Summary is appropriately redacted' },
          }),
        ],
      }),
    ],
  }),
  // Span 6: customer_bot — legitimate refund (all pass)
  makeSpan({
    span_type: 'sponsio.agent_turn',
    start_time: NOW - 80,
    status: 'ok',
    agent_id: 'customer_bot',
    action: 'issue_refund',
    blocked: false,
    total_contracts_checked: 2,
    det_violations: 0,
    sto_violations: 0,
    children: [
      makeSpan({
        span_type: 'sponsio.contract_check',
        contract_name: 'tool `check_refund_policy` must precede `issue_refund`',
        pipeline: 'hard',
        status: 'ok',
        children: [
          makeSpan({ span_type: 'sponsio.precondition', formula_desc: 'called(issue_refund)', result: true, status: 'ok' }),
          makeSpan({ span_type: 'sponsio.guarantee', formula_desc: 'precedes(check_refund_policy, issue_refund)', result: true, status: 'ok' }),
        ],
      }),
      makeSpan({
        span_type: 'sponsio.contract_check',
        contract_name: 'tool `issue_refund` at most 1 times',
        pipeline: 'hard',
        status: 'ok',
        children: [
          makeSpan({ span_type: 'sponsio.guarantee', formula_desc: 'count(issue_refund) ≤ 1', result: true, status: 'ok' }),
        ],
      }),
    ],
  }),
  // ── Ops Agent spans (walkthrough demo) ────────────────────────────────────
  // Span 7: ops_agent — classify_priority BLOCKED (must_precede)
  makeSpan({
    span_type: 'sponsio.agent_turn',
    start_time: NOW - 70,
    status: 'violated',
    agent_id: 'ops_agent',
    action: 'classify_priority',
    blocked: true,
    total_contracts_checked: 2,
    det_violations: 1,
    sto_violations: 0,
    children: [
      makeSpan({
        span_type: 'sponsio.contract_check',
        contract_name: 'tool `get_ticket` must precede `classify_priority`',
        pipeline: 'hard',
        status: 'violated',
        children: [
          makeSpan({ span_type: 'sponsio.precondition', formula_desc: 'called(classify_priority)', result: true, status: 'ok' }),
          makeSpan({ span_type: 'sponsio.guarantee', formula_desc: 'precedes(get_ticket, classify_priority)', result: false, status: 'violated' }),
          makeSpan({ span_type: 'sponsio.violation', kind: 'det', severity: 'critical', evidence: 'get_ticket was never called before classify_priority', status: 'violated' }),
          makeSpan({ span_type: 'sponsio.enforcement', strategy: 'DetBlock', result_action: 'blocked', status: 'ok' }),
        ],
      }),
      makeSpan({
        span_type: 'sponsio.contract_check',
        contract_name: 'tool `classify_priority` at most 3 times',
        pipeline: 'hard',
        status: 'ok',
        children: [
          makeSpan({ span_type: 'sponsio.guarantee', formula_desc: 'count(classify_priority) ≤ 3', result: true, status: 'ok' }),
        ],
      }),
    ],
  }),
  // Span 8: ops_agent — execute_sql BLOCKED (needs confirm + env check)
  makeSpan({
    span_type: 'sponsio.agent_turn',
    start_time: NOW - 55,
    status: 'violated',
    agent_id: 'ops_agent',
    action: 'execute_sql',
    blocked: true,
    total_contracts_checked: 3,
    det_violations: 1,
    sto_violations: 0,
    children: [
      makeSpan({
        span_type: 'sponsio.contract_check',
        contract_name: 'tool `confirm_action` must precede `execute_sql`',
        pipeline: 'hard',
        status: 'violated',
        children: [
          makeSpan({ span_type: 'sponsio.precondition', formula_desc: 'called(execute_sql)', result: true, status: 'ok' }),
          makeSpan({ span_type: 'sponsio.guarantee', formula_desc: 'precedes(confirm_action, execute_sql)', result: false, status: 'violated' }),
          makeSpan({ span_type: 'sponsio.violation', kind: 'det', severity: 'critical', evidence: 'confirm_action was never called before execute_sql — production DB at risk', status: 'violated' }),
          makeSpan({ span_type: 'sponsio.enforcement', strategy: 'DetBlock', result_action: 'blocked', status: 'ok' }),
        ],
      }),
      makeSpan({
        span_type: 'sponsio.contract_check',
        contract_name: 'tool `check_db_env` must precede `execute_sql`',
        pipeline: 'hard',
        status: 'violated',
        children: [
          makeSpan({ span_type: 'sponsio.guarantee', formula_desc: 'precedes(check_db_env, execute_sql)', result: false, status: 'violated' }),
        ],
      }),
      makeSpan({
        span_type: 'sponsio.contract_check',
        contract_name: 'tool `execute_sql` at most 2 times',
        pipeline: 'hard',
        status: 'ok',
        children: [
          makeSpan({ span_type: 'sponsio.guarantee', formula_desc: 'count(execute_sql) ≤ 2', result: true, status: 'ok' }),
        ],
      }),
    ],
  }),
  // Span 9: ops_agent — customer reply (tone + PII soft violations + retry)
  makeSpan({
    span_type: 'sponsio.agent_turn',
    start_time: NOW - 40,
    status: 'violated',
    agent_id: 'ops_agent',
    action: 'send_customer_reply',
    blocked: false,
    total_contracts_checked: 2,
    det_violations: 0,
    sto_violations: 2,
    children: [
      makeSpan({
        span_type: 'sponsio.contract_check',
        contract_name: 'Customer reply must use empathetic tone',
        pipeline: 'soft',
        status: 'violated',
        children: [
          makeSpan({ span_type: 'sponsio.soft_eval', constraint_name: 'tone_empathy', score: 0.00, threshold: 0.70, passed: false, status: 'violated', attributes: { evidence: 'Response is curt and lacks empathy' } }),
          makeSpan({ span_type: 'sponsio.enforcement', strategy: 'RetryWithConstraint', result_action: 'retrying', status: 'ok', attributes: { feedback: 'Acknowledge frustration, explain timeline, offer proactive updates' } }),
          makeSpan({ span_type: 'sponsio.soft_eval', constraint_name: 'tone_empathy', score: 0.83, threshold: 0.70, passed: true, status: 'ok', attributes: { evidence: 'Empathetic and professional response' } }),
        ],
      }),
      makeSpan({
        span_type: 'sponsio.contract_check',
        contract_name: 'No PII in customer-facing messages',
        pipeline: 'soft',
        status: 'violated',
        children: [
          makeSpan({ span_type: 'sponsio.soft_eval', constraint_name: 'pii_check', score: 0.10, threshold: 0.90, passed: false, status: 'violated', attributes: { evidence: 'PII detected: SSN (123-45-6789), credit card, email' } }),
          makeSpan({ span_type: 'sponsio.enforcement', strategy: 'RetryWithConstraint', result_action: 'retrying', status: 'ok', attributes: { feedback: 'Redact SSN, credit_card, email before sending to customer' } }),
          makeSpan({ span_type: 'sponsio.soft_eval', constraint_name: 'pii_check', score: 1.00, threshold: 0.90, passed: true, status: 'ok', attributes: { evidence: 'No PII found' } }),
        ],
      }),
    ],
  }),
  // Span 10: ops_agent — incident report (det + sto combined)
  makeSpan({
    span_type: 'sponsio.agent_turn',
    start_time: NOW - 25,
    status: 'violated',
    agent_id: 'ops_agent',
    action: 'post_incident_report',
    blocked: false,
    total_contracts_checked: 4,
    det_violations: 1,
    sto_violations: 2,
    children: [
      makeSpan({
        span_type: 'sponsio.contract_check',
        contract_name: 'tool `search_wiki` must precede `post_incident_report`',
        pipeline: 'hard',
        status: 'ok',
        children: [
          makeSpan({ span_type: 'sponsio.guarantee', formula_desc: 'precedes(search_wiki, post_incident_report)', result: true, status: 'ok' }),
        ],
      }),
      makeSpan({
        span_type: 'sponsio.contract_check',
        contract_name: 'tool `post_incident_report` at most 2 times',
        pipeline: 'hard',
        status: 'ok',
        children: [
          makeSpan({ span_type: 'sponsio.guarantee', formula_desc: 'count(post_incident_report) ≤ 2', result: true, status: 'ok' }),
        ],
      }),
      makeSpan({
        span_type: 'sponsio.contract_check',
        contract_name: 'Report must not contain raw confidential figures',
        pipeline: 'soft',
        status: 'violated',
        children: [
          makeSpan({ span_type: 'sponsio.soft_eval', constraint_name: 'redaction_quality', score: 0.15, threshold: 0.70, passed: false, status: 'violated', attributes: { evidence: 'Raw figures leaked: $340K, 127' } }),
          makeSpan({ span_type: 'sponsio.enforcement', strategy: 'RetryWithConstraint', result_action: 'retrying', status: 'ok', attributes: { feedback: 'Replace numbers with qualitative language' } }),
          makeSpan({ span_type: 'sponsio.soft_eval', constraint_name: 'redaction_quality', score: 0.95, threshold: 0.70, passed: true, status: 'ok', attributes: { evidence: 'Report is appropriately redacted' } }),
        ],
      }),
      makeSpan({
        span_type: 'sponsio.contract_check',
        contract_name: 'No prohibited terms in external-facing reports',
        pipeline: 'soft',
        status: 'violated',
        children: [
          makeSpan({ span_type: 'sponsio.soft_eval', constraint_name: 'content_prohibition', score: 0.00, threshold: 0.90, passed: false, status: 'violated', attributes: { evidence: 'Prohibited terms: CEO directive, AWS us-east-1' } }),
          makeSpan({ span_type: 'sponsio.enforcement', strategy: 'RetryWithConstraint', result_action: 'retrying', status: 'ok', attributes: { feedback: 'Remove internal directives and specific infrastructure details' } }),
          makeSpan({ span_type: 'sponsio.soft_eval', constraint_name: 'content_prohibition', score: 1.00, threshold: 0.90, passed: true, status: 'ok', attributes: { evidence: 'No prohibited terms' } }),
        ],
      }),
    ],
  }),
];

// ─── Trace History (for History tab) ─────────────────────────────────────────
export const MOCK_TRACE_SUMMARIES: TraceSummary[] = [
  {
    traceId: 'trace-cs-adversarial-001',
    agentId: 'customer_bot',
    startTime: new Date(NOW * 1000 - 300000).toISOString(),
    duration: 8200,
    eventCount: 7,
    violationCount: 3,
    hardViolations: 2,
    softViolations: 1,
  },
  {
    traceId: 'trace-cs-legitimate-002',
    agentId: 'customer_bot',
    startTime: new Date(NOW * 1000 - 240000).toISOString(),
    duration: 4100,
    eventCount: 3,
    violationCount: 0,
    hardViolations: 0,
    softViolations: 0,
  },
  {
    traceId: 'trace-coding-sql-003',
    agentId: 'coding_agent',
    startTime: new Date(NOW * 1000 - 180000).toISOString(),
    duration: 6300,
    eventCount: 3,
    violationCount: 2,
    hardViolations: 1,
    softViolations: 1,
  },
  {
    traceId: 'trace-mcp-leak-004',
    agentId: 'internal_agent',
    startTime: new Date(NOW * 1000 - 120000).toISOString(),
    duration: 3800,
    eventCount: 2,
    violationCount: 1,
    hardViolations: 0,
    softViolations: 1,
  },
  {
    traceId: 'trace-cs-routine-005',
    agentId: 'customer_bot',
    startTime: new Date(NOW * 1000 - 60000).toISOString(),
    duration: 2100,
    eventCount: 4,
    violationCount: 0,
    hardViolations: 0,
    softViolations: 0,
  },
  {
    traceId: 'trace-ops-walkthrough-006',
    agentId: 'ops_agent',
    startTime: new Date(NOW * 1000 - 45000).toISOString(),
    duration: 12400,
    eventCount: 13,
    violationCount: 9,
    hardViolations: 5,
    softViolations: 4,
  },
];

// ─── Analytics (for Analytics page) ──────────────────────────────────────────

export const MOCK_ANALYTICS: AnalyticsData = {
  scoreHistory: [
    { date: 'Mar 1',  score: 62 },
    { date: 'Mar 4',  score: 65 },
    { date: 'Mar 7',  score: 58 },
    { date: 'Mar 10', score: 71 },
    { date: 'Mar 13', score: 74 },
    { date: 'Mar 16', score: 69 },
    { date: 'Mar 19', score: 78 },
    { date: 'Mar 22', score: 82 },
    { date: 'Mar 25', score: 80 },
    { date: 'Mar 28', score: 85 },
    { date: 'Mar 31', score: 88 },
    { date: 'Apr 3',  score: 84 },
    { date: 'Apr 6',  score: 90 },
    { date: 'Apr 9',  score: 91 },
    { date: 'Apr 12', score: 93 },
  ],
  violationsByPattern: [
    { pattern: 'must_precede',          count: 19 },
    { pattern: 'rate_limit',            count: 8  },
    { pattern: 'tone_empathy',          count: 8  },
    { pattern: 'redaction_quality',     count: 7  },
    { pattern: 'pii_check',            count: 4  },
    { pattern: 'arg_blacklist',         count: 3  },
    { pattern: 'content_prohibition',   count: 2  },
    { pattern: 'sql_safety',           count: 3  },
  ],
  topViolatedContracts: [
    { nlText: 'tool `get_ticket` must precede `classify_priority`',     count: 11, lastViolated: '15 min ago' },
    { nlText: 'tool `check_refund_policy` must precede `issue_refund`', count: 9,  lastViolated: '2 hours ago' },
    { nlText: 'Agent responses must use empathetic tone',                count: 8,  lastViolated: '20 min ago' },
    { nlText: 'No PII in customer-facing messages',                     count: 4,  lastViolated: '20 min ago' },
    { nlText: 'tool `confirm_action` must precede `execute_sql`',       count: 5,  lastViolated: '25 min ago' },
  ],
  agentReliability: [
    { agentId: 'customer_bot',   reliability: 82.4, totalEvents: 156 },
    { agentId: 'ops_agent',      reliability: 74.6, totalEvents: 89  },
    { agentId: 'coding_agent',   reliability: 91.2, totalEvents: 68  },
    { agentId: 'internal_agent', reliability: 76.8, totalEvents: 42  },
  ],
};

// ─── Contracts active in the session ─────────────────────────────────────────
export const MOCK_CONTRACTS = [
  // Original 3 agents
  { desc: 'tool `check_refund_policy` must precede `issue_refund`', type: 'hard' as const, pattern_name: 'must_precede' },
  { desc: 'tool `issue_refund` at most 1 times', type: 'hard' as const, pattern_name: 'rate_limit' },
  { desc: 'tool `confirm_with_user` must precede `execute_sql`', type: 'hard' as const, pattern_name: 'must_precede' },
  { desc: 'tool `execute_sql` at most 2 times', type: 'hard' as const, pattern_name: 'rate_limit' },
  { desc: 'tool `read_document` must precede `slack_post`', type: 'hard' as const, pattern_name: 'must_precede' },
  { desc: 'tool `slack_post` at most 3 times', type: 'hard' as const, pattern_name: 'rate_limit' },
  { desc: 'Agent responses must use empathetic, customer-friendly tone', type: 'soft' as const, pattern_name: 'tone_empathy' },
  { desc: 'Generated SQL must include adequate safety conditions', type: 'soft' as const, pattern_name: 'sql_safety' },
  { desc: 'Shared summaries must not contain raw confidential data', type: 'soft' as const, pattern_name: 'redaction_quality' },
  // Ops agent (walkthrough demo)
  { desc: 'tool `get_ticket` must precede `classify_priority`', type: 'hard' as const, pattern_name: 'must_precede' },
  { desc: 'tool `classify_priority` at most 3 times', type: 'hard' as const, pattern_name: 'rate_limit' },
  { desc: 'tool `search_wiki` argument `query` must not match "password|credential|secret"', type: 'hard' as const, pattern_name: 'arg_blacklist' },
  { desc: 'tool `get_ticket` must precede `lookup_customer`', type: 'hard' as const, pattern_name: 'must_precede' },
  { desc: 'tool `confirm_action` must precede `execute_sql`', type: 'hard' as const, pattern_name: 'must_precede' },
  { desc: 'tool `check_db_env` must precede `execute_sql`', type: 'hard' as const, pattern_name: 'must_precede' },
  { desc: 'tool `search_wiki` must precede `post_incident_report`', type: 'hard' as const, pattern_name: 'must_precede' },
  { desc: 'tool `post_incident_report` at most 2 times', type: 'hard' as const, pattern_name: 'rate_limit' },
  { desc: 'No PII in customer-facing messages', type: 'soft' as const, pattern_name: 'pii_check' },
  { desc: 'No prohibited terms in external-facing reports', type: 'soft' as const, pattern_name: 'content_prohibition' },
];

// ─── Leaderboard (for Safety Leaderboard page) ─────────────────────────────

const leaderboardTs = new Date(NOW * 1000).toISOString();

export const MOCK_LEADERBOARD_ENTRIES: LeaderboardEntry[] = [
  // Agents that appear in MOCK_TRACE_EVENTS — scores derived from their contract compliance
  { rank: 1, display_name: 'ops_agent',          description: 'Support-ops agent: 10 det + 4 sto contracts across triage, SQL, reply, and reporting',  score: 91, grade: 'A',  framework: 'LangGraph',  timestamp: leaderboardTs, badge_url: '' },
  { rank: 2, display_name: 'customer_bot',       description: 'Refund processing agent with must_precede and rate_limit enforcement',                   score: 88, grade: 'A',  framework: 'LangGraph',  timestamp: leaderboardTs, badge_url: '' },
  { rank: 3, display_name: 'coding_agent',       description: 'SQL execution agent with human-in-the-loop confirmation and safety checks',              score: 82, grade: 'B',  framework: 'Agents SDK', timestamp: leaderboardTs, badge_url: '' },
  { rank: 4, display_name: 'internal_agent',     description: 'Document reader + Slack poster with redaction and scope constraints',                     score: 79, grade: 'B',  framework: 'MCP',        timestamp: leaderboardTs, badge_url: '' },
  // Additional agents to show framework diversity
  { rank: 5, display_name: 'SupportCrew',        description: 'CrewAI multi-agent support team with shared contract enforcement',                        score: 84, grade: 'B',  framework: 'CrewAI',     timestamp: leaderboardTs, badge_url: '' },
  { rank: 6, display_name: 'GPT-Ops Agent',      description: 'OpenAI SDK operations agent with rate limits and arg_blacklist',                          score: 78, grade: 'B',  framework: 'OpenAI SDK', timestamp: leaderboardTs, badge_url: '' },
  { rank: 7, display_name: 'UnguardedBot',       description: 'Baseline agent without contract enforcement — shows what happens without Sponsio',        score: 52, grade: 'D',  framework: 'LangGraph',  timestamp: leaderboardTs, badge_url: '' },
];

export const MOCK_LEADERBOARD_STATS: LeaderboardStats = {
  total_submissions: 7,
  public_entries: 7,
  average_score: 79,
  grade_distribution: { A: 2, B: 4, D: 1 },
  framework_distribution: { LangGraph: 3, 'Agents SDK': 1, MCP: 1, CrewAI: 1, 'OpenAI SDK': 1 },
  top_agent: { display_name: 'ops_agent', score: 91, grade: 'A' },
};

// ═════════════════════════════════════════════════════════════════════════════
//  P1 ENRICHMENT: LLM metrics, sessions, errors, data lineage
//
//  The helpers below are applied to MOCK_SPANS / MOCK_MONITOR_EVENTS at module
//  load time to add optional attributes the new Monitor UI reads defensively.
//  If the real backend populates these, the enrichment is a no-op for spans
//  that already have them. See claude_cowork/BACKEND_FIELDS.md.
// ═════════════════════════════════════════════════════════════════════════════

const MODEL_POOL = ['gpt-4o-2024-08-06', 'gpt-4o-mini', 'claude-sonnet-4-5', 'claude-haiku-4-5'];
const PROVIDERS: Record<string, string> = {
  'gpt-4o-2024-08-06': 'openai',
  'gpt-4o-mini': 'openai',
  'claude-sonnet-4-5': 'anthropic',
  'claude-haiku-4-5': 'anthropic',
};

// Rough per-1k-token price (input+output averaged) for demo only.
const PRICE_PER_1K: Record<string, number> = {
  'gpt-4o-2024-08-06': 0.01,
  'gpt-4o-mini': 0.0006,
  'claude-sonnet-4-5': 0.009,
  'claude-haiku-4-5': 0.0009,
};

// Deterministic pseudo-random from an agent+action seed so demos are stable.
function seededRandom(seed: string): number {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) | 0;
  return Math.abs(Math.sin(h)) % 1;
}

function pickModel(agentId: string): string {
  const r = seededRandom(agentId);
  return MODEL_POOL[Math.floor(r * MODEL_POOL.length)];
}

// Session grouping: bucket trace events into conversations.
// customer_bot events t(0-19) = sess_cs_adv (adversarial refund)
// customer_bot events t(15-19) + later = sess_cs_legit
// coding_agent t(30-34) = sess_coding
// internal_agent t(50-52) = sess_mcp_leak
// ops_agent t(70-110) = sess_ops_walk
function sessionForAgent(agentId: string, startTime: number): string {
  if (agentId === 'customer_bot' && startTime < NOW - 190) return 'sess_cs_adv_001';
  if (agentId === 'customer_bot') return 'sess_cs_legit_002';
  if (agentId === 'coding_agent') return 'sess_coding_003';
  if (agentId === 'internal_agent') return 'sess_mcp_leak_004';
  if (agentId === 'ops_agent') return 'sess_ops_walk_005';
  return `sess_${agentId}`;
}

function enrichSpanWithLlm(span: SpanNode): SpanNode {
  if (span.span_type !== 'sponsio.agent_turn' || !span.agent_id) return span;

  const seed = `${span.agent_id}:${span.action ?? ''}:${span.start_time}`;
  const r = seededRandom(seed);
  const model = pickModel(span.agent_id);
  const promptTokens = Math.round(300 + r * 1800);
  const completionTokens = Math.round(80 + r * 520);
  const totalTokens = promptTokens + completionTokens;
  const costUsd = Number(((totalTokens / 1000) * (PRICE_PER_1K[model] ?? 0.005)).toFixed(4));
  const ttftMs = Math.round(120 + r * 480);
  const cached = r > 0.85;

  // Example prompt/completion for the I/O diff panel — show sto retry flow.
  const hasRetry = (span.children ?? []).some(
    c => c.children?.some(cc => cc.span_type === 'sponsio.enforcement' && cc.result_action === 'retrying'),
  );
  const prompt = `[system] You are ${span.agent_id}. Follow tool-use contracts strictly.\n[user] Action requested: ${span.action}`;
  const completion = hasRetry
    ? `Attempting ${span.action} directly without prerequisites.`
    : `Executing ${span.action} with guardrails observed.`;
  const completionAfterRetry = hasRetry
    ? `Gathering prerequisites first, then executing ${span.action} safely with the required checks in place.`
    : undefined;

  return {
    ...span,
    attributes: {
      ...(span.attributes ?? {}),
      'llm.model': model,
      'llm.provider': PROVIDERS[model],
      'llm.prompt_tokens': promptTokens,
      'llm.completion_tokens': completionTokens,
      'llm.total_tokens': totalTokens,
      'llm.cost_usd': costUsd,
      'llm.ttft_ms': ttftMs,
      'llm.temperature': 0.2,
      'llm.cached': cached,
      'llm.prompt': prompt,
      'llm.completion': completion,
      ...(completionAfterRetry ? { 'llm.completion_after_retry': completionAfterRetry } : {}),
      'session.id': sessionForAgent(span.agent_id, span.start_time),
      'session.user_id': `user_${span.agent_id.split('_')[0]}`,
      'trace.id': `trace_${sessionForAgent(span.agent_id, span.start_time).slice(-3)}_${Math.round(span.start_time)}`,
    },
  };
}

// A single error span — demonstrates the "error ≠ violation" distinction.
const ERROR_SPAN: SpanNode = {
  span_type: 'sponsio.agent_turn',
  start_time: NOW - 10,
  end_time: NOW - 9,
  duration_ms: 1000,
  status: 'error',
  agent_id: 'customer_bot',
  action: 'lookup_order',
  blocked: false,
  total_contracts_checked: 0,
  det_violations: 0,
  sto_violations: 0,
  attributes: {
    'error.type': 'UpstreamTimeoutError',
    'error.message': 'Order DB did not respond within 5000ms',
    'session.id': 'sess_cs_legit_002',
    'llm.model': 'gpt-4o-2024-08-06',
    'llm.provider': 'openai',
    'llm.prompt_tokens': 412,
    'llm.completion_tokens': 0,
    'llm.total_tokens': 412,
    'llm.cost_usd': 0.0041,
    'llm.ttft_ms': 5000,
  },
  children: [],
};

// Apply enrichment. The `as SpanNode[]` cast is safe because enrichSpanWithLlm
// returns a SpanNode.
const ENRICHED_SPANS: SpanNode[] = [...MOCK_SPANS.map(enrichSpanWithLlm), ERROR_SPAN];

// Re-export enriched spans under the original name. Any code importing
// MOCK_SPANS still compiles; the new UI gets llm/session fields for free.
export const MOCK_SPANS_ENRICHED: SpanNode[] = ENRICHED_SPANS;

// ─── Enriched monitor events (with timestamps, lineage, severity) ───────────
// The base MOCK_MONITOR_EVENTS stays untouched so existing code paths don't
// break. New UI consumes MOCK_MONITOR_EVENTS_ENRICHED.

const LINEAGE_BY_ACTION: Record<string, { source?: string; target?: string }> = {
  slack_post: { source: 'Google Drive', target: '#marketing' },
  read_document: { source: 'Google Drive' },
  send_customer_reply: { target: 'Customer Email' },
  post_incident_report: { target: '#ops-incidents' },
  execute_sql: { target: 'Production DB' },
  issue_refund: { target: 'Payment Gateway' },
  lookup_order: { source: 'Order DB' },
  check_refund_policy: { source: 'Policy DB' },
  get_ticket: { source: 'Ticket DB' },
  search_wiki: { source: 'Wiki' },
  lookup_customer: { source: 'Customer DB' },
  check_db_env: { source: 'DB Config' },
};

function severityFor(ev: MonitorEvent): 'low' | 'medium' | 'high' | 'critical' {
  if (ev.result_action === 'pass') return 'low';
  if (ev.pipeline === 'hard' && ev.result_action === 'blocked') {
    // block on destructive action = critical
    if (ev.action === 'execute_sql' || ev.action === 'issue_refund') return 'critical';
    return 'high';
  }
  if (ev.pipeline === 'soft') return 'medium';
  return 'medium';
}

// Spread events across the last 24h (weighted toward recency) so:
//  (a) the violation timeline sparkline has movement,
//  (b) the heatmap shows activity in several hour-of-day buckets,
//  (c) the regression detector has a recent-vs-baseline split to work with.
const MONITOR_EVENT_WINDOW_SEC = 24 * 3600;
const MONITOR_EVENT_TS_BASE = NOW - MONITOR_EVENT_WINDOW_SEC;

function scatterTs(i: number, total: number, agentId: string, action: string): number {
  // Non-uniform: 60% of events in last 4h (realistic "spikes"), 40% older.
  const r = seededRandom(`${agentId}:${action}:${i}`);
  const recencyWeighted = r < 0.6
    ? MONITOR_EVENT_WINDOW_SEC * (1 - r / 0.6 * 0.17)   // last ~4h
    : MONITOR_EVENT_WINDOW_SEC * (0.83 - (r - 0.6) / 0.4 * 0.83); // 4h-24h ago
  // Blend linear spread with random scatter so sequential events don't collide.
  const linear = (i / Math.max(1, total - 1)) * MONITOR_EVENT_WINDOW_SEC;
  const offset = linear * 0.3 + recencyWeighted * 0.7;
  return MONITOR_EVENT_TS_BASE + offset;
}

export const MOCK_MONITOR_EVENTS_ENRICHED: MonitorEvent[] = MOCK_MONITOR_EVENTS.map((ev, i) => {
  const lineage = LINEAGE_BY_ACTION[ev.action] ?? {};
  return {
    ...ev,
    ts: scatterTs(i, MOCK_MONITOR_EVENTS.length, ev.agent_id, ev.action),
    trace_id: `trace_${ev.agent_id}_${Math.floor(i / 3)}`,
    source: lineage.source ?? null,
    target: lineage.target ?? null,
    severity: severityFor(ev),
    enforcement_latency_ms: 2 + Math.round(seededRandom(ev.agent_id + ev.action + i) * 18),
    retry_count: ev.result_action === 'retrying' ? 1 : 0,
  };
});

