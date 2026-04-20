"""Runnable monitoring demo for the scan_test_agent tools.

This script exercises the full Sponsio pipeline end-to-end:

    1. Resets the dashboard's in-memory monitor state.
    2. Creates an agent with the same tools as ``examples/scan_test_agent.py``.
    3. Commits a set of natural-language constraints (the same kind of
       contracts you'd get from clicking "Apply suggestions" after scanning
       ``scan_test_agent.py`` in the dashboard).
    4. Plays back a scripted sequence of tool calls through the Playground
       action endpoint, which runs each call through the monitor's
       enforcement pipeline.

Every action lands in the monitor's log, trace, and SSE stream, so if you
have the dashboard open at http://localhost:3000/monitor you'll see the
events appear in real-time, with both allowed calls and contract violations.

Usage::

    # Terminal 1:
    sponsio serve --dev   # starts API on :8000 and frontend on :3000

    # Terminal 2:
    python examples/scan_test_agent_run.py

    # Then open http://localhost:3000/monitor in your browser.

No LLM or API key required — the sequence is deterministic and designed
to trigger each of the constraints below at least once.
"""

from __future__ import annotations

import os
import random
import sys
import time
from typing import Any, Callable

import httpx

_FAST = os.environ.get("DEMO_FAST", "0") == "1"


DASHBOARD = "http://127.0.0.1:8000"
AGENT_ID = "scan_test_agent"


# ---------------------------------------------------------------------------
# Tool definitions — exist so this file is *also* uploadable to the Scan page
# ---------------------------------------------------------------------------
#
# CodeAnalyzer (the backend scanner) walks the AST looking for @tool-decorated
# functions. By defining the tools here alongside the runner logic, the same
# file can be:
#   - dragged into the Scan → Upload File tab (scanner finds 7 tools, scores
#     them, returns deductions + suggested contracts)
#   - executed directly via `python examples/scan_test_agent_run.py` (plays
#     back the scripted sequence below against a running dashboard)
#
# The @tool decorator is imported lazily so the runner still works in
# environments without langchain installed — it falls back to an identity
# decorator that keeps the functions callable but loses the langchain Tool
# wrapping (which the runner doesn't need anyway).

try:
    from langchain.tools import tool  # type: ignore[import-not-found]
except ImportError:
    def tool(fn: Callable[..., Any]) -> Callable[..., Any]:  # type: ignore[no-redef]
        """Identity fallback when langchain isn't installed."""
        return fn


@tool
def query_user_records(user_id: str) -> dict:
    """Read full PII records (email, SSN, address) from the users table.

    Sensitive read — pulls personally identifiable info that should never
    flow to external sinks without review.
    """
    return {"id": user_id, "email": "redacted@example.com"}


@tool
def query_orders(user_id: str) -> list:
    """Read a user's order history from the orders table."""
    return []


@tool
def issue_refund(order_id: str, amount: float) -> bool:
    """Issue a refund for an order. Writes to the orders + payments tables.

    Mutates financial state — must be idempotent, rate-limited, and
    preceded by a policy check.
    """
    return True


@tool
def delete_user(user_id: str) -> bool:
    """Delete a user record. Destructive and irreversible."""
    return True


@tool
def send_email(to: str, subject: str, body: str) -> bool:
    """Send an email to any recipient. External communication, no gating."""
    return True


@tool
def post_to_slack(channel: str, message: str) -> bool:
    """Post a message to a public Slack channel."""
    return True


@tool
def execute_sql(query: str) -> list:
    """Run arbitrary SQL against the production database.

    Privileged operation — no auth tool exists in this set, so this is a
    missing-auth gap that the scanner will flag.
    """
    return []

# Tools defined in examples/scan_test_agent.py. These names match the
# @tool-decorated functions so the monitor can attribute each action to a
# real tool in the agent's tool list.
TOOLS = [
    "query_user_records",
    "query_orders",
    "issue_refund",
    "delete_user",
    "send_email",
    "confirm_send",
    "post_to_slack",
    "execute_sql",
    "check_refund_policy",
]

# Constraints we want to enforce. These are deliberately a mix of
# must_precede (ordering) and rate_limit (frequency) so the script can
# trigger a variety of violations.
CONSTRAINTS = """\
tool `confirm_send` must precede `send_email`
tool `check_refund_policy` must precede `issue_refund`
tool `issue_refund` must not be called more than once
tool `delete_user` must not be called more than once
"""

# Scripted tool-call sequence. Each entry is (label, tool_name, expected_outcome)
# where expected_outcome is "ok" or "blocked" — shown only for readable output;
# the monitor is what actually decides.
SEQUENCE: list[tuple[str, str, str]] = [
    ("Read user's PII",                       "query_user_records",   "ok"),
    ("Read user's orders",                    "query_orders",         "ok"),
    ("Send email WITHOUT confirm → BLOCKED",  "send_email",           "blocked"),
    ("Confirm the outbound message",          "confirm_send",         "ok"),
    ("Send email (now allowed)",              "send_email",           "ok"),
    ("Refund WITHOUT policy check → BLOCKED", "issue_refund",         "blocked"),
    ("Check refund policy",                   "check_refund_policy",  "ok"),
    ("Issue refund (now allowed)",            "issue_refund",         "ok"),
    ("Second refund → rate_limit BLOCKED",    "issue_refund",         "blocked"),
    ("Delete user",                           "delete_user",          "ok"),
    ("Delete another user → rate_limit BLOCKED", "delete_user",       "blocked"),
    ("Post to Slack",                         "post_to_slack",        "ok"),
    ("Run privileged SQL",                    "execute_sql",          "ok"),
]


# ---------------------------------------------------------------------------
# Pretty output
# ---------------------------------------------------------------------------


def c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"


GREEN = "32"
RED = "31"
YELLOW = "33"
DIM = "2"
BOLD = "1"


def api(method: str, path: str, **kw) -> dict:
    """Send a request to the dashboard. Exits on hard errors."""
    try:
        r = httpx.request(method, f"{DASHBOARD}{path}", timeout=10.0, **kw)
    except httpx.ConnectError:
        print(c(RED, "✗ Cannot reach the dashboard at "), end="")
        print(c(BOLD, DASHBOARD))
        print(
            "  Start it with "
            + c(BOLD, "sponsio serve")
            + " in another terminal, then re-run this script."
        )
        sys.exit(1)
    if r.status_code >= 400:
        print(c(RED, f"✗ {method} {path} → {r.status_code}"))
        print(f"  {r.text[:300]}")
        sys.exit(1)
    return r.json() if r.content else {}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print(c(BOLD, "\n  Sponsio — scan_test_agent monitoring demo"))
    print(f"  dashboard: {DASHBOARD}")
    print(f"  agent:     {AGENT_ID}\n")

    # 1. Reset monitor so the trace starts fresh
    api("POST", "/api/playground/reset")
    print(c(DIM, "  · monitor reset"))

    # Also remove any previous constraints for this agent so repeated runs
    # don't stack them. 404 is fine — means there were none.
    try:
        httpx.delete(
            f"{DASHBOARD}/api/contracts/{AGENT_ID}", timeout=5.0
        )
    except httpx.ConnectError:
        pass

    # 2. Create the agent (idempotent — DELETE agent first if it existed)
    try:
        httpx.delete(f"{DASHBOARD}/api/agents/{AGENT_ID}", timeout=5.0)
    except httpx.ConnectError:
        pass
    api(
        "POST",
        "/api/agents",
        json={"id": AGENT_ID, "tools": TOOLS, "permissions": []},
    )
    print(c(DIM, f"  · agent created with {len(TOOLS)} tools"))

    # 3. Commit constraints
    resp = api(
        "POST",
        "/api/contracts",
        json={"agent_id": AGENT_ID, "nl_text": CONSTRAINTS.strip()},
    )
    print(c(DIM, f"  · committed {resp['contracts_count']} constraints"))

    print(c(DIM, "\n  Replaying tool-call sequence…\n"))

    # 4. Play back the sequence
    for label, tool, expected in SEQUENCE:
        result = api(
            "POST",
            "/api/playground/action",
            json={
                "agent_id": AGENT_ID,
                "action": tool,
                "event_type": "tool_call",
            },
        )
        allowed = result["allowed"]
        status = c(GREEN, "ALLOWED ") if allowed else c(RED, "BLOCKED ")

        # Highlight the label based on expected outcome
        label_col = (
            c(DIM, label)
            if allowed == (expected == "ok")
            else c(YELLOW, label)
        )
        print(f"  {status} {c(BOLD, tool):40s}  {label_col}")
        if not allowed and result["results"]:
            msg = result["results"][0]["message"]
            print(f"           {c(DIM, '↳ ' + msg[:80])}")
        if not _FAST:
            time.sleep(random.uniform(0.8, 1.5))

    print(c(DIM, "\n  Done. Open ") + c(BOLD, "http://localhost:3000/monitor"))
    print(c(DIM, "  to see the trace, violations, and enforcement log.\n"))


if __name__ == "__main__":
    main()
