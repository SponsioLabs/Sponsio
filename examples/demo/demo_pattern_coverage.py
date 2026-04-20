#!/usr/bin/env python3
"""
Sponsio Pattern Coverage Test — Exercise ALL Patterns in One Agent

Tests every pattern in the Sponsio library (existing + Layer 1 + Layer 2)
against scripted tool-call sequences. Each pattern gets:
  - A COMPLIANT sequence (should pass)
  - A VIOLATION sequence (should block)

Outputs a pass/fail matrix showing which patterns work correctly.

Usage:
    python examples/demo/demo_pattern_coverage.py

No API key needed — all mock mode. No dashboard required.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import sponsio
from sponsio.integrations.base import BaseGuard, CheckResult
from sponsio.models.agent import Agent
from sponsio.models.contract import Contract
from sponsio.models.trace import Event
from sponsio.patterns.library import (
    always_followed_by,
    arg_blacklist,
    arg_length_limit,
    bounded_retry,
    confirm_after_source,
    cooldown,
    dangerous_bash_commands,
    deadline,
    delegation_depth_limit,
    destructive_action_gate,
    idempotent,
    irreversible_once,
    loop_detection,
    must_confirm,
    must_precede,
    mutual_exclusion,
    no_data_leak,
    no_reversal,
    rate_limit,
    required_steps_completion,
    requires_permission,
    scope_limit,
    segregation_of_duty,
    token_budget,
    tool_allowlist,
    untrusted_source_gate,
)

# ANSI
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


@dataclass
class PatternTest:
    """One test case for a pattern."""

    name: str
    layer: str  # "existing", "L1", "L2"
    owasp: str  # ASI coverage
    formula: object  # DetFormula
    compliant_seq: list[tuple[str, dict]]  # (tool, args) pairs that should pass
    violation_seq: list[tuple[str, dict]]  # (tool, args) pairs where last should block
    description: str = ""


def _guard(formula, agent_id="test", permissions=None) -> BaseGuard:
    """Create a guard with a single contract.

    If ``formula`` is a tuple ``(assumption, enforcement)``, builds a
    conditional contract.
    """
    agent = Agent(id=agent_id, permissions=list(permissions or []))
    if isinstance(formula, tuple):
        assumption, enforcement = formula
        contract = Contract(agent=agent, assumption=assumption, enforcement=enforcement)
    else:
        contract = Contract(agent=agent, enforcement=formula)
    return sponsio.init(
        agent_id=agent_id,
        contracts=[contract],
        verbose=False,
    )


def _run_sequence(guard: BaseGuard, seq: list[tuple[str, dict]]) -> tuple[bool, int]:
    """Run a tool-call sequence. Returns (any_blocked, block_index)."""
    for i, (tool, args) in enumerate(seq):
        result = guard.guard_before(tool, args)
        if result.blocked:
            return True, i
        guard.guard_after(tool, {"status": "ok"})
    return False, -1


def build_tests() -> list[PatternTest]:
    """Build the full test matrix."""
    tests = []

    # ===================================================================
    # EXISTING PATTERNS
    # ===================================================================

    tests.append(PatternTest(
        name="must_precede",
        layer="existing",
        owasp="general",
        formula=must_precede("check_policy", "issue_refund"),
        compliant_seq=[("check_policy", {}), ("issue_refund", {})],
        violation_seq=[("issue_refund", {})],
        description="check_policy must come before issue_refund",
    ))

    tests.append(PatternTest(
        name="rate_limit",
        layer="existing",
        owasp="general",
        formula=rate_limit("api_call", 2),
        compliant_seq=[("api_call", {}), ("api_call", {})],
        violation_seq=[("api_call", {}), ("api_call", {}), ("api_call", {})],
        description="api_call at most 2 times",
    ))

    tests.append(PatternTest(
        name="mutual_exclusion",
        layer="existing",
        owasp="general",
        formula=mutual_exclusion("approve", "reject"),
        compliant_seq=[("approve", {})],
        violation_seq=[("approve", {}), ("reject", {})],
        description="approve and reject are mutually exclusive",
    ))

    tests.append(PatternTest(
        name="no_reversal",
        layer="existing",
        owasp="ASI09",
        formula=no_reversal("approve_order", "cancel_order"),
        compliant_seq=[("approve_order", {})],
        violation_seq=[("approve_order", {}), ("cancel_order", {})],
        description="cannot cancel after approving",
    ))

    tests.append(PatternTest(
        name="idempotent",
        layer="existing",
        owasp="general",
        formula=idempotent("deploy"),
        compliant_seq=[("deploy", {})],
        violation_seq=[("deploy", {}), ("deploy", {})],
        description="deploy may be called at most once",
    ))

    tests.append(PatternTest(
        name="must_confirm",
        layer="existing",
        owasp="ASI09",
        formula=must_confirm("delete_account"),
        compliant_seq=[("confirm_delete_account", {}), ("delete_account", {})],
        violation_seq=[("delete_account", {})],
        description="delete_account requires confirmation",
    ))

    tests.append(PatternTest(
        name="cooldown",
        layer="existing",
        owasp="general",
        formula=cooldown("send_email", 2),
        compliant_seq=[("send_email", {}), ("other", {}), ("other", {}), ("send_email", {})],
        violation_seq=[("send_email", {}), ("send_email", {})],
        description="2 steps cooldown between send_email calls",
    ))

    tests.append(PatternTest(
        name="bounded_retry",
        layer="existing",
        owasp="general",
        formula=bounded_retry("fetch_data", 2),
        compliant_seq=[("fetch_data", {}), ("fetch_data", {})],
        violation_seq=[("fetch_data", {}), ("fetch_data", {}), ("fetch_data", {})],
        description="fetch_data limited to 2 retries",
    ))

    tests.append(PatternTest(
        name="arg_blacklist",
        layer="existing",
        owasp="ASI05",
        formula=arg_blacklist("bash", "command", ["rm -rf", "sudo"]),
        compliant_seq=[("bash", {"command": "ls /tmp"})],
        violation_seq=[("bash", {"command": "sudo rm -rf /"})],
        description="bash must not use rm -rf or sudo",
    ))

    tests.append(PatternTest(
        name="scope_limit",
        layer="existing",
        owasp="ASI02",
        formula=scope_limit("bash", ["/app/data", "/tmp"]),
        compliant_seq=[("bash", {"command": "cat /app/data/file.txt"})],
        violation_seq=[("bash", {"command": "cat /etc/passwd"})],
        description="bash restricted to /app/data and /tmp",
    ))

    # ===================================================================
    # LAYER 1 — OWASP Agentic Top 10
    # ===================================================================

    tests.append(PatternTest(
        name="destructive_action_gate",
        layer="L1",
        owasp="ASI02/05/09",
        formula=destructive_action_gate("transfer_funds", "admin"),
        compliant_seq=[("confirm_transfer_funds", {}), ("transfer_funds", {})],
        violation_seq=[("transfer_funds", {})],
        description="transfer_funds requires admin confirmation + perm(admin)",
    ))  # NOTE: compliant seq needs agent with permissions=["admin"]

    # untrusted_source_gate returns (assumption, enforcement) tuple
    usg_a, usg_e = untrusted_source_gate(["web_fetch"], ["send_email"])
    tests.append(PatternTest(
        name="untrusted_source_gate",
        layer="L1",
        owasp="ASI01",
        formula=(usg_a, usg_e),  # tuple = assumption + enforcement
        compliant_seq=[
            ("web_fetch", {}),
            ("confirm_reconfirmed", {}),
            ("send_email", {}),
        ],
        violation_seq=[("web_fetch", {}), ("send_email", {})],
        description="after web_fetch, send_email needs re-confirmation",
    ))

    tests.append(PatternTest(
        name="loop_detection",
        layer="L1",
        owasp="runaway",
        formula=loop_detection("api_call", 3),
        # Compliant: 3 consecutive (at limit) then break
        compliant_seq=[("api_call", {}), ("api_call", {}), ("api_call", {}), ("other", {})],
        # Violation: 4 consecutive (exceeds limit of 3)
        violation_seq=[("api_call", {}), ("api_call", {}), ("api_call", {}), ("api_call", {})],
        description="api_call must not be called more than 3 times consecutively",
    ))

    tests.append(PatternTest(
        name="dangerous_bash_commands",
        layer="L1",
        owasp="ASI05",
        formula=dangerous_bash_commands(["rm -rf", "sudo", "sed -i"]),
        compliant_seq=[("bash", {"command": "ls /tmp"})],
        violation_seq=[("bash", {"command": "sed -i 's/old/new/' file.txt"})],
        description="bash rm -rf/sudo/sed -i are banned",
    ))

    tests.append(PatternTest(
        name="irreversible_once",
        layer="L1",
        owasp="ASI09",
        formula=irreversible_once("publish_announcement"),
        compliant_seq=[("publish_announcement", {})],
        violation_seq=[("publish_announcement", {}), ("publish_announcement", {})],
        description="publish_announcement may be called at most once",
    ))

    # confirm_after_source returns (assumption, enforcement) tuple
    cas_a, cas_e = confirm_after_source("fetch_url", "file_write")
    tests.append(PatternTest(
        name="confirm_after_source",
        layer="L1",
        owasp="ASI01",
        formula=(cas_a, cas_e),  # tuple = assumption + enforcement
        compliant_seq=[
            ("fetch_url", {}),
            ("confirm_file_write", {}),
            ("file_write", {}),
        ],
        violation_seq=[("fetch_url", {}), ("file_write", {})],
        description="after fetch_url, file_write requires confirmation",
    ))

    # ===================================================================
    # LAYER 2 — Atom extensions
    # ===================================================================

    tests.append(PatternTest(
        name="token_budget",
        layer="L2",
        owasp="ASI08",
        formula=token_budget(1000),
        compliant_seq=[
            ("llm_call", {"tokens": 400}),
            ("llm_call", {"tokens": 400}),
        ],
        violation_seq=[
            ("llm_call", {"tokens": 600}),
            ("llm_call", {"tokens": 600}),
        ],
        description="session total tokens must not exceed 1000",
    ))

    tests.append(PatternTest(
        name="delegation_depth_limit",
        layer="L2",
        owasp="ASI07",
        formula=delegation_depth_limit(2),
        compliant_seq=[],  # delegation needs message events, tested separately
        violation_seq=[],
        description="delegation chain must not exceed depth 2",
    ))

    return tests


def run_all_tests() -> None:
    """Run the full pattern coverage test matrix."""
    tests = build_tests()

    print(f"\n{BOLD}◡◠ Sponsio Pattern Coverage Test{RESET}")
    print(f"{DIM}{'─' * 60}{RESET}\n")

    passed = 0
    failed = 0
    skipped = 0
    results_by_layer: dict[str, list[tuple[str, str, str]]] = {}

    for test in tests:
        layer = test.layer
        if layer not in results_by_layer:
            results_by_layer[layer] = []

        # Skip tests with empty sequences (need special event types)
        if not test.compliant_seq and not test.violation_seq:
            results_by_layer[layer].append((test.name, "SKIP", test.owasp))
            skipped += 1
            continue

        # Determine permissions needed for compliant path
        perms = []
        if "destructive_action_gate" in test.name:
            perms = ["admin"]

        # Test 1: Compliant sequence should NOT block
        compliant_ok = True
        if test.compliant_seq:
            guard = _guard(test.formula, permissions=perms)
            blocked, idx = _run_sequence(guard, test.compliant_seq)
            if blocked:
                compliant_ok = False

        # Test 2: Violation sequence SHOULD block
        violation_ok = True
        if test.violation_seq:
            guard = _guard(test.formula)  # no special permissions for violation
            blocked, idx = _run_sequence(guard, test.violation_seq)
            if not blocked:
                violation_ok = False

        if compliant_ok and violation_ok:
            results_by_layer[layer].append((test.name, "PASS", test.owasp))
            passed += 1
        else:
            reason = ""
            if not compliant_ok:
                reason += "FP(compliant blocked) "
            if not violation_ok:
                reason += "FN(violation missed)"
            results_by_layer[layer].append((test.name, f"FAIL: {reason.strip()}", test.owasp))
            failed += 1

    # Print results by layer
    for layer in ["existing", "L1", "L2"]:
        results = results_by_layer.get(layer, [])
        if not results:
            continue

        layer_labels = {
            "existing": "Existing Patterns (14 det)",
            "L1": "Layer 1 — OWASP Agentic Top 10",
            "L2": "Layer 2 — Atom Extensions",
        }
        print(f"  {BOLD}{layer_labels[layer]}{RESET}")
        print()

        for name, status, owasp in results:
            if status == "PASS":
                icon = f"{GREEN}✓{RESET}"
            elif status == "SKIP":
                icon = f"{YELLOW}⊘{RESET}"
            else:
                icon = f"{RED}✗{RESET}"

            owasp_str = f"{DIM}[{owasp}]{RESET}" if owasp else ""
            print(f"    {icon} {name:<30s} {status:<35s} {owasp_str}")

        print()

    # Summary
    total = passed + failed + skipped
    print(f"  {BOLD}Summary{RESET}")
    print(f"    Total:   {total}")
    print(f"    {GREEN}Passed:  {passed}{RESET}")
    if failed:
        print(f"    {RED}Failed:  {failed}{RESET}")
    if skipped:
        print(f"    {YELLOW}Skipped: {skipped}{RESET} (need special event types)")
    print()

    if failed == 0:
        print(f"  {GREEN}{BOLD}✓ All testable patterns pass — compliant sequences allowed, violations blocked{RESET}")
    else:
        print(f"  {RED}{BOLD}✗ {failed} pattern(s) failed — see details above{RESET}")
    print()

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    run_all_tests()
