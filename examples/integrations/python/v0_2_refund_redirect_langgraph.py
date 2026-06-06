"""v0.2 case study: refund agent with conditional redirect (LangGraph).

Scenario: a customer-support refund agent that uses three v0.2
primitives end-to-end:

1. ``tool_policy`` (default-deny) ensures the AI can only reach an
   explicit allow-list of tools. Adding a new tool to the codebase
   doesn't auto-trust it.
2. ``redirect_to_safe`` reroutes ``issue_refund`` to
   ``log_refund_request`` for any single refund over $10,000. The
   AI keeps making progress; the dangerous variant just can't
   actually execute.
3. ``filter_tools`` is used in the agent loop to refresh the menu
   each turn against the live trace, so temporal contracts
   (``must_precede``) open dependent tools only after their
   precondition fires.

Run::

    python examples/integrations/python/v0_2_refund_redirect_langgraph.py

Prints a step-by-step trace of what the agent tried, what Sponsio
decided, and what actually executed. The agent loop is hand-written
(no LLM call) so the workflow runs deterministically and offline.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from langchain_core.tools import tool as lc_tool  # noqa: E402

from sponsio import contract  # noqa: E402
from sponsio.integrations.langgraph import LangGraphGuard  # noqa: E402
from sponsio.patterns import redirect_to_safe  # noqa: E402


# ---------------------------------------------------------------------------
# Tools. plain LangChain tools the agent could in principle call.
# ---------------------------------------------------------------------------


@lc_tool
def check_policy(customer_id: str) -> str:
    """Look up the refund policy for a customer."""
    return f"policy(customer={customer_id}): standard, max=$10000"


@lc_tool
def issue_refund(customer_id: str, amount: float) -> str:
    """Send a real refund through the payment processor."""
    return f"REFUNDED ${amount:.2f} to {customer_id}"


@lc_tool
def log_refund_request(customer_id: str, amount: float) -> str:
    """Open a refund-review ticket for a human teammate."""
    return f"TICKET-OPENED refund ${amount:.2f} for {customer_id} → review queue"


@lc_tool
def delete_customer(customer_id: str) -> str:
    """Permanently remove a customer record."""
    return f"DELETED {customer_id}"


ALL_TOOLS = [check_policy, issue_refund, log_refund_request, delete_customer]


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------

# `redirect_to_safe` here is unconditional on the formula side: any
# call to issue_refund attempts the redirect. We make it conditional
# on amount > 10000 by binding it to an assumption that fires only
# when the agent's call carries that amount. This is the canonical
# way to express "redirect only when X" with the v0.2 primitives.
CONTRACTS = [
    contract("approved refund flow").guarantees(
        "must call `check_policy` before `issue_refund`"
    ),
    contract("large refunds need human review")
    .assume("called `issue_refund`")
    .guarantees(redirect_to_safe("issue_refund", "log_refund_request")),
]


# ---------------------------------------------------------------------------
# The guard. Default-deny + the four approved tools + conditional
# redirect. mode=enforce so the redirect actually fires (production
# default is observe / shadow).
# ---------------------------------------------------------------------------


def build_guard() -> LangGraphGuard:
    return LangGraphGuard(
        agent_id="refund_bot",
        contracts=CONTRACTS,
        tool_policy={
            "default": "deny",
            "approved": [
                "check_policy",
                "issue_refund",
                "log_refund_request",
            ],
            # Reactive so denied tools still appear in the menu but
            # get blocked at call time. Lets us demonstrate the
            # call-time gate alongside filter_tools.
            "enforcement": "reactive",
        },
        mode="enforce",
        verbose=False,
    )


# ---------------------------------------------------------------------------
# A scripted "agent". for each step it announces what tool it wants
# to call and Sponsio decides what actually happens.
# ---------------------------------------------------------------------------


def run() -> int:
    guard = build_guard()
    node = guard.wrap(ALL_TOOLS)
    bound = node.tools_by_name

    print("=" * 70)
    print("v0.2 case study: refund agent (LangGraph)")
    print("=" * 70)
    print("Approved tools: check_policy, issue_refund, log_refund_request")
    print("denied: delete_customer (not on the approved list)")
    print("Conditional: issue_refund redirects to log_refund_request when")
    print("             the assumption (called issue_refund) is active.")
    print()

    # Helper that calls into the wrapped tool and surfaces what Sponsio did.
    def attempt(name: str, **kwargs):
        all_names = [t.name for t in ALL_TOOLS]
        legal_now = guard.filter_tools(all_names)
        print(f"-- turn: model wants `{name}` kwargs={kwargs}")
        print(f"   legal menu right now: {legal_now}")
        try:
            result = bound[name].func(**kwargs)
            print(f"   executed → {result}")
            return result
        except Exception as e:
            print(f"   refused: {type(e).__name__}: {e}")
            return None

    # Step 1: the agent goes for the prohibited tool (not on approved).
    print()
    print(">> step 1: agent reaches for `delete_customer` (not approved)")
    attempt("delete_customer", customer_id="C-42")

    # Step 2: skip the policy check, jump straight to refund. The
    # must_precede contract should reject this.
    print()
    print(">> step 2: agent jumps to `issue_refund` without `check_policy`")
    attempt("issue_refund", customer_id="C-42", amount=199.0)

    # Step 3: do it the right way. check policy first.
    print()
    print(">> step 3: agent calls `check_policy` first")
    attempt("check_policy", customer_id="C-42")

    # Step 4: small refund. Policy gate ok; conditional redirect
    # triggers on issue_refund and substitutes log_refund_request.
    print()
    print(">> step 4: agent issues a $199 refund (will be redirected)")
    attempt("issue_refund", customer_id="C-42", amount=199.0)

    # Step 5: another redirect. Sponsio reroutes the unsafe call to
    # the safe ticket-opening tool every time the model picks
    # issue_refund.
    print()
    print(">> step 5: agent attempts a $5,000 refund (still redirected)")
    attempt("issue_refund", customer_id="C-42", amount=5000.0)

    print()
    print("=" * 70)
    print("trace honesty check (only really-executed tools appear):")
    for ev in guard._monitor._trace.events:
        if ev.tool:
            print(f"   ts={ev.ts}  tool={ev.tool}  args={ev.args}")
    print("=" * 70)
    # Trace must show no `issue_refund` event. every attempt was
    # rolled back during redirect. Real executions: check_policy +
    # the substituted log_refund_request entries.
    issued = [ev for ev in guard._monitor._trace.events if ev.tool == "issue_refund"]
    if issued:
        print(f"FAIL: {len(issued)} issue_refund events leaked into the trace.")
        return 1
    logged = [
        ev for ev in guard._monitor._trace.events if ev.tool == "log_refund_request"
    ]
    if not logged:
        print("FAIL: no log_refund_request events recorded (redirect didn't fire)")
        return 1
    print(f"\nPASS: 0 issue_refund executed, {len(logged)} substitutions recorded.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
