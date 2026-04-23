"""Unit tests for sponsio/patterns/library.py — constraint DSL."""

import pytest

from sponsio.formulas.evaluator import evaluate
from sponsio.patterns.library import (
    DetFormula,
    always_followed_by,
    bounded_retry,
    confirm_after_source,
    cooldown,
    deadline,
    idempotent,
    must_confirm,
    must_precede,
    mutual_exclusion,
    never_together,
    no_data_leak,
    no_reversal,
    rate_limit,
    required_steps_completion,
    requires_permission,
    segregation_of_duty,
    untrusted_source_gate,
)


# ---------------------------------------------------------------------------
# Helpers — build minimal grounded traces
# ---------------------------------------------------------------------------


def _called(tool: str) -> dict:
    return {f"called({tool})": True}


def _precedes(before: str, after: str) -> dict:
    return {f"precedes({before}, {after})": True, f"called({after})": True}


def _with_perm(trace_step: dict, perm: str) -> dict:
    return {**trace_step, f"perm({perm})": True}


# ---------------------------------------------------------------------------
# DetFormula
# ---------------------------------------------------------------------------


def test_annotated_formula_has_attrs():
    af = must_precede("A", "B")
    assert isinstance(af, DetFormula)
    assert af.pattern_name == "must_precede"
    assert "A" in af.desc
    assert "B" in af.desc


def test_annotated_formula_custom_desc():
    af = must_precede("A", "B", desc="my custom description")
    assert af.desc == "my custom description"


def test_annotated_formula_delegates_operators():
    af = must_precede("A", "B")
    # Should not raise; delegates to inner formula
    result = ~af
    assert result is not None


# ---------------------------------------------------------------------------
# must_precede
# ---------------------------------------------------------------------------


def test_must_precede_violation_B_without_A():
    # B called without A ever being called — violation
    af = must_precede("check_policy", "issue_refund")
    trace = [_called("issue_refund")]
    assert evaluate(af.formula, trace) is False


def test_must_precede_satisfied():
    af = must_precede("check_policy", "issue_refund")
    trace = [
        _called("check_policy"),
        _precedes("check_policy", "issue_refund"),
    ]
    assert evaluate(af.formula, trace) is True


def test_must_precede_B_not_called():
    # B never called — vacuously satisfied
    af = must_precede("check_policy", "issue_refund")
    trace = [_called("check_policy")]
    assert evaluate(af.formula, trace) is True


# ---------------------------------------------------------------------------
# always_followed_by
# ---------------------------------------------------------------------------


def test_always_followed_by_satisfied():
    af = always_followed_by("send_email", "log_email")
    trace = [_called("send_email"), _called("log_email")]
    assert evaluate(af.formula, trace) is True


def test_always_followed_by_violated():
    af = always_followed_by("send_email", "log_email")
    trace = [_called("send_email")]  # log_email never called
    assert evaluate(af.formula, trace) is False


def test_always_followed_by_not_triggered():
    af = always_followed_by("send_email", "log_email")
    trace = [_called("other_tool")]  # trigger never fired
    assert evaluate(af.formula, trace) is True


# ---------------------------------------------------------------------------
# never_together
# ---------------------------------------------------------------------------


def test_never_together_delegates_to_mutual_exclusion():
    """never_together now delegates to mutual_exclusion with deprecation warning."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        af = never_together("approve", "reject")
    # Both called (same or different steps) → violated (mutual_exclusion semantics)
    trace = [{"called(approve)": True, "called(reject)": True}]
    assert evaluate(af.formula, trace) is False
    trace = [_called("approve"), _called("reject")]
    assert evaluate(af.formula, trace) is False


def test_never_together_neither_called():
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        af = never_together("approve", "reject")
    trace = [{}]
    assert evaluate(af.formula, trace) is True


# ---------------------------------------------------------------------------
# no_reversal
# ---------------------------------------------------------------------------


def test_no_reversal_contradiction_after_commitment_violated():
    af = no_reversal("approve_refund", "deny_refund")
    trace = [_called("approve_refund"), _called("deny_refund")]
    assert evaluate(af.formula, trace) is False


def test_no_reversal_only_commitment():
    af = no_reversal("approve_refund", "deny_refund")
    trace = [_called("approve_refund")]
    assert evaluate(af.formula, trace) is True


def test_no_reversal_neither():
    af = no_reversal("approve_refund", "deny_refund")
    trace = [{}]
    assert evaluate(af.formula, trace) is True


# ---------------------------------------------------------------------------
# requires_permission
# ---------------------------------------------------------------------------


def test_requires_permission_satisfied():
    af = requires_permission("transfer_funds", "manager")
    trace = [_with_perm(_called("transfer_funds"), "manager")]
    assert evaluate(af.formula, trace) is True


def test_requires_permission_violated():
    af = requires_permission("transfer_funds", "manager")
    trace = [_called("transfer_funds")]  # no perm
    assert evaluate(af.formula, trace) is False


def test_requires_permission_tool_not_called():
    af = requires_permission("transfer_funds", "manager")
    trace = [{}]
    assert evaluate(af.formula, trace) is True


# ---------------------------------------------------------------------------
# no_data_leak
# ---------------------------------------------------------------------------


def test_no_data_leak_violated():
    af = no_data_leak("pii", "external_api")
    trace = [{"contains(pii)": True, "flow(pii, external_api)": True}]
    assert evaluate(af.formula, trace) is False


def test_no_data_leak_contains_no_flow():
    af = no_data_leak("pii", "external_api")
    trace = [{"contains(pii)": True}]
    assert evaluate(af.formula, trace) is True


def test_no_data_leak_neither():
    af = no_data_leak("pii", "external_api")
    trace = [{}]
    assert evaluate(af.formula, trace) is True


# ---------------------------------------------------------------------------
# mutual_exclusion
# ---------------------------------------------------------------------------


def test_mutual_exclusion_both_called_violated():
    af = mutual_exclusion("approve", "reject")
    trace = [_called("approve"), _called("reject")]
    assert evaluate(af.formula, trace) is False


def test_mutual_exclusion_only_one():
    af = mutual_exclusion("approve", "reject")
    trace = [_called("approve"), _called("other")]
    assert evaluate(af.formula, trace) is True


def test_mutual_exclusion_neither():
    af = mutual_exclusion("approve", "reject")
    trace = [{}]
    assert evaluate(af.formula, trace) is True


# ---------------------------------------------------------------------------
# rate_limit
# ---------------------------------------------------------------------------


def test_rate_limit_within_limit():
    af = rate_limit("issue_refund", 1)
    trace = [{"count(issue_refund)": 1}]
    assert evaluate(af.formula, trace) is True


def test_rate_limit_exceeded():
    af = rate_limit("issue_refund", 1)
    trace = [{"count(issue_refund)": 2}]
    assert evaluate(af.formula, trace) is False


def test_rate_limit_zero_calls():
    af = rate_limit("issue_refund", 3)
    trace = [{}]  # count defaults to 0
    assert evaluate(af.formula, trace) is True


# ---------------------------------------------------------------------------
# idempotent
# ---------------------------------------------------------------------------


def test_idempotent_first_call_ok():
    af = idempotent("transfer")
    trace = [{"count(transfer)": 1}]
    assert evaluate(af.formula, trace) is True


def test_idempotent_second_call_violated():
    af = idempotent("transfer")
    trace = [{"count(transfer)": 1}, {"count(transfer)": 2}]
    assert evaluate(af.formula, trace) is False


def test_idempotent_never_called():
    af = idempotent("transfer")
    trace = [{}]
    assert evaluate(af.formula, trace) is True


# ---------------------------------------------------------------------------
# deadline
# ---------------------------------------------------------------------------


def test_deadline_met_within_steps():
    af = deadline("complaint", "create_ticket", 2)
    # Step 0: complaint called, step 1: create_ticket called (within 2 steps)
    trace = [
        _called("complaint"),
        _called("create_ticket"),
    ]
    assert evaluate(af.formula, trace) is True


def test_deadline_met_at_boundary():
    af = deadline("complaint", "create_ticket", 2)
    # Step 0: complaint, step 1: nothing, step 2: create_ticket
    trace = [
        _called("complaint"),
        {},
        _called("create_ticket"),
    ]
    assert evaluate(af.formula, trace) is True


def test_deadline_missed():
    af = deadline("complaint", "create_ticket", 2)
    # Step 0: complaint, step 1-3: nothing (missed deadline)
    trace = [
        _called("complaint"),
        {},
        {},
        _called("create_ticket"),  # too late
    ]
    assert evaluate(af.formula, trace) is False


def test_deadline_trigger_not_called():
    af = deadline("complaint", "create_ticket", 2)
    trace = [{}]  # trigger never fired — vacuously satisfied
    assert evaluate(af.formula, trace) is True


# ---------------------------------------------------------------------------
# must_confirm
# ---------------------------------------------------------------------------


def test_must_confirm_confirmed():
    af = must_confirm("issue_refund")
    trace = [
        _called("confirm_issue_refund"),
        _precedes("confirm_issue_refund", "issue_refund"),
    ]
    assert evaluate(af.formula, trace) is True


def test_must_confirm_not_confirmed():
    af = must_confirm("issue_refund")
    trace = [_called("issue_refund")]
    assert evaluate(af.formula, trace) is False


def test_must_confirm_action_not_called():
    af = must_confirm("issue_refund")
    trace = [{}]  # vacuously satisfied
    assert evaluate(af.formula, trace) is True


# ---------------------------------------------------------------------------
# cooldown
# ---------------------------------------------------------------------------


def test_cooldown_respected():
    af = cooldown("send_email", 2)
    # Call at step 0, next call at step 3 (2 steps gap)
    trace = [
        _called("send_email"),
        {},
        {},
        _called("send_email"),
    ]
    assert evaluate(af.formula, trace) is True


def test_cooldown_violated():
    af = cooldown("send_email", 2)
    # Call at step 0, next call at step 1 (too soon)
    trace = [
        _called("send_email"),
        _called("send_email"),
    ]
    assert evaluate(af.formula, trace) is False


def test_cooldown_single_call():
    af = cooldown("send_email", 3)
    trace = [_called("send_email")]
    assert evaluate(af.formula, trace) is True


# ---------------------------------------------------------------------------
# segregation_of_duty
# ---------------------------------------------------------------------------


def test_segregation_both_done_violated():
    af = segregation_of_duty("review", "approve")
    trace = [_called("review"), _called("approve")]
    assert evaluate(af.formula, trace) is False


def test_segregation_only_one():
    af = segregation_of_duty("review", "approve")
    trace = [_called("review")]
    assert evaluate(af.formula, trace) is True


def test_segregation_neither():
    af = segregation_of_duty("review", "approve")
    trace = [{}]
    assert evaluate(af.formula, trace) is True


# ---------------------------------------------------------------------------
# bounded_retry
# ---------------------------------------------------------------------------


def test_bounded_retry_within_limit():
    af = bounded_retry("api_call", 3)
    trace = [{"count(api_call)": 3}]
    assert evaluate(af.formula, trace) is True


def test_bounded_retry_exceeded():
    af = bounded_retry("api_call", 3)
    trace = [{"count(api_call)": 4}]
    assert evaluate(af.formula, trace) is False


def test_bounded_retry_zero():
    af = bounded_retry("api_call", 3)
    trace = [{}]
    assert evaluate(af.formula, trace) is True


# ---------------------------------------------------------------------------
# Degenerate-pattern rejection (Issue #14)
#
# Same-tool two-arg patterns and duplicated / empty tool names used to
# silently compile into either a tautology or a misleading no-op. The
# factory helpers now raise ValueError at construction time so the
# operator sees the mistake instead of a vacuously-passing contract.
# ---------------------------------------------------------------------------


class TestDegeneratePatternsRejected:
    @pytest.mark.parametrize(
        "factory, kwargs",
        [
            (must_precede, {"before": "A", "after": "A"}),
            (always_followed_by, {"trigger": "A", "response": "A"}),
            (no_reversal, {"commitment": "A", "contradiction": "A"}),
            (mutual_exclusion, {"a": "A", "b": "A"}),
            (segregation_of_duty, {"a": "A", "b": "A"}),
            (no_data_leak, {"source": "A", "external": "A"}),
        ],
    )
    def test_same_tool_rejected(self, factory, kwargs):
        """``f(X, X)`` now errors — previously compiled to a tautology."""
        with pytest.raises(ValueError, match="must refer to different"):
            factory(**kwargs)

    def test_deadline_same_tool_rejected(self):
        with pytest.raises(ValueError, match="must refer to different"):
            deadline("A", "A", 3)

    def test_deadline_nonpositive_steps_rejected(self):
        with pytest.raises(ValueError, match="positive integer"):
            deadline("trigger", "action", 0)
        with pytest.raises(ValueError, match="positive integer"):
            deadline("trigger", "action", -1)

    @pytest.mark.parametrize(
        "factory, kwargs",
        [
            (must_precede, {"before": "", "after": "B"}),
            (must_precede, {"before": "   ", "after": "B"}),
            (always_followed_by, {"trigger": "A", "response": ""}),
            (mutual_exclusion, {"a": "", "b": "B"}),
        ],
    )
    def test_empty_tool_name_rejected(self, factory, kwargs):
        """Empty / whitespace tool names silently disabled the contract."""
        with pytest.raises(ValueError, match="non-empty string"):
            factory(**kwargs)

    def test_confirm_after_source_same_tool_rejected(self):
        with pytest.raises(ValueError, match="must refer to different"):
            confirm_after_source("web_fetch", "web_fetch")

    def test_required_steps_trigger_in_set_rejected(self):
        """Trigger appearing as its own follow-up silently satisfied the F()."""
        with pytest.raises(ValueError, match="trigger .* cannot also appear"):
            required_steps_completion("start", ["start", "cleanup"])

    def test_required_steps_duplicate_rejected(self):
        with pytest.raises(ValueError, match="duplicate"):
            required_steps_completion("start", ["cleanup", "cleanup"])

    def test_required_steps_empty_rejected(self):
        with pytest.raises(ValueError, match="must not be empty"):
            required_steps_completion("start", [])

    def test_untrusted_source_gate_empty_sources_rejected(self):
        with pytest.raises(ValueError, match="'sources' must not be empty"):
            untrusted_source_gate([], ["send_email"])

    def test_untrusted_source_gate_overlap_rejected(self):
        """A tool that is both source and sink would trigger its own guard."""
        with pytest.raises(ValueError, match="overlap"):
            untrusted_source_gate(["do_it"], ["do_it"])

    def test_distinct_pairs_still_work(self):
        """Non-degenerate construction must not regress."""
        must_precede("verify", "transfer")
        always_followed_by("order", "deliver")
        mutual_exclusion("approve", "reject")
        deadline("alert", "respond", 3)
        required_steps_completion("open_case", ["triage", "close_case"])
        untrusted_source_gate(["web_fetch"], ["send_email"])
