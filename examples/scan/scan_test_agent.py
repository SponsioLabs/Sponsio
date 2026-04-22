"""Sample agent file for testing the Scan upload feature.

Drop this file into Scan → Upload File tab (or run `sponsio scan examples/
scan_test_agent.py` in your terminal) and you should see:

  * score around 50-60 (grade F) because every safety gap is present
  * 5+ deductions: UNGUARDED_WRITE, EXTERNAL_COMM_UNGATED,
    SENSITIVE_DATA_EXPOSED, NO_RATE_LIMIT_ON_WRITES, IDEMPOTENCY_GAP
  * 5+ suggested contracts ready to apply to the Rulebook

The tools below are deliberately unsafe to trigger every check in
`sponsio/scoring/scorer.py`. Do NOT use this file as a real agent.
"""

from langchain.tools import tool


@tool
def query_user_records(user_id: str) -> dict:
    """Read full PII records (email, SSN, address) from the users table.

    This is a sensitive read — it pulls personally identifiable info that
    should never flow to external sinks without review.
    """
    return {"id": user_id, "email": "redacted@example.com"}


@tool
def query_orders(user_id: str) -> list:
    """Read a user's order history from the orders table."""
    return []


@tool
def issue_refund(order_id: str, amount: float) -> bool:
    """Issue a refund for an order. Writes to the orders + payments tables.

    Mutates financial state — must be idempotent, rate-limited, and preceded
    by a policy check.
    """
    return True


@tool
def delete_user(user_id: str) -> bool:
    """Delete a user record from the users table. Destructive and irreversible."""
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
    missing-auth gap.
    """
    return []
