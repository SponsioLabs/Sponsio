"""Pattern library endpoint."""

from fastapi import APIRouter

router = APIRouter()

PATTERN_LIBRARY = [
    {
        "name": "must_precede",
        "category": "Safety",
        "example_nl": "tool `check_policy` must precede `issue_refund`",
        "description": "Enforces that one action must happen before another",
        "params": ["before_tool", "after_tool"],
    },
    {
        "name": "must_confirm",
        "category": "Safety",
        "example_nl": "`delete_record` requires confirmation",
        "description": "Requires a confirmation step before a dangerous action",
        "params": ["action"],
    },
    {
        "name": "requires_permission",
        "category": "Safety",
        "example_nl": "`transfer_funds` requires permission `manager_approval`",
        "description": "Tool requires a specific permission to execute",
        "params": ["tool", "permission"],
    },
    {
        "name": "no_data_leak",
        "category": "Safety",
        "example_nl": "no data leak from `db_query` to `call_external_api`",
        "description": "Prevents data flow from a source to an external sink",
        "params": ["source", "external_tool"],
    },
    {
        "name": "no_reversal",
        "category": "Compliance",
        "example_nl": "cannot `reject_claim` after `approve_claim`",
        "description": "Once action A is taken, action B is permanently forbidden",
        "params": ["first_action", "forbidden_action"],
    },
    {
        "name": "segregation_of_duty",
        "category": "Compliance",
        "example_nl": "review and approve by different agents",
        "description": "Two actions must be performed by different agents",
        "params": ["action_a", "action_b"],
    },
    {
        "name": "always_followed_by",
        "category": "Compliance",
        "example_nl": "`issue_refund` followed by `send_confirmation`",
        "description": "Whenever A happens, B must eventually follow",
        "params": ["trigger", "required_followup"],
    },
    {
        "name": "rate_limit",
        "category": "Operational",
        "example_nl": "`issue_refund` at most 3 times",
        "description": "Limits how many times an action can be called per session",
        "params": ["action", "max_count"],
    },
    {
        "name": "idempotent",
        "category": "Operational",
        "example_nl": "`transfer_funds` must execute at most once",
        "description": "Action may occur at most once (rate_limit with N=1)",
        "params": ["action"],
    },
    {
        "name": "cooldown",
        "category": "Operational",
        "example_nl": "2 steps between consecutive `send_email`",
        "description": "Minimum N steps between consecutive calls to an action",
        "params": ["action", "min_steps"],
    },
    {
        "name": "deadline",
        "category": "Operational",
        "example_nl": "`create_ticket` within 3 steps of `receive_complaint`",
        "description": "Action must happen within N steps of a trigger event",
        "params": ["trigger", "action", "max_steps"],
    },
    {
        "name": "bounded_retry",
        "category": "Operational",
        "example_nl": "`api_call` limited to 5 retries",
        "description": "Limits retry attempts for an action",
        "params": ["action", "max_retries"],
    },
    {
        "name": "never_together",
        "category": "Exclusion",
        "example_nl": "`approve` and `reject` never together",
        "description": "Two actions cannot both occur in the same session",
        "params": ["action_a", "action_b"],
    },
    {
        "name": "mutual_exclusion",
        "category": "Exclusion",
        "example_nl": "mutually exclusive `approve` and `reject`",
        "description": "At most one of two actions may ever be called",
        "params": ["action_a", "action_b"],
    },
    {
        "name": "arg_blacklist",
        "category": "FOL",
        "example_nl": "tool `execute_sql` must not contain `DROP` in `query` arg",
        "description": "Forbids specific patterns in tool arguments",
        "params": ["tool", "param", "forbidden_patterns"],
    },
    {
        "name": "scope_limit",
        "category": "FOL",
        "example_nl": "tool `read_file` restricted to `/safe/` paths",
        "description": "Restricts a tool to allowed argument values",
        "params": ["tool", "allowed_values"],
    },
]


@router.get("/library")
def get_pattern_library():
    """Return all available contract patterns with examples and descriptions."""
    return PATTERN_LIBRARY
