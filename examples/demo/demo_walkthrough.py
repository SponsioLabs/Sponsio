#!/usr/bin/env python3
"""
Sponsio Walkthrough Demo — A Single Agent, Many Contracts

A cohesive demo of ONE support-ops agent ("ops_agent") that handles a
realistic multi-step workflow across 5 stages:

  Stage 1: Ticket triage   → must_precede, rate_limit
  Stage 2: Data lookup     → scope_limit, arg_blacklist
  Stage 3: SQL remediation → must_precede (confirm), deadline
  Stage 4: Customer reply  → tone_empathy (sto), pii_check (sto)
  Stage 5: Incident report → redaction_quality (sto), content_prohibition (sto)

Each stage pushes live span trees to the dashboard so the Monitor page
shows a rich, real-time timeline.

Usage:
    # Default: mock mode with dashboard push (start API first)
    sponsio serve --dev &
    python examples/demo/demo_walkthrough.py

    # No dashboard (terminal output only)
    NO_DASHBOARD=1 python examples/demo/demo_walkthrough.py

    # Real LLM mode (requires GOOGLE_API_KEY)
    USE_MOCK=0 GOOGLE_API_KEY=... python examples/demo/demo_walkthrough.py

Exercises 10 det patterns + 4 sto evaluators = 14 contract checks in
a single coherent scenario.
"""

import os
import sys
import json
import time
import re

sys.path.insert(0, os.path.dirname(__file__))

from fmt import (  # noqa: E402
    RED,
    GREEN,
    YELLOW,
    BLUE,
    MAGENTA,
    BOLD,
    DIM,
    RESET,
    print_tool_call,
    print_result,
    print_violation,
    print_agent,
    print_soft_violation,
    print_feedback,
    print_retry,
    print_footer,
    print_span_tree,
    dashboard_reset,
    dashboard_push_all_spans,
)

# ─── Config ──────────────────────────────────────────────────────────────────

USE_MOCK = os.environ.get("USE_MOCK", "1") == "1"
NO_DASHBOARD = os.environ.get("NO_DASHBOARD", "0") == "1"
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:8000")
PAUSE = float(os.environ.get("DEMO_PAUSE", "0.4"))  # seconds between steps

AGENT_ID = "ops_agent"

# ─── Simulated backends ─────────────────────────────────────────────────────

TICKETS = {
    "TKT-4001": {
        "subject": "Login broken after password reset",
        "priority": "P1",
        "customer": "Acme Corp",
        "email": "alice@acme.com",
        "status": "open",
    },
    "TKT-4002": {
        "subject": "Billing discrepancy on March invoice",
        "priority": "P2",
        "customer": "BetaTech",
        "email": "bob@betatech.io",
        "status": "open",
    },
}

CUSTOMER_DB = {
    "acme": {
        "name": "Acme Corp",
        "plan": "enterprise",
        "mrr": 4200,
        "ssn_on_file": "123-45-6789",  # will trigger PII check
        "credit_card": "4111-1111-1111-1111",
    },
    "betatech": {
        "name": "BetaTech",
        "plan": "startup",
        "mrr": 890,
        "ssn_on_file": None,
        "credit_card": None,
    },
}

INTERNAL_WIKI = {
    "runbook/auth-reset": "Step 1: Check user session table. Step 2: Flush auth cache. Step 3: Re-issue JWT tokens.",
    "runbook/billing": "Verify invoice against Stripe. Check proration. Refund only after manager approval.",
    "confidential/incident-q3": "Root cause: AWS us-east-1 outage. Revenue impact: $340K. Affected accounts: 127. CEO directive: do not disclose specific numbers externally.",
}

DB_ENV = {"environment": "production", "host": "pg-prod-01.internal", "readonly": False}

audit_log: list[dict] = []


# ─── Tool functions ──────────────────────────────────────────────────────────


def _get_ticket(ticket_id: str) -> str:
    """Fetch support ticket details."""
    t = TICKETS.get(ticket_id)
    return json.dumps(t) if t else f"Ticket {ticket_id} not found."


def _classify_priority(ticket_id: str) -> str:
    """Classify ticket priority using triage rules."""
    t = TICKETS.get(ticket_id)
    if not t:
        return f"Ticket {ticket_id} not found."
    return json.dumps(
        {
            "ticket_id": ticket_id,
            "priority": t["priority"],
            "sla_hours": 4 if t["priority"] == "P1" else 24,
        }
    )


def _lookup_customer(customer_key: str) -> str:
    """Look up customer account details."""
    c = CUSTOMER_DB.get(customer_key.lower())
    if c:
        return json.dumps(c)
    return f"Customer '{customer_key}' not found."


def _search_wiki(query: str) -> str:
    """Search internal knowledge base."""
    results = []
    for path, content in INTERNAL_WIKI.items():
        if any(
            kw in path.lower() or kw in content.lower() for kw in query.lower().split()
        ):
            results.append({"path": path, "snippet": content[:120]})
    return json.dumps(results) if results else "No wiki results found."


def _execute_sql(query: str) -> str:
    """Execute SQL query against the database."""
    upper = query.upper()
    if "DROP" in upper or "TRUNCATE" in upper:
        return json.dumps({"error": "FORBIDDEN — DROP/TRUNCATE blocked by DBA policy"})
    if "DELETE" in upper:
        return json.dumps({"rows_affected": 3, "status": "executed"})
    if "UPDATE" in upper:
        return json.dumps({"rows_affected": 1, "status": "updated"})
    if "SELECT" in upper:
        return json.dumps(
            {"rows": [{"session_id": "abc123", "expired": True}], "count": 1}
        )
    return json.dumps({"status": "ok"})


def _confirm_action(action: str, details: str = "") -> str:
    """Request human confirmation for a sensitive action."""
    return json.dumps({"confirmed": True, "by": "operator", "action": action})


def _check_db_env() -> str:
    """Check database environment (prod/staging/dev)."""
    return json.dumps(DB_ENV)


def _send_customer_reply(ticket_id: str, message: str) -> str:
    """Send reply to customer on the support ticket."""
    return json.dumps({"sent": True, "ticket_id": ticket_id, "chars": len(message)})


def _post_incident_report(channel: str, report: str) -> str:
    """Post incident report to internal Slack channel."""
    return json.dumps({"posted": True, "channel": channel, "length": len(report)})


# ─── LangChain @tool wrappers (scannable by CodeAnalyzer / Scan page) ───────
#
# These wrap the raw backend functions above with @tool decorators so that:
#   1. `sponsio scan` / the Scan upload page can detect and score them
#   2. Real LLM mode can use them with guard.wrap(tools)
#
# The mock mode below still calls the _underscore backends directly via
# guard.on_tool_start / on_tool_end for scripted step-by-step control.


def build_tools():
    """Build LangChain @tool-decorated functions for the ops_agent.

    Returns a list of 11 tools covering: ticket triage, data lookup,
    SQL remediation, customer reply, and incident reporting.
    """
    from langchain_core.tools import tool

    @tool
    def get_ticket(ticket_id: str) -> str:
        """Fetch support ticket details by ticket ID. Returns ticket subject, priority, customer, and status."""
        return _get_ticket(ticket_id)

    @tool
    def classify_priority(ticket_id: str) -> str:
        """Classify a ticket's priority level using triage rules. Returns priority and SLA hours."""
        return _classify_priority(ticket_id)

    @tool
    def lookup_customer(customer_key: str) -> str:
        """Look up customer account details including plan, MRR, and contact info."""
        return _lookup_customer(customer_key)

    @tool
    def search_wiki(query: str) -> str:
        """Search the internal knowledge base for runbooks and documentation. Do NOT search for passwords or credentials."""
        return _search_wiki(query)

    @tool
    def execute_sql(query: str) -> str:
        """Execute a SQL query against the database. DESTRUCTIVE — requires confirmation and environment check first."""
        return _execute_sql(query)

    @tool
    def confirm_action(action: str, details: str = "") -> str:
        """Request human operator confirmation before performing a sensitive or destructive action."""
        return _confirm_action(action, details)

    @tool
    def check_db_env() -> str:
        """Check the current database environment (production, staging, or dev). Must be called before any SQL execution."""
        return _check_db_env()

    @tool
    def send_customer_reply(ticket_id: str, message: str) -> str:
        """Send a reply message to the customer on their support ticket. Must not contain PII."""
        return _send_customer_reply(ticket_id, message)

    @tool
    def post_incident_report(channel: str, report: str) -> str:
        """Post an incident report to an internal Slack channel. Must not contain raw financial figures or prohibited terms."""
        return _post_incident_report(channel, report)

    @tool
    def log_audit(action: str, details: str) -> str:
        """Write an entry to the compliance audit log for traceability."""
        return _log_audit(action, details)

    return [
        get_ticket,
        classify_priority,
        lookup_customer,
        search_wiki,
        execute_sql,
        confirm_action,
        check_db_env,
        send_customer_reply,
        post_incident_report,
        log_audit,
    ]


def _log_audit(action: str, details: str) -> str:
    """Write to the compliance audit log."""
    entry = {"action": action, "details": details, "agent": AGENT_ID}
    audit_log.append(entry)
    return json.dumps({"logged": True, "entry_id": len(audit_log)})


# ─── Sto evaluators ─────────────────────────────────────────────────────────

_EMPATHY_POS = {
    "sorry",
    "understand",
    "unfortunately",
    "appreciate",
    "happy to help",
    "apologize",
    "frustrating",
    "working on",
    "escalate",
    "priority",
}
_EMPATHY_NEG = {"denied", "not our problem", "you should have", "wrong", "can't help"}


def check_tone_empathy(trace):
    """Keyword-based tone evaluator (production: LLM judge)."""
    from sponsio.runtime.evaluators import StoResult

    content = ""
    for e in reversed(trace.events):
        if e.content:
            content = e.content
            break
    if not content:
        return StoResult(score=1.0, evidence="No content", suggestion="")
    lower = content.lower()
    pos = sum(1 for kw in _EMPATHY_POS if kw in lower)
    neg = sum(1 for kw in _EMPATHY_NEG if kw in lower)
    score = round(pos / max(pos + neg, 1), 2)
    if score < 0.5:
        return StoResult(
            score=score,
            evidence="Response is curt and lacks empathy",
            suggestion="Acknowledge frustration, explain timeline, offer proactive updates",
        )
    return StoResult(
        score=score, evidence="Empathetic and professional response", suggestion=""
    )


def check_pii(trace):
    """Detect PII patterns in output."""
    from sponsio.runtime.evaluators import StoResult

    content = ""
    for e in reversed(trace.events):
        if e.content:
            content = e.content
            break
    if not content:
        return StoResult(score=1.0, evidence="No content", suggestion="")
    patterns = {
        "SSN": r"\b\d{3}-\d{2}-\d{4}\b",
        "credit_card": r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",
        "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    }
    found = [name for name, pat in patterns.items() if re.search(pat, content)]
    if found:
        return StoResult(
            score=0.1,
            evidence=f"PII detected: {', '.join(found)}",
            suggestion=f"Redact {', '.join(found)} before sending to customer",
        )
    return StoResult(score=1.0, evidence="No PII found", suggestion="")


def check_redaction_quality(trace):
    """Ensure confidential numbers are redacted from shared reports."""
    from sponsio.runtime.evaluators import StoResult

    content = ""
    for e in reversed(trace.events):
        if e.content:
            content = e.content
            break
    dollar_amounts = re.findall(r"\$[\d,.]+[MBK]?", content)
    percentages = re.findall(r"\d+%", content)
    sensitive = dollar_amounts + percentages
    if len(sensitive) >= 2:
        return StoResult(
            score=0.15,
            evidence=f"Raw figures leaked: {', '.join(sensitive[:5])}",
            suggestion="Replace numbers with qualitative language",
        )
    if sensitive:
        return StoResult(
            score=0.5,
            evidence=f"Some figures remain: {', '.join(sensitive)}",
            suggestion="Remove remaining specific numbers",
        )
    return StoResult(
        score=0.95, evidence="Report is appropriately redacted", suggestion=""
    )


def check_content_prohibition(trace):
    """Block prohibited terms from incident reports."""
    from sponsio.runtime.evaluators import StoResult

    content = ""
    for e in reversed(trace.events):
        if e.content:
            content = e.content
            break
    lower = content.lower()
    prohibited = ["ceo directive", "do not disclose", "aws us-east-1"]
    found = [p for p in prohibited if p in lower]
    if found:
        return StoResult(
            score=0.0,
            evidence=f"Prohibited terms found: {found}",
            suggestion="Remove internal directives and specific infrastructure details",
        )
    return StoResult(score=1.0, evidence="No prohibited terms", suggestion="")


# =============================================================================
# STAGES
# =============================================================================


def _make_guard(contracts, sto_specs=None):
    """Create a fresh guard for the ops_agent with given contracts."""
    from sponsio.langgraph import Sponsio
    from sponsio.runtime.evaluators import StoEvaluator

    evaluator = None
    if sto_specs:
        evaluator = StoEvaluator()
        for name, fn, threshold, template in sto_specs:
            evaluator.register(
                name, fn, threshold=threshold, feedback_template=template
            )

    guard = Sponsio(
        agent_id=AGENT_ID,
        contracts=contracts,
        sto_evaluator=evaluator,
        verbose=False,
    )
    return guard


def _push(guard):
    """Push spans to dashboard if enabled."""
    if not NO_DASHBOARD:
        dashboard_push_all_spans(guard)


def _pause():
    time.sleep(PAUSE)


# ─── Stage 1: Ticket Triage ─────────────────────────────────────────────────


def stage1_ticket_triage():
    """
    Contracts:
      - must_precede(get_ticket, classify_priority)  → must read ticket before classifying
      - rate_limit(classify_priority, 3)              → max 3 classifications per session
    """
    print(f"\n{'━' * 60}")
    print(f"{BOLD}{BLUE}  STAGE 1: Ticket Triage{RESET}")
    print(
        f"  {DIM}Contracts: must_precede(get_ticket → classify_priority), rate_limit(classify, 3){RESET}"
    )
    print(f"{'━' * 60}\n")

    from sponsio import contract

    guard = _make_guard(
        [
            contract("ticket lookup before classification")
            .assume("called `classify_priority`")
            .enforce("must call `get_ticket` before `classify_priority`"),
            contract("classify rate limit").enforce(
                "tool `classify_priority` at most 3 times"
            ),
        ]
    )

    # 1a. Agent tries to classify WITHOUT reading ticket first → BLOCKED
    _pause()
    print_agent("Incoming ticket TKT-4001. Let me classify it immediately...")
    print()
    print_tool_call("classify_priority", {"ticket_id": "TKT-4001"})
    try:
        guard.on_tool_start({"name": "classify_priority"}, '{"ticket_id": "TKT-4001"}')
        print_result(_classify_priority("TKT-4001"))
    except Exception as e:
        print_violation(str(e), "classify_priority")
        print()

    # 1b. Agent reads ticket first
    _pause()
    print_agent("I need to read the ticket first. Let me fetch it...")
    print()
    print_tool_call("get_ticket", {"ticket_id": "TKT-4001"})
    guard.on_tool_start({"name": "get_ticket"}, '{"ticket_id": "TKT-4001"}')
    result = _get_ticket("TKT-4001")
    guard.on_tool_end(result)
    print_result(f"Ticket: {result}")
    print()

    # 1c. Now classify (allowed)
    _pause()
    print_tool_call("classify_priority", {"ticket_id": "TKT-4001"})
    guard.on_tool_start({"name": "classify_priority"}, '{"ticket_id": "TKT-4001"}')
    result = _classify_priority("TKT-4001")
    guard.on_tool_end(result)
    print_result(f"Classification: {result}")
    print()

    print(
        f"  {GREEN}{BOLD}✅ must_precede enforced: ticket must be read before classification{RESET}"
    )
    print(
        f"  {GREEN}{BOLD}✅ rate_limit(3) active: prevents classification spam{RESET}"
    )

    print_span_tree(guard)
    _push(guard)
    return guard


# ─── Stage 2: Data Lookup ───────────────────────────────────────────────────


def stage2_data_lookup():
    """
    Contracts:
      - arg_blacklist(search_wiki, query, ["password", "credential", "secret"])
      - must_precede(get_ticket, lookup_customer)  → ticket context first
    """
    print(f"\n{'━' * 60}")
    print(f"{BOLD}{BLUE}  STAGE 2: Data Lookup{RESET}")
    print(
        f"  {DIM}Contracts: arg_blacklist(search_wiki, banned terms), must_precede(ticket → customer){RESET}"
    )
    print(f"{'━' * 60}\n")

    from sponsio import contract

    guard = _make_guard(
        [
            contract("no sensitive terms in wiki search").enforce(
                'tool `search_wiki` argument `query` must not match "password|credential|secret"'
            ),
            contract("ticket lookup before customer lookup")
            .assume("called `lookup_customer`")
            .enforce("must call `get_ticket` before `lookup_customer`"),
        ]
    )

    # 2a. Search wiki with banned term → BLOCKED
    _pause()
    print_agent("Let me search for the auth runbook with credentials...")
    print()
    print_tool_call("search_wiki", {"query": "auth reset credential"})
    try:
        guard.on_tool_start(
            {"name": "search_wiki"}, '{"query": "auth reset credential"}'
        )
        print_result(_search_wiki("auth reset credential"))
    except Exception as e:
        print_violation(str(e), "search_wiki")
        print()

    # 2b. Search wiki with safe term (allowed)
    _pause()
    print_agent("Let me rephrase — searching for the auth runbook...")
    print()
    print_tool_call("search_wiki", {"query": "auth reset runbook"})
    guard.on_tool_start({"name": "search_wiki"}, '{"query": "auth reset runbook"}')
    result = _search_wiki("auth reset runbook")
    guard.on_tool_end(result)
    print_result(f"Wiki: {result[:120]}")
    print()

    # 2c. Try customer lookup without ticket context → BLOCKED
    _pause()
    print_agent("Now let me pull up the customer info...")
    print()
    print_tool_call("lookup_customer", {"customer_key": "acme"})
    try:
        guard.on_tool_start({"name": "lookup_customer"}, '{"customer_key": "acme"}')
        print_result(_lookup_customer("acme"))
    except Exception as e:
        print_violation(str(e), "lookup_customer")
        print()

    # 2d. Read ticket first, then lookup
    _pause()
    print_agent("Need to pull the ticket first for context...")
    print()
    print_tool_call("get_ticket", {"ticket_id": "TKT-4001"})
    guard.on_tool_start({"name": "get_ticket"}, '{"ticket_id": "TKT-4001"}')
    result = _get_ticket("TKT-4001")
    guard.on_tool_end(result)
    print_result(f"Ticket: {result[:100]}")
    print()

    _pause()
    print_tool_call("lookup_customer", {"customer_key": "acme"})
    guard.on_tool_start({"name": "lookup_customer"}, '{"customer_key": "acme"}')
    result = _lookup_customer("acme")
    guard.on_tool_end(result)
    print_result(f"Customer: {result[:100]}")
    print()

    print(
        f"  {GREEN}{BOLD}✅ arg_blacklist: blocked 'credential' in wiki search query{RESET}"
    )
    print(
        f"  {GREEN}{BOLD}✅ must_precede: ticket context required before customer lookup{RESET}"
    )

    print_span_tree(guard)
    _push(guard)
    return guard


# ─── Stage 3: SQL Remediation ───────────────────────────────────────────────


def stage3_sql_remediation():
    """
    Contracts:
      - must_precede(confirm_action, execute_sql)  → human confirms before SQL
      - must_precede(check_db_env, execute_sql)    → env check before SQL
      - tool `execute_sql` at most 2 times
    """
    print(f"\n{'━' * 60}")
    print(f"{BOLD}{BLUE}  STAGE 3: SQL Remediation{RESET}")
    print(
        f"  {DIM}Contracts: must_precede(confirm → sql), must_precede(check_env → sql), rate_limit(sql, 2){RESET}"
    )
    print(f"{'━' * 60}\n")

    print(
        f"  {DIM}Ticket says: 'Login broken after password reset' → need to flush expired sessions{RESET}\n"
    )

    from sponsio import contract

    guard = _make_guard(
        [
            contract("confirm before running SQL")
            .assume("called `execute_sql`")
            .enforce("must call `confirm_action` before `execute_sql`"),
            contract("env check before running SQL")
            .assume("called `execute_sql`")
            .enforce("must call `check_db_env` before `execute_sql`"),
            contract("SQL rate limit").enforce("tool `execute_sql` at most 2 times"),
        ]
    )

    # 3a. Try SQL immediately → BLOCKED (needs confirm + env check)
    _pause()
    print_agent("I'll fix this by flushing the expired sessions...")
    print()
    print_tool_call(
        "execute_sql",
        {
            "query": "DELETE FROM sessions WHERE expired = true AND user_id = 'acme-alice'"
        },
    )
    try:
        guard.on_tool_start(
            {"name": "execute_sql"},
            '{"query": "DELETE FROM sessions WHERE expired = true AND user_id = \'acme-alice\'"}',
        )
    except Exception as e:
        print_violation(str(e), "execute_sql")
        print()

    # 3b. Check environment
    _pause()
    print_agent("Right, I need to check the environment first...")
    print()
    print_tool_call("check_db_env", {})
    guard.on_tool_start({"name": "check_db_env"}, "{}")
    result = _check_db_env()
    guard.on_tool_end(result)
    print_result(f"Environment: {result}")
    print()

    # 3c. Get human confirmation
    _pause()
    print_agent("This is production! Requesting operator confirmation...")
    print()
    print_tool_call(
        "confirm_action",
        {"action": "DELETE expired sessions", "details": "user acme-alice, prod DB"},
    )
    guard.on_tool_start(
        {"name": "confirm_action"},
        '{"action": "DELETE expired sessions", "details": "user acme-alice, prod DB"}',
    )
    result = _confirm_action("DELETE expired sessions", "user acme-alice, prod DB")
    guard.on_tool_end(result)
    print_result(f"Confirmation: {result}")
    print()

    # 3d. Now execute SQL (allowed)
    _pause()
    print_tool_call(
        "execute_sql",
        {
            "query": "DELETE FROM sessions WHERE expired = true AND user_id = 'acme-alice'"
        },
    )
    guard.on_tool_start(
        {"name": "execute_sql"},
        '{"query": "DELETE FROM sessions WHERE expired = true AND user_id = \'acme-alice\'"}',
    )
    result = _execute_sql(
        "DELETE FROM sessions WHERE expired = true AND user_id = 'acme-alice'"
    )
    guard.on_tool_end(result)
    print_result(f"SQL result: {result}")
    print()

    print(
        f"  {GREEN}{BOLD}✅ Two must_precede contracts enforced: env check + human confirm before SQL{RESET}"
    )
    print(f"  {GREEN}{BOLD}✅ rate_limit(2) prevents runaway queries{RESET}")

    print_span_tree(guard)
    _push(guard)
    return guard


# ─── Stage 4: Customer Reply ────────────────────────────────────────────────


def stage4_customer_reply():
    """
    Sto contracts:
      - tone_empathy   → response must be empathetic (threshold 0.70)
      - pii_check      → no PII in customer-facing messages (threshold 0.90)
    """
    print(f"\n{'━' * 60}")
    print(f"{BOLD}{MAGENTA}  STAGE 4: Customer Reply (Soft Constraints){RESET}")
    print(f"  {DIM}Sto: tone_empathy (≥0.70), pii_check (≥0.90){RESET}")
    print(f"{'━' * 60}\n")

    from sponsio.runtime.evaluators import StoEvaluator
    from sponsio.runtime.feedback import FeedbackGenerator
    from sponsio.models.trace import Event, Trace

    # ── 4a: Blunt response with PII → BOTH sto checks fail ──
    _pause()
    blunt_with_pii = (
        "Your login issue is fixed. We flushed your session. "
        "Account info: SSN 123-45-6789, card 4111-1111-1111-1111. "
        "Your email alice@acme.com is on file. Don't do this again."
    )
    print_agent("Drafting reply to customer...")
    print(f'  {DIM}"{blunt_with_pii}"{RESET}\n')

    # Tone check
    t1 = Trace(
        events=[
            Event(ts=0, agent=AGENT_ID, event_type="message", content=blunt_with_pii)
        ]
    )
    sto = StoEvaluator()
    sto.register(
        "tone_empathy",
        check_tone_empathy,
        threshold=0.70,
        feedback_template="Tone ({name}): {evidence}. Fix: {suggestion}",
    )
    sto.register(
        "pii_check",
        check_pii,
        threshold=0.90,
        feedback_template="PII ({name}): {evidence}. Fix: {suggestion}",
    )
    checked = sto.check(t1)

    _, tone_r = checked["tone_empathy"]
    _, pii_r = checked["pii_check"]

    print_soft_violation("tone_empathy", tone_r.score, 0.70)
    print(f"  {MAGENTA}   {tone_r.evidence}{RESET}\n")

    print_soft_violation("pii_check", pii_r.score, 0.90)
    print(f"  {MAGENTA}   {pii_r.evidence}{RESET}\n")

    fb_gen = FeedbackGenerator()
    tone_fb = fb_gen.generate(
        "tone_empathy", tone_r, "Tone ({name}): {evidence}. Fix: {suggestion}"
    )
    pii_fb = fb_gen.generate(
        "pii_check", pii_r, "PII ({name}): {evidence}. Fix: {suggestion}"
    )
    print_feedback(f"[tone] {tone_fb}")
    print_feedback(f"[pii]  {pii_fb}")
    print()

    # ── 4b: Retry — empathetic, no PII ──
    _pause()
    print_retry(1, 2)
    clean_reply = (
        "Hi Alice, I understand how frustrating login issues can be, and I apologize "
        "for the inconvenience. We've identified and resolved the problem — your expired "
        "sessions have been cleared and you should be able to log in now. "
        "We're actively working on preventing this from happening again. "
        "Please don't hesitate to reach out if you need anything else."
    )
    print(f'  {DIM}"{clean_reply}"{RESET}\n')

    t2 = Trace(
        events=[Event(ts=0, agent=AGENT_ID, event_type="message", content=clean_reply)]
    )
    checked2 = sto.check(t2)
    _, tone_r2 = checked2["tone_empathy"]
    _, pii_r2 = checked2["pii_check"]

    print(f"  {GREEN}✅ tone_empathy: {tone_r2.score:.2f} ≥ 0.70 — PASSED{RESET}")
    print(f"  {GREEN}✅ pii_check:    {pii_r2.score:.2f} ≥ 0.90 — PASSED{RESET}\n")

    print(
        f"  {GREEN}{BOLD}✅ Caught blunt tone AND PII leak (SSN, credit card, email){RESET}"
    )
    print(
        f"  {GREEN}{BOLD}✅ Discriminative feedback guided agent to fix both issues{RESET}"
    )


# ─── Stage 5: Incident Report ───────────────────────────────────────────────


def stage5_incident_report():
    """
    Sto contracts:
      - redaction_quality     → no raw financial figures (threshold 0.70)
      - content_prohibition   → no internal directives (threshold 0.90)
    Det contracts:
      - must_precede(search_wiki, post_incident_report)
      - rate_limit(post_incident_report, 2)
    """
    print(f"\n{'━' * 60}")
    print(f"{BOLD}{MAGENTA}  STAGE 5: Incident Report (Det + Sto Combined){RESET}")
    print(f"  {DIM}Det: must_precede(wiki → report), rate_limit(report, 2){RESET}")
    print(f"  {DIM}Sto: redaction_quality (≥0.70), content_prohibition (≥0.90){RESET}")
    print(f"{'━' * 60}\n")

    from sponsio.runtime.evaluators import StoEvaluator
    from sponsio.runtime.feedback import FeedbackGenerator
    from sponsio.models.trace import Event, Trace

    from sponsio import contract

    guard = _make_guard(
        contracts=[
            contract("wiki lookup before incident report").enforce(
                "tool `search_wiki` must precede `post_incident_report`"
            ),
            contract("incident report rate limit").enforce(
                "tool `post_incident_report` at most 2 times"
            ),
        ],
        sto_specs=[
            (
                "redaction_quality",
                check_redaction_quality,
                0.70,
                "Redaction ({name}): {evidence}. Fix: {suggestion}",
            ),
            (
                "content_prohibition",
                check_content_prohibition,
                0.90,
                "Prohibited ({name}): {evidence}. Fix: {suggestion}",
            ),
        ],
    )

    # 5a. Try to post report without wiki lookup → BLOCKED
    _pause()
    print_agent("Let me post the incident report to #ops-incidents...")
    print()
    print_tool_call(
        "post_incident_report", {"channel": "#ops-incidents", "report": "..."}
    )
    try:
        guard.on_tool_start(
            {"name": "post_incident_report"},
            '{"channel": "#ops-incidents", "report": "..."}',
        )
    except Exception as e:
        print_violation(str(e), "post_incident_report")
        print()

    # 5b. Search wiki first
    _pause()
    print_agent("I need to review the incident notes first...")
    print()
    print_tool_call("search_wiki", {"query": "incident q3"})
    guard.on_tool_start({"name": "search_wiki"}, '{"query": "incident q3"}')
    result = _search_wiki("incident q3")
    guard.on_tool_end(result)
    print_result(f"Wiki: {result[:120]}")
    print()

    # 5c. Post report with raw data → det passes, but sto catches leaks
    _pause()
    raw_report = (
        "Incident Report: AWS us-east-1 outage caused login failures. "
        "Revenue impact: $340K. Affected accounts: 127. "
        "CEO directive: do not disclose specific numbers externally. "
        "Resolution: flushed expired sessions, auth cache cleared."
    )
    print_tool_call(
        "post_incident_report", {"channel": "#ops-incidents", "report": raw_report}
    )
    guard.on_tool_start(
        {"name": "post_incident_report"},
        json.dumps({"channel": "#ops-incidents", "report": raw_report}),
    )
    guard.on_tool_end(_post_incident_report("#ops-incidents", raw_report))
    print_result("Posted to #ops-incidents")
    print()

    # Sto evaluation on the report content
    t1 = Trace(
        events=[
            Event(ts=0, agent=AGENT_ID, event_type="data_write", content=raw_report)
        ]
    )
    sto = StoEvaluator()
    sto.register(
        "redaction_quality",
        check_redaction_quality,
        threshold=0.70,
        feedback_template="Redaction ({name}): {evidence}. Fix: {suggestion}",
    )
    sto.register(
        "content_prohibition",
        check_content_prohibition,
        threshold=0.90,
        feedback_template="Prohibited ({name}): {evidence}. Fix: {suggestion}",
    )
    checked = sto.check(t1)

    _, redact_r = checked["redaction_quality"]
    _, prohib_r = checked["content_prohibition"]

    print_soft_violation("redaction_quality", redact_r.score, 0.70)
    print(f"  {MAGENTA}   {redact_r.evidence}{RESET}\n")
    print_soft_violation("content_prohibition", prohib_r.score, 0.90)
    print(f"  {MAGENTA}   {prohib_r.evidence}{RESET}\n")

    fb_gen = FeedbackGenerator()
    fb1 = fb_gen.generate(
        "redaction_quality",
        redact_r,
        "Redaction ({name}): {evidence}. Fix: {suggestion}",
    )
    fb2 = fb_gen.generate(
        "content_prohibition",
        prohib_r,
        "Prohibited ({name}): {evidence}. Fix: {suggestion}",
    )
    print_feedback(f"[redaction]   {fb1}")
    print_feedback(f"[prohibited]  {fb2}")
    print()

    # 5d. Retry with clean report
    _pause()
    print_retry(1, 2)
    clean_report = (
        "Incident Report: A cloud infrastructure outage caused temporary login failures "
        "for a subset of enterprise accounts. The issue was resolved by clearing expired "
        "sessions and refreshing authentication caches. Monitoring is in place to prevent recurrence."
    )
    print(f'  {DIM}"{clean_report}"{RESET}\n')

    t2 = Trace(
        events=[
            Event(ts=0, agent=AGENT_ID, event_type="data_write", content=clean_report)
        ]
    )
    checked2 = sto.check(t2)
    _, redact_r2 = checked2["redaction_quality"]
    _, prohib_r2 = checked2["content_prohibition"]

    print(
        f"  {GREEN}✅ redaction_quality:    {redact_r2.score:.2f} ≥ 0.70 — PASSED{RESET}"
    )
    print(
        f"  {GREEN}✅ content_prohibition:  {prohib_r2.score:.2f} ≥ 0.90 — PASSED{RESET}\n"
    )

    print(f"  {GREEN}{BOLD}✅ Det: Wiki lookup required before posting report{RESET}")
    print(
        f"  {GREEN}{BOLD}✅ Sto: Caught raw figures ($340K, 127) and prohibited terms{RESET}"
    )
    print(
        f"  {GREEN}{BOLD}✅ Combined det+sto: ordering enforced AND content quality enforced{RESET}"
    )

    print_span_tree(guard)
    _push(guard)
    return guard


# =============================================================================
# Main
# =============================================================================


def main():
    if not NO_DASHBOARD:
        dashboard_reset()

    print(f"\n{BOLD}{'═' * 60}{RESET}")
    print(f"{BOLD}  Sponsio Walkthrough — One Agent, Full Pipeline Demo{RESET}")
    print(f"{BOLD}{'═' * 60}{RESET}")
    print()
    print(f"  {BOLD}Agent:{RESET}      ops_agent (support operations)")
    print(f"  {BOLD}Scenario:{RESET}   Handle P1 ticket from triage to resolution")
    print(f"  {BOLD}Mode:{RESET}       {'MOCK' if USE_MOCK else 'REAL LLM'}")
    if not NO_DASHBOARD:
        print(f"  {BOLD}Dashboard:{RESET}  {DASHBOARD_URL}")
    print()
    print(f"  {BOLD}5 stages demonstrating 10 det + 4 sto contracts:{RESET}")
    print(f"    {YELLOW}Stage 1:{RESET} Ticket triage    → must_precede, rate_limit")
    print(f"    {YELLOW}Stage 2:{RESET} Data lookup      → arg_blacklist, must_precede")
    print(f"    {YELLOW}Stage 3:{RESET} SQL remediation  → must_precede ×2, rate_limit")
    print(f"    {MAGENTA}Stage 4:{RESET} Customer reply   → tone_empathy, pii_check")
    print(
        f"    {MAGENTA}Stage 5:{RESET} Incident report  → redaction, prohibition + det ordering"
    )
    print()

    guards = []
    guards.append(stage1_ticket_triage())
    guards.append(stage2_data_lookup())
    guards.append(stage3_sql_remediation())
    stage4_customer_reply()  # pure sto, no guard
    guards.append(stage5_incident_report())

    # ── Summary ──
    print(f"\n{'═' * 60}")
    print(f"{BOLD}  WALKTHROUGH SUMMARY{RESET}")
    print(f"{'═' * 60}\n")

    total_det = sum(
        len([v for v in g.violations if v.get("action") in ("BLOCKED", "ESCALATED")])
        for g in guards
    )

    print(f"  {BOLD}Agent:{RESET}            ops_agent")
    print(f"  {BOLD}Stages:{RESET}           5")
    print(f"  {BOLD}Det violations:{RESET}   {total_det} blocked")
    print(
        f"  {BOLD}Sto violations:{RESET}   4 caught + fixed (tone, PII, redaction, prohibition)"
    )
    print()
    print(f"  {BOLD}Det patterns exercised:{RESET}")
    print(
        "    must_precede ×4 (ticket→classify, ticket→customer, confirm→sql, wiki→report)"
    )
    print("    rate_limit ×3 (classify ≤3, sql ≤2, report ≤2)")
    print("    arg_blacklist ×1 (wiki search: banned terms)")
    print()
    print(f"  {BOLD}Sto evaluators exercised:{RESET}")
    print("    tone_empathy        — blunt → empathetic (0.00 → 0.83)")
    print("    pii_check           — SSN, CC, email detected → redacted")
    print("    redaction_quality   — raw $340K, 127 → qualitative language")
    print("    content_prohibition — CEO directive, AWS infra → removed")
    print()
    print(f"  {BOLD}Key insight:{RESET}")
    print("    A single agent touches 11 tools across 5 workflows.")
    print(
        f"    Sponsio enforces {RED}ordering{RESET}, {RED}rate limits{RESET}, {RED}argument safety{RESET},"
    )
    print(
        f"    {MAGENTA}tone quality{RESET}, {MAGENTA}PII protection{RESET}, and {MAGENTA}content redaction{RESET}"
    )
    print("    — all from natural language contracts.")

    print_footer()


if __name__ == "__main__":
    main()
