"""Tests for the safety scoring module."""

from __future__ import annotations

from sponsio.scoring import Deduction, ScoringReport, ToolDef, score_tools


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ids(report: ScoringReport) -> set[str]:
    """Extract check_ids from a report."""
    return {d.check_id for d in report.deductions}


# ---------------------------------------------------------------------------
# Perfect score: well-configured tool set
# ---------------------------------------------------------------------------


class TestPerfectScore:
    def test_minimal_safe_set(self):
        """A read-only tool set should score A+."""
        tools = [
            ToolDef("list_items", "List all items in inventory"),
            ToolDef("search_items", "Search inventory by keyword"),
        ]
        report = score_tools(tools)
        assert report.score == 100
        assert report.grade == "A+"
        assert report.deductions == []

    def test_empty_tools(self):
        report = score_tools([])
        assert report.score == 100
        assert report.grade == "A+"


# ---------------------------------------------------------------------------
# Category 1: Configuration Risk Checks
# ---------------------------------------------------------------------------


class TestUnguardedWrite:
    def test_write_without_read(self):
        tools = [
            ToolDef(
                "delete_user", "Delete a user record from database", {"user_id": "str"}
            ),
        ]
        report = score_tools(tools)
        assert "UNGUARDED_WRITE" in _ids(report)

    def test_write_with_read_same_domain(self):
        tools = [
            ToolDef("get_user", "Fetch user profile from database", {"user_id": "str"}),
            ToolDef(
                "update_user", "Update user record in database", {"user_id": "str"}
            ),
        ]
        report = score_tools(tools)
        assert "UNGUARDED_WRITE" not in _ids(report)


class TestExternalCommUngated:
    def test_send_without_confirm(self):
        tools = [
            ToolDef(
                "send_email", "Send email to recipient", {"to": "str", "body": "str"}
            ),
        ]
        report = score_tools(tools)
        assert "EXTERNAL_COMM_UNGATED" in _ids(report)

    def test_send_with_confirm(self):
        tools = [
            ToolDef("send_email", "Send email to recipient"),
            ToolDef("confirm_send", "Confirm before sending"),
        ]
        report = score_tools(tools)
        assert "EXTERNAL_COMM_UNGATED" not in _ids(report)


class TestSensitiveDataExposed:
    def test_read_sensitive_plus_send(self):
        tools = [
            ToolDef("query_users", "Read user records from database"),
            ToolDef("send_email", "Send email to recipient"),
        ]
        report = score_tools(tools)
        assert "SENSITIVE_DATA_EXPOSED" in _ids(report)

    def test_read_sensitive_plus_send_with_confirm(self):
        tools = [
            ToolDef("query_users", "Read user records from database"),
            ToolDef("send_email", "Send email to recipient"),
            ToolDef("approve_message", "Approve outgoing message"),
        ]
        report = score_tools(tools)
        assert "SENSITIVE_DATA_EXPOSED" not in _ids(report)


class TestNoRateLimit:
    def test_writes_flagged(self):
        tools = [
            ToolDef("create_order", "Create a new order"),
            ToolDef("get_order", "Get order details"),
        ]
        report = score_tools(tools)
        assert "NO_RATE_LIMIT_ON_WRITES" in _ids(report)

    def test_no_writes_clean(self):
        tools = [ToolDef("get_order", "Get order details")]
        report = score_tools(tools)
        assert "NO_RATE_LIMIT_ON_WRITES" not in _ids(report)

    def test_single_write_reduced_deduction(self):
        tools = [
            ToolDef("create_order", "Create a new order"),
            ToolDef("get_order", "Get order details"),
        ]
        report = score_tools(tools)
        rl = [d for d in report.deductions if d.check_id == "NO_RATE_LIMIT_ON_WRITES"]
        assert rl[0].points_lost == 3

    def test_two_writes_medium_deduction(self):
        tools = [
            ToolDef("create_order", "Create a new order"),
            ToolDef("update_order", "Update an existing order"),
            ToolDef("get_order", "Get order details"),
        ]
        report = score_tools(tools)
        rl = [d for d in report.deductions if d.check_id == "NO_RATE_LIMIT_ON_WRITES"]
        assert rl[0].points_lost == 5

    def test_many_writes_full_deduction(self):
        tools = [
            ToolDef("create_order", "Create a new order"),
            ToolDef("update_order", "Update an order"),
            ToolDef("delete_order", "Delete an order"),
            ToolDef("get_order", "Get order details"),
        ]
        report = score_tools(tools)
        rl = [d for d in report.deductions if d.check_id == "NO_RATE_LIMIT_ON_WRITES"]
        assert rl[0].points_lost == 8


class TestMissingAuth:
    def test_admin_without_auth(self):
        tools = [
            ToolDef("admin_delete", "Admin delete operation"),
        ]
        report = score_tools(tools)
        assert "MISSING_AUTH_CHECK" in _ids(report)

    def test_admin_with_auth(self):
        tools = [
            ToolDef("admin_delete", "Admin delete operation"),
            ToolDef("check_permissions", "Verify role permissions"),
        ]
        report = score_tools(tools)
        assert "MISSING_AUTH_CHECK" not in _ids(report)


class TestOverPrivileged:
    def test_many_tools(self):
        tools = [ToolDef(f"tool_{i}", f"Tool number {i}") for i in range(10)]
        report = score_tools(tools)
        assert "SINGLE_AGENT_FULL_ACCESS" in _ids(report)

    def test_few_tools(self):
        tools = [ToolDef(f"tool_{i}", f"Tool number {i}") for i in range(5)]
        report = score_tools(tools)
        assert "SINGLE_AGENT_FULL_ACCESS" not in _ids(report)


# ---------------------------------------------------------------------------
# Category 2: Contract Compliance Checks
# ---------------------------------------------------------------------------


class TestMustPrecedeGap:
    def test_write_with_read_but_no_ordering(self):
        """Read tool exists but no contract enforces ordering."""
        tools = [
            ToolDef("get_user", "Fetch user profile from database"),
            ToolDef("update_user", "Update user record in database"),
        ]
        report = score_tools(tools)
        assert "MUST_PRECEDE_GAP" in _ids(report)

    def test_write_without_any_read(self):
        """No read tool at all — UNGUARDED_WRITE fires, not MUST_PRECEDE_GAP."""
        tools = [
            ToolDef("delete_user", "Delete a user from database"),
        ]
        report = score_tools(tools)
        assert "UNGUARDED_WRITE" in _ids(report)


class TestDataLeakGap:
    def test_no_double_count_without_confirms(self):
        """Without confirms, Cat 1 SENSITIVE_DATA_EXPOSED covers it.
        Cat 2 NO_DATA_LEAK_GAP should NOT also fire."""
        tools = [
            ToolDef("fetch_patient", "Fetch patient records from database"),
            ToolDef("send_slack", "Send a Slack message"),
        ]
        report = score_tools(tools)
        assert "SENSITIVE_DATA_EXPOSED" in _ids(report)
        assert "NO_DATA_LEAK_GAP" not in _ids(report)

    def test_fires_when_confirms_exist_but_no_contract(self):
        """With confirms, Cat 1 is skipped. Cat 2 catches the gap."""
        tools = [
            ToolDef("fetch_patient", "Fetch patient records from database"),
            ToolDef("send_slack", "Send a Slack message"),
            ToolDef("approve_send", "Approve outgoing message"),
        ]
        report = score_tools(tools)
        assert "SENSITIVE_DATA_EXPOSED" not in _ids(report)
        assert "NO_DATA_LEAK_GAP" in _ids(report)


class TestIdempotencyGap:
    def test_financial_tool_flagged(self):
        tools = [
            ToolDef("transfer_funds", "Transfer money between accounts"),
            ToolDef("get_balance", "Check account balance"),
        ]
        report = score_tools(tools)
        assert "IDEMPOTENCY_GAP" in _ids(report)

    def test_non_financial_write_not_flagged(self):
        tools = [
            ToolDef("create_note", "Create a text note"),
        ]
        report = score_tools(tools)
        assert "IDEMPOTENCY_GAP" not in _ids(report)


# ---------------------------------------------------------------------------
# Grade mapping
# ---------------------------------------------------------------------------


class TestGradeMapping:
    def test_perfect(self):
        assert score_tools([]).grade == "A+"

    def test_a_range(self):
        # Single small deduction
        tools = [ToolDef(f"tool_{i}", f"Tool {i}") for i in range(9)]
        report = score_tools(tools)
        assert report.grade in ("A+", "A", "B")  # 95 = A

    def test_worst_case(self):
        """A maximally dangerous tool set should score F."""
        tools = [
            ToolDef("admin_delete", "Admin delete all user records from database"),
            ToolDef("send_email", "Send email notification via webhook"),
            ToolDef("transfer_funds", "Transfer payment to external account"),
            ToolDef("deploy_prod", "Deploy to production"),
            ToolDef("query_patients", "Query patient records from database"),
            ToolDef("execute_sql", "Execute raw SQL on database"),
            ToolDef("post_webhook", "Post to external webhook"),
            ToolDef("drop_table", "Drop database table"),
            ToolDef("push_notification", "Send push notification to users"),
        ]
        report = score_tools(tools)
        assert report.score < 60
        assert report.grade == "F"
        assert len(report.deductions) > 3


# ---------------------------------------------------------------------------
# Report structure
# ---------------------------------------------------------------------------


class TestReportStructure:
    def test_suggested_contracts_populated(self):
        tools = [
            ToolDef("send_email", "Send email"),
            ToolDef("query_users", "Read user data from database"),
        ]
        report = score_tools(tools)
        assert len(report.suggested_contracts) == len(report.deductions)
        assert all(isinstance(s, str) and s for s in report.suggested_contracts)

    def test_deduction_fields(self):
        tools = [ToolDef("delete_all", "Delete everything")]
        report = score_tools(tools)
        for d in report.deductions:
            assert isinstance(d.check_id, str)
            assert d.points_lost > 0
            assert isinstance(d.description, str)
            assert isinstance(d.affected_tools, list)
            assert isinstance(d.suggested_contract, str)

    def test_score_clamped_at_zero(self):
        """Score can't go below 0."""
        # Pile on every possible deduction.
        tools = [
            ToolDef("admin_delete", "Admin delete user from database"),
            ToolDef("send_email", "Send email via webhook"),
            ToolDef("transfer_funds", "Transfer payment to account"),
            ToolDef("deploy_prod", "Deploy to production"),
            ToolDef("query_patients", "Query patient records from db"),
            ToolDef("execute_sql", "Execute SQL on database"),
            ToolDef("post_webhook", "Post to external webhook"),
            ToolDef("drop_table", "Drop database table"),
            ToolDef("push_msg", "Push notification message"),
            ToolDef("remove_all", "Remove all records"),
        ]
        report = score_tools(tools)
        assert report.score >= 0

    def test_to_dict(self):
        tools = [ToolDef("send_email", "Send email")]
        report = score_tools(tools)
        d = report.to_dict()
        assert isinstance(d, dict)
        assert d["score"] == report.score
        assert d["grade"] == report.grade
        assert d["agent_name"] == "anonymous"
        assert isinstance(d["timestamp"], str) and d["timestamp"]
        assert isinstance(d["deductions"], list)
        for ded in d["deductions"]:
            assert "check_id" in ded
            assert "points_lost" in ded
        assert isinstance(d["suggested_contracts"], list)

    def test_to_badge_url(self):
        report = score_tools([])
        url = report.to_badge_url()
        assert "img.shields.io" in url
        assert "Sponsio_Safety" in url
        assert "brightgreen" in url  # A+ = brightgreen

    def test_to_badge_url_failing(self):
        tools = [
            ToolDef("admin_delete", "Admin delete user from database"),
            ToolDef("send_email", "Send email via webhook"),
            ToolDef("transfer_funds", "Transfer payment to account"),
            ToolDef("deploy_prod", "Deploy to production"),
            ToolDef("query_patients", "Query patient records from db"),
            ToolDef("execute_sql", "Execute SQL on database"),
            ToolDef("post_webhook", "Post to external webhook"),
            ToolDef("drop_table", "Drop database table"),
            ToolDef("push_msg", "Push notification message"),
            ToolDef("remove_all", "Remove all records"),
        ]
        report = score_tools(tools)
        url = report.to_badge_url()
        assert "red" in url  # F = red

    def test_agent_name(self):
        report = score_tools([], agent_name="customer_service_bot")
        assert report.agent_name == "customer_service_bot"
        assert report.to_dict()["agent_name"] == "customer_service_bot"

    def test_default_agent_name(self):
        report = score_tools([])
        assert report.agent_name == "anonymous"

    def test_timestamp_populated(self):
        report = score_tools([])
        assert report.timestamp
        assert "T" in report.timestamp  # ISO format

    def test_deduction_to_dict(self):
        d = Deduction(
            check_id="TEST",
            points_lost=5,
            description="test",
            affected_tools=["a"],
            suggested_contract="fix it",
        )
        dd = d.to_dict()
        assert dd == {
            "check_id": "TEST",
            "points_lost": 5,
            "description": "test",
            "affected_tools": ["a"],
            "suggested_contract": "fix it",
        }
