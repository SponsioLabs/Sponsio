"""One violation, said two ways.

A deterministic contract already knows exactly what it checked, so the
sentence a business reader needs does not have to be guessed by a model.
It can be looked up. This module is that lookup: pattern name plus the
arguments the pattern was built with, in; a title, a plain-language
account, why it matters, and what to change, out.

Two audiences, on purpose. A platform embedding Sponsio shows the
developer line to whoever maintains the agent and the business line to
whoever bought it, and neither should have to read the other's:

    developer   must_precede(check_policy, issue_refund) failed at step 1
    business    The agent issued a refund before checking the refund
                policy. Sponsio stopped the call.

No model runs here. The same violation produces the same sentence on
every machine, forever, which is what makes it safe to put in a
customer-facing dashboard, an audit export, or a diff.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence


def phrase(name: Any) -> str:
    """A tool name as prose: ``issue_refund`` -> ``issue refund``.

    Deliberately literal. Inflecting to "issues a refund" would need a
    verb lexicon, and a wrong guess in a customer-facing sentence is
    worse than a plain one.
    """
    return str(name).replace("_", " ").replace(".", " ").strip()


def _list(values: Any, quote: str = "`") -> str:
    if isinstance(values, (list, tuple, set)):
        items = [f"{quote}{v}{quote}" for v in values]
    else:
        items = [f"{quote}{values}{quote}"]
    if not items:
        return "(none)"
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " or " + items[-1]


def _plain(values: Any) -> str:
    if isinstance(values, (list, tuple, set)):
        items = [phrase(v) for v in values]
    else:
        items = [phrase(values)]
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " or " + items[-1]


def _n(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Rule:
    """How one pattern explains itself.

    ``severity`` is the pattern's inherent weight, before enforcement is
    considered. :func:`explain` lowers it when the rule only watched,
    because a rule that fires in observe mode is a finding about the agent
    and a rule that blocked is a finding about a call that did not happen.
    """

    category: str
    severity: str
    title: Callable[[Sequence], str]
    business: Callable[[Sequence], str]
    why: str
    fix: Callable[[Sequence], str]


def _a(args: Sequence, i: int, default: Any = "") -> Any:
    try:
        return args[i]
    except (IndexError, TypeError):
        return default


# ---------------------------------------------------------------------------
# The table. One entry per pattern in sponsio/patterns/library.py.
# ---------------------------------------------------------------------------

RULES: dict[str, Rule] = {
    # -- ordering and workflow ---------------------------------------------
    "must_precede": Rule(
        category="sequence",
        severity="high",
        title=lambda a: f"{phrase(_a(a,1)).capitalize()} runs before {phrase(_a(a,0))}",
        business=lambda a: (
            f"The agent tried to {phrase(_a(a,1))} without first doing "
            f"{phrase(_a(a,0))}."
        ),
        why="A required step was skipped, so the action ran on unchecked ground.",
        fix=lambda a: f"Call `{_a(a,0)}` before `{_a(a,1)}`, and refuse `{_a(a,1)}` until it has returned.",
    ),
    "always_followed_by": Rule(
        category="sequence",
        severity="medium",
        title=lambda a: f"{phrase(_a(a,0)).capitalize()} is never followed by {phrase(_a(a,1))}",
        business=lambda a: (
            f"The agent did {phrase(_a(a,0))} and then never did "
            f"{phrase(_a(a,1))}, leaving the task half finished."
        ),
        why="The follow-up that closes the loop never happened.",
        fix=lambda a: f"After `{_a(a,0)}` succeeds, always call `{_a(a,1)}` before the run ends.",
    ),
    "workflow_step": Rule(
        category="sequence",
        severity="medium",
        title=lambda a: "A workflow step ran out of order",
        business=lambda a: "The agent took a step in the workflow before the step it depends on.",
        why="Workflow order encodes a real dependency; out of order means acting on stale state.",
        fix=lambda a: "Reorder the plan so each step waits for its prerequisite.",
    ),
    "required_steps_completion": Rule(
        category="sequence",
        severity="medium",
        title=lambda a: "The agent finished with required steps undone",
        business=lambda a: "The run ended before every step the workflow requires had happened.",
        why="An incomplete run looks successful to the caller and is not.",
        fix=lambda a: "Check the required steps before returning, and continue instead of ending early.",
    ),
    "deadline": Rule(
        category="sequence",
        severity="medium",
        title=lambda a: f"{phrase(_a(a,1)).capitalize()} did not happen in time",
        business=lambda a: (
            f"After {phrase(_a(a,0))}, the agent had {_n(_a(a,2))} steps to "
            f"{phrase(_a(a,1))} and did not."
        ),
        why="A commitment was made and the follow-through never arrived.",
        fix=lambda a: (
            f"Do `{_a(a,1)}` within {_n(_a(a,2))} steps of `{_a(a,0)}`, "
            f"or do not call `{_a(a,0)}`."
        ),
    ),
    "audit_after": Rule(
        category="compliance",
        severity="medium",
        title=lambda a: f"{phrase(_a(a,0)).capitalize()} was not audited",
        business=lambda a: (
            f"The agent did {phrase(_a(a,0))} and never wrote the audit record "
            f"that is supposed to follow it."
        ),
        why="The action happened but the record that proves it did not.",
        fix=lambda a: f"Call `{_a(a,1)}` immediately after every `{_a(a,0)}`.",
    ),
    "dry_run_before_commit": Rule(
        category="safety",
        severity="high",
        title=lambda a: f"{phrase(_a(a,1)).capitalize()} ran without a dry run",
        business=lambda a: "The agent committed a change without previewing it first.",
        why="A preview is the last chance to catch a wrong change before it is real.",
        fix=lambda a: f"Call `{_a(a,0)}` and inspect its output before `{_a(a,1)}`.",
    ),
    "backup_before_destructive": Rule(
        category="safety",
        severity="critical",
        title=lambda a: f"{phrase(_a(a,1)).capitalize()} ran with no backup",
        business=lambda a: (
            f"The agent did {phrase(_a(a,1))} without taking a backup first, "
            f"so the change cannot be undone."
        ),
        why="Destructive without a backup is unrecoverable.",
        fix=lambda a: f"Call `{_a(a,0)}` and confirm it succeeded before `{_a(a,1)}`.",
    ),
    "sanitized_before_sink": Rule(
        category="security",
        severity="high",
        title=lambda a: f"Unsanitized data reached {phrase(_a(a,2))}",
        business=lambda a: (
            f"Data from {phrase(_a(a,0))} reached {phrase(_a(a,2))} without "
            f"going through {phrase(_a(a,1))} first."
        ),
        why="Untrusted input reaching a sink is the shape of an injection.",
        fix=lambda a: f"Route every value from `{_a(a,0)}` through `{_a(a,1)}` before `{_a(a,2)}`.",
    ),
    "confirm_after_source": Rule(
        category="security",
        severity="high",
        title=lambda a: f"{phrase(_a(a,1)).capitalize()} ran unconfirmed after untrusted input",
        business=lambda a: (
            f"The agent read from {phrase(_a(a,0))} and then did "
            f"{phrase(_a(a,1))} without asking anyone."
        ),
        why="Content the agent read can steer what it does next; a human breaks that chain.",
        fix=lambda a: f"Require a confirmation step between `{_a(a,0)}` and `{_a(a,1)}`.",
    ),
    "confirm_after_source_assumption": Rule(
        category="security",
        severity="high",
        title=lambda a: "An untrusted read went unconfirmed",
        business=lambda a: "The agent acted on content it had just read from an untrusted place.",
        why="Content the agent read can steer what it does next.",
        fix=lambda a: "Insert a confirmation step after reading untrusted content.",
    ),
    "untrusted_source_gate": Rule(
        category="security",
        severity="high",
        title=lambda a: "An action followed untrusted content with no gate",
        business=lambda a: (
            f"The agent read from {_plain(_a(a,0))} and acted on it without a check."
        ),
        why="This is the prompt-injection path: read something, then do what it says.",
        fix=lambda a: "Gate the actions that may follow an untrusted read.",
    ),
    "untrusted_source_gate_assumption": Rule(
        category="security",
        severity="high",
        title=lambda a: "An untrusted source was read",
        business=lambda a: "The agent read content from a source it does not control.",
        why="Untrusted content is the entry point for injection.",
        fix=lambda a: "Treat the read as untrusted and gate what may follow it.",
    ),

    # -- limits and loops ---------------------------------------------------
    "rate_limit": Rule(
        category="limit",
        severity="high",
        title=lambda a: f"{phrase(_a(a,0)).capitalize()} exceeded its limit of {_n(_a(a,1))}",
        business=lambda a: (
            f"The agent tried to {phrase(_a(a,0))} more than {_n(_a(a,1))} "
            f"time{'' if _n(_a(a,1)) == 1 else 's'} in one run."
        ),
        why="Repeating a real-world action is how a small bug becomes an expensive one.",
        fix=lambda a: f"Track how many times `{_a(a,0)}` has run and stop at {_n(_a(a,1))}.",
    ),
    "duplicate_call_limit": Rule(
        category="limit",
        severity="medium",
        title=lambda a: f"{phrase(_a(a,0)).capitalize()} repeated with the same arguments",
        business=lambda a: (
            f"The agent called {phrase(_a(a,0))} with the same input more than "
            f"{_n(_a(a,2))} times."
        ),
        why="Identical repeats are usually a retry loop, and each one can have a side effect.",
        fix=lambda a: f"Cache or dedupe `{_a(a,0)}` results instead of calling again.",
    ),
    "bounded_retry": Rule(
        category="limit",
        severity="medium",
        title=lambda a: f"{phrase(_a(a,0)).capitalize()} retried past {_n(_a(a,1))}",
        business=lambda a: (
            f"The agent kept retrying {phrase(_a(a,0))} after "
            f"{_n(_a(a,1))} failed attempt{'' if _n(_a(a,1)) == 1 else 's'}."
        ),
        why="Retrying a failing call rarely fixes it and always costs.",
        fix=lambda a: f"Give up on `{_a(a,0)}` after {_n(_a(a,1))} attempts and surface the failure.",
    ),
    "loop_detection": Rule(
        category="limit",
        severity="medium",
        title=lambda a: f"{phrase(_a(a,0)).capitalize()} ran in a loop",
        business=lambda a: (
            f"The agent called {phrase(_a(a,0))} {_n(_a(a,1))} times in a row "
            f"with no progress in between."
        ),
        why="A stuck agent burns budget and never finishes the task.",
        fix=lambda a: f"Break out of the loop after {_n(_a(a,1))} consecutive `{_a(a,0)}` calls.",
    ),
    "cooldown": Rule(
        category="limit",
        severity="low",
        title=lambda a: f"{phrase(_a(a,0)).capitalize()} repeated too soon",
        business=lambda a: (
            f"The agent did {phrase(_a(a,0))} again before the required "
            f"{_n(_a(a,1))}-step gap had passed."
        ),
        why="The gap exists so a downstream system can settle before the next call.",
        fix=lambda a: f"Wait {_n(_a(a,1))} steps between `{_a(a,0)}` calls.",
    ),
    "token_budget": Rule(
        category="cost",
        severity="medium",
        title=lambda a: "The run went over its token budget",
        business=lambda a: (
            f"The agent used more than {_n(_a(a,0)):,} tokens "
            f"({phrase(_a(a,1)) or 'total'})."
        ),
        why="Unbounded token use is unbounded cost.",
        fix=lambda a: "Shorten the context or cap the number of turns.",
    ),
    "delegation_depth_limit": Rule(
        category="limit",
        severity="medium",
        title=lambda a: f"Delegation went deeper than {_n(_a(a,0))}",
        business=lambda a: (
            f"An agent handed work to another agent more than {_n(_a(a,0))} "
            f"level{'' if _n(_a(a,0)) == 1 else 's'} deep."
        ),
        why="Deep delegation chains lose the original intent and are hard to audit.",
        fix=lambda a: f"Cap sub-agent delegation at {_n(_a(a,0))} levels.",
    ),
    "idempotent": Rule(
        category="limit",
        severity="high",
        title=lambda a: f"{phrase(_a(a,0)).capitalize()} ran more than once",
        business=lambda a: f"The agent did {phrase(_a(a,0))} twice, and it is meant to happen once.",
        why="Doing a once-only action twice usually means a duplicate charge, message or record.",
        fix=lambda a: f"Guard `{_a(a,0)}` with an idempotency key.",
    ),
    "irreversible_once": Rule(
        category="safety",
        severity="critical",
        title=lambda a: f"{phrase(_a(a,0)).capitalize()} was attempted twice",
        business=lambda a: (
            f"The agent tried to {phrase(_a(a,0))} a second time. This action "
            f"cannot be undone."
        ),
        why="An irreversible action repeated cannot be walked back.",
        fix=lambda a: f"Record that `{_a(a,0)}` ran and refuse the second call.",
    ),

    # -- authorisation and gates -------------------------------------------
    "must_confirm": Rule(
        category="approval",
        severity="high",
        title=lambda a: f"{phrase(_a(a,0)).capitalize()} ran without confirmation",
        business=lambda a: (
            f"The agent did {phrase(_a(a,0))} without anyone confirming it."
        ),
        why="The confirmation is the human in the loop; without it the agent acted alone.",
        fix=lambda a: f"Call `confirm_{_a(a,0)}` and require its approval before `{_a(a,0)}`.",
    ),
    "requires_permission": Rule(
        category="approval",
        severity="high",
        title=lambda a: f"{phrase(_a(a,0)).capitalize()} ran without the {phrase(_a(a,1))} permission",
        business=lambda a: (
            f"The agent did {phrase(_a(a,0))} but does not hold the "
            f"{phrase(_a(a,1))} permission."
        ),
        why="The agent acted outside what it is authorised to do.",
        fix=lambda a: (
            f"Either grant this agent the `{_a(a,1)}` permission in its agent "
            f"definition, or stop it from reaching `{_a(a,0)}`."
        ),
    ),
    "destructive_action_gate": Rule(
        category="approval",
        severity="critical",
        title=lambda a: f"{phrase(_a(a,0)).capitalize()} ran without {phrase(_a(a,1))} approval",
        business=lambda a: (
            f"The agent did {phrase(_a(a,0))}, which requires sign-off from "
            f"{phrase(_a(a,1))}, and no sign-off was present."
        ),
        why="A destructive action went ahead without the person accountable for it.",
        fix=lambda a: f"Require an approval from `{_a(a,1)}` before `{_a(a,0)}`.",
    ),
    "approval_freshness": Rule(
        category="approval",
        severity="high",
        title=lambda a: f"{phrase(_a(a,1)).capitalize()} used a stale approval",
        business=lambda a: (
            f"The approval for {phrase(_a(a,1))} was more than {_n(_a(a,2))} "
            f"steps old when it was used."
        ),
        why="An old approval was given for an older situation.",
        fix=lambda a: f"Re-request `{_a(a,0)}` if more than {_n(_a(a,2))} steps have passed.",
    ),
    "approval_active": Rule(
        category="approval",
        severity="high",
        title=lambda a: f"{phrase(_a(a,0)).capitalize()} ran on an expired approval",
        business=lambda a: (
            f"The {phrase(_a(a,1))} approval for {phrase(_a(a,0))} had expired "
            f"(valid for {_n(_a(a,2))}s)."
        ),
        why="An expired approval is not an approval.",
        fix=lambda a: f"Refresh the `{_a(a,1)}` approval before `{_a(a,0)}`.",
    ),
    "segregation_of_duty": Rule(
        category="compliance",
        severity="high",
        title=lambda a: f"One agent did both {phrase(_a(a,0))} and {phrase(_a(a,1))}",
        business=lambda a: (
            f"The same agent both did {phrase(_a(a,0))} and {phrase(_a(a,1))}. "
            f"These are meant to be done by different parties."
        ),
        why="Separation of duties is what stops one actor completing a whole fraud alone.",
        fix=lambda a: f"Route `{_a(a,1)}` to a different agent or a human.",
    ),
    "mutual_exclusion": Rule(
        category="safety",
        severity="medium",
        title=lambda a: f"{phrase(_a(a,0)).capitalize()} and {phrase(_a(a,1))} both ran",
        business=lambda a: (
            f"The agent did both {phrase(_a(a,0))} and {phrase(_a(a,1))} in one "
            f"run; only one of them is allowed."
        ),
        why="The two actions contradict each other and the end state is undefined.",
        fix=lambda a: f"Pick one of `{_a(a,0)}` / `{_a(a,1)}` per run.",
    ),
    "no_reversal": Rule(
        category="safety",
        severity="high",
        title=lambda a: f"{phrase(_a(a,1)).capitalize()} reversed an earlier {phrase(_a(a,0))}",
        business=lambda a: (
            f"The agent committed to {phrase(_a(a,0))} and then did "
            f"{phrase(_a(a,1))}, which undoes it."
        ),
        why="A commitment the agent takes back leaves whoever relied on it wrong.",
        fix=lambda a: f"Once `{_a(a,0)}` has run, refuse `{_a(a,1)}`.",
    ),

    # -- data boundaries ----------------------------------------------------
    "no_data_leak": Rule(
        category="security",
        severity="critical",
        title=lambda a: f"Data from {phrase(_a(a,0))} reached {phrase(_a(a,1))}",
        business=lambda a: (
            f"The agent moved data out of {phrase(_a(a,0))} and into "
            f"{phrase(_a(a,1))}, which is outside the allowed boundary."
        ),
        why="Data crossed a boundary it was not meant to cross.",
        fix=lambda a: f"Stop `{_a(a,1)}` from receiving anything sourced from `{_a(a,0)}`.",
    ),
    "data_intact": Rule(
        category="compliance",
        severity="high",
        title=lambda a: "Protected data was modified",
        business=lambda a: (
            f"The agent changed data under {_plain(_a(a,1))}, which is meant to "
            f"stay as it is."
        ),
        why="The record is supposed to be immutable after this point.",
        fix=lambda a: f"Make `{_a(a,0)}` read-only for these paths.",
    ),
    "scope_limit": Rule(
        category="security",
        severity="high",
        title=lambda a: f"{phrase(_a(a,0)).capitalize()} reached outside its allowed paths",
        business=lambda a: (
            f"The agent used {phrase(_a(a,0))} on something outside "
            f"{_plain(_a(a,1))}."
        ),
        why="The tool was pointed at data it has no business touching.",
        fix=lambda a: f"Restrict `{_a(a,0)}` to {_list(_a(a,1))}.",
    ),
    "no_pii": Rule(
        category="privacy",
        severity="critical",
        title=lambda a: "Personal data appeared in the agent's output",
        business=lambda a: (
            f"The agent's reply contained {_plain(_a(a,0)) or 'personal data'}."
        ),
        why="Personal data in an output leaves the boundary the moment it is shown.",
        fix=lambda a: "Redact these fields before returning the response.",
    ),
    "no_keywords": Rule(
        category="policy",
        severity="medium",
        title=lambda a: "The agent used forbidden wording",
        business=lambda a: "The agent's reply used wording that is not allowed.",
        why="Certain phrasing carries legal or brand risk regardless of intent.",
        fix=lambda a: f"Remove {_list(_a(a,0))} from the response.",
    ),
    "max_length": Rule(
        category="policy",
        severity="low",
        title=lambda a: "The agent's reply was too long",
        business=lambda a: (
            f"The reply exceeded the allowed length "
            f"({_n(_a(a,0))} words / {_n(_a(a,1))} characters)."
        ),
        why="Long replies cost more and get read less.",
        fix=lambda a: "Constrain the response length in the prompt or post-process it.",
    ),

    # -- arguments ----------------------------------------------------------
    "arg_blacklist": Rule(
        category="security",
        severity="high",
        title=lambda a: f"{phrase(_a(a,0)).capitalize()} was called with a forbidden {phrase(_a(a,1))}",
        business=lambda a: (
            f"The agent called {phrase(_a(a,0))} with a {phrase(_a(a,1))} value "
            f"that is not allowed."
        ),
        why="The argument matched a pattern that is known to be dangerous.",
        fix=lambda a: f"Validate `{_a(a,1)}` before calling `{_a(a,0)}` and reject {_list(_a(a,2))}.",
    ),
    "arg_allowlist": Rule(
        category="security",
        severity="high",
        title=lambda a: f"{phrase(_a(a,0)).capitalize()} was called with an unlisted {phrase(_a(a,1))}",
        business=lambda a: (
            f"The agent called {phrase(_a(a,0))} with a {phrase(_a(a,1))} value "
            f"outside the approved set."
        ),
        why="Only known-good values are meant to reach this tool.",
        fix=lambda a: f"Restrict `{_a(a,1)}` to {_list(_a(a,2))}.",
    ),
    "arg_value_range": Rule(
        category="safety",
        severity="high",
        title=lambda a: f"{phrase(_a(a,1)).capitalize()} was out of range",
        business=lambda a: (
            f"The agent called {phrase(_a(a,0))} with a {phrase(_a(a,1))} outside "
            f"the allowed range ({_a(a,2)} to {_a(a,3)})."
        ),
        why="Out-of-range values are where the expensive mistakes live.",
        fix=lambda a: f"Clamp or validate `{_a(a,1)}` to [{_a(a,2)}, {_a(a,3)}] before `{_a(a,0)}`.",
    ),
    "arg_length_limit": Rule(
        category="safety",
        severity="low",
        title=lambda a: f"{phrase(_a(a,1)).capitalize()} was too long",
        business=lambda a: (
            f"The agent passed a {phrase(_a(a,1))} longer than {_n(_a(a,2))} "
            f"characters to {phrase(_a(a,0))}."
        ),
        why="Oversized arguments are usually a sign of a runaway prompt or an injection.",
        fix=lambda a: f"Truncate or reject `{_a(a,1)}` over {_n(_a(a,2))} characters.",
    ),
    "ctx_required": Rule(
        category="safety",
        severity="medium",
        title=lambda a: f"{phrase(_a(a,0)).capitalize()} ran without required context",
        business=lambda a: (
            f"The agent called {phrase(_a(a,0))} without {phrase(_a(a,1))} set to "
            f"{_plain(_a(a,2))}."
        ),
        why="The tool needs this context to behave correctly.",
        fix=lambda a: f"Set `{_a(a,1)}` before calling `{_a(a,0)}`.",
    ),
    "ctx_matches_required": Rule(
        category="safety",
        severity="medium",
        title=lambda a: f"{phrase(_a(a,1)).capitalize()} did not match what {phrase(_a(a,0))} requires",
        business=lambda a: (
            f"The {phrase(_a(a,1))} in context did not match the expected form "
            f"when {phrase(_a(a,0))} ran."
        ),
        why="Acting on context in the wrong shape means acting on the wrong thing.",
        fix=lambda a: f"Validate `{_a(a,1)}` against `{_a(a,2)}` before `{_a(a,0)}`.",
    ),
    "time_since": Rule(
        category="safety",
        severity="medium",
        title=lambda a: f"{phrase(_a(a,0)).capitalize()} was too old",
        business=lambda a: (
            f"The agent relied on {phrase(_a(a,0))} that was more than "
            f"{_n(_a(a,1))} seconds old."
        ),
        why="Stale state leads to decisions about a world that has moved on.",
        fix=lambda a: f"Refresh `{_a(a,0)}` when it is older than {_n(_a(a,1))}s.",
    ),

    # -- tools --------------------------------------------------------------
    "tool_allowlist": Rule(
        category="policy",
        severity="high",
        title=lambda a: "The agent used a tool it is not allowed to use",
        business=lambda a: (
            f"The agent called a tool outside its approved set "
            f"({_plain(_a(a,0))})."
        ),
        why="The agent reached for a capability nobody granted it.",
        fix=lambda a: "Either add the tool to the allowlist deliberately, or remove it from the agent's tool set.",
    ),
    "redirect_to_safe": Rule(
        category="safety",
        severity="medium",
        title=lambda a: f"{phrase(_a(a,0)).capitalize()} was replaced with {phrase(_a(a,1))}",
        business=lambda a: (
            f"The agent reached for {phrase(_a(a,0))}; Sponsio ran "
            f"{phrase(_a(a,1))} instead."
        ),
        why="The agent keeps choosing the unsafe tool, which is a prompt problem, not a runtime one.",
        fix=lambda a: f"Remove `{_a(a,0)}` from the tool set and describe `{_a(a,1)}` in its place.",
    ),
    "dangerous_bash_commands": Rule(
        category="security",
        severity="critical",
        title=lambda a: "The agent tried to run a dangerous shell command",
        business=lambda a: "The agent tried to run a shell command that can destroy data or the machine.",
        why="One of these commands succeeding is an outage, not a bug.",
        fix=lambda a: f"Block {_list(_a(a,0))} at the tool layer, not only in the prompt.",
    ),
    "dangerous_sql_verbs": Rule(
        category="security",
        severity="critical",
        title=lambda a: "The agent tried to run a destructive SQL statement",
        business=lambda a: (
            f"The agent tried to run a statement using {_plain(_a(a,1))} against "
            f"the database."
        ),
        why="A destructive statement from an agent is not recoverable from the agent's side.",
        fix=lambda a: f"Give `{_a(a,0)}` a read-only connection.",
    ),

    # -- claims and evidence ------------------------------------------------
    "claim_requires_evidence": Rule(
        category="grounding",
        severity="high",
        title=lambda a: f"An unsupported claim about {phrase(_a(a,0))}",
        business=lambda a: (
            f"The agent stated something about {phrase(_a(a,0))} that nothing in "
            f"the run supports."
        ),
        why="A confident answer with no source behind it is the failure users notice last.",
        fix=lambda a: f"Require a `{_a(a,0)}` lookup before the agent asserts it.",
    ),
    "underdetermined_must_clarify": Rule(
        category="grounding",
        severity="medium",
        title=lambda a: "The agent guessed instead of asking",
        business=lambda a: (
            f"The request did not determine {phrase(_a(a,0))} and the agent "
            f"proceeded anyway."
        ),
        why="Guessing on an ambiguous request produces confident wrong work.",
        fix=lambda a: "Ask a clarifying question when the input is ambiguous.",
    ),
    "claim_requires_smt_valid": Rule(
        category="grounding",
        severity="high",
        title=lambda a: f"A claim about {phrase(_a(a,0))} does not hold",
        business=lambda a: (
            f"The agent asserted something about {phrase(_a(a,0))} that is not "
            f"true under the stated constraints."
        ),
        why="The statement contradicts the constraints it was derived from.",
        fix=lambda a: "Have the agent verify the claim before stating it.",
    ),
    "smt_premises_must_be_consistent": Rule(
        category="grounding",
        severity="high",
        title=lambda a: f"The constraints around {phrase(_a(a,0))} contradict each other",
        business=lambda a: (
            "The conditions the agent was working from cannot all be true at "
            "once, so any answer it gives is meaningless."
        ),
        why="An inconsistent premise set makes every conclusion derivable.",
        fix=lambda a: "Surface the contradiction to the user instead of answering.",
    ),
}


_SEVERITY_ORDER = ["low", "medium", "high", "critical"]


def _lower_severity(base: str, steps: int = 1) -> str:
    try:
        i = _SEVERITY_ORDER.index(base)
    except ValueError:
        return base
    return _SEVERITY_ORDER[max(i - steps, 0)]


@dataclass(frozen=True)
class Explanation:
    """One violation, explained for both audiences."""

    pattern: str
    category: str
    severity: str
    title: str
    developer: str
    business: str
    why: str
    fix: str
    known: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "category": self.category,
            "severity": self.severity,
            "title": self.title,
            "explanation": {"developer": self.developer, "business": self.business},
            "why": self.why,
            "fix": self.fix,
            "known": self.known,
            **self.extra,
        }


def explain(
    pattern: str,
    args: Sequence = (),
    *,
    rule_text: str = "",
    tool: str = "",
    action: str = "",
    step: int | None = None,
    run: str = "",
) -> Explanation:
    """Explain one violation of ``pattern``.

    ``action`` is what the runtime did: ``blocked`` means the call never
    happened, ``observed`` means it did and only the log knows. Severity
    moves with it: a rule that stopped a wire transfer is a bigger event
    than the same rule watching one go through in shadow mode, and a
    dashboard that ranks them the same ranks nothing.

    An unknown pattern still gets an answer. The alternative, dropping
    it, would make a new pattern invisible in every embedded dashboard
    until this table caught up, which is exactly the kind of silent gap
    that gets noticed by a customer rather than by us.
    """
    rule = RULES.get(pattern)
    where = ""
    if step is not None:
        where = f" at step {step}" + (f" of run {run}" if run else "")
    elif run:
        where = f" in run {run}"

    if rule is None:
        text = rule_text or pattern.replace("_", " ")
        return Explanation(
            pattern=pattern or "unknown",
            category="other",
            severity="medium",
            title=text[:1].upper() + text[1:] if text else "Contract violated",
            developer=f"{pattern}({', '.join(str(x) for x in args)}) failed{where}.",
            business=f"The agent broke the rule: {text}.",
            why="A contract the operator wrote was not satisfied.",
            fix="Review the rule and the step that violated it.",
            known=False,
        )

    severity = rule.severity
    if action in ("observed", "warned", "redirected"):
        # It happened. The rule saw it and let it through (or substituted),
        # which is a weaker signal about the deployment even when the rule
        # is grave.
        severity = _lower_severity(severity)

    business = rule.business(args)
    if action == "blocked":
        business += " Sponsio stopped the call."
    elif action == "escalated":
        business += " Sponsio stopped the call and raised it to a human."
    elif action in ("observed", "warned"):
        business += " Sponsio recorded it; the call still ran."
    elif action == "redirected":
        business += " Sponsio substituted a safe alternative."

    developer = f"{pattern}({', '.join(repr(x) for x in args)}) failed{where}."
    if tool:
        developer += f" Offending call: {tool}."

    return Explanation(
        pattern=pattern,
        category=rule.category,
        severity=severity,
        title=rule.title(args),
        developer=developer,
        business=business,
        why=rule.why,
        fix=rule.fix(args),
    )


def coverage() -> dict[str, Any]:
    """Which library patterns this table explains. Used by the test."""
    return {"explained": sorted(RULES), "count": len(RULES)}
