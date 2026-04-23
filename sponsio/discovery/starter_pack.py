"""Name-heuristic starter contracts for no-LLM onboarding.

Produces a useful starter set of deterministic contracts from nothing but
the discovered tool inventory.  ``sponsio onboard`` calls this when the
user has no LLM provider configured (and no local Ollama running), so
the generated ``sponsio.yaml`` still protects something in observe mode
instead of shipping an empty agent block.

Every rule here maps directly onto an existing pattern from
``sponsio.patterns.library`` — we never invent new atoms, and every
proposed contract compiles through ``_compile_structured`` like any
other structured entry.  That means:

* the output round-trips through ``sponsio validate`` cleanly,
* users can trim / tune individual entries without special-casing,
* dropping the starter-pack later (once the user adds a provider) is
  a no-op — these contracts just get overwritten by the LLM pass.

Design principles:

1. **Conservative confidence.**  Every proposal ships with a
   confidence < 0.7 so ``sponsio scan``'s review hint naturally
   surfaces them for trimming.
2. **Zero false negatives for high-blast-radius actions.**  Anything
   that *looks* irreversible (``delete_*``, ``drop_*``, ``deploy_*``)
   gets ``irreversible_once`` even if the tool is misnamed.  Over-
   blocking is fine in observe mode; under-blocking is what we're
   trying to prevent.
3. **Every rule is framework-agnostic.**  No reliance on
   docstrings, param annotations, or call graphs — just names.
   Users who have those get richer contracts from the regular AST
   pass; starter-pack is the floor, not the ceiling.
"""

from __future__ import annotations

from collections.abc import Iterable

from sponsio.discovery._types import (
    ConstraintStatus,
    DiscoverySource,
    ProposedConstraint,
)
from sponsio.patterns.library import (
    arg_blacklist,
    dangerous_sql_verbs,
    delegation_depth_limit,
    irreversible_once,
    loop_detection,
    rate_limit,
    token_budget,
    tool_allowlist,
)

# Extractor tag used in ProposedConstraint.extractor.  Picked so
# ``generate_yaml`` treats starter-pack entries the same as other
# ``code_analysis*`` ones (``source: scan`` label in YAML).
EXTRACTOR_TAG = "code_analysis_starter"


# ---------------------------------------------------------------------------
# Name-based classifiers
# ---------------------------------------------------------------------------
#
# We match on *substring*, not word boundary.  A tool called
# ``deleteRecord`` or ``PurgeUserData`` has no whitespace to anchor on,
# and the dataflow risk is identical to ``delete_record``.  The only
# cost is slightly more false positives — acceptable because the
# resulting contract is safe in observe mode.

_IRREVERSIBLE_TOKENS: tuple[str, ...] = (
    # Data destruction
    "delete",
    "drop",
    "destroy",
    "wipe",
    "purge",
    "truncate",
    "remove_all",
    # Process / lifecycle (also captures ``shutdown_*``)
    "terminate",
    "kill",
    "shutdown",
    # Account / subscription state changes — added after the
    # ``cancel_subscription`` coverage gap surfaced from real user
    # tests; these are state transitions that re-entry would either
    # double-charge or trigger spurious downstream notifications.
    "cancel",
    "disable",
    "revoke",
    "deactivate",
    "suspend",
    "ban_user",
    "unsubscribe",
    # Deploy / release pipeline
    "deploy",
    "publish",
    "release",
    "force_push",
    "commit_and_push",
    "merge_pr",
    # Money movement — at-most-once is critical
    "execute_trade",
    "approve_payment",
    "issue_refund",
    "transfer_funds",
    "charge_card",
)

_BASH_TOKENS: tuple[str, ...] = (
    "bash",
    "shell",
    "run_command",
    "subprocess",
    "run_shell",
    "system_exec",
)

_SQL_TOKENS: tuple[str, ...] = (
    "sql",
    "execute_sql",
    "run_query",
    "query_db",
    "database_query",
    "postgres",
    "mysql",
    "sqlite",
    "bigquery",
)

_EXTERNAL_SEND_TOKENS: tuple[str, ...] = (
    "send_email",
    "send_sms",
    "send_message",
    "send_notification",
    "publish",
    "post_to",
    "tweet",
    "notify_channel",
    "broadcast",
    "webhook",
    "dispatch_webhook",
)


# Defaults — conservative caps that legitimate agents rarely bump into.
# All are tunable in the generated YAML.
RATE_LIMIT_DEFAULT = 10
LOOP_MAX_CONSECUTIVE = 5
TOKEN_BUDGET_DEFAULT = 100_000
DELEGATION_DEFAULT = 3


def _matches(name: str, tokens: tuple[str, ...]) -> str | None:
    """Return the token that matched, or ``None``.

    Case-insensitive substring match — see the class-doc on matching.
    Tokens are tried in declaration order so more specific ones
    (e.g. ``send_email``) take precedence over shorter prefixes.
    """
    n = name.lower()
    for tok in tokens:
        if tok in n:
            return tok
    return None


def _proposal(
    formula,
    args: list,
    nl: str,
    *,
    confidence: float = 0.6,
    heuristic: str = "starter_pack",
) -> ProposedConstraint:
    """Wrap a compiled DetFormula as a :class:`ProposedConstraint`.

    The ``evidence.args`` list is what :func:`sponsio.config._compile_structured`
    splats into the pattern function when the YAML is re-loaded, so it
    must match the original positional call shape exactly.
    """
    return ProposedConstraint(
        formula=formula,
        source=DiscoverySource.AUTO_EXTRACTED,
        extractor=EXTRACTOR_TAG,
        confidence=confidence,
        status=ConstraintStatus.PROPOSED,
        provenance="starter_pack",
        nl_description=nl,
        evidence={"args": args, "heuristic": heuristic},
    )


# ---------------------------------------------------------------------------
# Per-tool rules
# ---------------------------------------------------------------------------


def _per_tool_rules(name: str) -> list[ProposedConstraint]:
    out: list[ProposedConstraint] = []

    # Irreversible actions — at-most-once per session.  Highest
    # priority because double-triggering is the actual blast radius.
    if _matches(name, _IRREVERSIBLE_TOKENS):
        out.append(
            _proposal(
                irreversible_once(name),
                [name],
                f"{name} looks irreversible — allow at most once per session",
                confidence=0.7,
                heuristic="starter_irreversible",
            )
        )

    # Bash / shell-shaped tools — blacklist the classic footguns on
    # the first string-ish param.  We assume ``command`` by convention;
    # if the user's tool uses a different param name they'll rename
    # it in 10 seconds — cheaper than another round of AST inspection.
    if _matches(name, _BASH_TOKENS):
        patterns = [
            r"rm\s+-rf",
            r"\bsudo\b",
            r"chmod\s+-?R?\s*777",
            r"curl[^|]*\|\s*sh",
            r"wget[^|]*\|\s*sh",
        ]
        out.append(
            _proposal(
                arg_blacklist(name, "command", patterns),
                [name, "command", patterns],
                f"{name} must not run dangerous shell patterns",
                confidence=0.6,
                heuristic="starter_bash",
            )
        )

    # SQL tools — bind to the user's actual tool name.  We emit the
    # ``arg_blacklist`` shape directly (rather than ``dangerous_sql_verbs``)
    # because the latter *returns* an ``arg_blacklist`` formula whose
    # ``pattern_name`` is already ``"arg_blacklist"`` — the YAML round-
    # trip always splats args through the registered pattern name, so
    # emitting the aliased form would cause a signature mismatch on
    # reload.
    if _matches(name, _SQL_TOKENS):
        forbidden = ["DROP", "TRUNCATE", "ALTER", "DELETE"]
        out.append(
            _proposal(
                dangerous_sql_verbs(tool=name, forbidden=forbidden),
                [name, "query", forbidden],
                f"{name} must not use [{', '.join(forbidden)}]",
                confidence=0.6,
                heuristic="starter_sql",
            )
        )

    # External-send tools — conservative rate cap.  10/session catches
    # "LLM stuck in a loop emailing the same user" without bothering
    # legitimate bursty notifications (which should get a hand-tuned
    # limit anyway).
    if _matches(name, _EXTERNAL_SEND_TOKENS):
        out.append(
            _proposal(
                rate_limit(name, RATE_LIMIT_DEFAULT),
                [name, RATE_LIMIT_DEFAULT],
                f"{name} at most {RATE_LIMIT_DEFAULT} times per session",
                confidence=0.55,
                heuristic="starter_rate_limit",
            )
        )

    # Universal anti-runaway — calling any tool >5 times in a row is
    # almost always an agent stuck in a loop.  The cap is high enough
    # that legitimate retry chains (which should use ``bounded_retry``)
    # aren't affected.
    out.append(
        _proposal(
            loop_detection(name, LOOP_MAX_CONSECUTIVE),
            [name, LOOP_MAX_CONSECUTIVE],
            f"{name} at most {LOOP_MAX_CONSECUTIVE} consecutive calls",
            confidence=0.5,
            heuristic="starter_loop",
        )
    )

    return out


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def starter_contracts(
    tool_names: Iterable[str],
    *,
    include_delegation_limit: bool = True,
    include_token_budget: bool = True,
) -> list[ProposedConstraint]:
    """Produce starter det contracts from a bare tool inventory.

    Args:
        tool_names: Names of tools discovered in the user's code.
            Duplicates are de-duplicated; empty strings are dropped.
        include_delegation_limit: Emit ``delegation_depth_limit(3)``.
            Set False for frameworks that don't model delegation
            (plain function-calling loops) to avoid a rule that can
            never fire.
        include_token_budget: Emit a session-wide token cap.  Always
            safe; opt-out exists for tests that want a minimal bundle.

    Returns:
        A list of :class:`ProposedConstraint` objects, ready to be
        fed into ``CodeAnalyzer.generate_yaml`` via the standard
        proposals list.  Sorted by confidence (desc) so the YAML
        emitter's ``sorted(proposals, key=-confidence)`` keeps the
        most opinionated rules at the top of the file.
    """
    names = sorted({t for t in tool_names if t})
    proposals: list[ProposedConstraint] = []

    for name in names:
        proposals.extend(_per_tool_rules(name))

    # Global rules — apply regardless of tool inventory size.
    if names:
        # ``tool_allowlist`` is the first-line defence against
        # prompt-injected tool calls the agent never declared.
        proposals.append(
            _proposal(
                tool_allowlist(names),
                [names],
                f"only declared tools may be called ({len(names)} tool(s))",
                confidence=0.6,
                heuristic="starter_allowlist",
            )
        )

    if include_token_budget:
        proposals.append(
            _proposal(
                token_budget(TOKEN_BUDGET_DEFAULT, "total"),
                [TOKEN_BUDGET_DEFAULT, "total"],
                f"session token budget {TOKEN_BUDGET_DEFAULT:,}",
                confidence=0.5,
                heuristic="starter_token_budget",
            )
        )

    if include_delegation_limit:
        proposals.append(
            _proposal(
                delegation_depth_limit(DELEGATION_DEFAULT),
                [DELEGATION_DEFAULT],
                f"delegation chain max depth {DELEGATION_DEFAULT}",
                confidence=0.5,
                heuristic="starter_delegation",
            )
        )

    return proposals
