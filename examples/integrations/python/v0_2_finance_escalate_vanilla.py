"""v0.2 case study: finance agent with EscalateToHuman + notify side effects.

Scenario: an AP (accounts payable) automation agent that pays
invoices. The desired behaviour: any call that violates the approved
toolset should both **refuse the call** AND **notify a human team**
out-of-band (Slack channel, finance lead email, oncall pager).

The point of this example is the **side effect**. Until v0.2,
``EscalateToHuman`` returned an outcome with the word "escalated" in
the message but had no out-of-band notification. v0.2 lets the
strategy take one or more notifier callables that fire synchronously
whenever the contract trips. Failure isolation is also demonstrated:
one broken webhook (PagerDuty outage) does NOT stop the other
notifiers from firing, and the escalation outcome still surfaces.

Since 0.2.0a17 ``EscalateToHuman`` refuses the call by default, so
**block + notify** is one strategy: ``result.allowed`` is False and
``result.escalated`` is True. Pass ``hold=False`` for notify-only.
(Before a17 the call ran, and this example had to gate it by hand.)

Run::

    python examples/integrations/python/v0_2_finance_escalate_vanilla.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from sponsio import contract  # noqa: E402
from sponsio.core import Sponsio  # noqa: E402
from sponsio.patterns import tool_allowlist  # noqa: E402
from sponsio.runtime.strategies import EscalateToHuman  # noqa: E402


# ---------------------------------------------------------------------------
# Fake notifier infrastructure. substitute these for real Slack /
# PagerDuty / email integrations in production.
# ---------------------------------------------------------------------------

AUDIT_LOG: list[str] = []


def slack_oncall(violation, ctx, reason):
    """A working Slack webhook stand-in."""
    AUDIT_LOG.append(f"slack: #oncall ← '{ctx.agent_id}.{ctx.action} paused: {reason}'")


def email_cfo(violation, ctx, reason):
    """A working email notifier stand-in."""
    AUDIT_LOG.append(
        f"email: cfo@company.com subject='approval needed' body='{ctx.action} / {reason}'"
    )


def pagerduty_broken(_violation, _ctx, _reason):
    """A notifier that always fails (simulates an outage).
    The escalation must still complete and the OTHER notifiers
    must still fire."""
    raise RuntimeError("pagerduty 500. service down")


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------

# tool_allowlist is the rule that EscalateToHuman is attached to.
# Anything outside the approved list trips the rule and the strategy
# fires both the refusal AND the notifiers.
APPROVED = tool_allowlist(["read_invoice", "list_vendors", "pay_vendor"])


def build_guard() -> Sponsio:
    return Sponsio(
        agent_id="ap_bot",
        contracts=[
            contract("approved AP toolset").guarantees(APPROVED),
        ],
        policy={
            # Bind EscalateToHuman to the toolset rule. The policy
            # dict is keyed by the formula's desc. same lookup the
            # monitor uses internally.
            APPROVED.desc: EscalateToHuman(
                reason="non-allow-listed tool requires CFO approval",
                notify=[slack_oncall, pagerduty_broken, email_cfo],
            ),
        },
        mode="enforce",
        verbose=False,
    )


def run() -> int:
    guard = build_guard()

    print("=" * 70)
    print("v0.2 case study: AP agent with EscalateToHuman + notifiers")
    print("=" * 70)
    print("Approved tools: read_invoice, list_vendors, pay_vendor")
    print("Notifiers:      slack_oncall + pagerduty_broken + email_cfo")
    print("                (pagerduty_broken simulates a service outage)")
    print()

    # 1. A normal allowed call. no escalation, no notify.
    print(">> step 1: agent reads an invoice (allowed)")
    r1 = guard.guard_before("read_invoice", {"invoice_id": "INV-001"})
    print(f"   allowed={r1.allowed}  escalated={r1.escalated}")

    # 2. A wire to an unapproved external service. rule fires.
    # EscalateToHuman fires the notifiers and refuses the call.
    print()
    print(">> step 2: agent attempts `wire_transfer` (not approved)")
    r2 = guard.guard_before("wire_transfer", {"to": "vendor_X", "amount": 250000})
    escalated = r2.escalated
    print(f"   allowed:          {r2.allowed}")
    print(f"   escalation fired: {escalated}")
    print(f"   agent will see: {r2.det_violations[0].agent_msg}")

    print()
    print("--- audit of side effects fired during step 2 ---")
    for line in AUDIT_LOG:
        print(f"   {line}")
    print("---")

    # Verify the broken notifier did not silence the others.
    if not any(line.startswith("slack:") for line in AUDIT_LOG):
        print("FAIL: slack notifier didn't fire")
        return 1
    if not any(line.startswith("email:") for line in AUDIT_LOG):
        print("FAIL: email notifier didn't fire (broken pagerduty took it down)")
        return 1
    if not escalated:
        print("FAIL: escalation action didn't fire on the violation")
        return 1
    if r2.allowed:
        print("FAIL: the escalated call should have been refused")
        return 1
    print()
    print("PASS: escalation fired notifiers AND refused the call,")
    print("PASS: the broken notifier's exception did not crash the agent loop.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
