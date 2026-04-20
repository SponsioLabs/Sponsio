"""End-to-end pattern verification: NL string → BaseGuard.guard_before() → blocked/allowed."""

from __future__ import annotations

import warnings

from sponsio.generation.nl_to_contract import parse_nl_rule_based
from sponsio.integrations.base import BaseGuard


def _run_e2e(nl: str, steps: list[tuple[str, dict]], expect_blocked: set[int]):
    """Parse NL, build guard, run steps, check which are blocked."""
    parsed = parse_nl_rule_based(nl)
    assert parsed.ok, f"NL parse failed: {parsed.error}"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        guard = BaseGuard(agent_id="test", contracts=[nl], verbose=False)

    actual_blocked = set()
    for i, (tool, args) in enumerate(steps):
        r = guard.guard_before(tool, args)
        if r.blocked:
            actual_blocked.add(i)
        else:
            guard.guard_after(tool, {"status": "ok"})

    assert actual_blocked == expect_blocked, (
        f"expected blocked={expect_blocked}, got={actual_blocked}"
    )


# --- Patterns that work end-to-end ---


def test_must_precede():
    _run_e2e(
        "tool `verify_identity` must precede `transfer_funds`",
        [("transfer_funds", {}), ("verify_identity", {}), ("transfer_funds", {})],
        {0},
    )


def test_mutual_exclusion():
    _run_e2e(
        "tools `approve` and `reject` are mutually exclusive",
        [("approve", {}), ("reject", {})],
        {1},
    )


def test_never_together_routes_to_mutual_exclusion():
    _run_e2e(
        "tools `flag_content` and `delete_content` must never be called together",
        [("flag_content", {}), ("delete_content", {})],
        {1},
    )


def test_rate_limit():
    _run_e2e(
        "tool `transfer` at most 2 times",
        [("transfer", {}), ("transfer", {}), ("transfer", {})],
        {2},
    )


def test_no_reversal():
    # no_reversal(approve, reject): once approve is called, reject is forbidden
    _run_e2e(
        "tool `approve` cannot be reversed by `reject`",
        [("approve", {}), ("reject", {})],
        {1},
    )


def test_no_data_leak_routes_to_no_reversal():
    _run_e2e(
        "no data leak from `read_database` to `send_email`",
        [("read_database", {}), ("send_email", {})],
        {1},
    )


def test_requires_permission_routes_to_must_precede():
    _run_e2e(
        "tool `delete_content` requires permission `admin_permission`",
        [("delete_content", {}), ("admin_permission", {}), ("delete_content", {})],
        {0},
    )


def test_idempotent():
    _run_e2e(
        "tool `deploy` must be idempotent",
        [("deploy", {}), ("deploy", {})],
        {1},
    )


def test_must_confirm():
    _run_e2e(
        "tool `execute_query` must be confirmed before execution",
        [("execute_query", {}), ("confirm_execute_query", {}), ("execute_query", {})],
        {0},
    )


def test_cooldown():
    _run_e2e(
        "tool `write_api` cooldown of 2 steps",
        [
            ("write_api", {}),
            ("write_api", {}),
            ("filler_a", {}),
            ("filler_b", {}),
            ("write_api", {}),
        ],
        {1},
    )


def test_bounded_retry():
    _run_e2e(
        "tool `deploy_staging` at most 3 retries",
        [
            ("deploy_staging", {}),
            ("deploy_staging", {}),
            ("deploy_staging", {}),
            ("deploy_staging", {}),
        ],
        {3},
    )


def test_segregation_of_duty():
    _run_e2e(
        "tools `submit` and `approve` require segregation of duty",
        [("submit", {}), ("approve", {})],
        {1},
    )


# --- NL parsing only (patterns that parse but enforcement depends on grounding) ---


def test_always_followed_by_parses():
    parsed = parse_nl_rule_based(
        "whenever tool `start` is called, tool `cleanup` must eventually follow"
    )
    assert parsed.ok, f"Parse failed: {parsed.error}"
    assert parsed.pattern_name == "always_followed_by"
